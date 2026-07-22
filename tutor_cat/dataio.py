"""Load and validate the preprocessed dataset (data/scenarios.jsonl, data/rubrics.jsonl)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import SKILLS
from .schemas import Rubric, Scenario


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class ItemBank:
    scenarios: dict[str, Scenario]
    rubrics: dict[str, Rubric]

    def rubrics_for(self, scenario_id: str) -> list[Rubric]:
        """Criteria of a scenario in criterion_id order (fixed, recorded update order)."""
        scenario = self.scenarios[scenario_id]
        return sorted(
            (self.rubrics[cid] for cid in scenario.criterion_ids),
            key=lambda r: r.criterion_id,
        )


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {e}") from e
    return rows


def load_bank(scenarios_path: str | Path, rubrics_path: str | Path) -> tuple[ItemBank, ValidationReport]:
    report = ValidationReport()
    scenarios: dict[str, Scenario] = {}
    rubrics: dict[str, Rubric] = {}

    for obj in _load_jsonl(Path(scenarios_path)):
        try:
            s = Scenario.from_json(obj)
        except (KeyError, TypeError, ValueError) as e:
            report.errors.append(f"scenario {obj.get('scenario_id', '?')}: {e!r}")
            continue
        if s.scenario_id in scenarios:
            report.errors.append(f"duplicate scenario_id {s.scenario_id}")
        scenarios[s.scenario_id] = s

    for obj in _load_jsonl(Path(rubrics_path)):
        try:
            r = Rubric.from_json(obj)
        except (KeyError, TypeError, ValueError) as e:
            report.errors.append(f"rubric {obj.get('criterion_id', '?')}: {e!r}")
            continue
        if r.criterion_id in rubrics:
            report.errors.append(f"duplicate criterion_id {r.criterion_id}")
        rubrics[r.criterion_id] = r

    _validate(scenarios, rubrics, report)
    return ItemBank(scenarios, rubrics), report


def _validate(scenarios: dict[str, Scenario], rubrics: dict[str, Rubric], report: ValidationReport) -> None:
    for s in scenarios.values():
        if s.modality != "text":
            report.warnings.append(f"{s.scenario_id}: modality '{s.modality}' (pipeline is text-only for now)")
        if not s.criterion_ids:
            report.errors.append(f"{s.scenario_id}: no criterion_ids")
        for cid in s.criterion_ids:
            if cid not in rubrics:
                report.errors.append(f"{s.scenario_id}: criterion_id {cid} has no rubric")

    for r in rubrics.values():
        if r.scenario_id not in scenarios:
            report.errors.append(f"{r.criterion_id}: unknown scenario_id {r.scenario_id}")
        if not set(r.q.tolist()) <= {0, 1}:
            report.errors.append(f"{r.criterion_id}: q_mapping entries must be 0/1, got {r.q.tolist()}")
        if int(r.q.sum()) == 0:
            # Legal but skill-inert: contributes nothing to theta/SE/counts.
            # Judged only for the critical-failure report (run.unmapped_criteria).
            report.warnings.append(f"{r.criterion_id}: q_mapping maps to no skill (all zeros)")
        if (r.a < 0).any():
            report.errors.append(f"{r.criterion_id}: negative discrimination {r.a.tolist()}")
        if r.scoring_type != "binary":
            report.errors.append(f"{r.criterion_id}: scoring_type '{r.scoring_type}' unsupported (binary only)")
        if r.status != "approved":
            report.warnings.append(f"{r.criterion_id}: status '{r.status}' (not approved)")
        # a > 0 where q = 0 is harmless (the mask zeroes it) but suggests a calibration mismatch.
        for k, skill in enumerate(SKILLS):
            if r.q[k] == 0 and r.a[k] > 0:
                report.warnings.append(
                    f"{r.criterion_id}: discrimination {r.a[k]:.3f} on '{skill}' will be Q-masked to 0"
                )


def summarize(bank: ItemBank) -> str:
    per_skill = {s: 0 for s in SKILLS}
    critical = 0
    for r in bank.rubrics.values():
        for k, s in enumerate(SKILLS):
            per_skill[s] += int(r.q[k])
        critical += r.criticality.startswith("critical")
    lines = [
        f"scenarios: {len(bank.scenarios)}",
        f"criteria:  {len(bank.rubrics)}  (critical: {critical})",
        "criteria per skill: " + ", ".join(f"{s}={n}" for s, n in per_skill.items()),
    ]
    return "\n".join(lines)
