"""8-way data-parallel fleet: one *fresh process per model*, dispatched by one
feeder thread per GPU.

Data parallelism, not tensor parallelism: every <=7B model fits one B200, so we
run N models at once (tp=1 each) instead of sharding one model across GPUs. One
shard per model means workers never contend on a file.

Fresh-process-per-model (not a persistent worker looping over the queue): vLLM's
engine leaves CUDA-graph captures, KV-cache blocks, and NCCL state that Python GC
can't reclaim, so a persistent worker's *later* models OOM'd at engine init (the
leading hypothesis for the Group-A load failures). Letting each model run in its
own process and exit hands GPU memory reclamation to the OS — definitive, whatever
vLLM leaked. A parent thread per GPU pulls specs off a thread-safe queue and
spawns these short-lived processes one at a time on its device.
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import threading
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
    Never spawn more feeder threads than there are models to run."""
    if gpu_ids:
        ids = list(dict.fromkeys(gpu_ids))  # dedupe, preserve order
    else:
        n = gpus or detect()
        ids = list(range(max(1, n)))
    return ids[: max(1, n_specs)]


def _run_one_process(
    spec: ModelSpec,
    gpu_id: int,
    scenarios_path: str,
    out_dir: str,
    s3_uri: str | None,
    resume: bool,
    limit: int | None,
    result_q: "mp.Queue",
) -> None:
    """Process target: pin to one GPU, run EXACTLY ONE model, put its summary on
    the queue. Because this process exits after one model, the OS reclaims all of
    its GPU memory — the between-models leak can't accumulate across models."""
    import os

    # Pin THIS process to one GPU before importing torch/vllm (import reads it).
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    from .runner import load_scenarios, run_model

    try:
        scenarios = load_scenarios(scenarios_path, limit=limit)
        res = run_model(spec, scenarios, out_dir, s3_uri=s3_uri, resume=resume)
    except Exception as e:  # noqa: BLE001 - never let one model kill the fleet
        res = {"model": spec.id, "status": "worker_error", "error": repr(e)}
    result_q.put(res)


def _spawn_one(
    ctx,
    spec: ModelSpec,
    gpu_id: int,
    scenarios_path: str,
    out_dir: str,
    s3_uri: str | None,
    resume: bool,
    limit: int | None,
    poll: float = 1.0,
) -> dict[str, Any]:
    """Run one model in a fresh spawned process and return its summary dict.

    Drains the result queue while the child is alive (so a large item can never
    fill the pipe buffer and deadlock join), then joins. If the child died without
    producing a summary — OOM-killed, segfault, or a C-level CUDA crash that
    Python's except can't catch — synthesize a ``worker_crashed`` record so the
    model still gets a row and the fleet moves on."""
    result_q: "mp.Queue" = ctx.Queue()
    p = ctx.Process(
        target=_run_one_process,
        args=(spec, gpu_id, scenarios_path, out_dir, s3_uri, resume, limit, result_q),
    )
    p.start()
    res: dict[str, Any] | None = None
    while p.is_alive():
        try:
            res = result_q.get(timeout=poll)
            break
        except queue.Empty:
            continue
    if res is None:  # process ended; collect a result it may have queued at exit
        try:
            res = result_q.get(timeout=5.0)
        except queue.Empty:
            res = None
    p.join()
    if res is None:
        res = {
            "model": spec.id,
            "status": "worker_crashed",
            "error": f"process exited (code {p.exitcode}) without returning a result",
        }
    return res


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
    run_one: Callable[[ModelSpec, int], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Distribute `specs` across GPUs, running each model in its own fresh process.
    Blocks until all models are done; returns one summary dict per model.

    `gpu_ids` pins to explicit physical device indices (e.g. `[8]` = GPU 8 only);
    otherwise `gpus` (or all detected) devices 0..n-1 are used. Raises ValueError
    if a requested index is out of range for this node (checked before spawning).

    `run_one(spec, gpu_id) -> dict` is injectable for offline testing; the default
    spawns a fresh process per model via `_spawn_one`."""
    ids = _select_gpu_ids(gpu_ids, gpus, len(specs))
    _validate_gpu_ids(ids, count_devices())
    ctx = mp.get_context("spawn")  # required for CUDA + clean env inheritance

    if run_one is None:
        def run_one(spec: ModelSpec, gpu_id: int) -> dict[str, Any]:
            return _spawn_one(
                ctx, spec, gpu_id, str(scenarios_path), str(out_dir),
                s3_uri, resume, limit,
            )

    # Thread-safe work queue in the PARENT; one feeder thread per GPU drains it,
    # spawning a fresh model process at a time on its pinned device.
    task_q: "queue.Queue[ModelSpec]" = queue.Queue()
    for spec in specs:
        task_q.put(spec)

    results: list[dict[str, Any]] = []
    results_lock = threading.Lock()

    def feeder(gpu_id: int) -> None:
        while True:
            try:
                spec = task_q.get_nowait()
            except queue.Empty:
                return
            try:
                res = run_one(spec, gpu_id)
            except Exception as e:  # noqa: BLE001 - isolate a dispatch failure
                res = {"model": spec.id, "status": "worker_error", "error": repr(e)}
            with results_lock:
                results.append(res)

    threads = [threading.Thread(target=feeder, args=(gid,)) for gid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results
