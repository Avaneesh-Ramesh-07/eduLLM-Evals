"""Per-model worker: load one model (smoke test folded into the load), generate
over all outstanding scenarios, write the Model Output schema to this model's
shard, upload to S3.

Resume: existing shard Scenario ids are skipped, so an interrupted run continues
where it stopped. Failure isolation: a load or generation error is recorded as an
Issue cell (never crashes the fleet), so every (model, scenario) still has a row.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from . import prompts as P
from . import records as R
from .backends import GenParams, GenResult
from .manifest import ModelSpec
from .registry import ResolvedModel, resolve
from .s3 import maybe_upload
from .shard import ShardWriter, rewrite_shard, scan_shard, shard_path

# Always leave at least this many tokens for the model to answer. The prompt is
# truncated to (max_model_len - MIN_GEN) first; generation then takes whatever
# context is left, so a real prompt survives even when max_model_len is small.
MIN_GEN = 256

# Load robustness: retry a construction a few times (transient Hub 429s / network
# blips resolve on retry — Bug #1 Group B) before giving up on a backend.
_LOAD_ATTEMPTS = 3
_LOAD_RETRY_DELAY = 5.0


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


def _fit_prompt_and_budget(
    text: str, tokenizer, max_model_len: int, max_new_tokens: int
) -> tuple[str, int, bool, int]:
    """Fit the prompt into the context window and size the per-scenario generation
    budget. Returns (text, prompt_tokens, truncated, gen_budget).

    The prompt is kept up to ``max_model_len - MIN_GEN`` tokens, so a genuine
    prompt always survives; generation then gets ``min(max_new_tokens,
    max_model_len - prompt_tokens)`` (never below MIN_GEN). This replaces the old
    ``budget = max(1, max_model_len - max_new_tokens)`` which collapsed to 1 —
    left-truncating the whole prompt to its final token — whenever the model's
    context window was <= max_new_tokens (every model at max_model_len <= 4096).
    """
    ids = tokenizer(text, add_special_tokens=False)["input_ids"]
    prompt_cap = max(1, max_model_len - MIN_GEN)
    truncated = len(ids) > prompt_cap
    if truncated:
        ids = ids[-prompt_cap:]  # keep the most recent turn (the student's latest prompt)
        text = tokenizer.decode(ids, skip_special_tokens=False)
    prompt_tokens = len(ids)
    gen_budget = min(max_new_tokens, max(MIN_GEN, max_model_len - prompt_tokens))
    gen_budget = max(1, min(gen_budget, max_model_len - 1))
    return text, prompt_tokens, truncated, gen_budget


def _smoke(backend) -> None:
    """Tiny generation that must yield output; folds the load's health check in."""
    out = backend.generate(["Say OK."], GenParams(max_new_tokens=8, temperature=0.0))
    if not out or out[0].text is None:
        raise RuntimeError("smoke test produced no output")


def _free_backend(backend) -> None:
    """Best-effort GPU-memory reclamation between models. Never raises.

    The persistent worker used to load model after model without freeing the prior
    vLLM engine (KV-cache blocks, CUDA graphs, NCCL state that plain GC doesn't
    reclaim), so later models OOM'd at engine init — the leading hypothesis for the
    41 Group-A load failures. The orchestrator now also runs each model in its own
    process (definitive reclamation on exit); this is the in-process belt-and-braces
    for direct callers and between retry attempts."""
    try:
        import gc

        try:  # vLLM's own teardown of the tensor-parallel / device state
            from vllm.distributed.parallel_state import destroy_model_parallel

            destroy_model_parallel()
        except Exception:
            pass
        if backend is not None:
            for attr in ("llm", "model"):
                try:
                    if getattr(backend, attr, None) is not None:
                        setattr(backend, attr, None)
                except Exception:
                    pass
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    except Exception:
        pass


