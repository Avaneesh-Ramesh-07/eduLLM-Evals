"""Enumerate what the Wayback CDX index actually holds for the gap years
(2017, 2018, 2020) under the College Board PDF hosts. CDX wildcard search lists
every archived original URL matching a prefix, so we discover real filenames
instead of guessing them one at a time.
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

# broad prefixes; the filter keeps only US-History PDFs. One query per host dir.
PREFIXES = [
    "secure-media.collegeboard.org/digitalServices/pdf/ap/*",
    "secure-media.collegeboard.org/apc/*",
    "apcentral.collegeboard.org/media/pdf/*",
]


def cdx(prefix: str) -> list[tuple[str, str]]:
    q = (
        "http://web.archive.org/cdx/search/cdx?url="
        + urllib.parse.quote(prefix, safe="")
        + "&output=json&fl=original,timestamp&collapse=original"
        + "&filter=mimetype:application/pdf&limit=6000"
    )
    for attempt in range(5):
        try:
            req = urllib.request.Request(q, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, context=_CTX, timeout=120) as r:
                rows = json.loads(r.read())
            return [(row[0], row[1]) for row in rows[1:]]  # skip header row
        except Exception as e:
            print(f"  retry {attempt} ({e})")
            time.sleep(3 * (attempt + 1))
    return []


def main() -> None:
    hits: list[tuple[str, str]] = []
    for pre in PREFIXES:
        rows = cdx(pre)
        print(f"{pre}: {len(rows)} pdfs")
        for orig, ts in rows:
            low = orig.lower()
            if ("us" in low and "history" in low) or "ush" in low:
                # only the gap years of interest
                if any(tok in low for tok in ("2017", "17_", "-17", "ap17",
                                              "2018", "18_", "-18", "ap18",
                                              "2020", "20_", "-20", "ap20")):
                    hits.append((orig, ts))
        time.sleep(2)
    print("\n=== gap-year US History PDFs (2017/2018/2020) ===")
    for orig, ts in sorted(set(hits)):
        print(f"{ts}  {orig}")


if __name__ == "__main__":
    main()
