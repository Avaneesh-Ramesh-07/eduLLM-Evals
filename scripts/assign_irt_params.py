"""
Synthetic IRT item parameters (difficulty + discrimination) for the reformatted
TutorBench rubrics.

The CAT / MIRT evaluation loop scores a tutor per criterion with the compensatory
2PL-style model

    p_i = sigmoid( (q_i ⊙ a_i) · θ − b_i )

which needs, for every criterion, a scalar **difficulty** ``b_i`` and a per-skill
**discrimination** vector ``a_i = (a_content, a_diagnosis, a_scaffolding)``. Normally these
are *calibrated* from real judge responses. This is a preliminary experiment, so instead of
calibrating we **synthesize** plausible values, grounded in the metadata each criterion
already carries (so the values are reproducible and defensible rather than arbitrary noise):

- ``difficulty``  <- ``explicitness`` (+ a small ``critical_negative`` nudge) + jitter.
- ``discrimination`` <- ``objectivity`` × ``criticality`` × (primary vs. secondary skill),
  masked by ``q_mapping`` and with lognormal jitter.

A constant assignment (a=1, b=0) is deliberately avoided: it would make Fisher information
identical for every scenario, collapsing CAT's adaptive selection into the no-CAT baseline.

Reproducibility
---------------
Each criterion's jitter is drawn from an RNG seeded by ``[--seed, hash(criterion_id)]``, so
the output is deterministic, order-independent, and stable if the dataset grows. The transform
depends only on the (frozen) label fields -- never on previously written ``difficulty`` /
``discrimination`` -- so it is idempotent and safe to re-run.

These parameters are synthetic placeholders (``irt_params.source == "synthetic"``). The
downstream CAT pipeline reads ``difficulty`` / ``discrimination`` from these records and can
later be pointed at a file with the same schema holding *calibrated* values.

Usage
-----
.. code-block:: bash

    # Dry-run to a scratch file first (does not touch the _final files):
    python assign_irt_params.py \\
        --input data/rubrics_qmatrix.sample.jsonl \\
        --output /tmp/irt_check.jsonl

    # Augment the finalized set in place (writes both .jsonl and .json, one-time .bak):
    python assign_irt_params.py

Outputs
-------
- The augmented rubric records, written back **in place** to
  ``data/rubrics_qmatrix_final.{jsonl,json}`` (or the ``--output`` target and
  its ``.json`` twin). Each record keeps all existing fields and gains ``difficulty``,
  ``discrimination``, and an ``irt_params`` provenance block.
- On the first in-place write, one-time ``*.bak`` copies of the overwritten files (suppress
  with ``--no-backup``).
- ``qmatrix_irt_logs/manifest.json``: the constants + seed used and summary statistics.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

# ---------------------------------------------------------------------------
# Paths and run constants
# ---------------------------------------------------------------------------

DATA_DIR = Path("data")
DEFAULT_INPUT = DATA_DIR / "rubrics_qmatrix_final.jsonl"
LOG_DIR = Path("qmatrix_irt_logs")

# The three latent tutoring skills (order matters: it fixes the RNG draw order).
SKILLS = ("content", "diagnosis", "scaffolding")

DEFAULT_SEED = 42
METHOD_VERSION = "metadata_heuristic_v1"

# ---------------------------------------------------------------------------
# Tunable synthesis constants
# ---------------------------------------------------------------------------

# Difficulty b_i: additive contributions, then clip. Higher b = harder = lower pass prob.
B_BASE = 0.0
# Explicit criteria (asked for directly in the prompt) are easier; implicit ones are harder.
B_EXPLICITNESS = {"explicit": -0.4, "implicit": 0.4}
# ``critical_negative`` = "the response must NOT do X"; usually easy to pass (error avoided).
B_CRITICALITY = {"critical": 0.0, "not_critical": 0.0, "critical_negative": -0.3}
B_JITTER_SD = 0.5
B_CLIP = (-2.5, 2.5)

# Discrimination a_i (per loaded skill): multiplicative factors, lognormal jitter, then clip.
A_BASE = 1.0
# Objective criteria give a cleaner pass/fail signal -> sharper discrimination.
A_OBJECTIVITY = {"objective": 1.2, "subjective": 0.8}
A_CRITICALITY = {"critical": 1.15, "not_critical": 0.90, "critical_negative": 1.25}
A_PRIMARY = 1.20    # multiplier for the primary_skill dimension (when that skill is loaded)
A_SECONDARY = 0.85  # multiplier for other loaded skills when a loaded primary exists
A_NEUTRAL = 1.0     # multiplier for all loaded skills when primary is null / not loaded
A_JITTER_LOG_SD = 0.15
A_CLIP = (0.3, 2.5)

ROUND = 4  # decimal places for the written parameter values


# ---------------------------------------------------------------------------
# I/O helpers (mirror test.py / generate_qmatrix.py)
# ---------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSON-lines file into a list of dicts (blank lines skipped)."""
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: Iterable[dict]) -> None:
    """Write one JSON object per line."""
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_json(path: Path, records: list[dict]) -> None:
    """Write a single pretty-printed (multi-line) JSON array."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
        f.write("\n")


def utcnow_iso() -> str:
    """Return a UTC ISO-8601 timestamp for the run manifest."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


