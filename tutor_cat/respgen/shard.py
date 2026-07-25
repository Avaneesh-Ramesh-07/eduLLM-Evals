"""Per-model sharded JSONL output + resume.

One shard per model (``<sanitized_model_id>.jsonl``) means the 8 GPU workers
never write the same file, so no locking is needed. Resume is the same
per-(model, scenario) idempotency idea as tutors.CachedTutor: on start, read the
Scenario ids already in the shard and skip them. Append + flush per line
(mirrors engine._JsonlWriter) so a killed run loses at most the final line.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def sanitize_model_id(model_id: str) -> str:
    """Filesystem-safe shard name. Same rule as tutors.CachedTutor's safe_model."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", model_id)


def shard_path(out_dir: str | Path, model_id: str) -> Path:
    return Path(out_dir) / f"{sanitize_model_id(model_id)}.jsonl"


def completed_ids(path: str | Path, key: str = "Scenario") -> set[str]:
    """Scenario ids already present in a shard. Tolerates a torn final line from
    a killed run (skips it rather than raising)."""
    ids: set[str] = set()
    p = Path(path)
    if not p.exists():
        return ids
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn last line from an interrupted append
            sid = obj.get(key)
            if isinstance(sid, str):
                ids.add(sid)
    return ids


class ShardWriter:
    """Append-only, line-buffered JSONL writer."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("a", encoding="utf-8")

    def write(self, obj: dict[str, Any]) -> None:
        self._f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "ShardWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
