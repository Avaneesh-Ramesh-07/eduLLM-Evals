#!/usr/bin/env python3
"""
Re-verify only the *no-majority* rows and merge them back into the full verified output.

The full run (``verify_qmatrix.py --full``) resolves every 3-rater row by strict majority.
But on a handful of rows a verifier dropped out (a JSON parse error), leaving either an even
2-rater vote with no majority (``resolution == "tie"``) or no verifier at all
(``resolution == "generator_only"``). For those rows ``final_q_mapping`` defaulted to 0.

This script re-calls the verifiers on ONLY those rows so they can reach a true 3-rater
majority, then surgically patches the refreshed ``verification`` block back into the full
``rubrics_qmatrix_verified.jsonl`` / ``.json`` (a ``.bak`` copy is written first). Rows that
already had a majority are never re-called, and the original ``rubrics.jsonl`` is never
touched. The priority-sorted review queue is regenerated from the patched blocks so it shrinks
to match.

It reuses ``verify_qmatrix``'s Verifier / blind-input / aggregate / review-queue machinery and
reads the exact verifier slugs from the full-run manifest, so the retry is the same task as the
original (plus ``--strict-schema`` to cut the JSON-parse failures, and the client timeout fix).

Env (same as verify_qmatrix): ``ANTHROPIC_BASE_URL``, ``ANTHROPIC_AUTH_TOKEN``.

    python retry_ties.py            # patch the full verified output in place (backup written)
    python retry_ties.py --dry-run  # list the rows that would be retried; make no API calls
"""
import argparse
import collections
import json
import os
import shutil
import sys
from pathlib import Path

import generate_qmatrix as gq
import verify_qmatrix as vq
from verify_qmatrix import DATA_DIR, SKILLS, VERIFY_LOG_DIR

# Rows whose final_q_mapping is NOT a genuine >half majority: an even 2-rater split, or no
# verifier at all. These are exactly the rows a retry can promote to a real 3-rater majority.
RETRY_RESOLUTIONS = {"tie", "generator_only"}
RETRY_MODE = "retry"


def load_manifest_verifiers(mode: str = "full") -> tuple[list[str], list[str]]:
    """Read the verifier ``provider:model`` specs + short names from the full-run manifest."""
    manifest_path = VERIFY_LOG_DIR / mode / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"no manifest at {manifest_path}; run verify_qmatrix.py --{mode} first "
                 "or pass --verifier explicitly.")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    specs = [f'{v["provider"]}:{v["model"]}' for v in manifest["verifiers"]]
    names = [v["name"] for v in manifest["verifiers"]]
    return specs, names


