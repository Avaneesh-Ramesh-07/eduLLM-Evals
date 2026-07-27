"""Offline tests for the human-vs-LLM-judge comparison script."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "compare_judges.py"
SPEC = importlib.util.spec_from_file_location("compare_judges", SCRIPT_PATH)
assert SPEC and SPEC.loader
compare_judges = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = compare_judges
SPEC.loader.exec_module(compare_judges)


FIELDNAMES = [
    "case_id",
    "candidate_model",
    "scenario_id",
    "criterion_id",
    "human_label",
    "judge_a",
    "judge_b",
    "human_notes",
]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def sample_rows() -> list[dict[str, str]]:
    return [
        {
            "case_id": "case-1",
            "candidate_model": "gpt-5.5",
            "scenario_id": "q-1",
            "criterion_id": "q-1-c1",
            "human_label": "pass",
            "judge_a": "pass",
            "judge_b": "1",
            "human_notes": "",
        },
        {
            "case_id": "case-2",
            "candidate_model": "gpt-5.5",
            "scenario_id": "q-2",
            "criterion_id": "q-2-c1",
            "human_label": "fail",
            "judge_a": "fail",
            "judge_b": "pass",
            "human_notes": "",
        },
        {
            "case_id": "case-3",
            "candidate_model": "opus-4.8",
            "scenario_id": "q-3",
            "criterion_id": "q-3-c1",
            "human_label": "0",
            "judge_a": "",
            "judge_b": "fail",
            "human_notes": "judge A timed out",
        },
        {
            "case_id": "case-4",
            "candidate_model": "opus-4.8",
            "scenario_id": "q-4",
            "criterion_id": "q-4-c1",
            "human_label": "ambiguous",
            "judge_a": "pass",
            "judge_b": "fail",
            "human_notes": "needs adjudication",
        },
    ]


def test_metrics_exclude_ambiguous_gold_and_count_no_decision_as_wrong(tmp_path: Path):
    path = tmp_path / "labels.csv"
    write_csv(path, sample_rows())

    cases, judges, _ = compare_judges.load_cases(path)
    assert judges == ["judge_a", "judge_b"]

    metrics_a = compare_judges.calculate_metrics(cases, "judge_a")
    assert metrics_a["n"] == 3
    assert metrics_a["correct"] == 2
    assert metrics_a["accuracy"] == pytest.approx(2 / 3)
    assert metrics_a["conditional_accuracy"] == 1.0
    assert metrics_a["coverage"] == pytest.approx(2 / 3)
    assert metrics_a["balanced_accuracy"] == 0.75
    assert metrics_a["fail_recall"] == 0.5
    assert metrics_a["false_pass_rate"] == 0.0
    assert metrics_a["no_decision_n"] == 1

    metrics_b = compare_judges.calculate_metrics(cases, "judge_b")
    assert metrics_b["accuracy"] == pytest.approx(2 / 3)
    assert metrics_b["coverage"] == 1.0
    assert metrics_b["fail_recall"] == 0.5
    assert metrics_b["false_pass_rate"] == 0.5


def test_report_groups_by_tutor_and_bootstraps_by_scenario(tmp_path: Path):
    path = tmp_path / "labels.csv"
    write_csv(path, sample_rows())
    cases, judges, _ = compare_judges.load_cases(path)

    report = compare_judges.build_report(
        cases,
        judges,
        group_by="candidate_model",
        cluster_by="scenario_id",
        bootstrap_samples=100,
        seed=7,
    )

    assert report["human_gold"] == {
        "input_rows": 4,
        "binary_gold_n": 3,
        "excluded_ambiguous_or_missing_n": 1,
        "pass_n": 1,
        "fail_n": 2,
        "pass_rate": pytest.approx(1 / 3),
    }
    assert set(report["by_group"]["judge_a"]) == {"gpt-5.5", "opus-4.8"}
    assert report["judges"]["judge_a"]["accuracy_ci_95"] is not None


def test_cli_writes_machine_report_and_disagreements(tmp_path: Path):
    input_path = tmp_path / "labels.csv"
    json_path = tmp_path / "summary.json"
    disagreements_path = tmp_path / "disagreements.csv"
    write_csv(input_path, sample_rows())

    code = compare_judges.main(
        [
            str(input_path),
            "--bootstrap-samples",
            "0",
            "--json-out",
            str(json_path),
            "--disagreements-out",
            str(disagreements_path),
        ]
    )

    assert code == 0
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["judges"]["judge_a"]["accuracy"] == pytest.approx(2 / 3)
    with disagreements_path.open(encoding="utf-8", newline="") as handle:
        disagreements = list(csv.DictReader(handle))
    assert {row["case_id"] for row in disagreements} == {"case-2", "case-3"}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("human_label", "maybe", "unsupported human_label"),
        ("judge_a", "mostly pass", "unsupported value"),
    ],
)
def test_invalid_labels_fail_loudly(
    tmp_path: Path, field: str, value: str, message: str
):
    rows = sample_rows()[:1]
    rows[0][field] = value
    path = tmp_path / "labels.csv"
    write_csv(path, rows)

    with pytest.raises(compare_judges.InputValidationError, match=message):
        compare_judges.load_cases(path)


def test_duplicate_case_ids_are_rejected(tmp_path: Path):
    rows = sample_rows()[:2]
    rows[1]["case_id"] = rows[0]["case_id"]
    path = tmp_path / "labels.csv"
    write_csv(path, rows)

    with pytest.raises(compare_judges.InputValidationError, match="duplicate case_id"):
        compare_judges.load_cases(path)