def criterion_rng(criterion_id: str, seed: int) -> np.random.Generator:
    """
    Deterministic per-criterion RNG.

    Seeds from ``[seed, hash(criterion_id)]`` so each criterion's jitter depends only on its
    id and the global seed -- reproducible across runs and unaffected by dataset order/growth.
    """
    h = int(hashlib_sha256_int(criterion_id))
    return np.random.default_rng([seed, h])


def hashlib_sha256_int(text: str) -> int:
    """Stable 32-bit int from the SHA-256 of ``text`` (import-local to keep the top clean)."""
    import hashlib

    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def compute_difficulty(record: dict, rng: np.random.Generator) -> float:
    """Synthesize the scalar difficulty ``b_i`` for a criterion."""
    b = B_BASE
    b += B_EXPLICITNESS.get(record.get("explicitness"), 0.0)
    b += B_CRITICALITY.get(record.get("criticality"), 0.0)
    b += float(rng.normal(0.0, B_JITTER_SD))
    return round(float(np.clip(b, *B_CLIP)), ROUND)


def compute_discrimination(record: dict, rng: np.random.Generator) -> dict[str, float]:
    """
    Synthesize the per-skill discrimination vector ``a_i``.

    Skills not loaded in ``q_mapping`` get ``0.0`` (they are masked by ``q_i ⊙ a_i`` anyway).
    The primary-skill dimension is boosted relative to other loaded skills, but only when the
    primary skill is itself loaded; otherwise all loaded skills are treated neutrally.
    """
    q = record.get("q_mapping") or {}
    primary = record.get("primary_skill")
    obj_f = A_OBJECTIVITY.get(record.get("objectivity"), 1.0)
    crit_f = A_CRITICALITY.get(record.get("criticality"), 1.0)

    loaded = [s for s in SKILLS if q.get(s) == 1]
    primary_loaded = primary in loaded

    out: dict[str, float] = {}
    for skill in SKILLS:  # fixed order -> deterministic draws
        if q.get(skill) != 1:
            out[skill] = 0.0
            continue
        if primary_loaded:
            prim_f = A_PRIMARY if skill == primary else A_SECONDARY
        else:
            prim_f = A_NEUTRAL
        factor = A_BASE * obj_f * crit_f * prim_f
        a = factor * math.exp(float(rng.normal(0.0, A_JITTER_LOG_SD)))
        out[skill] = round(float(np.clip(a, *A_CLIP)), ROUND)
    return out


def assign_params(record: dict, seed: int) -> dict:
    """Add ``difficulty`` / ``discrimination`` / ``irt_params`` to a record (in place)."""
    rng = criterion_rng(record["criterion_id"], seed)
    # Draw difficulty first, then discrimination, in a fixed order for determinism.
    record["difficulty"] = compute_difficulty(record, rng)
    record["discrimination"] = compute_discrimination(record, rng)
    record["irt_params"] = {
        "source": "synthetic",
        "method": METHOD_VERSION,
        "seed": seed,
        "version": "1.0",
    }
    return record


