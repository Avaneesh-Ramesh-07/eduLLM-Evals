"""Offline unit tests for the response-generation pure logic (no torch/vllm/GPU).

Covers the pieces most likely to break silently: consecutive-user-turn
coalescing, the registry heuristics + max_model_len clamp, manifest parsing
(incl. the trailing-comma YAML trap), the Model Output schema keys, and
resume-by-shard.
"""

from __future__ import annotations

import json

import pytest

from tutor_cat.respgen import prompts as P
from tutor_cat.respgen import records as R
from tutor_cat.respgen import registry, shard
from tutor_cat.respgen.manifest import ModelSpec, load_manifest
from tutor_cat.respgen.s3 import parse_s3_uri
from tutor_cat.schemas import Scenario


# --- helpers ---------------------------------------------------------------

def _scn(use_case, context, prompt="PROMPT", sid="s1"):
    return Scenario(
        scenario_id=sid,
        prompt=prompt,
        criterion_ids=["c1"],
        use_case=use_case,
        conversation_context=context,
    )


# --- prompts: system selection + coalescing --------------------------------

def test_adaptive_messages_alternate_and_keep_context():
    scn = _scn(
        "adaptive_explanation",
        [{"role": "student", "content": "S0"}, {"role": "tutor", "content": "T0"}],
    )
    msgs = P.build_chat_messages(scn)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[0]["content"] == P.SYSTEM_PROMPTS["adaptive_explanation"]
    assert msgs[1]["content"] == "S0" and msgs[2]["content"] == "T0"
    assert msgs[3]["content"] == "PROMPT"


def test_feedback_coalesces_to_single_user_turn():
    scn = _scn("feedback", [{"role": "student", "content": "PROBLEM"}])
    msgs = P.build_chat_messages(scn)
    # the 333 user,user scenarios MUST become exactly [system, user]
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == P.SYSTEM_PROMPTS["feedback"]
    merged = msgs[1]["content"]
    assert "PROBLEM" in merged and "PROMPT" in merged
    assert "Student's solution:" in merged


def test_hint_coalesces_with_its_own_label():
    scn = _scn("hint_generation", [{"role": "student", "content": "WORK"}])
    msgs = P.build_chat_messages(scn)
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert "Student's work so far:" in msgs[1]["content"]


def test_no_consecutive_same_role_ever():
    for uc, ctx in [
        ("feedback", [{"role": "student", "content": "A"}]),
        ("hint_generation", [{"role": "student", "content": "A"}]),
        ("adaptive_explanation",
         [{"role": "student", "content": "A"}, {"role": "tutor", "content": "B"}]),
    ]:
        roles = [m["role"] for m in P.build_chat_messages(_scn(uc, ctx))]
        assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), roles


def test_render_base_prompt_shape():
    scn = _scn("feedback", [{"role": "student", "content": "PROBLEM"}])
    text = P.render_base_prompt(scn)
    assert "System:" in text and "Student:" in text
    assert text.rstrip().endswith("Tutor:")  # cues the model to answer as tutor


def test_unknown_use_case_falls_back_to_adaptive():
    msgs = P.build_chat_messages(_scn("", [{"role": "student", "content": "X"}]))
    assert msgs[0]["content"] == P.SYSTEM_PROMPTS["adaptive_explanation"]


# --- registry heuristics ---------------------------------------------------

@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("Qwen/Qwen2.5-1.5B-Instruct", True),
        ("google/gemma-2-2b-it", True),
        ("TinyLlama/TinyLlama-1.1B-Chat-v1.0", True),
        ("meta-llama/Llama-3.2-1B", False),
        ("EleutherAI/pythia-1b", False),
        ("state-spaces/mamba-1.4b-hf", False),
    ],
)
def test_guess_apply_chat_template(model_id, expected):
    assert registry.guess_apply_chat_template(model_id) is expected


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("meta-llama/Llama-3.2-1B-Instruct", True),
        ("google/gemma-2-2b-it", True),
        ("mistralai/Mistral-7B-Instruct-v0.3", True),
        ("Qwen/Qwen2.5-0.5B-Instruct", False),
        ("google/flan-t5-xl", False),  # google but not gemma -> not gated
    ],
)
def test_is_gated(model_id, expected):
    assert registry.is_gated(model_id) is expected