def resolution_counts(records: list[dict]) -> dict:
    """Count final-label resolutions across records that carry a full verification block."""
    return dict(collections.Counter(
        r["verification"].get("resolution")
        for r in records
        if isinstance(r.get("verification"), dict) and "final_q_mapping" in r["verification"]
    ))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-verify no-majority rows and merge them into the full verified output.")
    parser.add_argument("--verified", default=str(DATA_DIR / "rubrics_qmatrix_verified.jsonl"),
                        help="Full verified .jsonl to patch (default: %(default)s). "
                             "Its .json sibling is rewritten too.")
    parser.add_argument("--verifier", action="append", metavar="PROVIDER:MODEL",
                        help="Override verifier(s). Default: read from the full-run manifest.")
    parser.add_argument("--base-url", default=None,
                        help="Gateway root (else ANTHROPIC_BASE_URL).")
    parser.add_argument("--concurrency", type=int, default=gq.DEFAULT_CONCURRENCY,
                        help="Max concurrent calls (default: %(default)s).")
    parser.add_argument("--dry-run", action="store_true",
                        help="List the rows that would be retried and exit (no API calls).")
    args = parser.parse_args()

    verified_jsonl = Path(args.verified)
    verified_json = verified_jsonl.with_suffix(".json")
    if not verified_jsonl.exists():
        sys.exit(f"verified output not found: {verified_jsonl}")

    records = gq.read_jsonl(verified_jsonl)
    targets = [r for r in records
               if isinstance(r.get("verification"), dict)
               and r["verification"].get("resolution") in RETRY_RESOLUTIONS]

    print(f"{len(records)} verified records; {len(targets)} no-majority rows to retry.")
    print(f"  before: {resolution_counts(records)}")
    if not targets:
        print("Nothing to retry -- every row already has a majority.")
        return

    if args.dry_run:
        shown = 0
        for r in targets:
            v = r["verification"]
            print(f"  {r['criterion_id']:18s} {v.get('resolution'):15s} "
                  f"final={v.get('final_q_mapping')}")
            shown += 1
            if shown >= 60:
                print(f"  ... +{len(targets) - shown} more")
                break
        print("(dry run -- no API calls made)")
        return

    # --- resolve verifiers + gateway (mirrors verify_qmatrix._resolve_gateway) ---
    specs = args.verifier or load_manifest_verifiers("full")[0]
    base_url_root = args.base_url or os.environ.get("ANTHROPIC_BASE_URL")
    if not base_url_root:
        sys.exit("set ANTHROPIC_BASE_URL (or --base-url) to the TrueFoundry gateway root")
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("set ANTHROPIC_AUTH_TOKEN to your TrueFoundry user key")
    os.environ.pop("ANTHROPIC_API_KEY", None)  # force Bearer for the Anthropic client

    # strict_schema=True: the drop-outs we're retrying were JSON-parse failures, and a strict
    # response schema is the direct fix.
    verifiers = vq.build_verifiers(specs, base_url_root, key, strict_schema=True)
    verifier_names = [v.name for v in verifiers]

    # Blind inputs for the targets, rebuilt from the ORIGINAL rubrics.jsonl (tested path).
    labeled, user_contents, skipped, scenarios = vq.build_blind_inputs(targets)
    if skipped:
        print(f"note: {len(skipped)} target(s) had no generator label; left unchanged.")
    if not labeled:
        print("No retriable targets after blind-input build; nothing to do.")
        return

    per_criterion = vq.run_verification(verifiers, RETRY_MODE, args.concurrency,
                                        labeled, user_contents)
    new_blocks = [vq.aggregate(rec, per_criterion[i]) for i, rec in enumerate(labeled)]

    # Persist per-criterion retry logs for auditability.
    for i, rec in enumerate(labeled):
        vq.write_item_log(RETRY_MODE, rec["criterion_id"], user_contents[i], per_criterion[i])

    # --- patch refreshed blocks back into the full record set ---
    patched = {rec["criterion_id"]: blk for rec, blk in zip(labeled, new_blocks)}
    resolved, still = 0, 0
    for r in records:
        blk = patched.get(r.get("criterion_id"))
        if blk is None:
            continue
        r["verification"] = blk
        if blk["resolution"] in ("unanimous", "majority"):
            resolved += 1
        else:
            still += 1

    # --- write patched full output (backup first) ---
    shutil.copyfile(verified_jsonl, verified_jsonl.with_suffix(".jsonl.bak"))
    if verified_json.exists():
        shutil.copyfile(verified_json, verified_json.with_suffix(".json.bak"))
    gq.write_jsonl(verified_jsonl, records)
    gq.write_json(verified_json, records)

    # --- regenerate the review queue from ALL patched blocks so it shrinks to match ---
    labeled_all = [r for r in records
                   if isinstance(r.get("verification"), dict)
                   and "final_q_mapping" in r["verification"]]
    blocks_all = [r["verification"] for r in labeled_all]
    queue = vq.build_review_queue(labeled_all, blocks_all, scenarios)
    review_jsonl = DATA_DIR / "review_queue.jsonl"
    review_tsv = DATA_DIR / "review_queue.tsv"
    for p in (review_jsonl, review_tsv):
        if p.exists():
            shutil.copyfile(p, p.with_suffix(p.suffix + ".bak"))
    gq.write_jsonl(review_jsonl, queue)
    vq.write_review_tsv(review_tsv, queue, verifier_names)

    # --- retry manifest ---
    pvc = {n: {"ok": 0, "failed": 0} for n in verifier_names}
    for blk in new_blocks:
        for v in blk["verifiers"]:
            pvc.setdefault(v["name"], {"ok": 0, "failed": 0})
            pvc[v["name"]]["ok" if v["q_mapping"] is not None else "failed"] += 1
    retry_manifest = {
        "timestamp_utc": gq.now_iso(),
        "verifiers": specs,
        "targets": len(targets),
        "retried": len(labeled),
        "promoted_to_majority": resolved,
        "still_no_majority": still,
        "resolution_counts_after": resolution_counts(records),
        "per_verifier_counts": pvc,
    }
    (VERIFY_LOG_DIR / RETRY_MODE).mkdir(parents=True, exist_ok=True)
    with open(VERIFY_LOG_DIR / RETRY_MODE / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(retry_manifest, f, ensure_ascii=False, indent=2)

    # --- summary ---
    print(f"\nRetried {len(labeled)} rows: {resolved} now have a real majority, "
          f"{still} still no majority (kept default-0, still needs_review).")
    print("Per-verifier calls on retry (ok/failed):")
    for n in verifier_names:
        print(f"  {n:22s} {pvc[n]['ok']:4d} ok / {pvc[n]['failed']:3d} failed")
    print(f"  after:  {resolution_counts(records)}")
    print(f"\nPatched -> {verified_jsonl} (+ .json); backups at *.bak")
    print(f"Review queue regenerated -> {review_jsonl} ({len(queue)} rows) (+ {review_tsv.name})")
    print(f"Retry logs + manifest -> {VERIFY_LOG_DIR / RETRY_MODE}")


if __name__ == "__main__":
    main()