# ---------------------------------------------------------------------------
# Summary / manifest
# ---------------------------------------------------------------------------


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def summarize(records: list[dict], seed: int) -> dict:
    """Compute summary statistics and assemble the run manifest."""
    bs = np.array([r["difficulty"] for r in records], dtype=float)
    # Pass probability at the initial ability theta = 0: p = sigmoid(-b).
    p0 = np.array([sigmoid(-b) for b in bs], dtype=float)

    a_stats = {}
    for skill in SKILLS:
        vals = np.array(
            [r["discrimination"][skill] for r in records if r["discrimination"][skill] > 0.0],
            dtype=float,
        )
        a_stats[skill] = {
            "n_loaded": int(vals.size),
            "mean": round(float(vals.mean()), ROUND) if vals.size else None,
            "median": round(float(np.median(vals)), ROUND) if vals.size else None,
            "min": round(float(vals.min()), ROUND) if vals.size else None,
            "max": round(float(vals.max()), ROUND) if vals.size else None,
        }

    empty_q = sum(
        1
        for r in records
        if not any((r.get("q_mapping") or {}).get(s) == 1 for s in SKILLS)
    )

    def pct(arr: np.ndarray, q: float) -> float:
        return round(float(np.percentile(arr, q)), ROUND)

    return {
        "generated_at": utcnow_iso(),
        "method": METHOD_VERSION,
        "seed": seed,
        "n_records": len(records),
        "n_empty_q_mapping": empty_q,
        "constants": {
            "B_BASE": B_BASE,
            "B_EXPLICITNESS": B_EXPLICITNESS,
            "B_CRITICALITY": B_CRITICALITY,
            "B_JITTER_SD": B_JITTER_SD,
            "B_CLIP": list(B_CLIP),
            "A_BASE": A_BASE,
            "A_OBJECTIVITY": A_OBJECTIVITY,
            "A_CRITICALITY": A_CRITICALITY,
            "A_PRIMARY": A_PRIMARY,
            "A_SECONDARY": A_SECONDARY,
            "A_NEUTRAL": A_NEUTRAL,
            "A_JITTER_LOG_SD": A_JITTER_LOG_SD,
            "A_CLIP": list(A_CLIP),
        },
        "difficulty": {
            "mean": round(float(bs.mean()), ROUND),
            "std": round(float(bs.std()), ROUND),
            "min": round(float(bs.min()), ROUND),
            "p25": pct(bs, 25),
            "p50": pct(bs, 50),
            "p75": pct(bs, 75),
            "max": round(float(bs.max()), ROUND),
        },
        "discrimination": a_stats,
        "pass_prob_at_theta0": {
            "mean": round(float(p0.mean()), ROUND),
            "min": round(float(p0.min()), ROUND),
            "p25": pct(p0, 25),
            "p50": pct(p0, 50),
            "p75": pct(p0, 75),
            "max": round(float(p0.max()), ROUND),
        },
    }


def print_summary(manifest: dict) -> None:
    """Print a compact human-readable view of the manifest."""
    print(f"records:            {manifest['n_records']}")
    print(f"empty q_mapping:    {manifest['n_empty_q_mapping']} (all-zero -> unscorable, a=0)")
    d = manifest["difficulty"]
    print(
        "difficulty b:       "
        f"mean={d['mean']} std={d['std']} "
        f"[min={d['min']} p25={d['p25']} p50={d['p50']} p75={d['p75']} max={d['max']}]"
    )
    for skill in SKILLS:
        s = manifest["discrimination"][skill]
        if s["n_loaded"]:
            print(
                f"discrim a[{skill:<11}] n={s['n_loaded']:<5} "
                f"mean={s['mean']} median={s['median']} min={s['min']} max={s['max']}"
            )
    p = manifest["pass_prob_at_theta0"]
    print(
        "pass prob @ θ=0:    "
        f"mean={p['mean']} [min={p['min']} p25={p['p25']} p50={p['p50']} "
        f"p75={p['p75']} max={p['max']}]  (should center ~0.5, not saturated)"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def maybe_backup(path: Path, no_backup: bool) -> None:
    """Copy ``path`` to ``path.bak`` once, if it exists and no backup is present yet."""
    if no_backup:
        return
    bak = path.with_suffix(path.suffix + ".bak")
    if path.exists() and not bak.exists():
        shutil.copy2(path, bak)
        print(f"backup:  {path} -> {bak}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help=f"Input rubric JSONL (default: {DEFAULT_INPUT}).")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output JSONL. Default: in place (== --input). A .json twin is written alongside.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help=f"Global RNG seed (default: {DEFAULT_SEED}).")
    parser.add_argument("--no-backup", action="store_true",
                        help="Do not write one-time .bak copies before an in-place overwrite.")
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR,
                        help=f"Directory for the run manifest (default: {LOG_DIR}).")
    args = parser.parse_args()

    out_jsonl = args.output if args.output is not None else args.input
    out_json = out_jsonl.with_suffix(".json")
    in_place = out_jsonl.resolve() == args.input.resolve()

    records = read_jsonl(args.input)
    print(f"read:    {len(records)} records from {args.input}")

    for record in records:
        assign_params(record, args.seed)

    if in_place:
        maybe_backup(out_jsonl, args.no_backup)
        maybe_backup(out_json, args.no_backup)

    write_jsonl(out_jsonl, records)
    write_json(out_json, records)
    print(f"wrote:   {out_jsonl}")
    print(f"wrote:   {out_json}")

    manifest = summarize(records, args.seed)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.log_dir / "manifest.json"
    write_json_obj(manifest_path, manifest)
    print(f"wrote:   {manifest_path}\n")
    print_summary(manifest)


def write_json_obj(path: Path, obj: dict) -> None:
    """Write a single JSON object (pretty-printed)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
