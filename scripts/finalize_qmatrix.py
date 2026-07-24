#!/usr/bin/env python3
"""
Produce a lean, ready-to-parse copy of the verified Q-matrix dataset with the ``verification``
block stripped out.

The verification stage writes ``rubrics_qmatrix_verified.jsonl`` where every criterion keeps its
original (generator) top-level fields *plus* a fat ``verification`` block, and the resolved
majority label lives at ``verification.final_q_mapping``. That block is provenance -- useful for
audit, but not needed to actually *use* the dataset.

This script emits ``rubrics_qmatrix_final.jsonl`` / ``.json``: the same records with the
``verification`` block removed and, crucially, the authoritative resolved label promoted up:

  * ``q_mapping``      <- ``verification.final_q_mapping``  (the 3-rater majority vote)
  * ``primary_skill``  <- reconciled so it always points at a skill still marked 1 (or null)

Promoting matters: on the current data 713 rows have a resolved ``final_q_mapping`` that differs
from the generator's original top-level ``q_mapping``. Dropping the block without promoting would
silently ship the *un-verified* labels for those rows.

The original ``rubrics.jsonl`` and the verified file are never modified -- this only reads the
verified file and writes new ``*_final.*`` files.

    python finalize_qmatrix.py              # write the lean dataset (refuses if ties remain)
    python finalize_qmatrix.py --allow-ties # proceed anyway; unresolved rows keep default-0
"""
import argparse
import collections
import sys
from pathlib import Path

import generate_qmatrix as gq
from verify_qmatrix import DATA_DIR, SKILLS

# Rows whose final_q_mapping is NOT a genuine >half majority (even split -> default-0, or no
# verifier at all). "No ties" means none of these remain.
UNRESOLVED_RESOLUTIONS = {"tie", "generator_only"}


def reconcile_primary(rec: dict, final_map: dict) -> str | None:
    """
    Pick a ``primary_skill`` consistent with the resolved ``final_map``.

    The schema requires ``primary_skill`` to be one of the marked (==1) skills, or null if none
    are marked. Verification can flip a skill to 0, which may orphan the generator's original
    primary. Resolution order: null if nothing marked; keep the generator's primary if it's still
    marked; else the raters' most-common primary that is still marked; else the first marked skill.
    """
    marked = [s for s in SKILLS if final_map[s] == 1]
    if not marked:
        return None
    gen_primary = rec.get("primary_skill")
    if gen_primary in marked:
        return gen_primary
    prim_votes = rec.get("verification", {}).get("primary_skills", {})
    counts = collections.Counter(p for p in prim_votes.values() if p in marked)
    if counts:
        return counts.most_common(1)[0][0]
    return marked[0]


def make_clean(rec: dict) -> dict:
    """Return a copy of ``rec`` with ``verification`` dropped and the resolved label promoted."""
    verif = rec.get("verification") or {}
    final_map = verif.get("final_q_mapping")
    # Fall back to the record's own q_mapping if a row somehow has no verification block.
    if not isinstance(final_map, dict):
        final_map = {s: int(rec["q_mapping"][s]) for s in SKILLS}
    final_map = {s: int(final_map[s]) for s in SKILLS}

    clean = {k: v for k, v in rec.items() if k != "verification"}  # preserves field order
    clean["q_mapping"] = final_map                                 # reassign in place
    clean["primary_skill"] = reconcile_primary(rec, final_map)
    return clean


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strip the verification block and promote the resolved Q-matrix label.")
    parser.add_argument("--verified", default=str(DATA_DIR / "rubrics_qmatrix_verified.jsonl"),
                        help="Verified .jsonl to read (default: %(default)s).")
    parser.add_argument("--out", default=str(DATA_DIR / "rubrics_qmatrix_final.jsonl"),
                        help="Output .jsonl (its .json sibling is written too). "
                             "Default: %(default)s")
    parser.add_argument("--allow-ties", action="store_true",
                        help="Proceed even if unresolved (tie / generator_only) rows remain; "
                             "those rows keep their conservative default-0 label.")
    args = parser.parse_args()

    verified = Path(args.verified)
    if not verified.exists():
        sys.exit(f"verified file not found: {verified}")

    records = gq.read_jsonl(verified)
    labeled = [r for r in records
               if isinstance(r.get("verification"), dict)
               and "final_q_mapping" in r["verification"]]
    if len(labeled) != len(records):
        print(f"note: {len(records) - len(labeled)} record(s) have no verification block "
              "(generator q_mapping used as-is).")

    unresolved = [r for r in labeled
                  if r["verification"].get("resolution") in UNRESOLVED_RESOLUTIONS]
    if unresolved:
        print(f"WARNING: {len(unresolved)} row(s) are still unresolved (no majority):")
        counts = collections.Counter(r["verification"]["resolution"] for r in unresolved)
        print(f"  {dict(counts)}")
        for r in unresolved[:10]:
            print(f"    {r['criterion_id']:18s} {r['verification']['resolution']}")
        if len(unresolved) > 10:
            print(f"    ... +{len(unresolved) - 10} more")
        if not args.allow_ties:
            sys.exit("Refusing to finalize while ties remain. Re-run retry_ties.py to resolve "
                     "them, or pass --allow-ties to ship them with the default-0 label.")
        print("  (--allow-ties set: shipping these with their default-0 label.)")

    clean = [make_clean(r) for r in records]
    promoted = sum(
        1 for r in records
        if isinstance(r.get("verification"), dict)
        and "final_q_mapping" in r["verification"]
        and {s: int(r["q_mapping"][s]) for s in SKILLS} != r["verification"]["final_q_mapping"]
    )

    out_jsonl = Path(args.out)
    out_json = out_jsonl.with_suffix(".json")
    gq.write_jsonl(out_jsonl, clean)
    gq.write_json(out_json, clean)

    print(f"\nWrote {len(clean)} lean records (no verification block):")
    print(f"  {out_jsonl}")
    print(f"  {out_json}")
    print(f"Promoted {promoted} row(s) whose resolved label differed from the generator's.")


if __name__ == "__main__":
    main()