def _try_backend(make, attempts: int, delay: float, sleep=time.sleep):
    """Construct + smoke-test a backend, retrying transient failures. Frees any
    partially-built backend between attempts so a retry starts from clean memory.
    Raises the last error if every attempt fails."""
    last: Exception | None = None
    for i in range(attempts):
        backend = None
        try:
            backend = make()
            _smoke(backend)
            return backend
        except Exception as e:  # noqa: BLE001 - decide retry vs give up below
            last = e
            _free_backend(backend)
            if i < attempts - 1 and delay > 0:
                sleep(delay)
    raise last if last is not None else RuntimeError("backend construction failed")


@contextlib.contextmanager
def _capture_fd_stderr():
    """Redirect OS-level fd 2 to a temp file for the duration of the block, then
    replay it to the real stderr on exit.

    vLLM V1 runs its engine core in a SUBPROCESS that inherits fd 2; when it crashes
    at init the parent only sees ``Failed core proc(s): {}`` while the real traceback
    goes to that inherited stderr and is lost (the harness stored only repr(e)).
    Redirecting the fd (not Python's sys.stderr, which the subprocess doesn't share)
    captures it so the caller can fold the tail into the Issue Description. Yields the
    temp file, or None if the fd can't be duplicated (then capture is skipped)."""
    try:
        saved = os.dup(2)
    except Exception:
        yield None
        return
    tmp = tempfile.TemporaryFile(mode="w+b")
    try:
        os.dup2(tmp.fileno(), 2)
        yield tmp
    finally:
        try:
            os.dup2(saved, 2)
        finally:
            os.close(saved)
        try:  # replay captured bytes so vLLM's load logs aren't swallowed
            tmp.seek(0)
            data = tmp.read()
            if data:
                os.write(2, data)
        except Exception:
            pass
        tmp.close()


def _load_backend(resolved: ResolvedModel, sleep=time.sleep):
    """Construct the backend (with retries), smoke-test it, and — for vLLM-routed
    models — fall back to transformers if the vLLM engine won't come up, so a model
    still produces output instead of dead-lettering all 662 scenarios. On total
    failure the raised error carries the captured vLLM subprocess stderr tail.
    Returns (backend, revision, tokenizer)."""
    spec = resolved.spec
    revision = _resolve_revision(spec.id, spec.revision)
    rev = revision or None

    def mk_hf():
        from .backends import HFBackend

        return HFBackend(
            spec.id,
            revision=rev,
            max_model_len=resolved.max_model_len,
            architecture=resolved.architecture,
            tokenizer_id=resolved.tokenizer_id,
        )

    if resolved.backend == "hf_fallback":
        backend = _try_backend(mk_hf, _LOAD_ATTEMPTS, _LOAD_RETRY_DELAY, sleep)
        return backend, revision, backend.tokenizer

    def mk_vllm():
        from .backends import VLLMBackend

        return VLLMBackend(
            spec.id,
            revision=rev,
            max_model_len=resolved.max_model_len,
            gpu_memory_utilization=spec.gpu_memory_utilization,
            enforce_eager=bool(spec.enforce_eager),
        )

    stderr_tail = ""
    with _capture_fd_stderr() as errbuf:
        try:
            backend = _try_backend(mk_vllm, _LOAD_ATTEMPTS, _LOAD_RETRY_DELAY, sleep)
            return backend, revision, backend.tokenizer
        except Exception as e:  # noqa: BLE001 - fall back to transformers below
            primary = e
            if errbuf is not None:
                try:
                    errbuf.seek(0)
                    stderr_tail = errbuf.read().decode("utf-8", "replace")[-3000:]
                except Exception:
                    stderr_tail = ""

    # vLLM engine wouldn't start: transformers serves most causal LMs vLLM can't.
    try:
        backend = _try_backend(mk_hf, 2, _LOAD_RETRY_DELAY, sleep)
        return backend, revision, backend.tokenizer
    except Exception as hf_err:  # noqa: BLE001
        tail = f"\n--- vLLM stderr tail ---\n{stderr_tail}" if stderr_tail else ""
        raise RuntimeError(
            f"vLLM load failed: {primary!r}; transformers fallback also failed: "
            f"{hf_err!r}{tail}"
        ) from primary


