"""Tests for the Fisher-information scenario selector."""

import numpy as np

from tutor_cat.mirt import pass_probability
from tutor_cat.schemas import Rubric, Scenario
from tutor_cat.dataio import ItemBank
from tutor_cat.selector import (
    criterion_information,
    scenario_value,
    select_next,
    total_information_value,
)


def _rubric(cid, sid, q, a, b):
    return Rubric(
        criterion_id=cid, scenario_id=sid, criterion=cid,
        q=np.array(q), a=np.array(a, dtype=float), b=b,
    )


def _bank(rubrics):
    scenarios = {}
    by_sid = {}
    for r in rubrics:
        by_sid.setdefault(r.scenario_id, []).append(r.criterion_id)
    for sid, cids in by_sid.items():
        scenarios[sid] = Scenario(scenario_id=sid, prompt=sid, criterion_ids=cids)
    return ItemBank(scenarios, {r.criterion_id: r for r in rubrics})


def test_criterion_information_formula():
    theta = np.zeros(3)
    r = _rubric("c1", "s1", [1, 0, 0], [1.5, 0.0, 0.0], 0.0)
    p = pass_probability(theta, r.a, r.q, r.b)  # 0.5
    assert criterion_information(theta, r, 0) == p * (1 - p) * 1.5**2
    assert criterion_information(theta, r, 1) == 0.0  # q-masked


def test_information_peaks_at_coin_flip():
    r = _rubric("c1", "s1", [1, 0, 0], [1.5, 0.0, 0.0], 0.0)
    matched = criterion_information(np.zeros(3), r, 0)        # p = 0.5
    too_easy = criterion_information(np.array([3.0, 0, 0]), r, 0)  # p ~ 0.99
    assert matched > 10 * too_easy


def test_scenario_value_cost_normalization():
    theta = np.zeros(3)
    # s1: one applicable content criterion; s2: same criterion + an off-skill one.
    r1 = _rubric("s1_c1", "s1", [1, 0, 0], [1.5, 0, 0], 0.0)
    r2 = _rubric("s2_c1", "s2", [1, 0, 0], [1.5, 0, 0], 0.0)
    r3 = _rubric("s2_c2", "s2", [0, 1, 0], [0, 1.0, 0], 0.0)
    bank = _bank([r1, r2, r3])
    v1 = scenario_value(theta, bank.rubrics_for("s1"), 0)
    v2 = scenario_value(theta, bank.rubrics_for("s2"), 0)
    # Cost = applicable (q_k=1) criteria only, so the off-skill criterion is free.
    assert v1 == v2
    # Inapplicable scenario returns None
    assert scenario_value(theta, bank.rubrics_for("s2"), 2) is None


def test_select_next_prefers_high_information_and_is_seeded():
    theta = np.zeros(3)
    rubrics = []
    # 10 scenarios with increasing difficulty mismatch on content
    for i in range(10):
        rubrics.append(_rubric(f"s{i}_c1", f"s{i}", [1, 0, 0], [1.5, 0, 0], float(i) * 0.8))
    bank = _bank(rubrics)
    unused = sorted(bank.scenarios)

    res1 = select_next(theta, bank, unused, 0, np.random.default_rng(42), top_n=5)
    res2 = select_next(theta, bank, unused, 0, np.random.default_rng(42), top_n=5)
    assert res1.scenario_id == res2.scenario_id            # seeded reproducibility
    top_ids = {sid for sid, _ in res1.top_candidates}
    assert top_ids == {"s0", "s1", "s2", "s3", "s4"}       # b nearest theta win
    assert res1.mode == "target_skill"


def test_fallback_when_no_scenario_has_target_skill():
    theta = np.zeros(3)
    r1 = _rubric("s1_c1", "s1", [1, 0, 0], [0.8, 0, 0], 0.0)
    r2 = _rubric("s2_c1", "s2", [1, 0, 0], [1.9, 0, 0], 0.0)
    bank = _bank([r1, r2])
    res = select_next(theta, bank, ["s1", "s2"], 2, np.random.default_rng(0))  # scaffolding
    assert res.mode == "fallback_total_info"
    assert res.scenario_id == "s2"  # higher total information
    assert total_information_value(theta, bank.rubrics_for("s2")) > total_information_value(
        theta, bank.rubrics_for("s1")
    )


def test_exhausted_bank_returns_none():
    bank = _bank([_rubric("s1_c1", "s1", [1, 0, 0], [1.0, 0, 0], 0.0)])
    assert select_next(np.zeros(3), bank, [], 0, np.random.default_rng(0)) is None