@pytest.mark.parametrize(
    "model_id,backend",
    [
        ("Qwen/Qwen2.5-1.5B-Instruct", "vllm"),
        ("state-spaces/mamba-1.4b-hf", "hf_fallback"),
        ("apple/OpenELM-1_1B-Instruct", "hf_fallback"),
        ("google/gemma-3-1b-it", "hf_fallback"),
        ("google/gemma-2-2b-it", "vllm"),  # gemma-2 is fine in vLLM; only gemma-3 falls back
    ],
)
def test_guess_backend(model_id, backend):
    assert registry.guess_backend(model_id) == backend


def test_gpt_neo_routes_to_hf_fallback_but_not_neox():
    # GPT-Neo (GPTNeoForCausalLM) is unsupported by vLLM -> transformers.
    assert registry.guess_backend("EleutherAI/gpt-neo-1.3B") == "hf_fallback"
    assert registry.guess_backend("EleutherAI/gpt-neo-2.7B") == "hf_fallback"
    # ...but GPT-NeoX IS served by vLLM; the "gpt-neo-" marker must not catch it.
    assert registry.guess_backend("EleutherAI/gpt-neox-20b") == "vllm"
    assert registry.guess_backend("togethercomputer/RedPajama-INCITE-7B-Base") == "vllm"


def test_seq2seq_forces_hf_fallback_and_no_chat_template():
    r = registry.resolve(ModelSpec(id="google/flan-t5-xl"), fetch_config=lambda _id: {})
    assert r.architecture == "seq2seq"
    assert r.backend == "hf_fallback"
    assert r.apply_chat_template is False


def test_qwen3_thinking_disabled():
    r = registry.resolve(ModelSpec(id="Qwen/Qwen3-1.7B"), fetch_config=lambda _id: {})
    assert r.enable_thinking is False
    r2 = registry.resolve(ModelSpec(id="Qwen/Qwen2.5-1.5B-Instruct"), fetch_config=lambda _id: {})
    assert r2.enable_thinking is None


def test_explicit_spec_overrides_heuristics():
    spec = ModelSpec(id="state-spaces/mamba-1.4b-hf", backend="vllm",
                     apply_chat_template=True, gated=True)
    r = registry.resolve(spec, fetch_config=lambda _id: {})
    assert r.backend == "vllm" and r.apply_chat_template is True and r.gated is True


# --- max_model_len clamp (injected fetch, no network) ----------------------

def test_clamp_uses_min_of_cap_and_config():
    mml = registry.resolve_max_model_len(
        "x", cap=32768, fetch_config=lambda _id: {"max_position_embeddings": 4096}
    )
    assert mml == 4096


def test_clamp_falls_back_to_cap_when_no_positional_limit():
    # SSM/mamba configs carry no max_position_embeddings
    assert registry.resolve_max_model_len("x", cap=8192, fetch_config=lambda _id: {}) == 8192


def test_clamp_falls_back_to_cap_on_fetch_error():
    def boom(_id):
        raise RuntimeError("offline")

    assert registry.resolve_max_model_len("x", cap=16384, fetch_config=boom) == 16384


def test_clamp_reads_nested_text_config():
    mml = registry.resolve_max_model_len(
        "x", cap=32768, fetch_config=lambda _id: {"text_config": {"max_position_embeddings": 2048}}
    )
    assert mml == 2048


def test_clamp_reads_openelm_max_context_length():
    # OpenELM's config names its window "max_context_length"; without that probe key
    # it would fall through to the 32768 cap instead of its true ~2048 window.
    mml = registry.resolve_max_model_len(
        "apple/OpenELM-3B", cap=32768, fetch_config=lambda _id: {"max_context_length": 2048}
    )
    assert mml == 2048


def test_clamp_prefers_standard_key_over_max_context_length():
    # when both are present the standard key wins (checked first).
    mml = registry.resolve_max_model_len(
        "x", cap=32768,
        fetch_config=lambda _id: {"max_position_embeddings": 4096, "max_context_length": 999},
    )
    assert mml == 4096


