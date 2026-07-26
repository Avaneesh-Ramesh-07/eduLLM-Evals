"""End-to-end MCQ IRT + CAT run over the existing mcq/ CSVs.

For each benchmark: load the 0/1 grid, filter items, hold out a shared set of
models, calibrate a 2PL bank on the rest, estimate every model's full-bank
ability (EAP), run a CAT on the held-out models, and write calibration numbers
+ plots. No GPU, no scoring.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import report
from .ability import eap, normal_grid, prob_2pl
from .calibrate import fit_2pl
from .cat import run_cat
from .matrix import choose_diagnostic, filter_items, load_benchmark

DEFAULT_BENCHMARKS = ["arc_challenge", "arc_easy", "openbookqa", "sciq"]


def _dist(x: np.ndarray) -> dict[str, float]:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return {}
    q = np.percentile(x, [25, 50, 75])
    return {"min": float(x.min()), "q25": float(q[0]), "median": float(q[1]),
            "q75": float(q[2]), "max": float(x.max()), "mean": float(x.mean())}


def _corr(x: list[float], y: list[float]) -> float | None:
    if len(x) < 2:
        return None
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    if xa.std() == 0 or ya.std() == 0:
        return None
    return float(np.corrcoef(xa, ya)[0, 1])


def _recovery(cat_theta: list[float], full_theta: list[float]) -> dict[str, Any]:
    if not cat_theta:
        return {}
    d = np.asarray(cat_theta) - np.asarray(full_theta)
    return {"n": len(cat_theta), "corr": _corr(cat_theta, full_theta),
            "mae": float(np.abs(d).mean()), "bias": float(d.mean())}


def _pirt(mat: pd.DataFrame, items: list[str], a: np.ndarray, b: np.ndarray,
          theta_full: dict[str, float]) -> dict[str, Any]:
    """Reconstruct each model's overall accuracy from its ability and compare to
    observed accuracy (pIRT-style validity check)."""
    preds, obs = [], []
    for m in mat.index:
        r = mat.loc[m, items].to_numpy(dtype=float)
        preds.append(float(prob_2pl(theta_full[m], a, b).mean()))
        obs.append(float(r.mean()))
    d = np.asarray(preds) - np.asarray(obs)
    return {"mae": float(np.abs(d).mean()), "corr": _corr(preds, obs)}


def run(
    mcq_dir: str | Path,
    benchmarks: list[str] | None = None,
    out_dir: str | Path = "runs/mcq_irt",
    *,
    frac: float = 0.1,
    seed: int = 0,
    se_stop: float = 0.3,
    max_items: int = 50,
    method: str = "girth",
    min_point_biserial: float = 0.05,
) -> dict[str, Any]:
    benchmarks = benchmarks or DEFAULT_BENCHMARKS
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    grid = normal_grid()

    matrices = {}
    for bm in benchmarks:
        matrices[bm] = load_benchmark(mcq_dir, bm)
        print(f"loaded {bm}: {matrices[bm].shape[0]} models x {matrices[bm].shape[1]} items", flush=True)
    diag_models = choose_diagnostic(matrices, frac=frac, seed=seed)

    summary: dict[str, Any] = {
        "config": {"mcq_dir": str(mcq_dir), "benchmarks": benchmarks, "frac": frac,
                   "seed": seed, "se_stop": se_stop, "max_items": max_items,
                   "method": method, "min_point_biserial": min_point_biserial},
        "diagnostic_models": diag_models,
        "benchmarks": {},
    }

    for bm in benchmarks:
        print(f"[{bm}] filtering + fitting 2PL...", flush=True)
        kept, frep = filter_items(matrices[bm], benchmark=bm, min_point_biserial=min_point_biserial)
        models = list(kept.index)
        diag_present = [m for m in diag_models if m in kept.index]
        calib_models = [m for m in models if m not in set(diag_present)]

        calib = fit_2pl(kept.loc[calib_models], method=method)
        print(f"[{bm}] fit {calib.method}: kept {frep.n_items_kept}/{frep.n_items_raw} items, "
              f"{len(calib_models)} calib / {len(diag_present)} diag models", flush=True)
        a, b, items = calib.a, calib.b, calib.items
        a_by, b_by = calib.a_by_item(), calib.b_by_item()

        theta_full = {m: eap(kept.loc[m, items].to_numpy(float), a, b, grid)[0] for m in models}

        cat_results = {}
        for m in diag_present:
            resp_by = {it: int(kept.loc[m, it]) for it in items}
            cat_results[m] = run_cat(resp_by, a_by, b_by, max_items=max_items, se_stop=se_stop, grid=grid)

        cat_theta = [cat_results[m].theta for m in diag_present]
        full_theta = [theta_full[m] for m in diag_present]

        # outputs
        items_df = calib.as_frame().join(frep.item_stats[["p_value", "point_biserial"]])
        items_df.to_csv(out / f"{bm}_items.csv")
        report.plot_ab_hist(a, b, bm, out / f"{bm}_ab_hist.png")
        if len(diag_present) >= 2:
            report.plot_theta_recovery(cat_theta, full_theta, bm, out / f"{bm}_theta_recovery.png")

        n_items = [cat_results[m].n_items for m in diag_present]
        se_stop_vals = [cat_results[m].se for m in diag_present]
        summary["benchmarks"][bm] = {
            "n_models": frep.n_models,
            "n_calib_models": len(calib_models),
            "n_diag_models": len(diag_present),
            "n_items_raw": frep.n_items_raw,
            "n_items_kept": frep.n_items_kept,
            "dropped": {"all_pass": len(frep.dropped_all_pass),
                        "all_fail": len(frep.dropped_all_fail),
                        "low_point_biserial": len(frep.dropped_low_pbis)},
            "fit_method": calib.method,
            "n_extreme_a": calib.n_extreme_a,
            "a": _dist(a),
            "b": _dist(b),
            "cat": {"mean_items_to_stop": float(np.mean(n_items)) if n_items else None,
                    "max_items_hit": int(sum(1 for n in n_items if n >= max_items)),
                    "mean_se_at_stop": float(np.mean(se_stop_vals)) if se_stop_vals else None,
                    "max_items": max_items, "se_stop": se_stop},
            "theta_recovery": _recovery(cat_theta, full_theta),
            "pirt": _pirt(kept, items, a, b, theta_full),
        }

    report.save_json(summary, out / "summary.json")
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"diagnostic models ({len(summary['diagnostic_models'])}): "
          f"{', '.join(m.split('/')[-1] for m in summary['diagnostic_models'])}")
    for bm, s in summary["benchmarks"].items():
        a, rec, pirt = s["a"], s["theta_recovery"], s["pirt"]
        print(f"\n[{bm}] {s['n_calib_models']} calib / {s['n_diag_models']} diag models, "
              f"{s['n_items_kept']}/{s['n_items_raw']} items kept "
              f"(dropped {s['dropped']}), fit={s['fit_method']}")
        print(f"  a median={a.get('median'):.2f} (max {a.get('max'):.2f}, "
              f"{s['n_extreme_a']} extreme); b median={s['b'].get('median'):.2f}")
        print(f"  CAT: {s['cat']['mean_items_to_stop']} items to SE<{s['cat']['se_stop']} "
              f"(mean SE {s['cat']['mean_se_at_stop']:.3f}); "
              f"theta recovery corr={rec.get('corr')}, MAE={rec.get('mae'):.3f}; "
              f"pIRT MAE={pirt['mae']:.3f}")