def _effective_latency(latency_s: float | None, elapsed: float, n: int) -> float | None:
    """Per-request latency when the backend supplies it (HF), else the per-scenario
    batch average (vLLM V1 doesn't reliably populate RequestOutput.metrics, so its
    per-row latency is None — a coarse average beats a column of nulls)."""
    if latency_s is not None:
        return latency_s
    return round(elapsed / n, 4) if n > 0 else None


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
    # Resume counts a scenario done only if it has a *valid* row: Issue==0 AND a
    # real prompt was fed (Prompt Tokens > 1). This retries the 50 hard-failed
    # shards (Issue==1) AND the 18 truncation-corrupted shards (Prompt Tokens==1,
    # but Issue==0 so the old resume wrongly skipped them). Invalid/duplicate rows
    # are compacted out first so the regenerated shard has one clean row/scenario.
    if resume:
        done, valid_rows, had_invalid = scan_shard(path)
        if had_invalid:
            rewrite_shard(path, valid_rows)
    else:
        done = set()
    todo = [s for s in scenarios if s.scenario_id not in done]
    if not todo:
        return {"model": spec.id, "status": "already_complete", "written": 0, "shard": str(path)}

    # truncate=not resume: a --no-resume regeneration overwrites the shard instead
    # of appending a second set of rows on top (which would duplicate every cell).
    # On resume the shard was already compacted to valid_rows above, so append.
    writer = ShardWriter(path, truncate=not resume)

    # --- load (+ smoke test); a failure marks every outstanding cell as Issue ---
    backend = None
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
        _free_backend(backend)
        uploaded = maybe_upload(path, s3_uri)
        return {"model": spec.id, "status": "load_failed", "error": repr(e),
                "written": len(todo), "shard": str(path), "s3": uploaded}

    # --- render + tokenize + fit all prompts, then batch-generate ---
    rendered, applied, prompt_tokens, truncated, gen_budgets = [], [], [], [], []
    for s in todo:
        text, ct = _render_prompt(resolved, s, tokenizer)
        text, ptok, trunc, gbud = _fit_prompt_and_budget(
            text, tokenizer, resolved.max_model_len, spec.max_new_tokens
        )
        rendered.append(text)
        applied.append(ct)
        prompt_tokens.append(ptok)
        truncated.append(trunc)
        gen_budgets.append(gbud)

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
        # per-scenario max_tokens: short prompts get the full budget, long prompts
        # on a small window keep the prompt and still get >= MIN_GEN to answer.
        results = backend.generate(rendered, params, max_tokens_per_prompt=gen_budgets)
    except Exception as e:  # noqa: BLE001 - isolate generation failure to Issue cells
        results = None
        gen_error = repr(e)
    elapsed = time.time() - t0

    written = 0
    for i, s in enumerate(todo):
        # the effective (per-scenario) budget the model actually ran with
        gp = R.generation_params(
            spec.temperature, spec.top_p, gen_budgets[i], spec.repetition_penalty, spec.seed
        )
        if results is None:
            rec = R.error_record(
                scenario_id=s.scenario_id,
                model_id=spec.id,
                model_revision=revision,
                rendered_prompt=rendered[i],
                gen_params=gp,
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
                gen_params=gp,
                max_model_len=resolved.max_model_len,
                prompt_tokens=prompt_tokens[i],
                output_tokens=g.output_tokens,
                finish_reason=g.finish_reason,
                truncated=truncated[i],
                latency_s=_effective_latency(g.latency_s, elapsed, len(todo)),
                output=g.text,
            )
        writer.write(rec)
        written += 1
    writer.close()
    _free_backend(backend)  # reclaim GPU memory before this process exits / next model
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
        lines.append(
            f"{spec.id}\n    backend={r.backend} gated={r.gated} arch={r.architecture} "
            f"chat_template~{r.apply_chat_template} max_model_len={r.max_model_len} "
            f"thinking={r.enable_thinking}"
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