# --- manifest parsing ------------------------------------------------------

def test_manifest_merges_defaults(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(
        "defaults:\n  max_new_tokens: 1024\n  temperature: 0.0\n"
        "models:\n  - id: a/b\n  - id: c/d\n    max_new_tokens: 256\n",
        encoding="utf-8",
    )
    specs = load_manifest(p)
    assert [s.id for s in specs] == ["a/b", "c/d"]
    assert specs[0].max_new_tokens == 1024  # from defaults
    assert specs[1].max_new_tokens == 256   # per-model override


def test_manifest_string_entry_shorthand(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("models:\n  - a/b\n", encoding="utf-8")
    specs = load_manifest(p)
    assert specs[0].id == "a/b" and specs[0].max_new_tokens == 4096  # built-in default


def test_manifest_trailing_comma_is_rejected(tmp_path):
    # YAML parses `1.1,` as the string "1.1," — the loader must catch it.
    p = tmp_path / "m.yaml"
    p.write_text("models:\n  - id: a/b\n    repetition_penalty: '1.1,'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="trailing comma"):
        load_manifest(p)


def test_manifest_rejects_duplicate_ids(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("models:\n  - id: a/b\n  - id: a/b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(p)


def test_shipped_models_yaml_loads():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent  # tutor_cat/
    specs = load_manifest(root / "models.yaml")
    assert len(specs) == 100  # the 100 "common person" models (MIRT rows)
    ids = {s.id for s in specs}
    # spot-check representative rows across every backend path
    assert "Qwen/Qwen2.5-7B-Instruct" in ids     # vllm, chat template
    assert "meta-llama/Llama-3.2-1B" in ids       # vllm base, gated org
    assert "state-spaces/mamba-2.8b-hf" in ids    # hf_fallback (SSM)
    assert "EleutherAI/gpt-neo-1.3B" in ids       # hf_fallback (GPTNeoForCausalLM)
    assert "apple/OpenELM-1_1B" in ids            # hf_fallback + tokenizer override


def test_openelm_entries_borrow_the_ungated_tokenizer():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    specs = load_manifest(root / "models.yaml")
    openelm = [s for s in specs if s.id.startswith("apple/OpenELM")]
    # OpenELM ships no tokenizer; the manifest must point it at the UNGATED Llama-2
    # mirror. meta-llama/Llama-2-7b-hf is gated and 403s (the Bug #1 Group C cause).
    assert openelm
    assert all(s.tokenizer_id == "NousResearch/Llama-2-7b-hf" for s in openelm)


def test_manifest_parses_tokenizer_id(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(
        "models:\n  - id: apple/OpenELM-3B\n    tokenizer_id: NousResearch/Llama-2-7b-hf\n",
        encoding="utf-8",
    )
    specs = load_manifest(p)
    assert specs[0].tokenizer_id == "NousResearch/Llama-2-7b-hf"


def test_guess_tokenizer_id_only_for_openelm():
    # OpenELM (any casing/variant) borrows the ungated Llama-2 mirror; everything
    # else uses its own repo tokenizer (None => don't override).
    assert registry.guess_tokenizer_id("apple/OpenELM-1_1B") == "NousResearch/Llama-2-7b-hf"
    assert registry.guess_tokenizer_id("apple/OpenELM-3B-Instruct") == "NousResearch/Llama-2-7b-hf"
    assert registry.guess_tokenizer_id("Qwen/Qwen2.5-1.5B") is None
    assert registry.guess_tokenizer_id("meta-llama/Llama-3.2-1B") is None


def test_resolve_sets_openelm_tokenizer_without_manifest_key():
    # Even a bare spec (no tokenizer_id) must resolve OpenELM to the ungated mirror,
    # so the heuristic — not just the manifest — protects against the gated 403.
    r = registry.resolve(ModelSpec(id="apple/OpenELM-3B"), fetch_config=lambda _id: {})
    assert r.tokenizer_id == "NousResearch/Llama-2-7b-hf"
    # an explicit manifest override still wins
    r2 = registry.resolve(
        ModelSpec(id="apple/OpenELM-3B", tokenizer_id="some/other-tok"),
        fetch_config=lambda _id: {},
    )
    assert r2.tokenizer_id == "some/other-tok"


def test_manifest_parses_vllm_engine_knobs(tmp_path):
    # enforce_eager / gpu_memory_utilization are per-model vLLM tuning knobs; the
    # float coercion must accept gpu_memory_utilization (via _FLOAT_FIELDS).
    p = tmp_path / "m.yaml"
    p.write_text(
        "models:\n  - id: a/b\n    enforce_eager: true\n"
        "    gpu_memory_utilization: 0.6\n",
        encoding="utf-8",
    )
    specs = load_manifest(p)
    assert specs[0].enforce_eager is True
    assert specs[0].gpu_memory_utilization == 0.6


# --- Model Output schema ---------------------------------------------------

_SCHEMA_KEYS = {
    "Benchmark", "Scenario", "Model", "Model Revision", "Chat Template Applied",
    "Rendered Prompt", "Generation Params", "Max Model Len", "Prompt Tokens",
    "Output Tokens", "Finish Reason", "Truncated", "Latency (s)", "Output",
    "Issue", "Issue Description",
}


def test_record_has_exact_titlecase_keys():
    rec = R.build_record(
        scenario_id="s1", model_id="a/b", model_revision="abc",
        chat_template_applied=True, rendered_prompt="P",
        gen_params=R.generation_params(0.0, 1.0, 4096, 1.1, 0),
        max_model_len=4096, prompt_tokens=10, output_tokens=20,
        finish_reason="stop", truncated=False, latency_s=1.5, output="OUT",
    )
    assert set(rec) == _SCHEMA_KEYS
    assert rec["Benchmark"] == "TutorBench"
    assert rec["Chat Template Applied"] == 1 and rec["Truncated"] == 0 and rec["Issue"] == 0
    assert rec["Issue Description"] == "N/A"  # schema: "N/A otherwise"
    assert set(rec["Generation Params"]) == {
        "temperature", "top_p", "max_new_tokens", "repetition_penalty", "seed"
    }


def test_error_record_marks_issue():
    rec = R.error_record(scenario_id="s1", model_id="a/b", description="load failed")
    assert set(rec) == _SCHEMA_KEYS
    assert rec["Issue"] == 1 and rec["Finish Reason"] == "error"
    assert rec["Output"] == "" and rec["Issue Description"] == "load failed"


# --- shard resume ----------------------------------------------------------

def test_completed_ids_reads_scenario_field(tmp_path):
    p = tmp_path / "model.jsonl"
    with p.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"Scenario": "s1", "Model": "a/b"}) + "\n")
        f.write(json.dumps({"Scenario": "s2", "Model": "a/b"}) + "\n")
    assert shard.completed_ids(p) == {"s1", "s2"}


def test_completed_ids_tolerates_torn_last_line(tmp_path):
    p = tmp_path / "model.jsonl"
    with p.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"Scenario": "s1"}) + "\n")
        f.write('{"Scenario": "s2", "Output": "half-writ')  # killed mid-append
    assert shard.completed_ids(p) == {"s1"}


