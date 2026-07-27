"""Offline tests for the multi-judge validation runner.

These tests deliberately inject fake generation results.  They must not import
vLLM, download model weights, require a GPU, or contact an inference server.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts" / "run_judge_validation.py"
RUNNER_SPEC = importlib.util.spec_from_file_location(
    "run_judge_validation", RUNNER_PATH
)
assert RUNNER_SPEC and RUNNER_SPEC.loader
runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = runner
RUNNER_SPEC.loader.exec_module(runner)

COMPARE_PATH = ROOT / "scripts" / "compare_judges.py"
COMPARE_SPEC = importlib.util.spec_from_file_location(
    "compare_judges_for_runner_tests", COMPARE_PATH
)
assert COMPARE_SPEC and COMPARE_SPEC.loader
compare_judges = importlib.util.module_from_spec(COMPARE_SPEC)
sys.modules[COMPARE_SPEC.name] = compare_judges
COMPARE_SPEC.loader.exec_module(compare_judges)


def _case(case_id: str = "case-1") -> dict:
    return {
        "case_id": case_id,
        "response_id": "tb_0001__test_tutor",
        "scenario_id": "tb_0001",
        "criterion_id": "tb_0001_c01",
        "use_case": "feedback",
        "subject": "chemistry",
        "scenario_prompt": "Explain why the equilibrium shifts.",
        "conversation_context": [
            {"role": "student", "content": "I do not understand dilution."}
        ],
        "reference_solution": "Dilution favors the side with more particles.",
        "candidate_response": "The equilibrium shifts to the products.",
        "criterion": "The response must say that the equilibrium shifts right.",
        "expected_evidence": ["shifts right", "toward products"],
        "primary_skill": "content",
        "criticality": "critical",
    }


@pytest.mark.parametrize(
    ("judge", "text", "verdict", "score", "evidence"),
    [
        (
            "selene",
            "**Reasoning:** Evidence from the response: shifts to the products. "
            "Assessment: It directly meets the criterion.\n\n**Result:** Yes",
            "pass",
            1,
            "shifts to the products.",
        ),
        (
            "selene",
            "**Reasoning:** Evidence from the response: NONE. Assessment: The "
            "required explanation is absent.\n\n**Result:** No",
            "fail",
            0,
            "NONE.",
        ),
        (
            "flow",
            "<feedback>Evidence from output: shifts to the products. Criterion "
            "assessment: The criterion is met.</feedback>\n<score>1</score>",
            "pass",
            1,
            "shifts to the products.",
        ),
        (
            "flow",
            "<feedback>Evidence from output: NONE. Criterion assessment: The "
            "criterion is not met.</feedback>\n<score>0</score>",
            "fail",
            0,
            "NONE.",
        ),
        (
            "prometheus",
            "Feedback: Evidence from the response: NONE. Analysis: The response "
            "is partial. [RESULT] 3",
            "fail",
            3,
            "NONE.",
        ),
        (
            "prometheus",
            "Feedback: Evidence from the response: shifts to the products. "
            "Analysis: The response satisfies it. [RESULT] 4",
            "pass",
            4,
            "shifts to the products.",
        ),
        (
            "qwen",
            '{"verdict":"pass","rationale":"met","evidence":"quote"}',
            "pass",
            1,
            "quote",
        ),
        (
            "gemma",
            '{"verdict":"fail","rationale":"missing","evidence":"NONE"}',
            "fail",
            0,
            "NONE",
        ),
    ],
)
def test_native_outputs_are_normalized(
    judge: str, text: str, verdict: str, score: int, evidence: str
) -> None:
    parsed = runner.parse_judgment(text, runner.JUDGES[judge])

    assert parsed.verdict == verdict
    assert parsed.native_score == score
    assert parsed.evidence == evidence
    assert parsed.status == "ok"
    assert parsed.error is None


@pytest.mark.parametrize(
    ("judge", "text"),
    [
        ("selene", "**Result:** Yes\n**Result:** No"),
        ("flow", "<feedback>Unsure.</feedback>"),
        ("prometheus", "Feedback only, with no result marker."),
        ("qwen", '{"verdict":"maybe"}'),
        (
            "gemma",
            '{"verdict":"pass"}\n{"verdict":"fail"}',
        ),
    ],
)
def test_ambiguous_or_malformed_output_is_no_decision(
    judge: str, text: str
) -> None:
    parsed = runner.parse_judgment(text, runner.JUDGES[judge])

    assert parsed.verdict == "no_decision"
    assert parsed.status == "parse_error"
    assert parsed.error


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        ("**Reasoning:** supported\nResult: Yes", "pass"),
        ("**Reasoning:** absent. Result: No", "fail"),
    ],
)
def test_selene_accepts_one_explicit_unbolded_result(
    text: str, verdict: str
) -> None:
    parsed = runner.parse_judgment(text, runner.JUDGES["selene"])

    assert parsed.verdict == verdict
    assert parsed.status == "ok"


@pytest.mark.parametrize("judge", ["qwen", "gemma"])
def test_generic_parser_recovers_one_verdict_from_malformed_json(judge: str) -> None:
    text = '''```json
{
  "verdict": "fail",
  "rationale": "Uses invalid LaTeX escape \\(",
  "evidence": "NONE"
}
```'''
    parsed = runner.parse_judgment(text, runner.JUDGES[judge])

    assert parsed.verdict == "fail"
    assert parsed.native_score == 0
    assert parsed.status == "ok"
    assert parsed.rationale == ""
    assert parsed.evidence == ""


def test_generic_parser_rejects_duplicate_verdict_fields() -> None:
    text = '{"verdict":"pass","rationale":"reconsidered","verdict":"fail"}'
    parsed = runner.parse_judgment(text, runner.JUDGES["qwen"])

    assert parsed.verdict == "no_decision"
    assert parsed.status == "parse_error"
    assert "found 2" in str(parsed.error)


@pytest.mark.parametrize(
    ("judge", "text"),
    [
        ("selene", "My result: Yes, probably."),
        ("selene", "Assessment: the result: no longer holds."),
        ("selene", "The candidate literally wrote Result: Yes"),
        ("qwen", 'I considered "verdict":"pass" but did not emit JSON.'),
        ("qwen", '{"analysis":{"verdict":"pass"}}'),
        ("qwen", '{"verdict":"pass"'),
    ],
)
def test_recovery_parser_does_not_infer_from_prose_nested_or_bare_truncation(
    judge: str, text: str
) -> None:
    parsed = runner.parse_judgment(text, runner.JUDGES[judge])

    assert parsed.verdict == "no_decision"
    assert parsed.status == "parse_error"


def test_prometheus_threshold_is_explicit_and_validated() -> None:
    text = "Feedback: partial but acceptable. [RESULT] 3"
    parsed = runner.parse_judgment(
        text, runner.JUDGES["prometheus"], prometheus_pass_threshold=3
    )
    assert parsed.verdict == "pass"

    invalid = runner.parse_judgment(
        text, runner.JUDGES["prometheus"], prometheus_pass_threshold=1
    )
    assert invalid.verdict == "no_decision"
    assert invalid.status == "parse_error"


def test_prompt_version_identifies_evidence_gated_experiment() -> None:
    assert runner.PROMPT_VERSION == "judge-validation-v3"
    assert runner.NORMALIZATION_VERSION == "judge-normalization-v3"
    assert runner.EVIDENCE_POLICY_VERSION == "criterion-evidence-gate-v1"


@pytest.mark.parametrize("judge", sorted(runner.JUDGES))
def test_prompts_include_common_evidence_but_not_human_labels(judge: str) -> None:
    case = {
        **_case(),
        "human_label": "SECRET HUMAN PASS",
        "human_notes": "SECRET HUMAN NOTE",
        "anonymous_tutor": "SECRET TUTOR IDENTITY",
        "candidate_model": "SECRET MODEL IDENTITY",
    }

    prompt = "\n".join(
        message["content"]
        for message in runner.build_messages(case, runner.JUDGES[judge])
    )

    assert case["scenario_prompt"] in prompt
    assert case["conversation_context"][0]["content"] in prompt
    assert case["candidate_response"] in prompt
    assert case["criterion"] in prompt
    assert case["reference_solution"] in prompt
    assert case["expected_evidence"][0] in prompt
    assert runner.EVIDENCE_DECISION_POLICY in prompt
    assert "Evidence must come from the candidate response itself" in prompt
    assert "check every part" in prompt
    assert "cannot supply missing content" in prompt
    assert "missing, partial, vague, merely implied" in prompt
    assert "Equivalent wording, notation, or mathematically equivalent work" in prompt
    assert "negative or prohibition requirement" in prompt
    assert "SECRET HUMAN PASS" not in prompt
    assert "SECRET HUMAN NOTE" not in prompt
    assert "SECRET TUTOR IDENTITY" not in prompt
    assert "SECRET MODEL IDENTITY" not in prompt


@pytest.mark.parametrize("judge", sorted(runner.JUDGES))
def test_evidence_gate_is_unconditional_without_reference_or_authoring_hints(
    judge: str,
) -> None:
    case = {
        **_case(),
        "reference_solution": "",
        "expected_evidence": [],
        "criterion": "The response must not reveal the final answer.",
    }

    prompt = "\n".join(
        message["content"]
        for message in runner.build_messages(case, runner.JUDGES[judge])
    )

    assert runner.EVIDENCE_DECISION_POLICY in prompt
    assert "inspect the entire response" in prompt
    assert "quotation is not required to establish absence" in prompt
    assert "The response must not reveal the final answer." in prompt


@pytest.mark.parametrize("judge", sorted(runner.JUDGES))
@pytest.mark.parametrize("variant", runner.PROMPT_VARIANTS)
def test_controlled_prompt_variants_are_deterministic_blinded_and_preserve_payload(
    judge: str, variant: str
) -> None:
    case = {
        **_case(),
        "human_label": "SECRET HUMAN PASS",
        "human_notes": "SECRET HUMAN NOTE",
        "anonymous_tutor": "SECRET TUTOR IDENTITY",
        "candidate_model": "SECRET MODEL IDENTITY",
    }
    spec = runner.JUDGES[judge]

    messages = runner.build_variant_messages(case, spec, variant)

    assert messages == runner.build_variant_messages(case, spec, variant)
    assert [message["role"] for message in messages] == [
        message["role"] for message in runner.build_messages(case, spec)
    ]
    if variant == "canonical":
        assert messages == runner.build_messages(case, spec)
    else:
        assert messages != runner.build_messages(case, spec)

    prompt = "\n".join(message["content"] for message in messages)
    assert case["scenario_prompt"] in prompt
    assert case["conversation_context"][0]["content"] in prompt
    assert case["candidate_response"] in prompt
    assert case["criterion"] in prompt
    assert case["reference_solution"] in prompt
    assert case["expected_evidence"][0] in prompt
    assert runner.EVIDENCE_DECISION_POLICY in prompt
    assert "SECRET HUMAN PASS" not in prompt
    assert "SECRET HUMAN NOTE" not in prompt
    assert "SECRET TUTOR IDENTITY" not in prompt
    assert "SECRET MODEL IDENTITY" not in prompt


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_prepare_normalizes_packet_grades_and_blinds_judge_cases(
    tmp_path: Path,
) -> None:
    packet_dir = tmp_path / "packets"
    packet_dir.mkdir()
    (packet_dir / "grader_01.md").write_text(
        """# Human Grading Packet grader_01

