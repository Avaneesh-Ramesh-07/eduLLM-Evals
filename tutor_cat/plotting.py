"""SE-trajectory plots: per-skill standard error vs. scenarios administered.

Reads one or more runs/<run_id>/steps.jsonl and overlays them (e.g. CAT vs
baseline for the same tutor), which is the PRD's benchmark test artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import SKILLS


def _load_steps(run_dir: Path) -> tuple[str, list[dict]]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    label = f"{manifest['tutor_name']} ({manifest['mode']})"
    steps = [
        json.loads(line)
        for line in (run_dir / "steps.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return label, steps


def plot_se_trajectories(run_dirs: list[str | Path], out_path: str | Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(SKILLS), figsize=(5 * len(SKILLS), 4), sharey=True)
    runs = [_load_steps(Path(d)) for d in run_dirs]

    for k, skill in enumerate(SKILLS):
        ax = axes[k]
        for label, steps in runs:
            xs = [s["step"] for s in steps]
            ys = [s["se"][k] for s in steps]
            style = "--" if "(baseline)" in label else "-"
            ax.plot(xs, ys, style, marker=".", label=label)
        ax.set_title(f"SE: {skill}")
        ax.set_xlabel("scenarios administered")
        if k == 0:
            ax.set_ylabel("standard error")
        ax.grid(True, alpha=0.3)
    axes[0].legend(fontsize=8)
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path
