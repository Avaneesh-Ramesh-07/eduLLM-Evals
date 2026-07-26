"""Fit a unidimensional 2PL item bank from a model x item 0/1 matrix.

Primary fitter is girth's marginal-maximum-likelihood 2PL (`twopl_mml`, pure
numpy/scipy). With a thin person sample (~56 models here) MML discriminations can
run large, so we flag extreme `a` and offer a Rasch (1PL) fallback. py-irt
(Bayesian, priors that regularize thin data) is supported as an optional
`method="pyirt"` when the package is installed, but is not required to run.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Discriminations above this are treated as unstable (thin-sample artifacts).
EXTREME_A = 6.0


@dataclass
class Calibration:
    items: list[str]
    a: np.ndarray
    b: np.ndarray
    method: str
    n_models: int
    n_extreme_a: int

    def as_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {"a": self.a, "b": self.b}, index=pd.Index(self.items, name="item")
        )

    def a_by_item(self) -> dict[str, float]:
        return {it: float(v) for it, v in zip(self.items, self.a)}

    def b_by_item(self) -> dict[str, float]:
        return {it: float(v) for it, v in zip(self.items, self.b)}


def fit_2pl(mat: pd.DataFrame, method: str = "girth") -> Calibration:
    """Fit 2PL on a complete (no-NaN) model x item matrix. `method` is
    'girth' (default), 'rasch' (1PL), or 'pyirt' (optional)."""
    mat = mat.dropna(axis=0, how="any")
    items = list(mat.columns)
    data = mat.to_numpy(dtype=int).T  # girth wants (n_items, n_persons)

    if method == "pyirt":
        a, b = _fit_pyirt(mat)
        used = "pyirt"
    elif method == "rasch":
        from girth import rasch_mml

        est = rasch_mml(data)
        b = np.asarray(est["Difficulty"], dtype=float)
        a = np.ones_like(b)
        used = "girth_rasch_mml"
    else:
        from girth import twopl_mml

        est = twopl_mml(data)
        a = np.asarray(est["Discrimination"], dtype=float)
        b = np.asarray(est["Difficulty"], dtype=float)
        used = "girth_twopl_mml"

    n_extreme = int(np.sum(~np.isfinite(a)) + np.sum(np.abs(a) > EXTREME_A))
    return Calibration(
        items=items, a=a, b=b, method=used, n_models=mat.shape[0], n_extreme_a=n_extreme
    )


def crosscheck(primary: Calibration, other: Calibration) -> dict[str, float]:
    """Rank agreement of two fits on their shared items (Spearman-style via
    rank Pearson). Bayesian vs MML fits shift by design, so we compare ranks."""
    shared = [it for it in primary.items if it in set(other.items)]
    if len(shared) < 3:
        return {"n_shared": len(shared)}
    pa = primary.as_frame().loc[shared]
    oa = other.as_frame().loc[shared]

    def _rank_corr(x: pd.Series, y: pd.Series) -> float:
        return float(x.rank().corr(y.rank()))

    return {
        "n_shared": len(shared),
        "a_rank_corr": _rank_corr(pa["a"], oa["a"]),
        "b_rank_corr": _rank_corr(pa["b"], oa["b"]),
    }


def _fit_pyirt(mat: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Optional Bayesian 2PL via py-irt (writes a temp jsonlines dataset). Raises
    ImportError with guidance if py-irt (and its torch/pyro stack) is absent."""
    try:
        import json
        import tempfile
        from pathlib import Path

        from py_irt.config import IrtConfig
        from py_irt.dataset import Dataset
        from py_irt.training import IrtModelTrainer
    except ImportError as e:  # pragma: no cover - optional dependency
        raise ImportError(
            "method='pyirt' needs py-irt (pip install py-irt, pulls torch/pyro). "
            "Use method='girth' for the torch-free path."
        ) from e

    items = list(mat.columns)
    with tempfile.TemporaryDirectory() as d:  # pragma: no cover - exercised only when installed
        path = Path(d) / "responses.jsonlines"
        with path.open("w", encoding="utf-8") as f:
            for model, row in mat.iterrows():
                resp = {str(it): int(v) for it, v in row.items()}
                f.write(json.dumps({"subject_id": str(model), "responses": resp}) + "\n")
        dataset = Dataset.from_jsonlines(path)
        trainer = IrtModelTrainer(config=IrtConfig(model_type="2pl"), dataset=dataset)
        trainer.train()
        params = trainer.best_params
        item_index = {name: i for i, name in enumerate(dataset.ix_to_item_id.values())}
        diff = np.asarray(params["diff"], dtype=float)
        disc = np.asarray(params["disc"], dtype=float)
        a = np.array([disc[item_index[it]] for it in items])
        b = np.array([diff[item_index[it]] for it in items])
        return a, b
