"""Shared helpers for the APUSH scrapers (SAQ / DBQ / LEQ).

Responsibilities kept here so all three scrapers stay self-contained and DRY:
  * download()  - fetch a College Board PDF straight to a local cache (urllib,
                  browser UA, relaxed TLS for the corporate MITM cert). Only the
                  three most recent exam years (2023-2025) are still public on
                  apcentral; older years now 302 to the AP Classroom login page.
  * pdf_text()  - decrypt (owner-locked, empty password) + extract + normalize.
  * norm()      - NFKC + straighten smart quotes + drop (r)/(c) glyphs.
  * SETS / *_URL - the source manifest every scraper iterates.
"""
from __future__ import annotations

import json
import re
import ssl
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NamedTuple

from pypdf import PdfReader

# --- source manifest ----------------------------------------------------------
# 2023+ uses the `-set-{N}` suffix; earlier years used bare `apYY-sg-us-history.pdf`
# but those files were removed from public hosts (verified 2026-07: all redirect
# to /courses/past-exam-questions). So the reliably-scrapeable universe is 23-25.
SG_URL = "https://apcentral.collegeboard.org/media/pdf/ap{yy}-sg-us-history-set-{s}.pdf"
FRQ_URL = "https://apcentral.collegeboard.org/media/pdf/ap{yy}-frq-us-history-set-{s}.pdf"
SETS = [(2023, 1), (2023, 2), (2024, 1), (2024, 2), (2025, 1), (2025, 2)]

CACHE = Path(__file__).resolve().parent / "_pdf_cache"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
# College Board is fronted by a TLS interceptor on this network; verification is
# disabled deliberately and only for these public, read-only PDF fetches.
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

BULLET = "•"


def sg_url(year: int, set_num: int) -> str:
    return SG_URL.format(yy=str(year)[2:], s=set_num)


def frq_url(year: int, set_num: int) -> str:
    return FRQ_URL.format(yy=str(year)[2:], s=set_num)


# --- exam-form manifest -------------------------------------------------------
# A "form" is one administration with ONE combined Scoring Guidelines PDF and ONE
# combined Free-Response PDF, in the CURRENT SG document grammar (header
# "Question N: Short Answer/Document-Based/Long Essay", scoring row anchored by
# "Row A" + "0 points / Does not meet the criteria for one point" + polytomous
# "2 points"). NOTE: this DOCUMENT layout dates to the ~2020 SG redesign - it is
# NOT the same as the 2018-2019 SG layout ("A: Thesis/Claim (0-1) ... (1 point)"),
# even though the 7/6-point rubric scoring is unchanged. So only 2021+ is covered
# by the current parsers; 2018-2019 and 2015-2017 each get their own build path.
#   * 2023-2025: still first-party on apcentral, set-based (`wayback=False`).
#   * 2021/2022: single-form, only reachable through the Internet Archive.
#   * 2020 omitted: the COVID-year exam was a single modified DBQ; only a "sample
#     questions" PDF was ever posted - not an operational SAQ/DBQ/LEQ set.
# `sg`/`frq` are the CANONICAL College Board URLs recorded verbatim in `source`.
_MEDIA = "https://apcentral.collegeboard.org/media/pdf/"


class Form(NamedTuple):
    year: int
    label: str          # "set-1" / "set-2" / "single"
    sg_src: str         # canonical CB URL (recorded in `source`)
    frq_src: str        # canonical CB URL
    wayback: bool       # fetch via Internet Archive?

    @property
    def sg_path(self) -> Path:
        return download(self.sg_src, wayback=self.wayback)

    @property
    def frq_path(self) -> Path:
        return download(self.frq_src, wayback=self.wayback)


def current_forms() -> list[Form]:
    """Every exam form in the current SG document grammar, oldest first.

    2021-2022 via the Internet Archive, 2023-2025 first-party. 2018-2019 use the
    older SG layout and are handled by their own build path, not this list.
    """
    forms = [
        Form(2021, "single", f"{_MEDIA}ap21-sg-us-history.pdf",
             f"{_MEDIA}ap21-frq-us-history.pdf", True),
        Form(2022, "single", f"{_MEDIA}ap22-sg-us-history.pdf",
             f"{_MEDIA}ap22-frq-us-history.pdf", True),
    ]
    for year, s in SETS:
        forms.append(Form(year, f"set-{s}", sg_url(year, s), frq_url(year, s), False))
    return forms


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, context=_CTX, timeout=90) as r:
        return r.read()


def _wayback_snapshot(canonical: str) -> str:
    """Resolve the closest Wayback snapshot timestamp for a canonical URL.

    Uses the availability API (lighter than CDX, tolerates the archive's frequent
    503s with linear backoff). Raises if no snapshot exists.
    """
    api = "http://archive.org/wayback/available?url=" + urllib.parse.quote(
        canonical, safe=""
    )
    last = "no snapshot"
    for attempt in range(4):
        try:
            data = json.loads(_fetch(api))
            snap = data.get("archived_snapshots", {}).get("closest")
            if snap and snap.get("available"):
                return snap["timestamp"]
            last = "archive has no snapshot"
            break
        except Exception as e:  # transient 503 / timeout -> back off and retry
            last = str(e)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Wayback lookup failed for {canonical}: {last}")


