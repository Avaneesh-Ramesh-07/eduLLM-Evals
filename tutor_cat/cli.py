"""CLI: tutor-cat validate | run | plot | generate   (or: python -m tutor_cat ...)"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .dataio import load_bank, summarize
from .engine import RunConfig, run_evaluation
from .judge import OpenAICompatibleJudge, RESULT_PASS_THRESHOLD_DEFAULT
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
        data_scenarios=cfg.get("data", {}).get("scenarios"),
        data_rubrics=cfg.get("data", {}).get("rubrics"),
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
        result_pass_threshold=jcfg.get(
            "result_pass_threshold", RESULT_PASS_THRESHOLD_DEFAULT
        ),
    )

    specs = cfg["tutors"]
    if args.tutor != "all":
        specs = [t for t in specs if t["name"] == args.tutor]
        if not specs:
            print(f"unknown tutor '{args.tutor}' (config has: "
                  f"{[t['name'] for t in cfg['tutors']]})", file=sys.stderr)
            return 1

    run_cfg = _run_config(cfg)
    if args.max_scenarios is not None:
        run_cfg.max_scenarios = args.max_scenarios
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


def cmd_generate(args: argparse.Namespace) -> int:
    """Run open-weight models over TutorBench scenarios (vLLM/HF on GPU).

    respgen imports are deferred to here so `validate`/`run`/`plot` work without
    the heavy [gen] deps. --dry-run stays fully offline (no torch/vllm, no Hub)."""
    _load_env()  # HF_TOKEN from .env for gated models (never committed/pushed)
    from .respgen.manifest import load_manifest
    from .respgen.runner import dry_run, load_scenarios

    specs = load_manifest(args.models)
    if args.model:
        specs = [s for s in specs if s.id == args.model]
        if not specs:
            print(f"unknown model id '{args.model}' (not in {args.models})", file=sys.stderr)
            return 1

    if args.dry_run:
        n = args.limit or 5
        scenarios = load_scenarios(args.scenarios, limit=n)
        # no-network config fetch => max_model_len falls back to the cap
        print(dry_run(specs, scenarios, fetch_config=lambda _id: {}, n=n))
        return 0

    gpu_ids = None
    if args.gpu_ids:
        try:
            gpu_ids = [int(x) for x in args.gpu_ids.split(",") if x.strip() != ""]
        except ValueError:
            print(f"--gpu-ids must be comma-separated integers, got {args.gpu_ids!r}",
                  file=sys.stderr)
            return 1
        if not gpu_ids:
            print("--gpu-ids was empty", file=sys.stderr)
            return 1

    from .respgen.orchestrator import run_fleet

    try:
        results = run_fleet(
            specs,
            args.scenarios,
            args.out_dir,
            s3_uri=args.s3_uri,
            resume=not args.no_resume,
            gpus=args.gpus,
            gpu_ids=gpu_ids,
            limit=args.limit,
        )
    except ValueError as e:  # e.g. --gpu-ids out of range for this node
        print(f"generate: {e}", file=sys.stderr)
        return 1
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    failed = [r for r in results if r.get("status") not in ("ok", "already_complete")]
    print(f"generate: {len(results) - len(failed)}/{len(results)} models ok", file=sys.stderr)
    return 1 if failed else 0


def _force_utf8_stdio() -> None:
    # Model prompts/outputs contain arbitrary Unicode (math superscripts, CJK,
    # emoji). The Windows console defaults to cp1252 and raises
    # UnicodeEncodeError on write; UTF-8 with errors="replace" keeps a long run
    # from dying on one stray glyph. No-op where already UTF-8 (Linux GPU box).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(prog="tutor-cat", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="validate data/*.jsonl against the PRD schemas")
    p_val.add_argument("--config", default="config.yaml")
    p_val.set_defaults(fn=cmd_validate)

    p_run = sub.add_parser("run", help="run the CAT (or baseline) evaluation")
    p_run.add_argument("--config", default="config.yaml")
    p_run.add_argument("--tutor", default="all", help="tutor name from config, or 'all'")
    p_run.add_argument("--mode", choices=["cat", "baseline", "both"], default="cat")
    p_run.add_argument("--max-scenarios", type=int, default=None,
                       help="override run.max_scenarios (e.g. 3 for a quick smoke test)")
    p_run.set_defaults(fn=cmd_run)

    p_plot = sub.add_parser("plot", help="plot SE trajectories for one or more runs")
    p_plot.add_argument("runs", nargs="+", help="paths to runs/<run_id> directories")
    p_plot.add_argument("--out", default="se_trajectories.png")
    p_plot.set_defaults(fn=cmd_plot)

    p_gen = sub.add_parser(
        "generate", help="run open-weight models over TutorBench scenarios (AWS/vLLM)"
    )
    p_gen.add_argument("--models", default="models.yaml", help="model manifest YAML")
    p_gen.add_argument("--scenarios", default="data/scenarios.jsonl")
    p_gen.add_argument("--out-dir", default="runs/responses",
                       help="one JSONL shard per model is written here")
    p_gen.add_argument("--s3-uri", default=None,
                       help="s3://bucket/prefix to upload each finished shard (instance IAM)")
    p_gen.add_argument("--gpus", type=int, default=None,
                       help="worker processes (default: detected GPU count)")
    p_gen.add_argument("--gpu-ids", default=None,
                       help="comma-separated physical CUDA device indices to pin "
                            "workers to (e.g. '8' = GPU 8 only); overrides --gpus")
    p_gen.add_argument("--limit", type=int, default=None,
                       help="cap scenarios per model (smoke test)")
    p_gen.add_argument("--model", default=None, help="run only this model id")
    p_gen.add_argument("--dry-run", action="store_true",
                       help="print resolved config + sample prompts; no model load, no network")
    p_gen.add_argument("--no-resume", action="store_true",
                       help="ignore existing shards and regenerate")
    p_gen.set_defaults(fn=cmd_generate)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
