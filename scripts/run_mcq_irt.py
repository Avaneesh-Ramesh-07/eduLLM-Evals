"""Run the MCQ IRT calibration + CAT over the existing mcq/ CSVs.

Example:
    python scripts/run_mcq_irt.py --mcq-dir /Users/isabellachen/Documents/mcq \
        --out runs/mcq_irt
"""

from __future__ import annotations

import argparse

from tutor_cat.mcq_irt.pipeline import DEFAULT_BENCHMARKS, _print_summary, run


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mcq-dir", required=True, help="folder holding <benchmark>/<model>.csv grids")
    p.add_argument("--benchmarks", nargs="+", default=DEFAULT_BENCHMARKS)
    p.add_argument("--out", default="runs/mcq_irt")
    p.add_argument("--frac", type=float, default=0.1, help="fraction of models held out as diagnostic")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--se-stop", type=float, default=0.3)
    p.add_argument("--max-items", type=int, default=50)
    p.add_argument("--method", default="girth", choices=["girth", "rasch", "pyirt"])
    p.add_argument("--min-point-biserial", type=float, default=0.05)
    args = p.parse_args()

    summary = run(
        args.mcq_dir, args.benchmarks, args.out,
        frac=args.frac, seed=args.seed, se_stop=args.se_stop,
        max_items=args.max_items, method=args.method,
        min_point_biserial=args.min_point_biserial,
    )
    _print_summary(summary)
    print(f"\nwrote {args.out}/summary.json + per-benchmark items CSVs and plots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
