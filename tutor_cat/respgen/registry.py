"""Derive per-model runtime facts the manifest doesn't state explicitly:
whether to apply a chat template, whether the repo is gated, which backend to
use, the architecture, Qwen3 thinking mode, and the clamped max_model_len.

Every derivation is a heuristic overridable by an explicit ModelSpec field, so
the manifest always wins when it disagrees. The max_model_len clamp takes an
injectable config-fetch fn (default: download config.json from the Hub) so the
math is unit-testable with a stub — no network, no transformers import.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .manifest import ModelSpec

# id substrings that mark an instruction/chat-tuned checkpoint.
_INSTRUCT_MARKERS = (
    "-instruct", "-it", "-chat", "zephyr", "vicuna", "-sft", "sft-",
    "-dpo", "-rlhf", "-tulu", "openhermes", "-hermes",
)
# Architectures/families vLLM often can't serve -> transformers fallback.
# "gpt-neo-" (with trailing hyphen) matches GPT-Neo 1.3B/2.7B (GPTNeoForCausalLM,
# unsupported by vLLM) WITHOUT matching gpt-neox, which vLLM serves fine.
_HF_FALLBACK_MARKERS = (
    "mamba", "openelm", "gemma-3", "falcon-h1", "granite-4.0-h",
    "lfm2", "rwkv", "zamba", "recurrentgemma", "gpt-neo-",
)
# Encoder-decoder (seq2seq) families: need AutoModelForSeq2SeqLM, no chat template.
_SEQ2SEQ_MARKERS = ("flan-t5", "t5-", "-t5", "bart", "pegasus", "flan-ul2", "ul2")

# OpenELM ships NO tokenizer and documents "use the Llama-2 tokenizer". The
# obvious source (meta-llama/Llama-2-7b-hf) is GATED and 403s without an accepted
# license — the exact Bug #1 Group C load failure. NousResearch/Llama-2-7b-hf is a
# byte-identical, UNGATED mirror, so borrowing from it needs no license grant.
_OPENELM_TOKENIZER = "NousResearch/Llama-2-7b-hf"


def guess_apply_chat_template(model_id: str) -> bool:
    mid = model_id.lower()
    return any(m in mid for m in _INSTRUCT_MARKERS)


def is_gated(model_id: str) -> bool:
    """Repos requiring an accepted license / HF token. Heuristic by org/family."""
    mid = model_id.lower()
    return mid.startswith(("meta-llama/", "mistralai/")) or "google/gemma" in mid


def guess_architecture(model_id: str) -> str:
    mid = model_id.lower()
    return "seq2seq" if any(m in mid for m in _SEQ2SEQ_MARKERS) else "causal"


def guess_backend(model_id: str) -> str:
    mid = model_id.lower()
    if any(m in mid for m in _HF_FALLBACK_MARKERS):
        return "hf_fallback"
    return "vllm"


def guess_enable_thinking(model_id: str) -> bool | None:
    """Qwen3 emits <think> reasoning by default; disable for clean tutor output.
    None => don't pass the kwarg (model doesn't support it)."""
    return False if "qwen3" in model_id.lower() else None


def guess_tokenizer_id(model_id: str) -> str | None:
    """A tokenizer to borrow when the repo ships none. OpenELM -> ungated Llama-2
    mirror. None => use the model's own repo. Overridable via ModelSpec.tokenizer_id."""
    return _OPENELM_TOKENIZER if "openelm" in model_id.lower() else None


# --- max_model_len clamp ---------------------------------------------------

def _max_position_embeddings(cfg: dict) -> int | None:
    # "max_context_length" is OpenELM's key; without it OpenELM (and any repo that
    # names its window that way) would fall through to the 32768 cap instead of its
    # true ~2048 window. Standard keys are checked first so they win when both exist.
    for key in ("max_position_embeddings", "n_positions", "max_seq_len",
                "seq_length", "max_context_length"):
        val = cfg.get(key)
        if val:
            return int(val)
    text_cfg = cfg.get("text_config")  # multimodal configs nest the LM here
    if isinstance(text_cfg, dict):
        return _max_position_embeddings(text_cfg)
    return None


def _default_fetch_config(model_id: str) -> dict:
    from huggingface_hub import hf_hub_download  # lazy: [gen] extra

    path = hf_hub_download(model_id, "config.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_max_model_len(
    model_id: str,
    cap: int,
    fetch_config: Callable[[str], dict] | None = None,
) -> int:
    """min(cap, model's max positions). Falls back to cap when the config can't
    be read (offline) or has no positional limit (SSM/mamba)."""
    fetch = fetch_config or _default_fetch_config
    try:
        cfg = fetch(model_id)
    except Exception:
        return cap
    mpe = _max_position_embeddings(cfg)
    return cap if mpe is None else min(cap, mpe)


@dataclass
class ResolvedModel:
    spec: ModelSpec
    apply_chat_template: bool
    gated: bool
    backend: str
    architecture: str
    enable_thinking: bool | None
    max_model_len: int
    tokenizer_id: str | None = None  # borrowed tokenizer (OpenELM); None => own repo


def resolve(
    spec: ModelSpec,
    fetch_config: Callable[[str], dict] | None = None,
) -> ResolvedModel:
    """Combine explicit ModelSpec overrides with derived defaults."""
    apply_ct = (
        spec.apply_chat_template
        if spec.apply_chat_template is not None
        else guess_apply_chat_template(spec.id)
    )
    gated = spec.gated if spec.gated is not None else is_gated(spec.id)
    architecture = spec.architecture or guess_architecture(spec.id)
    backend = spec.backend or guess_backend(spec.id)
    # seq2seq can't go through the causal vLLM path; force the HF fallback.
    if architecture == "seq2seq":
        backend = "hf_fallback"
        apply_ct = False
    thinking = (
        spec.enable_thinking
        if spec.enable_thinking is not None
        else guess_enable_thinking(spec.id)
    )
    max_model_len = resolve_max_model_len(spec.id, spec.max_model_len_cap, fetch_config)
    tokenizer_id = spec.tokenizer_id or guess_tokenizer_id(spec.id)
    return ResolvedModel(
        spec=spec,
        apply_chat_template=apply_ct,
        gated=gated,
        backend=backend,
        architecture=architecture,
        enable_thinking=thinking,
        max_model_len=max_model_len,
        tokenizer_id=tokenizer_id,
    )
