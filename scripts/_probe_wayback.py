"""Probe the Internet Archive (Wayback) for archived APUSH FRQ + SG PDFs, 2015-2022.

For each candidate canonical College Board URL, ask the availability API for the
closest snapshot; if found, fetch the RAW file via the `…/web/<ts>id_/<url>` form
and confirm %PDF magic bytes. Prints a manifest of what is actually retrievable so
the scraper's year map can be built from ground truth rather than guesses.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.request

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# candidate canonical URLs per year. Multiple filename conventions are tried;
# the probe reports which (if any) resolves.
MEDIA = "https://apcentral.collegeboard.org/media/pdf/"
SECURE = "https://secure-media.collegeboard.org/digitalServices/pdf/ap/"
APC = "https://secure-media.collegeboard.org/apc/"

CANDIDATES: dict[int, dict[str, list[str]]] = {}
for _yy in range(15, 23):
    y = 2000 + _yy
    CANDIDATES[y] = {
        "frq": [
            f"{MEDIA}ap{_yy}-frq-us-history.pdf",
            f"{SECURE}ap{_yy}_frq_us_history.pdf",
            f"{SECURE}ap-us-history-frq-{y}.pdf",
            f"{APC}ap{_yy}_frq_us_history.pdf",
            f"{APC}ap-us-history-frq-{y}.pdf",
        ],
        "sg": [
            f"{MEDIA}ap{_yy}-sg-us-history.pdf",
            f"{SECURE}ap{_yy}_us_history_sg.pdf",
            f"{SECURE}ap-us-history-sg-{y}.pdf",
            f"{SECURE}ap{_yy}_us_history_scoring_guidelines.pdf",
            f"{APC}ap{_yy}_us_history_sg.pdf",
            f"{APC}ap{_yy}_us_history_scoring_guidelines.pdf",
        ],
    }


def available(url: str) -> tuple[str, str] | None:
    """Return (snapshot_ts, snapshot_url) if Wayback has a snapshot, else None."""
    api = "http://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
    for attempt in range(4):
        try:
            req = urllib.request.Request(api, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, context=_CTX, timeout=30) as r:
                data = json.loads(r.read())
            snap = data.get("archived_snapshots", {}).get("closest")
            if snap and snap.get("available"):
                return snap["timestamp"], snap["url"]
            return None
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def raw_ok(ts: str, url: str) -> tuple[bool, int]:
    """Fetch the raw file via the id_ form; return (is_pdf, size)."""
    raw = f"http://web.archive.org/web/{ts}id_/{url}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(raw, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, context=_CTX, timeout=90) as r:
                data = r.read()
            return data[:4] == b"%PDF", len(data)
        except Exception:
            time.sleep(2 * (attempt + 1))
    return False, 0


def main() -> None:
    manifest: dict[int, dict[str, str]] = {}
    for year in sorted(CANDIDATES):
        manifest[year] = {}
        for kind in ("frq", "sg"):
            hit = None
            for cand in CANDIDATES[year][kind]:
                snap = available(cand)
                time.sleep(0.6)
                if snap:
                    ts, _snap_url = snap
                    ok, size = raw_ok(ts, cand)
                    time.sleep(0.6)
                    status = f"PDF {size//1024}KB @ {ts}" if ok else f"NON-PDF @ {ts}"
                    print(f"{year} {kind}: {status}\n    {cand}")
                    if ok:
                        hit = cand
                        manifest[year][kind] = cand
                        break
                    # snapshot exists but not a clean PDF; keep trying candidates
            if hit is None:
                print(f"{year} {kind}: NONE FOUND")
    print("\n=== resolved manifest ===")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
