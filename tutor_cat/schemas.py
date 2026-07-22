"""Dataclasses mirroring the PRD schemas (Scenario, Rubric, Judge Result)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import SKILLS


@dataclass
class Scenario:
    scenario_id: str
    prompt: str
    criterion_ids: list[str]
    use_case: str = ""
    subject: str = ""
    grade_band: str = ""
    modality: str = "text"
    conversation_context: list[dict[str, str]] = field(default_factory=list)
    reference_solution: str = ""
    source: str = ""
    split: str = ""
    version: str = "1.0"

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "Scenario":
        return cls(
            scenario_id=obj["scenario_id"],
            prompt=obj["prompt"],
            criterion_ids=list(obj["criterion_ids"]),
            use_case=obj.get("use_case", ""),
            subject=obj.get("subject", ""),
            grade_band=obj.get("grade_band", ""),
            modality=obj.get("modality", "text"),
            conversation_context=obj.get("conversation_context") or [],
            reference_solution=obj.get("reference_solution", ""),
            source=obj.get("source", ""),
            split=obj.get("split", ""),
            version=obj.get("version", "1.0"),
        )


@dataclass
class Rubric:
    criterion_id: str
    scenario_id: str
    criterion: str
    q: np.ndarray          # (3,) ints in {0,1}, order = SKILLS; `adaptation` is ignored
    a: np.ndarray          # (3,) calibrated discrimination, order = SKILLS (frozen)
    b: float               # calibrated difficulty (frozen)
    primary_skill: str = ""
    scoring_type: str = "binary"
    criticality: str = "standard"
    objectivity: str = ""
    explicitness: str = ""
    q_rationale: str = ""
    calibration_version: str = ""
    source: str = ""
    status: str = "approved"
    version: str = "1.0"

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "Rubric":
        q_map = obj["q_mapping"]
        a_map = obj["discrimination"]
        return cls(
            criterion_id=obj["criterion_id"],
            scenario_id=obj["scenario_id"],
            criterion=obj["criterion"],
            q=np.array([int(q_map[s]) for s in SKILLS], dtype=int),
            a=np.array([float(a_map[s]) for s in SKILLS], dtype=float),
            b=float(obj["difficulty"]),
            primary_skill=obj.get("primary_skill", ""),
            scoring_type=obj.get("scoring_type", "binary"),
            criticality=obj.get("criticality", "standard"),
            objectivity=obj.get("objectivity", ""),
            explicitness=obj.get("explicitness", ""),
            q_rationale=obj.get("q_rationale", ""),
            calibration_version=obj.get("calibration_version", ""),
            source=obj.get("source", ""),
            status=obj.get("status", "approved"),
            version=obj.get("version", "1.0"),
        )


@dataclass
class JudgeVerdict:
    """Normalized output of one judge call for one criterion."""

    verdict: str                       # "pass" | "fail"
    evidence: str = ""
    rationale: str = ""
    unscorable_reason: str | None = None
    raw_output: str = ""

    @property
    def y(self) -> int:
        return 1 if self.verdict == "pass" else 0