## grader_01_item_01

- Scenario ID: `tb_test`
- Use case: `feedback`
- Subject: `chemistry`
- Tutor: `Tutor A`

### Scenario Prompt

Prompt shown to the human.

### Conversation Context

No prior context.

### Reference Solution

Reference shown to the human.

### Tutor Response

Candidate response with Markdown, commas, and $x^2$.

### Criteria To Grade

#### tb_test_c01

- Criterion: The response must contain the answer.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: human-only secret note
""",
        encoding="utf-8",
    )
    scenarios_path = tmp_path / "scenarios.jsonl"
    rubrics_path = tmp_path / "rubrics.jsonl"
    _write_jsonl(
        scenarios_path,
        [
            {
                "scenario_id": "tb_test",
                "prompt": "The actual scenario prompt.",
                "criterion_ids": ["tb_test_c01"],
                "use_case": "feedback",
                "subject": "chemistry",
                "conversation_context": [],
                "reference_solution": "The actual reference.",
            }
        ],
    )
    _write_jsonl(
        rubrics_path,
        [
            {
                "criterion_id": "tb_test_c01",
                "scenario_id": "tb_test",
                "criterion": "The response must contain the answer.",
                "expected_evidence": ["the answer"],
                "primary_skill": "content",
                "criticality": "critical",
            }
        ],
    )

    cases, humans = runner.prepare_cases(
        packet_dir,
        scenarios_path,
        rubrics_path,
        require_complete_matrix=False,
    )

    assert len(cases) == len(humans) == 1
    assert humans[0]["human_label"] == "pass"
    assert humans[0]["human_notes"] == "human-only secret note"
    assert humans[0]["candidate_model"] == "gpt-5.5"
    assert humans[0]["case_input_hash"] == runner.stable_hash(cases[0])
    assert cases[0]["response_id"] == "grader_01_item_01"
    assert cases[0]["scenario_prompt"] == "The actual scenario prompt."
    assert cases[0]["conversation_context"] == []
    assert cases[0]["reference_solution"] == "The actual reference."
    assert cases[0]["expected_evidence"] == []
    assert humans[0]["packet_prompt_matches_source"] is False
    assert humans[0]["packet_context_matches_source"] is False
    assert humans[0]["packet_reference_matches_source"] is False
    assert humans[0]["packet_criterion_matches_source"] is True
    assert cases[0]["candidate_response"].startswith("Candidate response")
    assert "human_label" not in cases[0]
    assert "human_notes" not in cases[0]
    assert "anonymous_tutor" not in cases[0]
    assert "candidate_model" not in cases[0]
    assert "human-only secret note" not in json.dumps(cases[0])


def test_prepare_enforces_complete_scenario_by_tutor_matrix(tmp_path: Path) -> None:
    scenarios = ROOT / "grader_packets" / "sample_scenarios.jsonl"
    rubrics = ROOT / "grader_packets" / "sample_rubrics.jsonl"

    complete_cases, _ = runner.prepare_cases(
        ROOT / "grader_packets", scenarios, rubrics
    )
    scenario_count = len({case["scenario_id"] for case in complete_cases})
    response_count = len({case["response_id"] for case in complete_cases})
    assert response_count == scenario_count * len(runner.TUTOR_MAP)

    incomplete_packets = tmp_path / "incomplete_packets"
    incomplete_packets.mkdir()
    source_packet = ROOT / "grader_packets" / "grader_01.md"
    (incomplete_packets / source_packet.name).write_text(
        source_packet.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="complete scenario-by-tutor matrix"):
        runner.prepare_cases(incomplete_packets, scenarios, rubrics)


def test_packet_not_provided_reference_is_normalized_to_empty(tmp_path: Path) -> None:
    packet = tmp_path / "grader_01.md"
    packet.write_text(
        """# Human Grading Packet grader_01

## grader_01_item_01

- Scenario ID: `tb_test`
- Use case: `feedback`
- Subject: `chemistry`
- Tutor: `Tutor A`

### Scenario Prompt

Explain the result.

### Conversation Context

_No prior conversation context._

