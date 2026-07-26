"""8-way data-parallel fleet: one worker process per GPU, each pinned via
CUDA_VISIBLE_DEVICES set BEFORE torch/vllm import, pulling models off a shared
queue until it drains.

Data parallelism, not tensor parallelism: every <=3B model fits one B200, so we
run N models at once (tp=1 each) instead of sharding one model across GPUs. One
shard per model means workers never contend on a file.
"""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
from typing import Any, Callable

from .manifest import ModelSpec


def _detect_gpus() -> int:
    try:
        import torch  # lazy

        return max(1, torch.cuda.device_count())
    except Exception:
        return 1


def _cuda_device_count() -> int | None:
    """Physical GPU count for validation, or None if it can't be determined
    (torch missing, or CUDA unavailable). None means "don't block" — we only
    reject an index we can prove is out of range."""
    try:
        import torch  # lazy

        if not torch.cuda.is_available():
            return None
        return torch.cuda.device_count()
    except Exception:
        return None


def _validate_gpu_ids(gpu_ids: list[int], count: int | None) -> None:
    """Fail fast in the PARENT (before spawning) when a requested physical device
    index doesn't exist on this node — otherwise each worker sets an invalid
    CUDA_VISIBLE_DEVICES and dies mid-load with a cryptic "invalid device
    ordinal". Skipped when `count` is None (can't tell -> don't block)."""
    if count is None:
        return
    bad = [i for i in gpu_ids if i < 0 or i >= count]
    if not bad:
        return
    hint = ""
    if count in bad:  # classic 0-index off-by-one: asked for N with N GPUs (0..N-1)
        hint = f" — GPUs are 0-indexed, so the {count}th GPU is index {count - 1}"
    raise ValueError(
        f"--gpu-ids {bad} not present: this node has {count} GPU(s), "
        f"valid indices 0..{count - 1}{hint}"
    )


def _select_gpu_ids(
    gpu_ids: list[int] | None,
    gpus: int | None,
    n_specs: int,
    detect=_detect_gpus,
) -> list[int]:
    """Resolve the *physical* CUDA device indices to spawn workers on.

    Explicit `gpu_ids` win — pin to exactly those devices (deduped, order kept),
    e.g. `[8]` runs the whole roster on GPU 8 and nothing else. Otherwise use the
    first `gpus` devices (or all detected) as 0..n-1, the historical behavior.
    Never spawn more workers than there are models to run."""
    if gpu_ids:
        ids = list(dict.fromkeys(gpu_ids))  # dedupe, preserve order
    else:
        n = gpus or detect()
        ids = list(range(max(1, n)))
    return ids[: max(1, n_specs)]


def _worker(
    gpu_id: int,
    task_q: "mp.Queue",
    result_q: "mp.Queue",
    scenarios_path: str,
    out_dir: str,
    s3_uri: str | None,
    resume: bool,
    limit: int | None,
) -> None:
    import os

    # Pin THIS process to one GPU before importing torch/vllm (import reads it).
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    from .runner import load_scenarios, run_model

    scenarios = load_scenarios(scenarios_path, limit=limit)
    while True:
        spec = task_q.get()
        if spec is None:  # sentinel: no more work
            break
        try:
            res = run_model(spec, scenarios, out_dir, s3_uri=s3_uri, resume=resume)
        except Exception as e:  # noqa: BLE001 - never let one model kill the worker
            res = {"model": spec.id, "status": "worker_error", "error": repr(e)}
        result_q.put(res)


def run_fleet(
    specs: list[ModelSpec],
    scenarios_path: str | Path,
    out_dir: str | Path,
    *,
    s3_uri: str | None = None,
    resume: bool = True,
    gpus: int | None = None,
    gpu_ids: list[int] | None = None,
    limit: int | None = None,
    count_devices: Callable[[], int | None] = _cuda_device_count,
) -> list[dict[str, Any]]:
    """Distribute `specs` across worker processes, one per GPU. Blocks until all
    models are done; returns one summary dict per model.

    `gpu_ids` pins to explicit physical device indices (e.g. `[8]` = GPU 8 only);
    otherwise `gpus` (or all detected) devices 0..n-1 are used. Raises ValueError
    if a requested index is out of range for this node (checked before spawning)."""
    ids = _select_gpu_ids(gpu_ids, gpus, len(specs))
    _validate_gpu_ids(ids, count_devices())
    ctx = mp.get_context("spawn")  # required for CUDA + clean env inheritance
    task_q: mp.Queue = ctx.Queue()
    result_q: mp.Queue = ctx.Queue()

    for spec in specs:
        task_q.put(spec)
    for _ in range(len(ids)):
        task_q.put(None)  # one stop sentinel per worker

    workers = []
    for gpu_id in ids:
        p = ctx.Process(
            target=_worker,
            args=(gpu_id, task_q, result_q, str(scenarios_path), str(out_dir),
                  s3_uri, resume, limit),
        )
        p.start()
        workers.append(p)

    results = [result_q.get() for _ in range(len(specs))]
    for p in workers:
        p.join()
    return results