def test_completed_ids_missing_file_is_empty(tmp_path):
    assert shard.completed_ids(tmp_path / "nope.jsonl") == set()


def test_shard_path_sanitizes_model_id(tmp_path):
    path = shard.shard_path(tmp_path, "meta-llama/Llama-3.2-1B-Instruct")
    assert path.name == "meta-llama_Llama-3.2-1B-Instruct.jsonl"


def test_shard_writer_appends(tmp_path):
    path = tmp_path / "m.jsonl"
    with shard.ShardWriter(path) as w:
        w.write({"Scenario": "s1"})
    with shard.ShardWriter(path) as w:
        w.write({"Scenario": "s2"})
    assert shard.completed_ids(path) == {"s1", "s2"}


# --- resume: valid-only scan + compaction ----------------------------------

def _row(sid, issue=0, prompt_tokens=500):
    return {"Scenario": sid, "Model": "a/b", "Issue": issue,
            "Prompt Tokens": prompt_tokens, "Output": "ok"}


def _write_rows(path, rows):
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_scan_shard_skips_issue_and_truncation_bug_rows(tmp_path):
    p = tmp_path / "m.jsonl"
    _write_rows(p, [
        _row("s1"),                       # valid -> done
        _row("s2", issue=1, prompt_tokens=0),   # hard failure -> retry
        _row("s3", prompt_tokens=1),      # 1-token truncation bug -> retry
    ])
    done, valid_rows, had_invalid = shard.scan_shard(p)
    assert done == {"s1"}
    assert [r["Scenario"] for r in valid_rows] == ["s1"]
    assert had_invalid is True