### Reference Solution

_(not provided)_

### Tutor Response

The candidate response.

### Criteria To Grade

#### tb_test_c01

- Criterion: The response must explain the result.
- Primary skill: `content`
- Criticality: `critical`
- Grade (P/F): P
- Notes: ____
""",
        encoding="utf-8",
    )

    items = runner._packet_items(packet)

    assert len(items) == 1
    assert items[0]["reference_solution"] == ""
    assert runner._normalize_optional_packet_section("_(not provided)_") == ""


@pytest.mark.parametrize("field", sorted(runner.FORBIDDEN_JUDGE_CASE_FIELDS))
def test_judge_cases_reject_gold_and_tutor_identity_fields(field: str) -> None:
    contaminated = {**_case(), field: "must never reach a judge"}

    with pytest.raises(ValueError, match="forbidden gold/identity"):
        runner.validate_judge_cases([contaminated])


class FakeGenerator:
    def __init__(self, outputs: list[runner.GenerationResult]) -> None:
        self.outputs = list(outputs)
        self.calls: list[list[list[dict[str, str]]]] = []

    def generate(
        self, messages: list[list[dict[str, str]]]
    ) -> list[runner.GenerationResult]:
        self.calls.append(messages)
        count = len(messages)
        result, self.outputs = self.outputs[:count], self.outputs[count:]
        return result


def _configuration(judge: str = "selene") -> dict:
    spec = runner.JUDGES[judge]
    return {
        "model_id": spec.model_id,
        "revision": spec.revision,
        "judge_name": spec.name,
        "adapter": spec.adapter,
        "prompt_version": runner.PROMPT_VERSION,
        "evidence_policy_version": runner.EVIDENCE_POLICY_VERSION,
    }


def test_v3_configuration_hashes_separate_experiment_and_align_waves() -> None:
    base = _configuration()
    legacy = {
        **base,
        "prompt_version": "judge-validation-v2",
        "evidence_policy_version": "none",
        "prompt_variant": "canonical",
        "replicate_id": "r1",
    }
    v3 = {
        **base,
        "prompt_variant": "canonical",
        "replicate_id": "r1",
    }

    assert runner.stable_hash(v3) != runner.stable_hash(legacy)
    assert runner.frozen_configuration_hash(v3) != runner.frozen_configuration_hash(
        legacy
    )

    waves = [
        {**base, "prompt_variant": variant, "replicate_id": replicate}
        for variant, replicate in (
            ("canonical", "r1"),
            ("canonical", "r2"),
            ("canonical", "r3"),
            ("whitespace", "r1"),
            ("header_synonyms", "r1"),
            ("instruction_politeness", "r1"),
        )
    ]
    assert len({runner.stable_hash(configuration) for configuration in waves}) == 6
    assert len(
        {runner.frozen_configuration_hash(configuration) for configuration in waves}
    ) == 1


def test_run_cases_records_raw_output_errors_and_resumes_idempotently(
    tmp_path: Path,
) -> None:
    spec = runner.JUDGES["selene"]
    cases = [_case("case-1"), _case("case-2")]
    output = tmp_path / "selene.jsonl"
    raw_pass = "**Reasoning:** met\n\n**Result:** Yes"
    first = FakeGenerator(
        [
            runner.GenerationResult(text=raw_pass, latency_ms=4.0),
            runner.GenerationResult(error="CUDA out of memory", latency_ms=5.0),
        ]
    )

    written, resumed = runner.run_cases(
        cases,
        spec,
        first,
        output,
        configuration=_configuration(),
        batch_size=2,
        resume=False,
        prometheus_pass_threshold=4,
    )

    assert (written, resumed) == (2, 0)
    rows = runner.load_jsonl(output)
    assert rows[0]["verdict"] == "pass"
    assert rows[0]["raw_output"] == raw_pass
    assert rows[0]["status"] == "ok"
    assert rows[1]["verdict"] == "no_decision"
    assert rows[1]["status"] == "generation_error"
    assert rows[1]["error"] == "CUDA out of memory"

    second = FakeGenerator([])
    written, resumed = runner.run_cases(
        cases,
        spec,
        second,
        output,
        configuration=_configuration(),
        batch_size=2,
        resume=True,
        prometheus_pass_threshold=4,
    )
    assert (written, resumed) == (0, 2)
    assert second.calls == []
    assert runner.load_jsonl(output) == rows


def test_run_cases_records_variant_and_replicate_and_hashes_selected_prompt(
    tmp_path: Path,
) -> None:
    spec = runner.JUDGES["selene"]
    case = _case()
    output = tmp_path / "selene-whitespace-r2.jsonl"
    configuration = {
        **_configuration(),
        "prompt_variant": "whitespace",
        "replicate_id": "r2",
    }
    generator = FakeGenerator(
        [runner.GenerationResult(text="**Reasoning:** met\n\n**Result:** Yes")]
    )

    runner.run_cases(
        [case],
        spec,
        generator,
        output,
        configuration=configuration,
        batch_size=1,
        resume=False,
        prometheus_pass_threshold=4,
    )

    expected_messages = runner.build_variant_messages(case, spec, "whitespace")
    row = runner.load_jsonl(output)[0]
    assert generator.calls == [[expected_messages]]
    assert row["prompt_variant"] == "whitespace"
    assert row["replicate_id"] == "r2"
    assert row["prompt_version"] == "judge-validation-v3"
    assert row["normalization_version"] == "judge-normalization-v3"
    assert row["evidence_policy_version"] == "criterion-evidence-gate-v1"
    assert row["configuration_hash"] == runner.stable_hash(configuration)
    assert row["frozen_configuration_hash"] == runner.frozen_configuration_hash(
        configuration
    )
    assert row["prompt_hash"] == runner.stable_hash(expected_messages)
    assert row["prompt_hash"] != runner.stable_hash(runner.build_messages(case, spec))

    written, resumed = runner.run_cases(
        [case],
        spec,
        FakeGenerator([]),
        output,
        configuration=configuration,
        batch_size=1,
        resume=True,
        prometheus_pass_threshold=4,
    )
    assert (written, resumed) == (0, 1)


def test_resume_validates_prompt_hash_against_selected_variant(tmp_path: Path) -> None:
    spec = runner.JUDGES["selene"]
    output = tmp_path / "selene-header-r3.jsonl"
    configuration = {
        **_configuration(),
        "prompt_variant": "header_synonyms",
        "replicate_id": "r3",
    }
    runner.run_cases(
        [_case()],
        spec,
        FakeGenerator(
            [runner.GenerationResult(text="**Reasoning:** met\n\n**Result:** Yes")]
        ),
        output,
        configuration=configuration,
        batch_size=1,
        resume=False,
        prometheus_pass_threshold=4,
    )
    row = runner.load_jsonl(output)[0]
    row["prompt_hash"] = runner.stable_hash(runner.build_messages(_case(), spec))
    runner.write_jsonl(output, [row])

    with pytest.raises(ValueError, match="rendered prompt changed"):
        runner.run_cases(
            [_case()],
            spec,
            FakeGenerator([]),
            output,
            configuration=configuration,
            batch_size=1,
            resume=True,
            prometheus_pass_threshold=4,
        )


def test_retry_errors_archives_failure_and_reexecutes_only_failed_case(
    tmp_path: Path,
) -> None:
    spec = runner.JUDGES["selene"]
    cases = [_case("case-1"), _case("case-2")]
    output = tmp_path / "selene.jsonl"
    first = FakeGenerator(
        [
            runner.GenerationResult(
                text="**Reasoning:** met\n\n**Result:** Yes", latency_ms=1.0
            ),
            runner.GenerationResult(error="temporary OOM", latency_ms=2.0),
        ]
    )
    runner.run_cases(
        cases,
        spec,
        first,
        output,
        configuration=_configuration(),
        batch_size=2,
        resume=False,
        prometheus_pass_threshold=4,
    )

    retry = FakeGenerator(
        [
            runner.GenerationResult(
                text="**Reasoning:** recovered\n\n**Result:** No", latency_ms=3.0
            )
        ]
    )
    written, resumed = runner.run_cases(
        cases,
        spec,
        retry,
        output,
        configuration=_configuration(),
        batch_size=2,
        resume=True,
        prometheus_pass_threshold=4,
        retry_errors=True,
    )

    assert (written, resumed) == (1, 1)
    assert len(retry.calls) == 1
    assert len(retry.calls[0]) == 1
    canonical = runner.load_jsonl(output)
    assert [row["case_id"] for row in canonical] == ["case-1", "case-2"]
    assert canonical[0]["attempt"] == 1
    assert canonical[0]["verdict"] == "pass"
    assert canonical[1]["attempt"] == 2
    assert canonical[1]["verdict"] == "fail"
    assert canonical[1]["status"] == "ok"

    history = runner.load_jsonl(runner._retry_history_path(output))
    assert len(history) == 1
    assert history[0]["case_id"] == "case-2"
    assert history[0]["attempt"] == 1
    assert history[0]["status"] == "generation_error"
    assert history[0]["error"] == "temporary OOM"
    assert history[0]["retry_archived_at"]


def test_resume_refuses_changed_configuration_or_case_input(tmp_path: Path) -> None:
    spec = runner.JUDGES["selene"]
    output = tmp_path / "selene.jsonl"
    generator = FakeGenerator(
        [runner.GenerationResult(text="**Reasoning:** met\n\n**Result:** Yes")]
    )
    runner.run_cases(
        [_case()],
        spec,
        generator,
        output,
        configuration=_configuration(),
        batch_size=1,
        resume=False,
        prometheus_pass_threshold=4,
    )

    with pytest.raises(ValueError, match="stale configuration"):
        runner.run_cases(
            [_case()],
            spec,
            FakeGenerator([]),
            output,
            configuration={**_configuration(), "revision": "different"},
            batch_size=1,
            resume=True,
            prometheus_pass_threshold=4,
        )

    changed = {**_case(), "criterion": "A changed criterion."}
    with pytest.raises(ValueError, match="case input changed"):
        runner.run_cases(
            [changed],
            spec,
            FakeGenerator([]),
            output,
            configuration=_configuration(),
            batch_size=1,
            resume=True,
            prometheus_pass_threshold=4,
        )


def test_resume_refuses_changed_rendered_prompt_hash(tmp_path: Path) -> None:
    spec = runner.JUDGES["selene"]
    output = tmp_path / "selene.jsonl"
    runner.run_cases(
        [_case()],
        spec,
        FakeGenerator(
            [runner.GenerationResult(text="**Reasoning:** met\n\n**Result:** Yes")]
        ),
        output,
        configuration=_configuration(),
        batch_size=1,
        resume=False,
        prometheus_pass_threshold=4,
    )
    row = runner.load_jsonl(output)[0]
    row["prompt_hash"] = "tampered-prompt-hash"
    runner.write_jsonl(output, [row])

    with pytest.raises(ValueError, match="rendered prompt changed"):
        runner.run_cases(
            [_case()],
            spec,
            FakeGenerator([]),
            output,
            configuration=_configuration(),
            batch_size=1,
            resume=True,
            prometheus_pass_threshold=4,
        )


def test_resume_recovers_only_a_truncated_final_jsonl_row(tmp_path: Path) -> None:
    spec = runner.JUDGES["selene"]
    output = tmp_path / "selene.jsonl"
    runner.run_cases(
        [_case("case-1")],
        spec,
        FakeGenerator(
            [runner.GenerationResult(text="**Reasoning:** met\n\n**Result:** Yes")]
        ),
        output,
        configuration=_configuration(),
        batch_size=1,
        resume=False,
        prometheus_pass_threshold=4,
    )
    with output.open("a", encoding="utf-8") as handle:
        handle.write('{"case_id":"truncated-tail"')

    written, resumed = runner.run_cases(
        [_case("case-1"), _case("case-2")],
        spec,
        FakeGenerator(
            [runner.GenerationResult(text="**Reasoning:** missing\n\n**Result:** No")]
        ),
        output,
        configuration=_configuration(),
        batch_size=1,
        resume=True,
        prometheus_pass_threshold=4,
    )

    assert (written, resumed) == (1, 1)
    assert [row["case_id"] for row in runner.load_jsonl(output)] == [
        "case-1",
        "case-2",
    ]
    corrupt_tail = output.with_name("selene.corrupt_tail.txt")
    assert corrupt_tail.exists()
    assert '"case_id":"truncated-tail"' in corrupt_tail.read_text(encoding="utf-8")


def test_resume_appends_after_valid_final_json_row_without_newline(
    tmp_path: Path,
) -> None:
    spec = runner.JUDGES["selene"]
    output = tmp_path / "selene.jsonl"
    runner.run_cases(
        [_case("case-1")],
        spec,
        FakeGenerator(
            [runner.GenerationResult(text="**Reasoning:** met\n\n**Result:** Yes")]
        ),
        output,
        configuration=_configuration(),
        batch_size=1,
        resume=False,
        prometheus_pass_threshold=4,
    )
    # A valid JSON object without a trailing newline is not a corrupt tail.
    output.write_bytes(output.read_bytes().removesuffix(b"\n"))

    written, resumed = runner.run_cases(
        [_case("case-1"), _case("case-2")],
        spec,
        FakeGenerator(
            [runner.GenerationResult(text="**Reasoning:** absent\n\n**Result:** No")]
        ),
        output,
        configuration=_configuration(),
        batch_size=1,
        resume=True,
        prometheus_pass_threshold=4,
    )

    assert (written, resumed) == (1, 1)
    assert output.read_bytes().endswith(b"\n")
    assert [row["case_id"] for row in runner.load_jsonl(output)] == [
        "case-1",
        "case-2",
    ]


def test_resume_rejects_corrupt_json_before_the_final_tail(tmp_path: Path) -> None:
    output = tmp_path / "corrupt.jsonl"
    output.write_text(
        '{"case_id":"broken"\n{"another":"valid-line"}\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="outside a recoverable tail"):
        runner._load_resume_rows(output, recover_truncated_tail=True)


def test_run_cases_rejects_generator_output_count_mismatch(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="returned 1 results for 2 cases"):
        runner.run_cases(
            [_case("case-1"), _case("case-2")],
            runner.JUDGES["selene"],
            FakeGenerator(
                [runner.GenerationResult(text="**Reasoning:** met\n\n**Result:** Yes")]
            ),
            tmp_path / "results.jsonl",
            configuration=_configuration(),
            batch_size=2,
            resume=False,
            prometheus_pass_threshold=4,
        )


def _judgment(
    case_id: str,
    judge_name: str,
    verdict: str,
    input_hash: str,
    *,
    configuration_hash: str | None = None,
    status: str = "ok",
) -> dict:
    return {
        "case_id": case_id,
        "judge_name": judge_name,
        "judge_model": f"test/{judge_name}",
        "judge_revision": "test-revision",
        "adapter": f"{judge_name}-adapter",
        "prompt_version": runner.PROMPT_VERSION,
        "normalization_version": runner.NORMALIZATION_VERSION,
        "checkpoint_provenance": "test",
        "configuration_hash": configuration_hash or f"{judge_name}-configuration",
        "input_hash": input_hash,
        "status": status,
        "verdict": verdict,
    }


def _write_humans(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "case_input_hash",
                "candidate_model",
                "scenario_id",
                "criterion_id",
                "human_label",
            ],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "case_id": "case-1",
                    "case_input_hash": "input-hash-1",
                    "candidate_model": "tutor-a",
                    "scenario_id": "scenario-1",
                    "criterion_id": "criterion-1",
                    "human_label": "pass",
                },
                {
                    "case_id": "case-2",
                    "case_input_hash": "input-hash-2",
                    "candidate_model": "tutor-b",
                    "scenario_id": "scenario-2",
                    "criterion_id": "criterion-2",
                    "human_label": "fail",
                },
            ]
        )


def test_merge_outputs_feed_existing_comparison_and_preserve_missing_coverage(
    tmp_path: Path,
) -> None:
    humans = tmp_path / "humans.csv"
    selene = tmp_path / "selene.jsonl"
    flow = tmp_path / "flow.jsonl"
    merged = tmp_path / "merged.csv"
    _write_humans(humans)
    _write_jsonl(
        selene,
        [
            _judgment("case-1", "selene", "pass", "input-hash-1"),
            _judgment("case-2", "selene", "fail", "input-hash-2"),
        ],
    )
    # A partial independently-run shard must become no_decision, not fail.
    _write_jsonl(
        flow,
        [_judgment("case-1", "flow", "pass", "input-hash-1")],
    )

    fields, rows, judges = runner.merge_judgments(humans, [selene, flow])
    runner.write_csv(merged, fields, rows)

    assert judges == ["judge_selene", "judge_flow"]
    assert rows[1]["judge_flow"] == "no_decision"
    cases, loaded_judges, _ = compare_judges.load_cases(merged)
    assert loaded_judges == judges
    selene_metrics = compare_judges.calculate_metrics(cases, "judge_selene")
    flow_metrics = compare_judges.calculate_metrics(cases, "judge_flow")
    assert selene_metrics["accuracy"] == 1.0
    assert selene_metrics["coverage"] == 1.0
    assert flow_metrics["accuracy"] == 0.5
    assert flow_metrics["coverage"] == 0.5
    assert flow_metrics["no_decision_n"] == 1


def test_merge_rejects_duplicate_or_unknown_judgment_cases(tmp_path: Path) -> None:
    humans = tmp_path / "humans.csv"
    _write_humans(humans)
    duplicate = tmp_path / "duplicate.jsonl"
    _write_jsonl(
        duplicate,
        [
            _judgment("case-1", "flow", "pass", "input-hash-1"),
            _judgment("case-1", "flow", "fail", "input-hash-1"),
        ],
    )
    with pytest.raises(ValueError, match="duplicate case_id"):
        runner.merge_judgments(humans, [duplicate])

    unknown = tmp_path / "unknown.jsonl"
    _write_jsonl(
        unknown,
        [_judgment("case-unknown", "flow", "pass", "unknown-input-hash")],
    )
    with pytest.raises(ValueError, match="unknown case_id"):
        runner.merge_judgments(humans, [unknown])


def test_merge_rejects_inconsistent_checkpoint_provenance(tmp_path: Path) -> None:
    humans = tmp_path / "humans.csv"
    judgments = tmp_path / "selene.jsonl"
    _write_humans(humans)
    first = _judgment("case-1", "selene", "pass", "input-hash-1")
    second = _judgment("case-2", "selene", "fail", "input-hash-2")
    second["checkpoint_provenance"] = "different-checkpoint-source"
    _write_jsonl(judgments, [first, second])

    with pytest.raises(ValueError, match="one checkpoint_provenance value"):
        runner.merge_judgments(humans, [judgments])


def test_merge_rejects_uniformly_blank_required_checkpoint_provenance(
    tmp_path: Path,
) -> None:
    humans = tmp_path / "humans.csv"
    judgments = tmp_path / "selene.jsonl"
    _write_humans(humans)
    rows = [
        _judgment("case-1", "selene", "pass", "input-hash-1"),
        _judgment("case-2", "selene", "fail", "input-hash-2"),
    ]
    for row in rows:
        row["checkpoint_provenance"] = ""
    _write_jsonl(judgments, rows)

    with pytest.raises(ValueError, match="checkpoint_provenance is blank"):
        runner.merge_judgments(humans, [judgments])


def test_merge_rejects_judgment_from_a_different_prepared_input(
    tmp_path: Path,
) -> None:
    humans = tmp_path / "humans.csv"
    judgments = tmp_path / "selene.jsonl"
    _write_humans(humans)
    _write_jsonl(
        judgments,
        [
            _judgment("case-1", "selene", "pass", "wrong-input-hash"),
            _judgment("case-2", "selene", "fail", "input-hash-2"),
        ],
    )

    with pytest.raises(ValueError, match="does not match prepared case"):
        runner.merge_judgments(humans, [judgments])


@pytest.mark.parametrize("human_label", ["ambiguous", "maybe", ""])
def test_compare_strict_label_gate_rejects_nonbinary_human_labels(
    tmp_path: Path, human_label: str
) -> None:
    humans = tmp_path / "humans.csv"
    judgments = tmp_path / "selene.jsonl"
    out_csv = tmp_path / "comparison.csv"
    with humans.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "case_input_hash",
                "candidate_model",
                "scenario_id",
                "criterion_id",
                "human_label",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "case_id": "case-1",
                "case_input_hash": "input-hash-1",
                "candidate_model": "tutor-a",
                "scenario_id": "scenario-1",
                "criterion_id": "criterion-1",
                "human_label": human_label,
            }
        )
    _write_jsonl(
        judgments,
        [_judgment("case-1", "selene", "pass", "input-hash-1")],
    )
    args = runner.build_parser().parse_args(
        [
            "compare",
            "--human-labels",
            str(humans),
            "--judgments",
            str(judgments),
            "--out-csv",
            str(out_csv),
            "--require-complete-labels",
        ]
    )

    with pytest.raises(ValueError, match="missing or nonbinary"):
        runner._compare_command(args)
    assert not out_csv.exists()


def _run_cli_args(tmp_path: Path) -> list[str]:
    return [
        "run",
        "--cases",
        str(tmp_path / "cases.jsonl"),
        "--judge",
        "selene",
        "--output",
        str(tmp_path / "selene.jsonl"),
    ]


def test_run_cli_prompt_variant_and_replicate_defaults_and_overrides(
    tmp_path: Path,
) -> None:
    defaults = runner.build_parser().parse_args(_run_cli_args(tmp_path))
    assert defaults.prompt_variant == "canonical"
    assert defaults.replicate_id == "r1"

    configured = runner.build_parser().parse_args(
        _run_cli_args(tmp_path)
        + ["--prompt-variant", "instruction_politeness", "--replicate-id", "r3"]
    )
    assert configured.prompt_variant == "instruction_politeness"
    assert configured.replicate_id == "r3"


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--prompt-variant", "unknown"],
        ["--replicate-id", ""],
        ["--replicate-id", "repeat 1"],
        ["--replicate-id", "../r1"],
    ],
)
def test_run_cli_rejects_invalid_prompt_study_identifiers(
    tmp_path: Path, extra_args: list[str]
) -> None:
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args(_run_cli_args(tmp_path) + extra_args)


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--concurrency", "0"),
        ("--timeout", "0"),
        ("--batch-size", "0"),
        ("--tensor-parallel-size", "0"),
        ("--gpu-memory-utilization", "0"),
        ("--gpu-memory-utilization", "1.01"),
        ("--max-model-len", "0"),
        ("--max-tokens", "0"),
        ("--temperature", "-0.01"),
        ("--top-p", "0"),
        ("--limit", "0"),
        ("--prometheus-pass-threshold", "1"),
        ("--prometheus-pass-threshold", "6"),
    ],
)
def test_run_cli_rejects_out_of_range_numeric_values(
    tmp_path: Path, flag: str, value: str
) -> None:
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args(_run_cli_args(tmp_path) + [flag, value])


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--timeout", "nan"),
        ("--timeout", "inf"),
        ("--temperature", "nan"),
        ("--temperature", "inf"),
        ("--gpu-memory-utilization", "nan"),
        ("--top-p", "nan"),
    ],
)
def test_run_cli_rejects_nonfinite_numeric_values(
    tmp_path: Path, flag: str, value: str
) -> None:
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args(_run_cli_args(tmp_path) + [flag, value])


@pytest.mark.parametrize(
    ("abbreviated_flag", "value"),
    [
        ("--temp", "0.7"),
        ("--model-i", "unapproved/model"),
        ("--prometheus-pass-th", "3"),
    ],
)
def test_run_cli_rejects_abbreviated_frozen_options(
    tmp_path: Path, abbreviated_flag: str, value: str
) -> None:
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args(
            _run_cli_args(tmp_path) + [abbreviated_flag, value]
        )


def test_compare_cli_rejects_negative_bootstrap_count(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args(
            [
                "compare",
                "--human-labels",
                str(tmp_path / "humans.csv"),
                "--judgments",
                str(tmp_path / "judge.jsonl"),
                "--out-csv",
                str(tmp_path / "merged.csv"),
                "--bootstrap-samples",
                "-1",
            ]
        )


def test_run_cli_accepts_valid_numeric_boundaries(tmp_path: Path) -> None:
    args = runner.build_parser().parse_args(
        _run_cli_args(tmp_path)
        + [
            "--concurrency",
            "1",
            "--timeout",
            "0.1",
            "--batch-size",
            "1",
            "--tensor-parallel-size",
            "1",
            "--gpu-memory-utilization",
            "1",
            "--max-model-len",
            "1",
            "--max-tokens",
            "1",
            "--temperature",
            "0",
            "--top-p",
            "1",
            "--limit",
            "1",
            "--prometheus-pass-threshold",
            "5",
        ]
    )
    assert args.gpu_memory_utilization == 1.0
    assert args.temperature == 0.0
    assert args.limit == 1


def test_manifest_records_generator_startup_failure_without_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases_path = tmp_path / "cases.jsonl"
    output = tmp_path / "selene.jsonl"
    runner.write_jsonl(cases_path, [_case()])

    class StartupFailure:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("offline startup failure")

    monkeypatch.setattr(runner, "VLLMGenerator", StartupFailure)
    args = runner.build_parser().parse_args(
        [
            "run",
            "--cases",
            str(cases_path),
            "--judge",
            "selene",
            "--output",
            str(output),
            "--prompt-variant",
            "header_synonyms",
            "--replicate-id",
            "r2",
        ]
    )

    with pytest.raises(RuntimeError, match="offline startup failure"):
        runner._run_command_unlocked(args)

    manifest = json.loads(
        output.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["error"] == "RuntimeError: offline startup failure"
    assert manifest["finished_at"]
    assert manifest["case_count"] == 1
    assert manifest["prompt_variant"] == "header_synonyms"
    assert manifest["replicate_id"] == "r2"
    assert manifest["configuration"]["prompt_variant"] == "header_synonyms"
    assert manifest["configuration"]["replicate_id"] == "r2"
    assert not output.exists()


def test_manifest_marks_all_generation_errors_as_no_usable_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases_path = tmp_path / "cases.jsonl"
    output = tmp_path / "selene.jsonl"
    runner.write_jsonl(cases_path, [_case()])

    class OfflineErrorGenerator:
        def __init__(self, *args, **kwargs) -> None:
            self.closed = False

        def generate(self, messages):
            return [
                runner.GenerationResult(error="offline generation failure")
                for _ in messages
            ]

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(runner, "VLLMGenerator", OfflineErrorGenerator)
    args = runner.build_parser().parse_args(
        [
            "run",
            "--cases",
            str(cases_path),
            "--judge",
            "selene",
            "--output",
            str(output),
        ]
    )

    assert runner._run_command_unlocked(args) == 3
    manifest = json.loads(
        output.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed_no_usable_decisions"
    assert manifest["usable_decisions"] == 0
    assert manifest["no_decision_rows"] == 1
    assert manifest["status_counts"] == {"generation_error": 1}
    rows = runner.load_jsonl(output)
    assert rows[0]["verdict"] == "no_decision"
    assert rows[0]["error"] == "offline generation failure"


def test_manifest_marks_post_generation_finalization_failure_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases_path = tmp_path / "cases.jsonl"
    output = tmp_path / "selene.jsonl"
    runner.write_jsonl(cases_path, [_case()])

    class OfflinePassGenerator:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def generate(self, messages):
            return [
                runner.GenerationResult(
                    text="**Reasoning:** met\n\n**Result:** Yes"
                )
                for _ in messages
            ]

        def close(self) -> None:
            pass

    def fail_final_validation(*args, **kwargs):
        raise RuntimeError("offline finalization validation failure")

    monkeypatch.setattr(runner, "VLLMGenerator", OfflinePassGenerator)
    monkeypatch.setattr(runner, "_load_existing_results", fail_final_validation)
    args = runner.build_parser().parse_args(
        [
            "run",
            "--cases",
            str(cases_path),
            "--judge",
            "selene",
            "--output",
            str(output),
        ]
    )

    with pytest.raises(RuntimeError, match="finalization validation failure"):
        runner._run_command_unlocked(args)

    manifest = json.loads(
        output.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["error"] == (
        "RuntimeError: offline finalization validation failure"
    )
    assert manifest["finished_at"]
    # Generation completed and its audit row remains available for diagnosis.
    assert output.exists()
    assert runner.load_jsonl(output)[0]["verdict"] == "pass"


@pytest.mark.parametrize(
    ("uri", "bucket", "prefix"),
    [
        ("s3://eval-bucket", "eval-bucket", ""),
        ("s3://eval-bucket/study/run-01", "eval-bucket", "study/run-01"),
        ("  s3://eval-bucket/study/run-01/  ", "eval-bucket", "study/run-01"),
    ],
)
def test_parse_s3_uri_normalizes_valid_prefixes(
    uri: str, bucket: str, prefix: str
) -> None:
    parsed = runner.parse_s3_uri(uri)

    assert parsed.bucket == bucket
    assert parsed.key_prefix == prefix
    expected_root = f"s3://{bucket}" + (f"/{prefix}" if prefix else "")
    assert parsed.as_uri() == expected_root
    expected_key = f"{prefix}/judge.jsonl" if prefix else "judge.jsonl"
    assert parsed.key_for("/tmp/judge.jsonl") == expected_key
    assert parsed.uri_for("/tmp/judge.jsonl") == f"s3://{bucket}/{expected_key}"


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "https://eval-bucket/study",
        "s3:///study",
        "s3://user:password@eval-bucket/study",
        "s3://eval-bucket:443/study",
        "s3://eval-bucket/study?version=1",
        "s3://eval-bucket/study#fragment",
        "s3://eval-bucket/study/../other",
        "s3://eval-bucket/./study",
    ],
)
def test_parse_s3_uri_rejects_unsafe_or_non_s3_values(uri: str) -> None:
    with pytest.raises(ValueError, match="invalid S3 prefix"):
        runner.parse_s3_uri(uri)


class FakeS3NotFound(Exception):
    def __init__(self) -> None:
        self.response = {
            "Error": {"Code": "NoSuchKey"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }
        super().__init__("NoSuchKey")


class FakeS3Client:
    """Small in-memory S3 double; it never imports boto3 or touches a network."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}
        self.put_calls: list[dict] = []
        self.head_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.head_overrides: dict[str, object] = {}

    def put_object(self, **kwargs):
        body = kwargs["Body"].read()
        call = {**kwargs, "Body": body}
        self.put_calls.append(call)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = {
            "Body": body,
            "ContentLength": kwargs["ContentLength"],
            "ChecksumSHA256": kwargs["ChecksumSHA256"],
            "Metadata": dict(kwargs["Metadata"]),
        }
        return {"ETag": '"fake-etag"'}

    def head_object(self, **kwargs):
        self.head_calls.append(dict(kwargs))
        key = (kwargs["Bucket"], kwargs["Key"])
        if key not in self.objects:
            raise FakeS3NotFound()
        stored = dict(self.objects[key])
        stored.pop("Body")
        stored.update(self.head_overrides)
        return stored

    def get_object(self, **kwargs):
        self.get_calls.append(dict(kwargs))
        stored = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        return {
            "Body": io.BytesIO(stored["Body"]),
            "ChecksumSHA256": stored["ChecksumSHA256"],
        }


