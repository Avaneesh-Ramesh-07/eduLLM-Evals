"""Choosing Next Scenario (PRD): Fisher-information scenario selection.

    V_kc(theta) = q_kc × P_c(theta) × (1 − P_c(theta)) × a_kc²
    ScenarioValue(S, k) = sum_{c in S} V_kc(theta) / #{c in S : q_kc = 1}

Selection: rank eligible scenarios descending, take top n (default 5), pick one
uniformly at random with the run's seeded RNG.

Fallback (no eligible scenario for the target skill): the unused scenario with
the highest total information summed across all three skills, per criterion.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .dataio import ItemBank
from .mirt import pass_probability
from .schemas import Rubric


@dataclass
class SelectionResult:
    scenario_id: str
    mode: str                              # "target_skill" | "fallback_total_info"
    target_skill_index: int | None
    value: float
    top_candidates: list[tuple[str, float]]  # (scenario_id, value) of the ranked pool


def criterion_information(theta: np.ndarray, rubric: Rubric, k: int) -> float:
    """V_kc = q_kc * P(1-P) * a_kc^2 for target skill index k."""
    if rubric.q[k] == 0:
        return 0.0
    p = pass_probability(theta, rubric.a, rubric.q, rubric.b)
    return float(p * (1.0 - p) * rubric.a[k] ** 2)


def scenario_value(theta: np.ndarray, rubrics: list[Rubric], k: int) -> float | None:
    """Average information about skill k per applicable criterion; None if inapplicable."""
    applicable = sum(int(r.q[k]) for r in rubrics)
    if applicable == 0:
        return None
    total = sum(criterion_information(theta, r, k) for r in rubrics)
    return total / applicable


def total_information_value(theta: np.ndarray, rubrics: list[Rubric]) -> float:
    """Fallback score: information summed across all skills, per criterion."""
    total = 0.0
    for r in rubrics:
        p = pass_probability(theta, r.a, r.q, r.b)
        total += p * (1.0 - p) * float(((r.q * r.a) ** 2).sum())
    return total / len(rubrics)


def select_next(
    theta: np.ndarray,
    bank: ItemBank,
    unused_scenario_ids: list[str],
    target_skill_index: int,
    rng: np.random.Generator,
    top_n: int = 5,
) -> SelectionResult | None:
    """Pick the next scenario per the PRD. Returns None if the bank is exhausted."""
    if not unused_scenario_ids:
        return None

    scored: list[tuple[str, float]] = []
    for sid in unused_scenario_ids:
        value = scenario_value(theta, bank.rubrics_for(sid), target_skill_index)
        if value is not None:
            scored.append((sid, value))

    if scored:
        # Deterministic order: value descending, scenario_id as tie-break.
        scored.sort(key=lambda t: (-t[1], t[0]))
        top = scored[:top_n]
        sid, value = top[int(rng.integers(len(top)))]
        return SelectionResult(sid, "target_skill", target_skill_index, value, top)

    # Fallback: no unused scenario touches the target skill.
    fallback = [
        (sid, total_information_value(theta, bank.rubrics_for(sid)))
        for sid in unused_scenario_ids
    ]
    fallback.sort(key=lambda t: (-t[1], t[0]))
    sid, value = fallback[0]
    return SelectionResult(sid, "fallback_total_info", None, value, fallback[:top_n])
