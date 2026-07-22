"""Shared fixtures: a synthetic item bank + simulated tutor/judge for offline tests.

The simulated judge samples verdicts from the M2PL model itself using a KNOWN
true theta*, so tests can check that the pipeline recovers theta* and shrinks SE
— without any network calls.
"""

from __future__ import annotations

import numpy as np
import pytest

from tutor_cat import SKILLS
from tutor_cat.dataio import ItemBank
from tutor_cat.mirt import pass_probability
from tutor_cat.schemas import JudgeVerdict, Rubric, Scenario


def make_bank(
    n_scenarios: int = 40,
    criteria_per_scenario: tuple[int, int] = (3, 6),
    seed: int = 7,
) -> ItemBank:
    """Synthetic bank: mostly single-skill criteria, plausible a/b ranges."""
    rng = np.random.default_rng(seed)
    scenarios: dict[str, Scenario] = {}
    rubrics: dict[str, Rubric] = {}

    for i in range(n_scenarios):
        sid = f"syn_{i:03d}"
        n_crit = int(rng.integers(criteria_per_scenario[0], criteria_per_scenario[1] + 1))
        cids = []
        for j in range(n_crit):
            cid = f"{sid}_c{j:02d}"
            q = np.zeros(len(SKILLS), dtype=int)
            if rng.random() < 0.85:  # single-skill criterion
                q[rng.integers(len(SKILLS))] = 1
            else:  # cross-loading criterion
                k1, k2 = rng.choice(len(SKILLS), size=2, replace=False)
                q[k1] = q[k2] = 1
            a = np.round(q * rng.uniform(0.6, 2.0, size=len(SKILLS)), 3)
            rubrics[cid] = Rubric(
                criterion_id=cid,
                scenario_id=sid,
                criterion=f"Synthetic criterion {cid}",
                q=q,
                a=a,
                b=float(np.round(rng.normal(0.0, 1.0), 3)),
                criticality="critical" if rng.random() < 0.1 else "standard",
            )
            cids.append(cid)
        scenarios[sid] = Scenario(
            scenario_id=sid,
            prompt=f"Synthetic tutoring prompt {sid}",
            criterion_ids=cids,
        )
    return ItemBank(scenarios, rubrics)


class SimTutor:
    """Stands in for a real tutor API; the response text is irrelevant because
    the SimJudge derives verdicts from theta*, not from the text."""

    def __init__(self, name: str = "sim-tutor"):
        self.name = name
        self.model = name

    def respond(self, scenario: Scenario) -> str:
        return f"simulated response to {scenario.scenario_id}"


class SimJudge:
    """Draws y ~ Bernoulli(p(theta*)) from the same M2PL model (seeded)."""

    def __init__(self, true_theta: np.ndarray, seed: int = 123):
        self.true_theta = np.asarray(true_theta, dtype=float)
        self.name = "sim-judge"
        self.prompt_version = "sim-v1"
        self.seed = seed
        self._rng = np.random.default_rng(seed)

    def evaluate(self, scenario: Scenario, rubric: Rubric, response: str) -> JudgeVerdict:
        p = pass_probability(self.true_theta, rubric.a, rubric.q, rubric.b)
        y = int(self._rng.random() < p)
        return JudgeVerdict(
            verdict="pass" if y else "fail",
            rationale=f"simulated (p={p:.3f})",
        )


@pytest.fixture
def bank() -> ItemBank:
    return make_bank()