def test_s3_publisher_uploads_and_verifies_size_and_sha256_metadata(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "selene.jsonl"
    content = b'{"case_id":"case-1"}\n'
    artifact.write_bytes(content)
    client = FakeS3Client()
    publisher = runner.S3Publisher(
        "s3://eval-bucket/study/run-01", client=client
    )

    uri = publisher.upload(
        artifact,
        metadata={"artifact_kind": "judge_result", "Run ID": "run-01"},
    )

    assert uri == "s3://eval-bucket/study/run-01/selene.jsonl"
    assert len(client.put_calls) == 1
    put = client.put_calls[0]
    expected_hex = hashlib.sha256(content).hexdigest()
    expected_b64 = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
    assert put["Bucket"] == "eval-bucket"
    assert put["Key"] == "study/run-01/selene.jsonl"
    assert put["Body"] == content
    assert put["ContentLength"] == len(content)
    assert put["ChecksumSHA256"] == expected_b64
    assert put["Metadata"] == {
        "sha256": expected_hex,
        "artifact-kind": "judge_result",
        "run-id": "run-01",
    }
    assert client.head_calls == [
        {
            "Bucket": "eval-bucket",
            "Key": "study/run-01/selene.jsonl",
            "ChecksumMode": "ENABLED",
        }
    ]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"ContentLength": 999}, "size verification failed"),
        ({"Metadata": {"sha256": "wrong"}}, "metadata mismatch"),
        ({"ChecksumSHA256": "wrong"}, "checksum verification failed"),
    ],
)
def test_s3_publisher_rejects_unverified_upload(
    tmp_path: Path, override: dict, message: str
) -> None:
    artifact = tmp_path / "artifact.jsonl"
    artifact.write_text("one row\n", encoding="utf-8")
    client = FakeS3Client()
    client.head_overrides = override
    publisher = runner.S3Publisher("s3://eval-bucket/run", client=client)

    with pytest.raises(RuntimeError, match=message):
        publisher.upload(artifact)


