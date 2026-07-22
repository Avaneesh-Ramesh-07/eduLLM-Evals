"""Generate PLACEHOLDER item parameters (discrimination a, difficulty b) for every
criterion, pending real MIRT calibration.

Heuristic, deterministic, and auditable — estimates derive only from rubric
metadata (objectivity, explicitness, primary_skill, criticality, criterion text)
plus a small hash-based jitter to break ties. Both a and b are scaled into
[0, 1] per team decision (2026-07-22).

Reads  data/rubrics_qmatrix.jsonl
Writes data/rubrics_calibrated.jsonl   (original fields + discrimination,
                                        difficulty, calibration_version)

Usage:  python scripts/estimate_placeholder_params.py
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

SKILLS = ("content", "diagnosis", "scaffolding")
CALIBRATION_VERSION = "heuristic-v0-placeholder"

SRC = Path("data/rubrics_qmatrix.jsonl")
DST = Path("data/rubrics_calibrated.jsonl")

# Words suggesting a harder, multi-part, or reasoning-heavy criterion.
_HARD_WORDS = ("justify", "explain why", "derive", "prove", "misconception",
               "alternative", "all steps", "each step", "compare", "evaluate")
# Words suggesting a simple state-the-fact criterion.
_EASY_WORDS = ("state", "include", "mention", "provide the answer",
               "provide the formula", "identify the")


def _jitter(key: str, span: float = 0.05) -> float:
    """Deterministic uniform jitter in [-span, +span] from the criterion id."""
    h = zlib.crc32(key.encode()) / 0xFFFFFFFF   # [0, 1]
    return (h * 2.0 - 1.0) * span


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def estimate_discrimination(rub: dict) -> dict[str, float]:
    """Per-skill a in [0, 1]. Higher when the judge can grade the criterion
    reliably (objective, explicitly requested) — reliable grading is what lets
    a criterion separate strong from weak tutors."""
    base = 0.60
    if rub.get("objectivity") == "objective":
        base += 0.15
    if rub.get("explicitness") == "explicit":
        base += 0.10

    out = {}
    for skill in SKILLS:
        if not rub["q_mapping"].get(skill, 0):
            out[skill] = 0.0
            continue
        a = base + (0.10 if rub.get("primary_skill") == skill else -0.10)
        a += _jitter(f"{rub['criterion_id']}:a:{skill}")
        out[skill] = round(_clip(a, 0.05, 1.0), 3)
    return out


def estimate_difficulty(rub: dict) -> float:
    """Scalar b in [0, 1]. Higher = harder for a tutor to satisfy."""
    b = 0.35
    if rub.get("explicitness") == "explicit":
        b -= 0.15        # directly requested -> tutors usually address it
    elif rub.get("explicitness") == "implicit":
        b += 0.15        # tutor must realize it matters on its own
    if rub.get("objectivity") == "subjective":
        b += 0.10        # fuzzier bar to clear

    primary = rub.get("primary_skill", "")
    if primary == "diagnosis":
        b += 0.15        # spotting the student's misconception is the hard part
    elif primary == "scaffolding":
        b += 0.10        # structuring guidance is harder than stating facts

    if rub.get("criticality", "").startswith("critical"):
        b -= 0.05        # fundamental expectations, more routinely met

    text = rub.get("criterion", "").lower()
    if any(w in text for w in _HARD_WORDS):
        b += 0.10
    if any(w in text for w in _EASY_WORDS):
        b -= 0.05
    if len(text) > 200:
        b += 0.05        # long, multi-clause criteria are harder to fully satisfy

    b += _jitter(f"{rub['criterion_id']}:b")
    return round(_clip(b, 0.05, 0.95), 3)


def main() -> None:
    n = 0
    with SRC.open(encoding="utf-8") as fin, DST.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            rub = json.loads(line)
            rub["discrimination"] = estimate_discrimination(rub)
            rub["difficulty"] = estimate_difficulty(rub)
            rub["calibration_version"] = CALIBRATION_VERSION
            fout.write(json.dumps(rub, ensure_ascii=False) + "\n")
            n += 1
    print(f"wrote {DST} ({n} criteria, calibration_version={CALIBRATION_VERSION})")


if __name__ == "__main__":
    main()