def test_scan_shard_clean_shard_needs_no_compaction(tmp_path):
    p = tmp_path / "m.jsonl"
    _write_rows(p, [_row("s1"), _row("s2"), _row("s3")])
    done, valid_rows, had_invalid = shard.scan_shard(p)
    assert done == {"s1", "s2", "s3"}
    assert had_invalid is False  # all valid, unique -> shard is left untouched


def test_scan_shard_flags_duplicate_valid_rows(tmp_path):
    p = tmp_path / "m.jsonl"
    _write_rows(p, [_row("s1"), _row("s1")])  # duplicate -> compact to one
    done, valid_rows, had_invalid = shard.scan_shard(p)
    assert done == {"s1"} and len(valid_rows) == 1 and had_invalid is True


def test_rewrite_shard_compacts_to_valid_rows(tmp_path):
    p = tmp_path / "m.jsonl"
    _write_rows(p, [_row("s1"), _row("s2", issue=1, prompt_tokens=0), _row("s3", prompt_tokens=1)])
    _, valid_rows, _ = shard.scan_shard(p)
    shard.rewrite_shard(p, valid_rows)
    # only the valid scenario survives; a re-scan is now clean
    assert shard.completed_ids(p) == {"s1"}
    done2, _, had_invalid2 = shard.scan_shard(p)
    assert done2 == {"s1"} and had_invalid2 is False


def test_scan_shard_missing_file(tmp_path):
    done, valid_rows, had_invalid = shard.scan_shard(tmp_path / "nope.jsonl")
    assert done == set() and valid_rows == [] and had_invalid is False


# --- prompt fit + per-scenario generation budget (the truncation-bug fix) ---

from tutor_cat.respgen import runner


class _CharTok:
    """Minimal tokenizer stub: one token per character (no torch needed)."""

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": list(range(len(text)))}

    def decode(self, ids, skip_special_tokens=False):
        return "x" * len(ids)


@pytest.mark.parametrize("max_model_len", [2048, 4096, 8192, 32768])
def test_fit_prompt_never_guts_the_prompt(max_model_len):
    # a typical ~600-token TutorBench prompt: the old code left 1 token at mml<=4096
    text, ptok, trunc, gen = runner._fit_prompt_and_budget(
        "x" * 600, _CharTok(), max_model_len, max_new_tokens=4096
    )
    assert ptok == 600 and trunc is False
    assert ptok > 1                                   # the bug regression guard
    assert ptok + gen <= max_model_len               # fits the window
    assert gen >= runner.MIN_GEN                      # room to actually answer


def test_fit_prompt_long_prompt_reserves_min_gen(tmp_path):
    # a prompt longer than the whole small window: keep as much as fits, still
    # guarantee MIN_GEN output tokens.
    text, ptok, trunc, gen = runner._fit_prompt_and_budget(
        "x" * 5000, _CharTok(), 2048, max_new_tokens=4096
    )
    assert trunc is True
    assert ptok == 2048 - runner.MIN_GEN
    assert gen == runner.MIN_GEN
    assert ptok + gen == 2048


def test_fit_prompt_large_window_gets_full_budget():
    text, ptok, trunc, gen = runner._fit_prompt_and_budget(
        "x" * 600, _CharTok(), 32768, max_new_tokens=4096
    )
    assert gen == 4096  # short prompt on a big model -> full requested budget