def test_s3_publisher_download_verifies_before_atomic_replace(tmp_path: Path) -> None:
    source = tmp_path / "remote" / "judge.jsonl"
    source.parent.mkdir()
    content = b"remote checkpoint\n"
    source.write_bytes(content)
    client = FakeS3Client()
    publisher = runner.S3Publisher("s3://eval-bucket/checkpoints", client=client)
    publisher.upload(source)
    source.unlink()

    assert publisher.download_if_exists(source) is True
    assert source.read_bytes() == content
    assert client.get_calls == [
        {
            "Bucket": "eval-bucket",
            "Key": "checkpoints/judge.jsonl",
            "ChecksumMode": "ENABLED",
        }
    ]


def test_run_cases_checkpoints_after_every_completed_batch(tmp_path: Path) -> None:
    cases = [_case(f"case-{index}") for index in range(1, 6)]
    output = tmp_path / "selene.jsonl"
    generator = FakeGenerator(
        [
            runner.GenerationResult(text="**Reasoning:** met\n\n**Result:** Yes")
            for _ in cases
        ]
    )
    checkpoints: list[tuple[Path, int]] = []

    def checkpoint(path: Path) -> None:
        # The callback must run only after the batch is flushed to valid JSONL.
        checkpoints.append((path, len(runner.load_jsonl(path))))

    written, resumed = runner.run_cases(
        cases,
        runner.JUDGES["selene"],
        generator,
        output,
        configuration=_configuration(),
        batch_size=2,
        resume=False,
        prometheus_pass_threshold=4,
        checkpoint_callback=checkpoint,
    )

    assert (written, resumed) == (5, 0)
    assert checkpoints == [(output, 2), (output, 4), (output, 5)]
    assert len(generator.calls) == 3


