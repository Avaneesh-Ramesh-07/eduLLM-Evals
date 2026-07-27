"""Offline tests for the six-wave judge reliability comparison."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "compare_judge_reliability.py"
SPEC = importlib.util.spec_from_file_location("compare_judge_reliability", SCRIPT_PATH)
assert SPEC and SPEC.loader
reliability = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reliability
SPEC.loader.exec_module(reliability)


HUMAN_FIELDS = [
    "case_id",
    "case_input_hash",
    "human_label",
    "scenario_id",
    "criterion_id",
    "primary_skill",
    "criticality",
]


def human_rows() -> list[dict[str, str]]:
    return [
        {
            "case_id": "case-1",
            "case_input_hash": "input-hash-1",
            "human_label": "pass",
            "scenario_id": "scenario-1",
            "criterion_id": "criterion-1",
            "primary_skill": "content",
            "criticality": "critical",
        },
        {
            "case_id": "case-2",
            "case_input_hash": "input-hash-2",
            "human_label": "fail",
            "scenario_id": "scenario-1",
            "criterion_id": "criterion-2",
            "primary_skill": "content",
            "criticality": "critical",
        },
        {
            "case_id": "case-3",
            "case_input_hash": "input-hash-3",
            "human_label": "pass",
            "scenario_id": "scenario-2",
            "criterion_id": "criterion-3",
            "primary_skill": "scaffolding",
            "criticality": "not_critical",
        },
        {
            "case_id": "case-4",
            "case_input_hash": "input-hash-4",
            "human_label": "fail",
            "scenario_id": "scenario-2",
            "criterion_id": "criterion-4",
            "primary_skill": "scaffolding",
            "criticality": "critical_negative",
        },
        {
            "case_id": "case-5",
            "case_input_hash": "input-hash-5",
            "human_label": "pass",
            "scenario_id": "scenario-3",
            "criterion_id": "criterion-5",
            "primary_skill": "",
            "criticality": "not_critical",
        },
        {
            "case_id": "case-6",
            "case_input_hash": "input-hash-6",
            "human_label": "fail",
            "scenario_id": "scenario-3",
            "criterion_id": "criterion-6",
            "primary_skill": "",
            "criticality": "not_critical",
        },
    ]


def write_humans(path: Path, rows: list[dict[str, str]] | None = None) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HUMAN_FIELDS)
        writer.writeheader()
        writer.writerows(rows if rows is not None else human_rows())


def judgment_rows(
    wave: str,
    decisions: list[str],
    *,
    judge: str = "judge-a",
    model: str = "test/judge-a",
) -> list[dict]:
    variant, replicate = reliability.EXPECTED_WAVES[wave]
    rows = []
    for human, decision in zip(human_rows(), decisions):
        ok = decision in {"pass", "fail"}
        rows.append(
            {
                "case_id": human["case_id"],
                "scenario_id": human["scenario_id"],
                "criterion_id": human["criterion_id"],
                "judge_name": judge,
                "judge_model": model,
                "judge_revision": "0123456789abcdef",
                "adapter": "test-binary",
                "prompt_version": "judge-validation-v3",
                "normalization_version": "judge-normalization-v1",
                "checkpoint_provenance": "test-fixture",
                "configuration_hash": f"configuration-{wave}",
                "frozen_configuration_hash": "frozen-configuration-a",
                "prompt_variant": variant,
                "replicate_id": replicate,
                "input_hash": human["case_input_hash"],
                "prompt_hash": (
                    f"canonical-prompt-{human['case_id']}"
                    if variant == "canonical"
                    else f"{variant}-prompt-{human['case_id']}"
                ),
                "status": "ok" if ok else "parse_error",
                "verdict": decision,
            }
        )
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


IMPERFECT_DECISIONS = {
    "canonical_r1": ["pass", "fail", "fail", "no_decision", "pass", "fail"],
    "canonical_r2": ["pass", "fail", "fail", "fail", "pass", "fail"],
    "canonical_r3": ["pass", "pass", "fail", "fail", "pass", "fail"],
    "whitespace_r1": ["pass", "fail", "fail", "no_decision", "pass", "fail"],
    "header_synonyms_r1": ["pass", "pass", "fail", "fail", "pass", "fail"],
    "instruction_politeness_r1": [
        "no_decision",
        "fail",
        "fail",
        "no_decision",
        "pass",
        "fail",
    ],
}


def write_study(
    tmp_path: Path,
    *,
    decisions: dict[str, list[str]] | None = None,
) -> tuple[Path, dict[str, Path]]:
    humans = tmp_path / "human_labels.csv"
    write_humans(humans)
    paths = {}
    for wave in reliability.EXPECTED_WAVES:
        path = tmp_path / f"judge-a.{wave}.jsonl"
        write_jsonl(
            path,
            judgment_rows(wave, (decisions or IMPERFECT_DECISIONS)[wave]),
        )
        paths[wave] = path
    return humans, paths


def cli_args(humans: Path, paths: dict[str, Path]) -> list[str]:
    args = [str(humans)]
    for wave in reliability.EXPECTED_WAVES:
        args.extend(["--wave", f"judge-a:{wave}:{paths[wave]}"])
    return args


def load_study(
    humans: Path, paths: dict[str, Path]
) -> tuple[list, dict[str, reliability.WaveData]]:
    cases, _ = reliability.load_human_labels(humans)
    by_id = {case.case_id: case for case in cases}
    waves = {
        wave: reliability.load_wave(
            path, judge="judge-a", wave=wave, humans_by_id=by_id
        )
        for wave, path in paths.items()
    }
    reliability.validate_judge_waves("judge-a", waves)
    return cases, waves


def test_metrics_penalize_no_decisions_and_report_required_slices(tmp_path: Path):
    humans, paths = write_study(tmp_path)
    cases, waves = load_study(humans, paths)

    canonical = reliability.performance_report(cases, waves["canonical_r1"].decisions)
    assert canonical["n"] == 6
    assert canonical["coverage"] == pytest.approx(5 / 6)
    assert canonical["macro_f1"] == pytest.approx((0.8 + 2 / 3) / 2)
    assert canonical["weighted_f1"] == pytest.approx((0.8 + 2 / 3) / 2)
    assert canonical["mcc"] == pytest.approx(2 / 3)
    assert canonical["mcc_scope"] == "decided_cases_only"
    assert canonical["critical_failures"]["critical_human_failure_n"] == 2
    assert canonical["critical_failures"]["sensitivity"] == 0.5
    assert canonical["by_primary_skill"]["content"]["macro_f1"] == 1.0
    assert canonical["by_primary_skill"]["scaffolding"]["macro_f1"] == 0.0
    assert canonical["by_primary_skill"]["unmapped"]["macro_f1"] == 1.0
    assert canonical["by_primary_skill"]["unmapped"]["mapped"] is False


def test_repeat_and_prompt_consistency_treat_no_decision_as_inconsistent(
    tmp_path: Path,
):
    humans, paths = write_study(tmp_path)
    cases, waves = load_study(humans, paths)

    retest = reliability.test_retest_report(cases, waves)
    assert retest["exact_three_repeat_strict_agreement_rate"] == pytest.approx(4 / 6)
    assert retest["worst_pairwise_strict_agreement_rate"] == pytest.approx(4 / 6)
    first_pair = retest["pairwise"]["canonical_r1_vs_canonical_r2"]
    assert first_pair["raw_agreement_rate"] == pytest.approx(5 / 6)
    assert first_pair["strict_agreement_rate"] == pytest.approx(5 / 6)
    assert first_pair["cohen_kappa"] is not None

    prompt = reliability.prompt_consistency_report(cases, waves)
    whitespace = prompt["variants"]["whitespace_r1"]
    # The two identical no_decisions on case-4 still count as inconsistent.
    assert whitespace["raw_disagreement_rate"] == 0.0
    assert whitespace["flip_rate"] == pytest.approx(1 / 6)
    assert prompt["worst_variant_flip_rate"] == pytest.approx(2 / 6)


@pytest.mark.parametrize("field", ["prompt_version", "normalization_version"])
def test_cross_judge_comparison_rejects_mixed_experiment_versions(
    tmp_path: Path, field: str
) -> None:
    humans, paths = write_study(tmp_path)
    _, waves = load_study(humans, paths)
    other = {
        wave_name: replace(
            wave,
            judge="judge-b",
            metadata={
                **wave.metadata,
                "judge_name": "judge-b",
                field: f"different-{field}",
            },
        )
        for wave_name, wave in waves.items()
    }

    with pytest.raises(reliability.InputValidationError, match=field):
        reliability.validate_cross_judge_versions(
            {"judge-a": waves, "judge-b": other}
        )


def test_cli_writes_reports_and_only_enforces_thresholds_when_requested(
    tmp_path: Path,
):
    humans, paths = write_study(tmp_path)
    json_out = tmp_path / "summary.json"
    csv_out = tmp_path / "summary.csv"
    args = cli_args(humans, paths) + [
        "--bootstrap-samples",
        "0",
        "--json-out",
        str(json_out),
        "--csv-out",
        str(csv_out),
    ]

    assert reliability.main(args) == 0
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["schema_version"] == "judge-reliability-comparison-v1"
    assert report["human_gold"]["criterion_case_n"] == 6
    assert report["acceptance"]["all_judges_pass"] is False
    assert (
        report["acceptance"]["judges"]["judge-a"]["marginal_reliability"][
            "status"
        ]
        == "not_computed"
    )
    assert report["inputs"]["waves"]["judge-a"]["canonical_r1"]["sha256"]
    with csv_out.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["judge"] == "judge-a"
    assert rows[0]["acceptance_passed"] == "False"

    assert reliability.main(args + ["--enforce-thresholds"]) == 3


def test_perfect_judge_passes_default_acceptance_thresholds(tmp_path: Path):
    gold = [row["human_label"] for row in human_rows()]
    decisions = {wave: list(gold) for wave in reliability.EXPECTED_WAVES}
    humans, paths = write_study(tmp_path, decisions=decisions)
    output = tmp_path / "perfect.json"

    code = reliability.main(
        cli_args(humans, paths)
        + [
            "--bootstrap-samples",
            "0",
            "--json-out",
            str(output),
            "--enforce-thresholds",
        ]
    )

    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    acceptance = report["acceptance"]["judges"]["judge-a"]
    assert acceptance["passed"] is True
    assert acceptance["checks"]["mapped_primary_skills"]["passed"] is True
    assert (
        report["judges"]["judge-a"]["prompt_consistency"][
            "worst_variant_flip_rate"
        ]
        == 0.0
    )


def test_scenario_clustered_bootstrap_adds_key_intervals(tmp_path: Path):
    humans, paths = write_study(tmp_path)
    cases, waves = load_study(humans, paths)

    report = reliability.build_report(
        humans,
        cases,
        {"judge-a": waves},
        bootstrap_samples=60,
        seed=11,
    )

    judge = report["judges"]["judge-a"]
    macro_ci = judge["waves"]["canonical_r1"]["confidence_intervals_95"][
        "macro_f1"
    ]
    assert macro_ci["requested_resamples"] == 60
    assert macro_ci["lower"] <= macro_ci["upper"]
    assert (
        judge["test_retest"]["confidence_intervals_95"][
            "worst_pairwise_strict_agreement_rate"
        ]
        is not None
    )
    assert judge["prompt_consistency"]["worst_variant_flip_rate_ci_95"] is not None


def test_wave_rejects_incomplete_coverage_and_wrong_input_hash(tmp_path: Path):
    humans, paths = write_study(tmp_path)
    cases, _ = reliability.load_human_labels(humans)
    by_id = {case.case_id: case for case in cases}

    incomplete = judgment_rows("canonical_r1", IMPERFECT_DECISIONS["canonical_r1"])[
        :-1
    ]
    write_jsonl(paths["canonical_r1"], incomplete)
    with pytest.raises(reliability.InputValidationError, match="incomplete case coverage"):
        reliability.load_wave(
            paths["canonical_r1"],
            judge="judge-a",
            wave="canonical_r1",
            humans_by_id=by_id,
        )

    wrong_hash = judgment_rows("canonical_r1", IMPERFECT_DECISIONS["canonical_r1"])
    wrong_hash[0]["input_hash"] = "different-prepared-input"
    write_jsonl(paths["canonical_r1"], wrong_hash)
    with pytest.raises(reliability.InputValidationError, match="input_hash does not match"):
        reliability.load_wave(
            paths["canonical_r1"],
            judge="judge-a",
            wave="canonical_r1",
            humans_by_id=by_id,
        )


def test_wave_rejects_wrong_wave_metadata_and_cross_wave_provenance(tmp_path: Path):
    humans, paths = write_study(tmp_path)
    cases, _ = reliability.load_human_labels(humans)
    by_id = {case.case_id: case for case in cases}

    rows = judgment_rows("whitespace_r1", IMPERFECT_DECISIONS["whitespace_r1"])
    for row in rows:
        row["prompt_variant"] = "canonical"
    write_jsonl(paths["whitespace_r1"], rows)
    with pytest.raises(reliability.InputValidationError, match="requires prompt_variant"):
        reliability.load_wave(
            paths["whitespace_r1"],
            judge="judge-a",
            wave="whitespace_r1",
            humans_by_id=by_id,
        )

    humans, paths = write_study(tmp_path)
    unchanged = judgment_rows(
        "whitespace_r1", IMPERFECT_DECISIONS["whitespace_r1"]
    )
    unchanged[0]["prompt_hash"] = "canonical-prompt-case-1"
    write_jsonl(paths["whitespace_r1"], unchanged)
    waves = {
        wave: reliability.load_wave(
            path, judge="judge-a", wave=wave, humans_by_id=by_id
        )
        for wave, path in paths.items()
    }
    with pytest.raises(reliability.InputValidationError, match="did not change prompt_hash"):
        reliability.validate_judge_waves("judge-a", waves)

    humans, paths = write_study(tmp_path)
    changed = judgment_rows(
        "header_synonyms_r1",
        IMPERFECT_DECISIONS["header_synonyms_r1"],
        model="test/different-checkpoint",
    )
    write_jsonl(paths["header_synonyms_r1"], changed)
    waves = {
        wave: reliability.load_wave(
            path, judge="judge-a", wave=wave, humans_by_id=by_id
        )
        for wave, path in paths.items()
    }
    with pytest.raises(reliability.InputValidationError, match="judge_model differs"):
        reliability.validate_judge_waves("judge-a", waves)

    humans, paths = write_study(tmp_path)
    changed = judgment_rows(
        "instruction_politeness_r1",
        IMPERFECT_DECISIONS["instruction_politeness_r1"],
    )
    for row in changed:
        row["frozen_configuration_hash"] = "different-frozen-settings"
    write_jsonl(paths["instruction_politeness_r1"], changed)
    waves = {
        wave: reliability.load_wave(
            path, judge="judge-a", wave=wave, humans_by_id=by_id
        )
        for wave, path in paths.items()
    }
    with pytest.raises(
        reliability.InputValidationError,
        match="frozen_configuration_hash differs",
    ):
        reliability.validate_judge_waves("judge-a", waves)


def test_cli_rejects_duplicate_and_missing_wave_specs(tmp_path: Path, capsys):
    humans, paths = write_study(tmp_path)
    missing_args = cli_args(humans, paths)[:-2] + ["--bootstrap-samples", "0"]
    assert reliability.main(missing_args) == 2
    assert "missing wave" in capsys.readouterr().err

    duplicate_args = cli_args(humans, paths) + [
        "--wave",
        f"judge-a:canonical_r1:{paths['canonical_r1']}",
        "--bootstrap-samples",
        "0",
    ]
    assert reliability.main(duplicate_args) == 2
    assert "duplicate --wave" in capsys.readouterr().err