# --- latency fallback (Bug #3.1: vLLM V1 leaves per-request metrics empty) --

def test_effective_latency_prefers_backend_value():
    # HF supplies a real per-request latency; use it verbatim.
    assert runner._effective_latency(1.25, elapsed=10.0, n=4) == 1.25


def test_effective_latency_falls_back_to_batch_average():
    # vLLM leaves latency None -> record the per-scenario average, not a null column.
    assert runner._effective_latency(None, elapsed=10.0, n=4) == 2.5


def test_effective_latency_none_when_empty_batch():
    assert runner._effective_latency(None, elapsed=10.0, n=0) is None


# --- load robustness: retries + vLLM->HF fallback (Bug #1 A/B) --------------

class _FakeBackend:
    """Stands in for a constructed backend; its smoke generate() always yields."""

    def __init__(self, tokenizer="TOK"):
        self.tokenizer = tokenizer

    def generate(self, prompts, params, max_tokens_per_prompt=None):
        return [runner.GenResult(text="OK", output_tokens=1, finish_reason="stop")]


def test_try_backend_retries_then_succeeds():
    # transient Hub blip on the first attempt (Bug #1 Group B), success on retry.
    calls = {"n": 0}

    def make():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("HTTP 429 rate limited")
        return _FakeBackend()

    slept = []
    b = runner._try_backend(make, attempts=3, delay=5.0, sleep=slept.append)
    assert isinstance(b, _FakeBackend)
    assert calls["n"] == 2 and slept == [5.0]  # retried once, slept once


def test_try_backend_raises_last_error_after_exhausting():
    def make():
        raise RuntimeError("still broken")

    with pytest.raises(RuntimeError, match="still broken"):
        runner._try_backend(make, attempts=2, delay=0.0, sleep=lambda _s: None)


def test_load_backend_falls_back_to_hf_when_vllm_fails(monkeypatch):
    # vLLM engine won't come up (Group A); the model must still load via transformers
    # instead of dead-lettering all 662 scenarios.
    from tutor_cat.respgen import backends

    def boom_vllm(*a, **k):
        raise RuntimeError("Engine core initialization failed. Failed core proc(s): {}")

    monkeypatch.setattr(backends, "VLLMBackend", boom_vllm)
    monkeypatch.setattr(backends, "HFBackend", lambda *a, **k: _FakeBackend("HFTOK"))
    monkeypatch.setattr(runner, "_resolve_revision", lambda _id, _o: "")

    resolved = registry.resolve(ModelSpec(id="some/vllm-model"), fetch_config=lambda _id: {})
    assert resolved.backend == "vllm"
    backend, revision, tok = runner._load_backend(resolved, sleep=lambda _s: None)
    assert tok == "HFTOK"  # fell back to the HF backend


def test_load_backend_raises_with_stderr_tail_when_both_fail(monkeypatch):
    from tutor_cat.respgen import backends

    def boom_vllm(*a, **k):
        raise RuntimeError("vLLM dead")

    def boom_hf(*a, **k):
        raise RuntimeError("HF dead too")

    monkeypatch.setattr(backends, "VLLMBackend", boom_vllm)
    monkeypatch.setattr(backends, "HFBackend", boom_hf)
    monkeypatch.setattr(runner, "_resolve_revision", lambda _id, _o: "")

    resolved = registry.resolve(ModelSpec(id="some/vllm-model"), fetch_config=lambda _id: {})
    with pytest.raises(RuntimeError, match="transformers fallback also failed"):
        runner._load_backend(resolved, sleep=lambda _s: None)


# --- ShardWriter truncate (Bug #3.2: --no-resume must not duplicate rows) ----

def test_shard_writer_truncate_overwrites(tmp_path):
    path = tmp_path / "m.jsonl"
    with shard.ShardWriter(path) as w:            # first run (append/default)
        w.write({"Scenario": "s1"})
        w.write({"Scenario": "s2"})
    with shard.ShardWriter(path, truncate=True) as w:  # --no-resume regeneration
        w.write({"Scenario": "s1"})
    # the old rows are gone, not stacked underneath -> no duplicate cells
    rows = list(shard._iter_rows(path))
    assert [r["Scenario"] for r in rows] == ["s1"]