def test_s3_cli_options_parse_and_required_upload_needs_a_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = runner.build_parser()
    args = parser.parse_args(
        _run_cli_args(tmp_path)
        + [
            "--s3-output-prefix",
            "s3://eval-bucket/study/run-01",
            "--require-s3-upload",
            "--allow-s3-takeover",
        ]
    )
    assert args.s3_output_prefix == "s3://eval-bucket/study/run-01"
    assert args.require_s3_upload is True
    assert args.allow_s3_takeover is True

    monkeypatch.delenv("JUDGE_S3_OUTPUT_PREFIX", raising=False)
    missing = parser.parse_args(_run_cli_args(tmp_path) + ["--require-s3-upload"])
    with pytest.raises(ValueError, match="neither --s3-output-prefix"):
        runner._s3_publisher_from_args(missing)

    invalid = parser.parse_args(
        _run_cli_args(tmp_path)
        + ["--s3-output-prefix", "https://not-an-s3-bucket/path"]
    )
    with pytest.raises(ValueError, match="invalid S3 prefix"):
        runner._s3_publisher_from_args(invalid)


class RecordingPublisher:
    def __init__(self, prefix: str = "s3://eval-bucket/study") -> None:
        self.prefix = runner.parse_s3_uri(prefix)
        self.uploads: list[tuple[Path, dict[str, str]]] = []
        self.heads: dict[str, dict | None] = {}

    def uri_for(self, path: str | Path) -> str:
        return self.prefix.uri_for(path)

    def key_for(self, path: str | Path) -> str:
        return self.prefix.key_for(path)

    def head(self, path: str | Path) -> dict | None:
        return self.heads.get(Path(path).name)

    def upload(self, path: str | Path, *, metadata=None) -> str:
        local = Path(path)
        assert local.is_file(), f"attempted to upload missing artifact {local}"
        self.uploads.append((local, dict(metadata or {})))
        return self.uri_for(local)


