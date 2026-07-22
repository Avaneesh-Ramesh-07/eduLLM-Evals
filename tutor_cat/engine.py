"""CAT Integration loop (PRD): select scenario -> tutor responds -> judge grades
each criterion -> update theta/U per criterion -> check stopping rule -> repeat.

Also implements the non-adaptive baseline (seeded-random scenario order, same
updates and stopping rule) for the CAT-vs-baseline comparison.

Each tutor gets a fully independent run: its own theta, U, administered set,
scorable-evaluation counts, and RNG stream.

Run outputs (runs/<run_id>/):
    manifest.json            config echo, seeds, model ids, prompt version
    judge_results.jsonl      one PRD judge-result record per criterion
    criterion_updates.jsonl  per-criterion theta/U trace (y, p, theta_after, se_after)
    steps.jsonl              per-scenario trace (target skill, selection, theta, SE, counts)
    critical_failures.json   failed criticality=critical criteria (separate report)
    final_result.json        final theta, SE, counts, stop reason, precision_reached
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from . import SKILLS, __version__
from .dataio import ItemBank
from .mirt import initial_state, standard_errors, update
from .schemas import JudgeVerdict, Rubric, Scenario
from .selector import SelectionResult, select_next


class JudgeClient(Protocol):
    name: str
    prompt_version: str
    seed: int

    def evaluate(self, scenario: Scenario, rubric: Rubric, response: str) -> JudgeVerdict: ...


class TutorLike(Protocol):
    name: str
    model: str

    def respond(self, scenario: Scenario) -> str: ...


@dataclass
class RunConfig:
    seed: int = 42
    top_n: int = 5
    theta_init: list[float] | None = None
    u_init_diag: list[float] | None = None
    max_se: dict[str, float] = field(
        default_factory=lambda: {s: 0.30 for s in SKILLS}
    )
    min_evals_per_skill: int = 15
    max_scenarios: int = 50
    output_dir: str = "runs"
    # All-zero q_mapping criteria carry no skill information; "judge" grades them
    # anyway so critical failures are still caught, "skip" saves the judge calls.
    unmapped_criteria: str = "judge"  # "judge" | "skip"


def derive_seed(master_seed: int, tutor_name: str, mode: str) -> int:
    """Deterministic per-(tutor, mode) seed derived from the master seed."""
    return (master_seed * 1_000_003 + zlib.crc32(f"{tutor_name}:{mode}".encode())) % (2**32)


class _JsonlWriter:
    def __init__(self, path: Path):
        self._f = path.open("w", encoding="utf-8")

    def write(self, obj: dict[str, Any]) -> None:
        self._f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def run_evaluation(
    bank: ItemBank,
    tutor: TutorLike,
    judge: JudgeClient,
    cfg: RunConfig,
    mode: str = "cat",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run one full evaluation (mode = 'cat' or 'baseline'). Returns final_result."""
    if mode not in ("cat", "baseline"):
        raise ValueError("mode must be 'cat' or 'baseline'")

    run_seed = derive_seed(cfg.seed, tutor.name, mode)
    rng = np.random.default_rng(run_seed)
    run_id = run_id or f"run_{datetime.now():%Y%m%d_%H%M%S}_{tutor.name}_{mode}"
    out_dir = Path(cfg.output_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    theta, U = initial_state(cfg.theta_init, cfg.u_init_diag)
    max_se = np.array([cfg.max_se[s] for s in SKILLS])
    counts = np.zeros(len(SKILLS), dtype=int)  # scorable evaluations per skill
    administered: list[str] = []
    critical_failures: list[dict[str, Any]] = []

    manifest = {
        "run_id": run_id,
        "mode": mode,
        "package_version": __version__,
        "candidate_model": tutor.model,
        "tutor_name": tutor.name,
        "judge_model": judge.name,
        "judge_prompt_version": judge.prompt_version,
        "judge_seed": judge.seed,
        "master_seed": cfg.seed,
        "run_seed": run_seed,
        "top_n": cfg.top_n,
        "theta_init": list(cfg.theta_init or [0.0] * len(SKILLS)),
        "u_init_diag": list(cfg.u_init_diag or [1.0] * len(SKILLS)),
        "max_se": cfg.max_se,
        "min_evals_per_skill": cfg.min_evals_per_skill,
        "max_scenarios": cfg.max_scenarios,
        "n_scenarios_in_bank": len(bank.scenarios),
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    judge_log = _JsonlWriter(out_dir / "judge_results.jsonl")
    update_log = _JsonlWriter(out_dir / "criterion_updates.jsonl")
    step_log = _JsonlWriter(out_dir / "steps.jsonl")

    # Baseline: fixed seeded-random order over the whole bank, decided up front.
    baseline_order: list[str] = []
    if mode == "baseline":
        baseline_order = [str(s) for s in rng.permutation(sorted(bank.scenarios))]

    def precision_reached() -> bool:
        se = standard_errors(U)
        return bool((se < max_se).all() and (counts >= cfg.min_evals_per_skill).all())

    stop_reason = None
    try:
        while True:
            # --- stopping rule (checked between scenarios) ---
            if precision_reached():
                stop_reason = "precision_reached"
                break
            if len(administered) >= cfg.max_scenarios:
                stop_reason = "max_scenarios_reached"
                break

            # --- choose next scenario ---
            unused = [s for s in sorted(bank.scenarios) if s not in administered]
            if not unused:
                stop_reason = "bank_exhausted"
                break

            se = standard_errors(U)
            if mode == "cat":
                target_k = int(np.argmax(se))
                selection: SelectionResult | None = select_next(
                    theta, bank, unused, target_k, rng, cfg.top_n
                )
                if selection is None:
                    stop_reason = "bank_exhausted"
                    break
                sid = selection.scenario_id
                selection_info = {
                    "mode": selection.mode,
                    "target_skill": SKILLS[target_k],
                    "scenario_value": selection.value,
                    "top_candidates": selection.top_candidates,
                }
            else:
                sid = baseline_order[len(administered)]
                selection_info = {"mode": "baseline_random_order"}

            scenario = bank.scenarios[sid]

            # --- tutor answers, judge grades every criterion in criterion_id order ---
            response = tutor.respond(scenario)
            for rubric in bank.rubrics_for(sid):
                if cfg.unmapped_criteria == "skip" and int(rubric.q.sum()) == 0:
                    continue
                verdict = judge.evaluate(scenario, rubric, response)
                y = verdict.y

                theta, U, p = update(theta, U, rubric.a, rubric.q, rubric.b, y)
                counts += rubric.q  # scorable evaluation for every skill with q=1

                # dataset uses both "critical" and "critical_negative"
                if rubric.criticality.startswith("critical") and y == 0:
                    critical_failures.append(
                        {
                            "scenario_id": sid,
                            "criterion_id": rubric.criterion_id,
                            "criterion": rubric.criterion,
                            "rationale": verdict.rationale,
                            "unscorable_reason": verdict.unscorable_reason,
                        }
                    )

                judge_log.write(
                    {
                        "run_id": run_id,
                        "candidate_model": tutor.model,
                        "scenario_id": sid,
                        "criterion_id": rubric.criterion_id,
                        "candidate_response": response,
                        "judge_model": judge.name,
                        "judge_prompt_version": judge.prompt_version,
                        "verdict": verdict.verdict,
                        "score": y,
                        "evidence": verdict.evidence,
                        "rationale": verdict.rationale,
                        "unscorable_reason": verdict.unscorable_reason,
                        "seed": judge.seed,
                    }
                )
                update_log.write(
                    {
                        "scenario_id": sid,
                        "criterion_id": rubric.criterion_id,
                        "y": y,
                        "p_before": round(float(p), 6),
                        "theta_after": [round(float(x), 6) for x in theta],
                        "se_after": [round(float(x), 6) for x in standard_errors(U)],
                    }
                )

            administered.append(sid)
            step_log.write(
                {
                    "step": len(administered),
                    "scenario_id": sid,
                    "selection": selection_info,
                    "theta": [round(float(x), 6) for x in theta],
                    "se": [round(float(x), 6) for x in standard_errors(U)],
                    "counts": counts.tolist(),
                }
            )
    finally:
        judge_log.close()
        update_log.close()
        step_log.close()

    se = standard_errors(U)
    final = {
        "run_id": run_id,
        "mode": mode,
        "candidate_model": tutor.model,
        "stop_reason": stop_reason,
        "precision_reached": precision_reached(),
        # PRD: if stopped early, state that precision was not reached.
        "note": (
            None
            if precision_reached()
            else "Evaluation ended without reaching the required measurement precision."
        ),
        "scenarios_administered": len(administered),
        "theta": {s: round(float(theta[k]), 6) for k, s in enumerate(SKILLS)},
        "se": {s: round(float(se[k]), 6) for k, s in enumerate(SKILLS)},
        "scorable_evaluations": {s: int(counts[k]) for k, s in enumerate(SKILLS)},
        "critical_failure_count": len(critical_failures),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "critical_failures.json").write_text(
        json.dumps(critical_failures, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "final_result.json").write_text(
        json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return final