# --- gpu selection (orchestrator, no CUDA/spawn) ---------------------------

from tutor_cat.respgen.orchestrator import _select_gpu_ids, _validate_gpu_ids


def test_gpu_ids_pin_to_exact_devices():
    # the whole point of --gpu-ids 8: one worker, device 8, nothing else
    assert _select_gpu_ids([8], None, n_specs=30, detect=lambda: 8) == [8]


def test_gpu_ids_override_gpus_and_detect():
    assert _select_gpu_ids([8], gpus=8, n_specs=30, detect=lambda: 8) == [8]


def test_gpu_ids_deduped_order_preserved():
    assert _select_gpu_ids([8, 8, 9], None, n_specs=30, detect=lambda: 8) == [8, 9]


def test_gpu_ids_capped_by_model_count():
    # no point spawning more workers than models
    assert _select_gpu_ids([8, 9, 10], None, n_specs=2, detect=lambda: 16) == [8, 9]


def test_no_gpu_ids_falls_back_to_count():
    assert _select_gpu_ids(None, gpus=4, n_specs=30, detect=lambda: 99) == [0, 1, 2, 3]


def test_no_gpu_ids_no_count_uses_detect():
    assert _select_gpu_ids(None, None, n_specs=30, detect=lambda: 2) == [0, 1]


def test_validate_gpu_ids_ok_within_range():
    _validate_gpu_ids([0, 3], count=8)  # in range -> no raise


def test_validate_gpu_ids_rejects_out_of_range_with_zeroindex_hint():
    # --gpu-ids 8 on an 8-GPU node (indices 0..7): fail fast, point to index 7
    with pytest.raises(ValueError, match="index 7"):
        _validate_gpu_ids([8], count=8)


def test_validate_gpu_ids_skipped_when_count_unknown():
    _validate_gpu_ids([8], count=None)  # can't detect -> don't block the run


# --- run_fleet dispatch (injected run_one, no CUDA/spawn) -------------------

def test_run_fleet_dispatches_every_model_once():
    from tutor_cat.respgen import orchestrator

    specs = [ModelSpec(id=f"org/m{i}") for i in range(5)]
    seen = []
    lock = __import__("threading").Lock()

    def fake_run_one(spec, gpu_id):
        with lock:
            seen.append((spec.id, gpu_id))
        return {"model": spec.id, "status": "ok", "gpu": gpu_id}

    results = orchestrator.run_fleet(
        specs, "scenarios.jsonl", "out",
        gpu_ids=[2, 3], count_devices=lambda: None, run_one=fake_run_one,
    )
    # every model ran exactly once, and only on the pinned devices
    assert len(results) == 5
    assert {r["model"] for r in results} == {s.id for s in specs}
    assert {sid for sid, _ in seen} == {s.id for s in specs}
    assert all(gpu in (2, 3) for _, gpu in seen)


def test_run_fleet_isolates_a_dispatch_failure():
    from tutor_cat.respgen import orchestrator

    specs = [ModelSpec(id="org/good"), ModelSpec(id="org/bad")]

    def fake_run_one(spec, gpu_id):
        if spec.id == "org/bad":
            raise RuntimeError("dispatch blew up")
        return {"model": spec.id, "status": "ok"}

    results = orchestrator.run_fleet(
        specs, "scenarios.jsonl", "out",
        gpu_ids=[0], count_devices=lambda: None, run_one=fake_run_one,
    )
    by_id = {r["model"]: r for r in results}
    assert by_id["org/good"]["status"] == "ok"
    assert by_id["org/bad"]["status"] == "worker_error"  # isolated, fleet survived


# --- s3 uri parsing --------------------------------------------------------

def test_parse_s3_uri():
    assert parse_s3_uri("s3://bucket/a/b") == ("bucket", "a/b")
    assert parse_s3_uri("s3://bucket") == ("bucket", "")
    with pytest.raises(ValueError):
        parse_s3_uri("https://example.com/x")
