"""Unit tests for Q-matrix human-review agreement calculations."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/analyze_qmatrix_human_review.py"
SPEC = importlib.util.spec_from_file_location("analyze_qmatrix_human_review", SCRIPT)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)


def test_provisional_consensus():
    assert MOD.provisional_consensus([1, 1]) == 1
    assert MOD.provisional_consensus([0, 0]) == 0
    assert MOD.provisional_consensus([0, 1]) is None
    assert MOD.provisional_consensus([1, 1, 0]) == 1
    assert MOD.provisional_consensus([0, 0, 1]) == 0


def test_binary_metrics():
    rows = [
        {"ai_label": 1, "human_consensus": 1},
        {"ai_label": 1, "human_consensus": 0},
        {"ai_label": 0, "human_consensus": 1},
        {"ai_label": 0, "human_consensus": 0},
        {"ai_label": 0, "human_consensus": None},
    ]
    result = MOD.binary_metrics(rows)
    assert result["resolved"] == 4 and result["unresolved"] == 1
    assert result["tp"] == result["fp"] == result["fn"] == result["tn"] == 1
    assert result["accuracy"] == 0.5


def test_perfect_kappa():
    result = MOD.cohen_kappa([0, 1, 0, 1], [0, 1, 0, 1])
    assert result["agreement"] == 1.0
    assert result["kappa"] == 1.0

    fleiss = MOD.fleiss_kappa([[0, 0, 0], [1, 1, 1]])
    assert fleiss["agreement"] == 1.0
    assert fleiss["kappa"] == 1.0
