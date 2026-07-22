"""CLI: tutor-cat validate | run | plot   (or: python -m tutor_cat ...)"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .dataio import load_bank, summarize
from .engine import RunConfig, run_evaluation
from .judge import OpenAICompatibleJudge
from .tutors import build_tutor


def _load_env() -> None:
    # Use the OS certificate store (corporate networks TLS-intercept with a
    # company root CA that Python's bundled CA list doesn't know about).
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def _load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _run_config(cfg: dict) -> RunConfig:
    run = cfg.get("run", {})
    return RunConfig(
        seed=run.get("seed", 42),
        top_n=run.get("top_n", 5),
        theta_init=run.get("theta_init"),
        u_init_diag=run.get("u_init_diag"),
        max_se=run.get("max_se", {"content": 0.3, "diagnosis": 0.3, "scaffolding": 0.3}),
        min_evals_per_skill=run.get("min_evals_per_skill", 15),
        max_scenarios=run.get("max_scenarios", 50),
        output_dir=run.get("output_dir", "runs"),
        unmapped_criteria=run.get("unmapped_criteria", "judge"),
    )


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    bank, report = load_bank(cfg["data"]["scenarios"], cfg["data"]["rubrics"])
    print(summarize(bank))
    _MAX_SHOWN = 15
    for w in report.warnings[:_MAX_SHOWN]:
        print(f"WARNING: {w}")
    if len(report.warnings) > _MAX_SHOWN:
        print(f"... and {len(report.warnings) - _MAX_SHOWN} more warnings")
    for e in report.errors[:_MAX_SHOWN]:
        print(f"ERROR:   {e}")
    if len(report.errors) > _MAX_SHOWN:
        print(f"... and {len(report.errors) - _MAX_SHOWN} more errors")
    print("validation:", "OK" if report.ok else f"FAILED ({len(report.errors)} errors)")
    return 0 if report.ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    _load_env()
    cfg = _load_config(args.config)
    bank, report = load_bank(cfg["data"]["scenarios"], cfg["data"]["rubrics"])
    if not report.ok:
        print(f"dataset failed validation ({len(report.errors)} errors); run 'validate'", file=sys.stderr)
        return 1

    jcfg = cfg["judge"]
    judge = OpenAICompatibleJudge(
        base_url=jcfg["base_url"],
        model=jcfg["model"],
        api_key_env=jcfg.get("api_key_env", "JUDGE_API_KEY"),
        temperature=jcfg.get("temperature", 0.0),
        max_tokens=jcfg.get("max_tokens", 512),
        seed=jcfg.get("seed", 42),
    )

    specs = cfg["tutors"]
    if args.tutor != "all":
        specs = [t for t in specs if t["name"] == args.tutor]
        if not specs:
            print(f"unknown tutor '{args.tutor}' (config has: "
                  f"{[t['name'] for t in cfg['tutors']]})", file=sys.stderr)
            return 1

    run_cfg = _run_config(cfg)
    cache_dir = cfg.get("cache_dir", "cache")
    modes = ["cat", "baseline"] if args.mode == "both" else [args.mode]

    for spec in specs:
        tutor = build_tutor(spec, cache_dir)
        for mode in modes:
            final = run_evaluation(bank, tutor, judge, run_cfg, mode=mode)
            print(json.dumps(final, indent=2))
    return 0


def cmd_plot(args: argparse.Namespace) -> int:
    from .plotting import plot_se_trajectories

    out = plot_se_trajectories(args.runs, args.out)
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tutor-cat", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="validate data/*.jsonl against the PRD schemas")
    p_val.add_argument("--config", default="config.yaml")
    p_val.set_defaults(fn=cmd_validate)

    p_run = sub.add_parser("run", help="run the CAT (or baseline) evaluation")
    p_run.add_argument("--config", default="config.yaml")
    p_run.add_argument("--tutor", default="all", help="tutor name from config, or 'all'")
    p_run.add_argument("--mode", choices=["cat", "baseline", "both"], default="cat")
    p_run.set_defaults(fn=cmd_run)

    p_plot = sub.add_parser("plot", help="plot SE trajectories for one or more runs")
    p_plot.add_argument("runs", nargs="+", help="paths to runs/<run_id> directories")
    p_plot.add_argument("--out", default="se_trajectories.png")
    p_plot.set_defaults(fn=cmd_plot)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
