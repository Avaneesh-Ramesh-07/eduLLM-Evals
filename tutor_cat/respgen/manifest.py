"""Parse models.yaml (defaults + per-model overrides) into ModelSpec objects.

The manifest is data: the exact model roster is decoupled from pipeline logic,
so growing/shrinking the set changes only this file. Numeric fields are coerced
with a clear error, which also catches the classic YAML trap where a trailing
comma turns `1.1` into the string `"1.1,"`.

Pure module (yaml only). ModelSpec is a plain dataclass so it pickles cleanly
across the orchestrator's spawn processes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

# Manifest-level defaults, overridable under `defaults:` and per model entry.
_DEFAULTS: dict[str, object] = {
    "max_model_len_cap": 32768,
    "max_new_tokens": 4096,
    "tensor_parallel_size": 1,
    "scoring_method": "generate",
    "temperature": 0.0,
    "top_p": 1.0,
    "repetition_penalty": 1.1,
    "seed": 0,
}


@dataclass
class ModelSpec:
    id: str
    max_model_len_cap: int = 32768
    max_new_tokens: int = 4096
    tensor_parallel_size: int = 1
    scoring_method: str = "generate"
    temperature: float = 0.0
    top_p: float = 1.0
    repetition_penalty: float = 1.1
    seed: int = 0
    # None => let registry.resolve() derive it from the model id / config.
    apply_chat_template: bool | None = None
    gated: bool | None = None
    backend: str | None = None          # "vllm" | "hf_fallback"
    architecture: str | None = None     # "causal" | "seq2seq"
    enable_thinking: bool | None = None  # Qwen3: False to suppress <think> traces
    revision: str | None = None          # pin a commit SHA; None => resolve at load
    tokenizer_id: str | None = None      # borrow another repo's tokenizer (OpenELM -> Llama-2)
    # vLLM engine knobs, tunable per model on a shared/constrained box. None/False
    # => vLLM defaults. enforce_eager skips CUDA-graph capture (less memory, some
    # engine-init asserts avoided); gpu_memory_utilization caps the KV-cache
    # reservation so a co-tenant on the same GPU doesn't OOM engine init.
    enforce_eager: bool | None = None
    gpu_memory_utilization: float | None = None
    extra: dict = field(default_factory=dict)


_INT_FIELDS = {"max_model_len_cap", "max_new_tokens", "tensor_parallel_size", "seed"}
_FLOAT_FIELDS = {"temperature", "top_p", "repetition_penalty", "gpu_memory_utilization"}
_SPEC_FIELDS = {f.name for f in fields(ModelSpec)} - {"extra"}


def _coerce(value: object, cast, name: str, model_id: str):
    try:
        return cast(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"models.yaml: field '{name}' for model '{model_id}' must be "
            f"{cast.__name__}, got {value!r} — a trailing comma in YAML turns "
            f"`1.1` into the string `'1.1,'`."
        ) from None


def _spec_from(merged: dict) -> ModelSpec:
    model_id = merged["id"]
    kwargs: dict = {"id": model_id}
    extra: dict = {}
    for key, value in merged.items():
        if key == "id":
            continue
        if key not in _SPEC_FIELDS:
            extra[key] = value
            continue
        if value is None:
            kwargs[key] = None
        elif key in _INT_FIELDS:
            kwargs[key] = _coerce(value, int, key, model_id)
        elif key in _FLOAT_FIELDS:
            kwargs[key] = _coerce(value, float, key, model_id)
        else:
            kwargs[key] = value
    kwargs["extra"] = extra
    return ModelSpec(**kwargs)


def load_manifest(path: str | Path) -> list[ModelSpec]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = {**_DEFAULTS, **(raw.get("defaults") or {})}
    specs: list[ModelSpec] = []
    seen: set[str] = set()
    for entry in raw.get("models") or []:
        if isinstance(entry, str):
            entry = {"id": entry}
        if not isinstance(entry, dict) or "id" not in entry:
            raise ValueError(f"models.yaml: model entry missing 'id': {entry!r}")
        if entry["id"] in seen:
            raise ValueError(f"models.yaml: duplicate model id {entry['id']!r}")
        seen.add(entry["id"])
        specs.append(_spec_from({**defaults, **entry}))
    if not specs:
        raise ValueError(f"models.yaml: no models listed in {path}")
    return specs
