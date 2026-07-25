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
    assert len(specs) >= 20
    ids = {s.id for s in specs}
    assert "google/flan-t5-xl" in ids and "state-spaces/mamba-1.4b-hf" in ids


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


# --- gpu selection (orchestrator, no CUDA/spawn) ---------------------------

from tutor_cat.respgen.orchestrator import _select_gpu_ids


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


# --- s3 uri parsing --------------------------------------------------------

def test_parse_s3_uri():
    assert parse_s3_uri("s3://bucket/a/b") == ("bucket", "a/b")
    assert parse_s3_uri("s3://bucket") == ("bucket", "")
    with pytest.raises(ValueError):
        parse_s3_uri("https://example.com/x")