def download(url: str, wayback: bool = False) -> Path:
    """Fetch `url` into the local cache and return the path. Cached on disk.

    With `wayback=True`, `url` is the CANONICAL College Board URL (the value we
    record in `source`); the bytes are pulled from the Internet Archive via the
    `…/web/<ts>id_/<canonical>` raw form (the `id_` suffix returns the original
    file, unrewritten by the archive's toolbar). Pre-2023 exams are gone from the
    first-party hosts, so this is the only durable path to them.
    """
    CACHE.mkdir(exist_ok=True)
    dest = CACHE / url.rsplit("/", 1)[-1].split("?", 1)[0]
    if dest.exists() and dest.stat().st_size > 10_000:
        return dest

    if wayback:
        ts = _wayback_snapshot(url)
        raw = f"http://web.archive.org/web/{ts}id_/{url}"
        data = b""
        for attempt in range(3):
            try:
                data = _fetch(raw)
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
    else:
        data = _fetch(url)

    if data[:4] != b"%PDF":  # soft-404s return an HTML redirect page, not a 404
        raise RuntimeError(f"{url} did not return a PDF (head={data[:12]!r})")
    dest.write_bytes(data)
    return dest


def norm(text: str) -> str:
    """NFKC-normalize, straighten smart quotes, drop registered/copyright glyphs."""
    text = unicodedata.normalize("NFKC", text)
    return (
        text.replace("’", "'").replace("‘", "'")
        .replace("“", '"').replace("”", '"')
        .replace("–", "-").replace("—", "-")
        .replace("®", "").replace("©", "")
    )


def pdf_text(path: Path) -> str:
    """Full normalized text of a (possibly owner-locked) PDF."""
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    return norm("\n".join((pg.extract_text() or "") for pg in reader.pages))


def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def dejoin(text: str) -> str:
    """Rejoin words the PDF extractor split across a line as a lone letter.

    Some SG pages (2024 Set 2, 2025 Set 2) orphan a word's first character onto
    its own line - "f\\nor" -> "for", "c\\nontinuity" -> "continuity",
    "l\\nine" -> "line". A lone lowercase letter bracketed by newlines is never a
    real word here, so splice it onto the following fragment. This both lets the
    scoring-row anchor match and keeps split words out of the stored text.

    Only the lone-letter case is repaired. A general "[a-z]\\n[a-z]" join is
    tempting but AMBIGUOUS: "nua\\nnce" (mid-word) and "Americans\\ncelebrated"
    (word boundary with a dropped space) share that shape, and joining the latter
    without a space fuses two real words - worse than the rare cosmetic split
    left behind ("nua nce"), which clean_ws turns into a harmless in-word space.
    """
    return re.sub(r"\n([a-z])\n", r"\1", text)


def denoise_sg(full: str) -> str:
    """Drop running header/footer/page-number lines from a Scoring Guidelines PDF.

    Keeps structural markers the parsers rely on (e.g. "Decision Rules and
    Scoring Notes", "Row A", "0 points") - only strips the page furniture that
    interleaves at page breaks and would otherwise corrupt bullet fragments.
    """
    keep = []
    for line in full.splitlines():
        s = line.strip()
        if "College Board" in s:
            continue
        if re.search(r"United States History\s+\d{4}\s+Scoring Guidelines", s):
            continue
        if re.fullmatch(r"(?:AP\s+)?United States History", s):
            continue
        if re.fullmatch(r"S?c?oring Guidelines", s):  # incl. wrapped "oring Guidelines"
            continue
        if re.fullmatch(r"Reporting", s) or re.fullmatch(r"Category", s):
            continue
        if re.fullmatch(r"(?:Category\s+)?Scoring Criteria", s):
            continue
        if re.fullmatch(r"\d{1,3}", s):  # bare page numbers
            continue
        keep.append(line)
    return "\n".join(keep)


def denoise_frq(full: str) -> str:
    """Drop running header/footer/page-number lines from a Free-Response PDF."""
    keep = []
    for line in full.splitlines():
        s = line.strip()
        if "College Board" in s or "collegeboard.org" in s:
            continue
        if re.fullmatch(r"(?:AP\s+)?United States History\s+\d{4}\s+Free-Response Questions", s):
            continue
        if s.startswith("GO ON TO THE NEXT PAGE"):
            continue
        if re.fullmatch(r"_{5,}", s):
            continue
        if re.fullmatch(r"\d{1,3}", s):
            continue
        keep.append(line)
    return "\n".join(keep)