def _comparison_args(
    tmp_path: Path,
    humans: Path,
    judgments: Path,
    *,
    with_optional_outputs: bool,
) -> tuple[object, Path, Path | None, Path | None]:
    out_csv = tmp_path / "comparison.csv"
    json_out = tmp_path / "summary.json" if with_optional_outputs else None
    disagreements = (
        tmp_path / "disagreements.csv" if with_optional_outputs else None
    )
    argv = [
        "compare",
        "--human-labels",
        str(humans),
        "--judgments",
        str(judgments),
        "--out-csv",
        str(out_csv),
        "--bootstrap-samples",
        "0",
        "--s3-output-prefix",
        "s3://eval-bucket/comparison/run-01",
    ]
    if json_out is not None:
        argv.extend(["--json-out", str(json_out)])
    if disagreements is not None:
        argv.extend(["--disagreements-out", str(disagreements)])
    return runner.build_parser().parse_args(argv), out_csv, json_out, disagreements


def _comparison_inputs(tmp_path: Path) -> tuple[Path, Path]:
    humans = tmp_path / "human_labels.csv"
    judgments = tmp_path / "selene.jsonl"
    _write_humans(humans)
    _write_jsonl(
        judgments,
        [
            _judgment("case-1", "selene", "pass", "input-hash-1"),
            _judgment("case-2", "selene", "fail", "input-hash-2"),
        ],
    )
    return humans, judgments


