"""End-to-end offline test: full CAT run against a simulated tutor whose true
theta* is known. Verifies SE shrinkage, theta recovery, stopping behavior,
run artifacts, and CAT-vs-baseline comparison — no network calls."""

import json

import numpy as np
import pytest

from tutor_cat import SKILLS
from tutor_cat.engine import RunConfig, derive_seed, run_evaluation

from conftest import SimJudge, SimTutor, make_bank

TRUE_THETA = np.array([0.8, -0.5, 0.2])


def _cfg(tmp_path, **overrides) -> RunConfig:
    defaults = dict(
        seed=42,
        top_n=5,
        max_se={s: 0.35 for s in SKILLS},
        min_evals_per_skill=15,
        max_scenarios=40,
        output_dir=str(tmp_path / "runs"),
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


def test_cat_run_end_to_end(tmp_path, bank):
    final = run_evaluation(
        bank, SimTutor(), SimJudge(TRUE_THETA), _cfg(tmp_path), mode="cat", run_id="t_cat"
    )

    assert final["stop_reason"] in ("precision_reached", "max_scenarios_reached", "bank_exhausted")
    # every skill saw at least the minimum scorable evaluations (unless capped early)
    if final["precision_reached"]:
        assert all(v >= 15 for v in final["scorable_evaluations"].values())
        assert all(v < 0.35 for v in final["se"].values())
        assert final["note"] is None
    else:
        assert final["note"] is not None

    run_dir = tmp_path / "runs" / "t_cat"
    steps = [json.loads(l) for l in (run_dir / "steps.jsonl").read_text().splitlines()]
    assert steps, "no steps logged"

    # SE never increases step-over-step, and ends below where it started
    for k in range(len(SKILLS)):
        ses = [s["se"][k] for s in steps]
        # logged SEs are rounded to 6 dp, so allow that much slack
        assert all(b <= a + 1e-6 for a, b in zip(ses, ses[1:]))
        assert ses[-1] < 1.0

    # theta estimate lands near the simulated tutor's true theta
    theta = np.array([final["theta"][s] for s in SKILLS])
    assert np.abs(theta - TRUE_THETA).max() < 0.6

    # artifacts exist and judge log matches the PRD judge-result schema
    for name in ("manifest.json", "judge_results.jsonl", "criterion_updates.jsonl",
                 "critical_failures.json", "final_result.json"):
        assert (run_dir / name).exists()
    first = json.loads((run_dir / "judge_results.jsonl").read_text().splitlines()[0])
    for field in ("run_id", "candidate_model", "scenario_id", "criterion_id",
                  "candidate_response", "judge_model", "judge_prompt_version",
                  "verdict", "score", "evidence", "rationale", "unscorable_reason", "seed"):
        assert field in first


def test_runs_are_reproducible(tmp_path, bank):
    f1 = run_evaluation(bank, SimTutor(), SimJudge(TRUE_THETA, seed=9),
                        _cfg(tmp_path, output_dir=str(tmp_path / "r1")), mode="cat", run_id="a")
    f2 = run_evaluation(bank, SimTutor(), SimJudge(TRUE_THETA, seed=9),
                        _cfg(tmp_path, output_dir=str(tmp_path / "r2")), mode="cat", run_id="b")
    assert f1["theta"] == f2["theta"]
    assert f1["scenarios_administered"] == f2["scenarios_administered"]


def test_baseline_mode_runs_and_differs_from_cat(tmp_path, bank):
    cat = run_evaluation(bank, SimTutor(), SimJudge(TRUE_THETA), _cfg(tmp_path),
                         mode="cat", run_id="c")
    base = run_evaluation(bank, SimTutor(), SimJudge(TRUE_THETA), _cfg(tmp_path),
                          mode="baseline", run_id="d")
    assert base["stop_reason"] in ("precision_reached", "max_scenarios_reached", "bank_exhausted")

    cat_steps = [json.loads(l) for l in
                 (tmp_path / "runs" / "c" / "steps.jsonl").read_text().splitlines()]
    base_steps = [json.loads(l) for l in
                  (tmp_path / "runs" / "d" / "steps.jsonl").read_text().splitlines()]
    assert [s["scenario_id"] for s in cat_steps] != [s["scenario_id"] for s in base_steps]
    assert base_steps[0]["selection"]["mode"] == "baseline_random_order"
    assert cat_steps[0]["selection"]["mode"] in ("target_skill", "fallback_total_info")


def test_max_scenarios_cap_reports_missing_precision(tmp_path, bank):
    final = run_evaluation(
        bank, SimTutor(), SimJudge(TRUE_THETA),
        _cfg(tmp_path, max_scenarios=2, max_se={s: 0.05 for s in SKILLS}),
        mode="cat", run_id="capped",
    )
    assert final["stop_reason"] == "max_scenarios_reached"
    assert final["precision_reached"] is False
    assert "without reaching" in final["note"]


def test_critical_failures_are_reported_separately(tmp_path, bank):
    weak = np.array([-3.0, -3.0, -3.0])  # fails a lot -> critical failures occur
    run_evaluation(bank, SimTutor(), SimJudge(weak), _cfg(tmp_path),
                   mode="cat", run_id="weak")
    report = json.loads((tmp_path / "runs" / "weak" / "critical_failures.json").read_text())
    assert isinstance(report, list) and len(report) > 0
    assert {"scenario_id", "criterion_id", "criterion", "rationale"} <= set(report[0])


def test_derive_seed_is_stable_and_distinct():
    assert derive_seed(42, "gpt-5.5", "cat") == derive_seed(42, "gpt-5.5", "cat")
    assert derive_seed(42, "gpt-5.5", "cat") != derive_seed(42, "gpt-5.5", "baseline")
    assert derive_seed(42, "gpt-5.5", "cat") != derive_seed(42, "opus-4.8", "cat")


def test_judge_parsing_unscorable_counts_as_fail():
    from tutor_cat.judge import parse_verdict

    v = parse_verdict("I am not sure what to do with this.")
    assert v.verdict == "fail" and v.y == 0
    assert v.unscorable_reason == "unparseable_judge_output"

    v2 = parse_verdict('{"verdict": "pass", "evidence": "e", "rationale": "r"}')
    assert v2.verdict == "pass" and v2.y == 1 and v2.unscorable_reason is None

    v3 = parse_verdict("Feedback: weak answer [RESULT] fail")
    assert v3.verdict == "fail"
