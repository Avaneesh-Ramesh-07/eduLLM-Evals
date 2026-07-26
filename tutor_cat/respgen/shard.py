"""Per-model sharded JSONL output + resume.

One shard per model (``<sanitized_model_id>.jsonl``) means the 8 GPU workers
never write the same file, so no locking is needed. Resume is the same
per-(model, scenario) idempotency idea as tutors.CachedTutor: on start, read the
Scenario ids already in the shard and skip them. Append + flush per line
(mirrors engine._JsonlWriter) so a killed run loses at most the final line.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterator


def sanitize_model_id(model_id: str) -> str:
    """Filesystem-safe shard name. Same rule as tutors.CachedTutor's safe_model."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", model_id)


def shard_path(out_dir: str | Path, model_id: str) -> Path:
    return Path(out_dir) / f"{sanitize_model_id(model_id)}.jsonl"


def _iter_rows(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield each JSON row in a shard, tolerating a torn final line from a killed
    run (skips it rather than raising)."""
    p = Path(path)
    if not p.exists():
        return
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # torn last line from an interrupted append


def completed_ids(path: str | Path, key: str = "Scenario") -> set[str]:
    """Every Scenario id present in a shard, regardless of Issue/Truncated state.
    Low-level primitive; resume uses ``scan_shard`` for the *valid*-only view."""
    return {sid for obj in _iter_rows(path)
            if isinstance(sid := obj.get(key), str)}


def _is_valid_row(obj: dict[str, Any]) -> bool:
    """A row is a real, reusable result — not to be regenerated on resume — when
    generation succeeded (Issue==0) AND a genuine prompt reached the model
    (Prompt Tokens > 1). Prompt Tokens <= 1 is the fingerprint of the old
    truncation bug that discarded the whole prompt; no real TutorBench prompt
    (system prompt + student turn) is ever that short. A row missing the field
    (legacy/minimal) is assumed complete."""
    if obj.get("Issue", 0) == 1:
        return False
    pt = obj.get("Prompt Tokens")
    return pt is None or (isinstance(pt, int) and pt > 1)


def scan_shard(
    path: str | Path, key: str = "Scenario"
) -> tuple[set[str], list[dict[str, Any]], bool]:
    """Resume view of a shard. Returns (done_ids, valid_rows, had_invalid):

      * done_ids     - scenarios with a valid row (skip these on resume)
      * valid_rows   - one row per done scenario, in file order (for compaction)
      * had_invalid  - True if ANY Issue / truncation-corrupted / duplicate row
                       was seen, i.e. the shard should be rewritten to valid_rows
                       so failed + corrupted cells regenerate into a clean shard.
    """
    done: set[str] = set()
    valid_rows: list[dict[str, Any]] = []
    had_invalid = False
    for obj in _iter_rows(path):
        sid = obj.get(key)
        if not isinstance(sid, str):
            had_invalid = True
            continue
        if _is_valid_row(obj) and sid not in done:
            done.add(sid)
            valid_rows.append(obj)
        else:
            had_invalid = True  # Issue row, 1-token-bug row, or duplicate
    return done, valid_rows, had_invalid


def rewrite_shard(path: str | Path, rows: list[dict[str, Any]]) -> None:
    """Atomically replace a shard with `rows` (temp file + os.replace). Used to
    compact out Issue/corrupted/duplicate rows before regenerating them, so the
    resumed shard ends with exactly one clean row per scenario."""
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for obj in rows:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


class ShardWriter:
    """Line-buffered JSONL writer. Appends by default (resume); ``truncate=True``
    starts the shard fresh so a ``--no-resume`` regeneration overwrites the old
    rows instead of stacking a second set on top (which would duplicate every
    (model, scenario) cell)."""

    def __init__(self, path: str | Path, truncate: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("w" if truncate else "a", encoding="utf-8")

    def write(self, obj: dict[str, Any]) -> None:
        self._f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "ShardWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
