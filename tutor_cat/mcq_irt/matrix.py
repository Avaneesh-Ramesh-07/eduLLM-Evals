"""Read the mcq/ loglikelihood CSVs into per-benchmark model x item 0/1 matrices,
filter non-informative items, and choose a held-out diagnostic set of models.

Each CSV is one model's results on one benchmark with columns
`question_id, model, benchmark, predicted, gold, result, scoring_method`; the
`result` column (correct/wrong) is the 0/1 grid, so no scoring is needed here.

Item filtering follows the ATLAS rules: drop items every model passes or every
model fails (no variance -> no discrimination) and items with a low or negative
point-biserial (item-rest correlation), which are non-discriminating or mis-keyed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class FilterReport:
    benchmark: str
    n_models: int
    n_items_raw: int
    n_items_kept: int
    dropped_all_pass: list[str]
    dropped_all_fail: list[str]
    dropped_low_pbis: list[str]
    item_stats: pd.DataFrame  # index=item, cols: p_value, point_biserial, kept, drop_reason


def load_benchmark(mcq_dir: str | Path, benchmark: str) -> pd.DataFrame:
    """Return a model x item DataFrame of 0/1 correctness for one benchmark."""
    bdir = Path(mcq_dir) / benchmark
    files = sorted(bdir.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no CSVs in {bdir}")
    series: dict[str, pd.Series] = {}
    for f in files:
        df = pd.read_csv(f, usecols=["question_id", "model", "result"])
        if df.empty:
            continue
        model = str(df["model"].iloc[0])
        correct = (df["result"] == "correct").astype("int8")
        s = pd.Series(correct.values, index=df["question_id"].values)
        # a model should not repeat an item; keep the first if it does
        series[model] = s[~s.index.duplicated(keep="first")]
    mat = pd.DataFrame(series).T  # rows=models, cols=items
    mat.index.name = "model"
    mat.columns.name = "item"
    return mat.sort_index()


def _point_biserial(X: np.ndarray) -> np.ndarray:
    """Per-item item-rest point-biserial correlation. X is (n_models, n_items),
    0/1 (no NaN). Uses rest score (total minus the item) to avoid self-inflation."""
    total = X.sum(axis=1, keepdims=True)
    rest = total - X
    xc = X - X.mean(axis=0, keepdims=True)
    rc = rest - rest.mean(axis=0, keepdims=True)
    cov = (xc * rc).sum(axis=0)
    sx = np.sqrt((xc**2).sum(axis=0))
    sr = np.sqrt((rc**2).sum(axis=0))
    denom = sx * sr
    with np.errstate(invalid="ignore", divide="ignore"):
        pbis = np.where(denom > 0, cov / denom, np.nan)
    return pbis


def filter_items(
    mat: pd.DataFrame, benchmark: str = "", min_point_biserial: float = 0.05
) -> tuple[pd.DataFrame, FilterReport]:
    """Drop all-pass/all-fail and low/negative point-biserial items. Assumes a
    complete (no-NaN) matrix; drop any model row with missing cells first."""
    mat = mat.dropna(axis=0, how="any")
    X = mat.to_numpy(dtype=float)
    items = list(mat.columns)
    p = X.mean(axis=0)
    pbis = _point_biserial(X)

    all_pass, all_fail, low_pbis, kept = [], [], [], []
    reasons: dict[str, str] = {}
    for j, it in enumerate(items):
        if p[j] >= 1.0:
            all_pass.append(it); reasons[it] = "all_pass"
        elif p[j] <= 0.0:
            all_fail.append(it); reasons[it] = "all_fail"
        elif not np.isfinite(pbis[j]) or pbis[j] < min_point_biserial:
            low_pbis.append(it); reasons[it] = "low_point_biserial"
        else:
            kept.append(it); reasons[it] = "kept"

    stats = pd.DataFrame(
        {"p_value": p, "point_biserial": pbis,
         "kept": [reasons[it] == "kept" for it in items],
         "drop_reason": [reasons[it] for it in items]},
        index=pd.Index(items, name="item"),
    )
    report = FilterReport(
        benchmark=benchmark, n_models=mat.shape[0], n_items_raw=len(items),
        n_items_kept=len(kept), dropped_all_pass=all_pass, dropped_all_fail=all_fail,
        dropped_low_pbis=low_pbis, item_stats=stats,
    )
    return mat[kept], report


def choose_diagnostic(
    matrices: dict[str, pd.DataFrame], frac: float = 0.1, seed: int = 0
) -> list[str]:
    """Pick ~frac of the models (present in EVERY benchmark) as the shared
    held-out diagnostic set, so the CAT diagnostic is comparable across banks."""
    common: set[str] | None = None
    for mat in matrices.values():
        s = set(mat.index)
        common = s if common is None else (common & s)
    models = sorted(common or set())
    if not models:
        raise ValueError("no models common to all benchmarks")
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(models))
    n_diag = max(1, round(frac * len(models)))
    diag = sorted(models[i] for i in order[:n_diag])
    return diag
