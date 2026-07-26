"""Plots and JSON output for the MCQ IRT/CAT run. Uses the non-interactive Agg
backend so it runs headless on any box.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


def save_json(obj: dict[str, Any], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=_json_default)


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {type(o)}")


def plot_ab_hist(a: np.ndarray, b: np.ndarray, benchmark: str, out: str | Path) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.2))
    ax1.hist(a, bins=30)
    ax1.set_title(f"{benchmark}: discrimination a")
    ax1.set_xlabel("a")
    ax1.set_ylabel("items")
    ax2.hist(b, bins=30)
    ax2.set_title(f"{benchmark}: difficulty b")
    ax2.set_xlabel("b")
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def plot_theta_recovery(
    cat_theta: list[float], full_theta: list[float], benchmark: str, out: str | Path
) -> None:
    fig, ax = plt.subplots(figsize=(4.2, 4.0))
    ax.scatter(full_theta, cat_theta)
    lo = min(min(cat_theta), min(full_theta)) - 0.3
    hi = max(max(cat_theta), max(full_theta)) + 0.3
    ax.plot([lo, hi], [lo, hi], ls="--", color="gray", lw=1)
    ax.set_title(f"{benchmark}: CAT theta vs full-bank theta")
    ax.set_xlabel("full-bank EAP theta")
    ax.set_ylabel("CAT theta")
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