def test_compare_uploads_outputs_only_after_success_with_manifest_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    humans, judgments = _comparison_inputs(tmp_path)
    args, out_csv, json_out, disagreements = _comparison_args(
        tmp_path, humans, judgments, with_optional_outputs=True
    )
    assert json_out is not None and disagreements is not None
    publisher = RecordingPublisher("s3://eval-bucket/comparison/run-01")
    monkeypatch.setattr(runner, "_s3_publisher_from_args", lambda args: publisher)

    def successful_compare(command, **kwargs):
        json_out.write_text('{"status":"ok"}\n', encoding="utf-8")
        disagreements.write_text("case_id\n", encoding="utf-8")
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(runner.subprocess, "run", successful_compare)

    assert runner._compare_command(args) == 0
    uploaded_names = [path.name for path, _ in publisher.uploads]
    assert uploaded_names == [
        out_csv.name,
        json_out.name,
        disagreements.name,
        "comparison.manifest.json",
    ]
    assert humans.name not in uploaded_names
    assert judgments.name not in uploaded_names
    assert [metadata["artifact_kind"] for _, metadata in publisher.uploads] == [
        "comparison_result",
        "comparison_result",
        "comparison_result",
        "comparison_manifest",
    ]
    manifest = json.loads(
        (tmp_path / "comparison.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "complete"
    assert set(manifest["s3"]["artifacts"]) == {
        out_csv.name,
        json_out.name,
        disagreements.name,
    }


def test_failed_compare_uploads_nothing_and_writes_no_completion_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    humans, judgments = _comparison_inputs(tmp_path)
    args, out_csv, _, _ = _comparison_args(
        tmp_path, humans, judgments, with_optional_outputs=False
    )
    publisher = RecordingPublisher("s3://eval-bucket/comparison/run-01")
    monkeypatch.setattr(runner, "_s3_publisher_from_args", lambda args: publisher)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: type("Failed", (), {"returncode": 7})(),
    )

    assert runner._compare_command(args) == 7
    assert out_csv.exists()  # Useful local input for diagnosing the failed report.
    assert publisher.uploads == []
    assert not (tmp_path / "comparison.manifest.json").exists()


def test_comparison_s3_destination_rejects_remote_conflicts(tmp_path: Path) -> None:
    artifact = tmp_path / "comparison.csv"
    manifest = tmp_path / "comparison.manifest.json"
    publisher = RecordingPublisher()
    publisher.heads[artifact.name] = {
        "Metadata": {
            "configuration-hash": "different-configuration",
            "inputs-hash": "inputs-a",
        }
    }

    with pytest.raises(ValueError, match="conflicting object"):
        runner._validate_comparison_s3_destination(
            publisher,
            artifacts=[artifact],
            manifest_path=manifest,
            configuration_hash="configuration-a",
            inputs_hash="inputs-a",
        )

    publisher.heads[artifact.name] = {
        "Metadata": {
            "configuration-hash": "configuration-a",
            "inputs-hash": "inputs-a",
        }
    }
    runner._validate_comparison_s3_destination(
        publisher,
        artifacts=[artifact],
        manifest_path=manifest,
        configuration_hash="configuration-a",
        inputs_hash="inputs-a",
    )


def test_run_s3_hydration_requires_takeover_for_starting_remote_run(
    tmp_path: Path,
) -> None:
    target = tmp_path / "selene.jsonl"
    manifest = tmp_path / "selene.manifest.json"
    target.write_text('{"case_id":"case-1"}\n', encoding="utf-8")
    manifest.write_text('{"status":"starting"}\n', encoding="utf-8")
    client = FakeS3Client()
    publisher = runner.S3Publisher("s3://eval-bucket/run-01", client=client)
    publisher.upload(target, metadata={"artifact_kind": "judge_result"})
    publisher.upload(
        manifest,
        metadata={
            "artifact_kind": "run_manifest",
            "configuration_hash": "configuration-a",
            "cases_sha256": "cases-a",
            "run_status": "starting",
        },
    )
    target.unlink()
    manifest.unlink()

    with pytest.raises(RuntimeError, match="may still be active"):
        runner._hydrate_run_from_s3(
            publisher,
            target=target,
            manifest_path=manifest,
            configuration_hash="configuration-a",
            cases_sha256="cases-a",
            resume=True,
            allow_takeover=False,
        )
    assert not target.exists() and not manifest.exists()

    runner._hydrate_run_from_s3(
        publisher,
        target=target,
        manifest_path=manifest,
        configuration_hash="configuration-a",
        cases_sha256="cases-a",
        resume=True,
        allow_takeover=True,
    )
    assert target.read_text(encoding="utf-8") == '{"case_id":"case-1"}\n'
    assert manifest.read_text(encoding="utf-8") == '{"status":"starting"}\n'


def test_run_s3_hydration_rejects_remote_configuration_mismatch(
    tmp_path: Path,
) -> None:
    target = tmp_path / "selene.jsonl"
    manifest = tmp_path / "selene.manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    client = FakeS3Client()
    publisher = runner.S3Publisher("s3://eval-bucket/run-01", client=client)
    publisher.upload(
        manifest,
        metadata={
            "configuration_hash": "remote-configuration",
            "cases_sha256": "cases-a",
            "run_status": "complete",
        },
    )
    manifest.unlink()

    with pytest.raises(ValueError, match="different judge configuration"):
        runner._hydrate_run_from_s3(
            publisher,
            target=target,
            manifest_path=manifest,
            configuration_hash="local-configuration",
            cases_sha256="cases-a",
            resume=True,
            allow_takeover=False,
        )


def test_run_s3_sync_uploads_retry_history_before_manifest(tmp_path: Path) -> None:
    target = tmp_path / "selene.jsonl"
    retry_history = runner._retry_history_path(target)
    manifest_path = tmp_path / "selene.manifest.json"
    target.write_text('{"case_id":"case-1"}\n', encoding="utf-8")
    retry_history.write_text('{"case_id":"case-1","attempt":1}\n', encoding="utf-8")
    publisher = RecordingPublisher("s3://eval-bucket/run-01")
    manifest = {"status": "starting"}

    runner._sync_run_to_s3(
        publisher,
        target=target,
        manifest_path=manifest_path,
        manifest=manifest,
        configuration_hash="configuration-a",
        cases_sha256="cases-a",
    )

    assert [path.name for path, _ in publisher.uploads] == [
        target.name,
        retry_history.name,
        manifest_path.name,
    ]
    assert publisher.uploads[-1][1]["artifact_kind"] == "run_manifest"
    written_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(written_manifest["s3"]["artifacts"]) == {
        target.name,
        retry_history.name,
    }


def test_compare_s3_rejects_completion_manifest_filename_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    humans, judgments = _comparison_inputs(tmp_path)
    out_csv = tmp_path / "comparison.csv"
    json_out = tmp_path / "comparison.manifest.json"
    argv = [
        "compare",
        "--human-labels",
        str(humans),
        "--judgments",
        str(judgments),
        "--out-csv",
        str(out_csv),
        "--s3-output-prefix",
        "s3://eval-bucket/comparison/run-collision",
    ]
    argv.extend(["--json-out", str(json_out)])
    args = runner.build_parser().parse_args(argv)
    publisher = RecordingPublisher(
        "s3://eval-bucket/comparison/run-collision"
    )
    monkeypatch.setattr(runner, "_s3_publisher_from_args", lambda args: publisher)

    def must_not_run(*args, **kwargs):
        raise AssertionError("comparison subprocess ran despite filename collision")

    monkeypatch.setattr(runner.subprocess, "run", must_not_run)

    with pytest.raises(ValueError, match="conflicts with the generated S3"):
        runner._compare_command(args)
    assert publisher.uploads == []
    assert not out_csv.exists()
