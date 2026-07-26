"""Per-model worker: load one model (smoke test folded into the load), generate
over all outstanding scenarios, write the Model Output schema to this model's
shard, upload to S3.

Resume: existing shard Scenario ids are skipped, so an interrupted run continues
where it stopped. Failure isolation: a load or generation error is recorded as an
Issue cell (never crashes the fleet), so every (model, scenario) still has a row.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from . import prompts as P
from . import records as R
from .backends import GenParams, GenResult
from .manifest import ModelSpec
from .registry import ResolvedModel, resolve
from .s3 import maybe_upload
from .shard import ShardWriter, completed_ids, shard_path


def load_scenarios(path: str | Path, limit: int | None = None) -> list:
    """Load scenarios.jsonl as Scenario objects (text modality only). Reuses the
    project's _load_jsonl + Scenario.from_json rather than reparsing."""
    from ..dataio import _load_jsonl
    from ..schemas import Scenario

    rows = _load_jsonl(Path(path))
    scenarios = [
        Scenario.from_json(o) for o in rows if o.get("modality", "text") == "text"
    ]
    return scenarios[:limit] if limit else scenarios


def _resolve_revision(model_id: str, override: str | None) -> str:
    """Pin the exact commit for provenance. Empty string if the Hub is
    unreachable (recorded as-is; the default branch is then used at load)."""
    if override:
        return override
    try:
        from huggingface_hub import HfApi  # lazy

        return HfApi().model_info(model_id).sha or ""
    except Exception:
        return ""


def _render_prompt(resolved: ResolvedModel, scenario, tokenizer) -> tuple[str, bool]:
    """Return (rendered prompt string, chat_template_applied). Effective rule:
    apply the chat template only if the model is chat-tuned AND the tokenizer
    actually ships one; otherwise fall back to the flat base rendering."""
    has_template = getattr(tokenizer, "chat_template", None) is not None
    if resolved.apply_chat_template and has_template:
        messages = P.build_chat_messages(scenario)
        kwargs: dict[str, Any] = {"tokenize": False, "add_generation_prompt": True}
        if resolved.enable_thinking is not None:
            kwargs["enable_thinking"] = resolved.enable_thinking
        try:
            return tokenizer.apply_chat_template(messages, **kwargs), True
        except TypeError:
            kwargs.pop("enable_thinking", None)  # tokenizer without that kwarg
            return tokenizer.apply_chat_template(messages, **kwargs), True
    return P.render_base_prompt(scenario), False


def _prompt_tokens_and_truncate(
    text: str, tokenizer, max_model_len: int, max_new_tokens: int
) -> tuple[str, int, bool]:
    """Count prompt tokens; left-truncate to reserve the generation budget so
    prompt_len + max_new_tokens <= max_model_len. Returns (text, n_tokens, truncated)."""
    ids = tokenizer(text, add_special_tokens=False)["input_ids"]
    budget = max(1, max_model_len - max_new_tokens)
    if len(ids) <= budget:
        return text, len(ids), False
    ids = ids[-budget:]  # keep the most recent turn (the student's latest prompt)
    return tokenizer.decode(ids, skip_special_tokens=False), len(ids), True


def _build_hf_backend(resolved: ResolvedModel, rev: str | None):
    from .backends import HFBackend

    return HFBackend(
        resolved.spec.id,
        revision=rev,
        max_model_len=resolved.max_model_len,
        architecture=resolved.architecture,
        tokenizer_id=resolved.tokenizer_id,
    )


def _smoke_test(backend) -> None:
    smoke = backend.generate(["Say OK."], GenParams(max_new_tokens=8, temperature=0.0))
    if not smoke or smoke[0].text is None:
        raise RuntimeError("smoke test produced no output")


def _free_backend(backend) -> None:
    """Best-effort GPU-memory release between models in a reused worker process.

    UNTESTED. vLLM leaves KV-cache blocks / CUDA graphs / NCCL state that plain
    GC doesn't reclaim, so a worker that loads model after model accumulates
    device memory until a later engine init OOMs (the leading hypothesis for the
    generic 'Engine core initialization failed' shards). Guarded so it can never
    raise and abort an otherwise-finished model."""
    if backend is None:
        return
    try:
        import gc

        try:
            from vllm.distributed.parallel_state import destroy_model_parallel

            destroy_model_parallel()
        except Exception:
            pass
        for attr in ("llm", "model"):
            if hasattr(backend, attr):
                setattr(backend, attr, None)
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    except Exception:
        pass


def _load_backend(resolved: ResolvedModel):
    """Construct the backend and run a tiny smoke generation (must yield output).
    Returns (backend, revision, tokenizer).

    UNTESTED: when the vLLM engine fails to initialize (e.g. an architecture the
    installed vLLM can't serve), fall back to the transformers path rather than
    dead-lettering all 662 scenarios. This only runs on the vLLM error path, so
    models that already load under vLLM are unaffected."""
    spec = resolved.spec
    revision = _resolve_revision(spec.id, spec.revision)
    rev = revision or None
    if resolved.backend == "hf_fallback":
        backend = _build_hf_backend(resolved, rev)
        _smoke_test(backend)
        return backend, revision, backend.tokenizer

    from .backends import VLLMBackend

    backend = None
    try:
        backend = VLLMBackend(spec.id, revision=rev, max_model_len=resolved.max_model_len)
        _smoke_test(backend)
        return backend, revision, backend.tokenizer
    except Exception:
        # Release whatever vLLM partially allocated, then retry on transformers.
        _free_backend(backend)
        backend = _build_hf_backend(resolved, rev)
        _smoke_test(backend)
        return backend, revision, backend.tokenizer


