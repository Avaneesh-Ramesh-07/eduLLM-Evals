"""Tests for the three-reviewer Q-matrix audit packet design."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_qmatrix_human_review.py"
SPEC = importlib.util.spec_from_file_location("prepare_qmatrix_human_review", SCRIPT)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)


def _row(cid: str) -> dict:
    return {"criterion_id": cid, "final": {"scenario_id": "s1"}}


def test_assignment_overlap_design():
    groups = {
        "CORE": [_row(f"core-{i}") for i in range(10)],
        "AB": [_row(f"ab-{i}") for i in range(5)],
        "AC": [_row(f"ac-{i}") for i in range(5)],
        "BC": [_row(f"bc-{i}") for i in range(5)],
    }
    assignments = MOD.reviewer_assignments(groups)
    assert {reviewer: len(rows) for reviewer, rows in assignments.items()} == {
        "A": 20, "B": 20, "C": 20,
    }
    counts = {}
    for rows in assignments.values():
        for row in rows:
            counts[row["criterion_id"]] = counts.get(row["criterion_id"], 0) + 1
    assert len(counts) == 25
    assert sorted(counts.values()).count(3) == 10
    assert sorted(counts.values()).count(2) == 15


def test_reviewer_html_is_blind_and_exportable():
    row = {
        "criterion_id": "c1",
        "final": {
            "scenario_id": "s1",
            "criterion": "The tutor identifies the misconception.",
            "expected_evidence": ["Names the student's error"],
        },
    }
    scenario = {
        "scenario_id": "s1",
        "prompt": "Help this student.",
        "conversation_context": [],
        "reference_solution": "The student confused numerator and denominator.",
    }
    page = MOD.render_html("A", [row], {"s1": scenario})
    assert "generated_q_mapping" not in page
    assert "final_q_mapping" not in page
    assert "q_rationale" not in page
    assert "Validate and download CSV" in page
    assert "content" in page and "diagnosis" in page and "scaffolding" in page