def run_model(
    spec: ModelSpec,
    scenarios: list,
    out_dir: str | Path,
    *,
    s3_uri: str | None = None,
    resume: bool = True,
    fetch_config: Callable[[str], dict] | None = None,
) -> dict[str, Any]:
    """Generate every outstanding scenario for one model. Returns a summary dict."""
    resolved = resolve(spec, fetch_config=fetch_config)
    path = shard_path(out_dir, spec.id)
    done = completed_ids(path) if resume else set()
    todo = [s for s in scenarios if s.scenario_id not in done]
    if not todo:
        return {"model": spec.id, "status": "already_complete", "written": 0, "shard": str(path)}

    gen_params_rec = R.generation_params(
        spec.temperature, spec.top_p, spec.max_new_tokens, spec.repetition_penalty, spec.seed
    )
    # --no-resume regenerates from scratch: truncate so new rows replace the old
    # shard (e.g. a prior run's 662 error cells) instead of appending after them.
    writer = ShardWriter(path, truncate=not resume)

    # --- load (+ smoke test); a failure marks every outstanding cell as Issue ---
    try:
        backend, revision, tokenizer = _load_backend(resolved)
    except Exception as e:  # noqa: BLE001 - any load failure is isolated to this model
        for s in todo:
            writer.write(
                R.error_record(
                    scenario_id=s.scenario_id,
                    model_id=spec.id,
                    max_model_len=resolved.max_model_len,
                    description=f"load failed: {e!r}",
                )
            )
        writer.close()
        uploaded = maybe_upload(path, s3_uri)
        return {"model": spec.id, "status": "load_failed", "error": repr(e),
                "written": len(todo), "shard": str(path), "s3": uploaded}

    # --- render + tokenize + truncate all prompts, then batch-generate ---
    rendered, applied, prompt_tokens, truncated = [], [], [], []
    for s in todo:
        text, ct = _render_prompt(resolved, s, tokenizer)
        text, ptok, trunc = _prompt_tokens_and_truncate(
            text, tokenizer, resolved.max_model_len, spec.max_new_tokens
        )
        rendered.append(text)
        applied.append(ct)
        prompt_tokens.append(ptok)
        truncated.append(trunc)

    params = GenParams(
        temperature=spec.temperature,
        top_p=spec.top_p,
        max_new_tokens=spec.max_new_tokens,
        repetition_penalty=spec.repetition_penalty,
        seed=spec.seed,
    )
    t0 = time.time()
    gen_error: str | None = None
    results: list[GenResult] | None
    try:
        results = backend.generate(rendered, params)
    except Exception as e:  # noqa: BLE001 - isolate generation failure to Issue cells
        results = None
        gen_error = repr(e)
    elapsed = time.time() - t0

    written = 0
    for i, s in enumerate(todo):
        if results is None:
            rec = R.error_record(
                scenario_id=s.scenario_id,
                model_id=spec.id,
                model_revision=revision,
                rendered_prompt=rendered[i],
                gen_params=gen_params_rec,
                max_model_len=resolved.max_model_len,
                description=f"generation failed: {gen_error}",
            )
        else:
            g = results[i]
            rec = R.build_record(
                scenario_id=s.scenario_id,
                model_id=spec.id,
                model_revision=revision,
                chat_template_applied=applied[i],
                rendered_prompt=rendered[i],
                gen_params=gen_params_rec,
                max_model_len=resolved.max_model_len,
                prompt_tokens=prompt_tokens[i],
                output_tokens=g.output_tokens,
                finish_reason=g.finish_reason,
                truncated=truncated[i],
                latency_s=g.latency_s,
                output=g.text,
            )
        writer.write(rec)
        written += 1
    writer.close()
    # Free this model's GPU memory before the worker pulls the next model off
    # the queue (outputs are already flushed above, so this can't affect them).
    _free_backend(backend)
    uploaded = maybe_upload(path, s3_uri)
    return {
        "model": spec.id,
        "status": "ok" if results is not None else "generation_failed",
        "written": written,
        "shard": str(path),
        "s3": uploaded,
        "elapsed_s": round(elapsed, 2),
        "throughput_scenarios_per_s": round(written / elapsed, 3) if elapsed > 0 else None,
    }


def dry_run(
    specs: list[ModelSpec],
    scenarios: list,
    fetch_config: Callable[[str], dict] | None = None,
    n: int = 5,
) -> str:
    """Build prompts + resolve model facts WITHOUT importing torch/vllm or loading
    weights, to eyeball prompt construction. Pass a no-network fetch_config
    (e.g. lambda _id: {}) to keep max_model_len == cap and stay fully offline."""
    lines: list[str] = ["=== model resolution ==="]
    for spec in specs:
        r = resolve(spec, fetch_config=fetch_config)
        tok = f" tokenizer={r.tokenizer_id}" if r.tokenizer_id else ""
        lines.append(
            f"{spec.id}\n    backend={r.backend} gated={r.gated} arch={r.architecture} "
            f"chat_template~{r.apply_chat_template} max_model_len={r.max_model_len} "
            f"thinking={r.enable_thinking}{tok}"
        )
    lines.append("\n=== sample prompts ===")
    for s in scenarios[:n]:
        msgs = P.build_chat_messages(s)
        roles = [m["role"] for m in msgs]
        lines.append(f"\n-- {s.scenario_id} [{s.use_case}] roles={roles}")
        for m in msgs:
            preview = " ".join(m["content"].split())[:200]
            lines.append(f"   [{m['role']}] {preview}")
    return "\n".join(lines)
