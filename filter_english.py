"""Filter rurbichub_v1_sample_220rows.csv down to English-only entries.

A row counts as English only if all three of `query`, `answer`, and the rubric
`criterion` strings are English. Rows pairing a non-English source document with
an English answer (e.g. the Catalan LongAlign contexts, the Spanish->English
translation asks in wildchat) are therefore dropped, and written to a separate
_dropped.csv so nothing is lost.

Detection is three signals, because no single one holds up on this data:

  1. Script ratio - catches non-Latin scripts directly. Needs an explicit
     threshold rather than "any occurrence": row 16 quotes a book title in
     Chinese (the two chars of the Three-Body Problem) inside an otherwise
     English rubric set, and row 88 embeds a Chinese gloss inside Catalan prose.
     Greek is deliberately NOT a signal here - in this dataset it is math
     notation (pi, alpha, mu) across ~30 otherwise-English science rows.

  2. langid (py3langid) - the only thing that catches non-English *Latin-script*
     text: Catalan (88, 96), Hausa (29), Malay (24), plus the usual fr/de/es/it/
     pt/pl rows. Run on normalized text: lowercased (otherwise the IF-RLVR
     "respond in all capitals" rows classify as Norwegian) and stripped of
     digits, math operators and emoji (otherwise chemistry answers full of
     "5.0 x 10^-13" and "<=>" classify as Latin or Lao).

  3. English function-word ratio - a rescue for langid false positives that
     survive normalization. Chemistry/data-heavy English has little prose left
     for langid to work with, but what remains is dense in English function
     words; genuine fr/de/es/ca/ms text scores near zero on this list. A field
     is only condemned if langid says non-English AND this ratio is below
     FUNCTION_WORD_MIN.

Usage:
    python filter_english.py            # write filtered + dropped CSVs
    python filter_english.py --report   # diagnostics only, write nothing
    python filter_english.py --diag     # add per-field ratios to the report
"""

import csv
import json
import re
import sys
from collections import Counter

import py3langid as langid

SRC = "rurbichub_v1_sample_220rows.csv"
DST = "rurbichub_v1_sample_english.csv"
DST_DROPPED = "rurbichub_v1_sample_dropped.csv"

csv.field_size_limit(10**9)

# Scripts that indicate a non-English language. Greek excluded on purpose (math).
NON_LATIN = re.compile(
    "["
    "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"  # CJK
    "\u3040-\u30ff\u31f0-\u31ff"               # Hiragana / Katakana
    "\uac00-\ud7af\u1100-\u11ff\u3130-\u318f"  # Hangul
    "\u0400-\u052f"                            # Cyrillic
    "\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff"  # Arabic
    "\u0590-\u05ff"                            # Hebrew
    "\u0900-\u097f"                            # Devanagari
    "\u0980-\u09ff"                            # Bengali
    "\u0a00-\u0a7f"                            # Gurmukhi
    "\u0a80-\u0aff"                            # Gujarati
    "\u0b00-\u0b7f"                            # Oriya
    "\u0b80-\u0bff"                            # Tamil
    "\u0c00-\u0c7f"                            # Telugu
    "\u0c80-\u0cff"                            # Kannada
    "\u0d00-\u0d7f"                            # Malayalam
    "\u0d80-\u0dff"                            # Sinhala
    "\u0e00-\u0e7f"                            # Thai
    "\u0e80-\u0eff"                            # Lao
    "\u0f00-\u0fff"                            # Tibetan
    "\u1000-\u109f"                            # Myanmar
    "\u10a0-\u10ff"                            # Georgian
    "\u0530-\u058f"                            # Armenian
    "\u1200-\u137f"                            # Ethiopic
    "\u1780-\u17ff"                            # Khmer
    "]"
)

# Fraction of non-Latin-script chars above which a field is non-English. 0.2%
# keeps incidental glyphs (a quoted title) while catching any real sentence.
SCRIPT_RATIO_MAX = 0.002

# Below this many chars of normalized text, langid is noise - script check only.
MIN_CHARS_FOR_LANGID = 60

# langid runs on a prefix; the full 34k-char LongAlign contexts add cost, not signal.
LANGID_PREFIX = 4000

# Minimum English function-word ratio to overturn a non-'en' langid verdict.
# Empirical separation on this file: English false positives land at 0.10-0.30,
# genuine non-English fields at 0.00-0.04.
FUNCTION_WORD_MIN = 0.07

# Second rescue: accept if 'en' is a near-tie runner-up. Notation-dense English
# (row 168: la -621.6 vs en -623.1) leaves langid nearly indifferent, whereas
# genuine non-English wins outright and 'en' falls off the list entirely
# (row 63: pl -313.6, 'en' not in the top 4).
LANGID_MARGIN = 25.0
LANGID_RANK_DEPTH = 5

# langid needs actual words. A field of random tokens (row 72's "aB7c9 dE3f1 ..."
# answer) is language-neutral, not non-English, and langid picks a coin-flip
# winner from Aragonese/Luxembourgish/Occitan. Require this many >=3-char
# alphabetic tokens before trusting any langid verdict.
MIN_PROSE_TOKENS = 8

# Function words chosen to be distinctly English. Deliberately omits forms that
# collide with other languages here (in, an, at, be, was, so, me, la, de, no, on,
# for, or, but, if) so Catalan/German/Spanish/Italian cannot accumulate hits.
FUNCTION_WORDS = frozenset("""
the and of to is are that this these those with which have has had been
not its it as by we you they their there would should could than then
when what how why because about into through also will can may must
my your our his her him them who whose where whether while during
each both between among such other another does did doing done
here very much many more most some any every only just even still
above below over under after before again always never
""".split())

WORD_RE = re.compile(r"[a-z']+")
PROSE_TOKEN_RE = re.compile(r"[a-z]{3,}")


def clean(text):
    """Strip code, LaTeX, URLs and markdown so langid sees prose, not notation."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\\\[.*?\\\]|\\\(.*?\\\)", " ", text, flags=re.S)
    text = re.sub(r"\$\$.*?\$\$", " ", text, flags=re.S)
    text = re.sub(r"\$[^$\n]{0,200}\$", " ", text)
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", text)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"[#*_>|~\[\]{}=+/\\^]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_langid(text):
    """Lowercase and drop digits/math/emoji, the two langid failure modes here.

    Uppercase English classifies as Norwegian (IF-RLVR all-caps rows); dense
    numeric and operator runs classify as Latin or Lao (chemistry rows).
    """
    text = text.lower()
    text = re.sub(r"[0-9]+", " ", text)
    # superscripts, sub/superscript digits, arrows, comparison and math operators
    text = re.sub(r"[\u2070-\u209f\u2190-\u21ff\u2200-\u22ff\u00b0\u00b1\u00d7\u00f7]", " ", text)
    text = re.sub(r"[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def function_word_ratio(text):
    """Fraction of alphabetic tokens that are distinctly-English function words."""
    words = WORD_RE.findall(text.lower())
    if len(words) < 10:
        return 0.0
    return sum(w in FUNCTION_WORDS for w in words) / len(words)


def rubric_text(raw):
    """Concatenate just the criterion strings, so JSON keys don't skew langid."""
    try:
        return " ".join(c.get("criterion", "") for c in json.loads(raw))
    except (json.JSONDecodeError, TypeError, AttributeError):
        return ""


def judge(row):
    """Return (is_english, per_field_detail) for one row."""
    fields = {
        "query": row["query"] or "",
        "answer": row["answer"] or "",
        "rubrics": rubric_text(row["rubrics"] or ""),
    }
    detail, english = {}, True
    for name, raw in fields.items():
        if not raw:
            detail[name] = {"lang": "empty", "ok": True}
            continue

        ratio = len(NON_LATIN.findall(raw)) / len(raw)
        norm = normalize_for_langid(clean(raw))
        fw = function_word_ratio(norm)

        prose_tokens = len(PROSE_TOKEN_RE.findall(norm))
        margin = None
        if len(norm) < MIN_CHARS_FOR_LANGID:
            lang, score, verdict = "too-short", 0.0, "script-only"
        elif prose_tokens < MIN_PROSE_TOKENS:
            lang, score, verdict = "no-prose", 0.0, "script-only"
        else:
            ranked = langid.rank(norm[:LANGID_PREFIX])[:LANGID_RANK_DEPTH]
            lang, score = ranked[0]
            score = float(score)
            en = next((float(s) for l, s in ranked if l == "en"), None)
            margin = None if en is None else round(score - en, 1)
            verdict = "langid"

        script_ok = ratio <= SCRIPT_RATIO_MAX
        neutral = lang in ("en", "too-short", "no-prose")
        lang_ok = (
            neutral
            or fw >= FUNCTION_WORD_MIN
            or (margin is not None and margin <= LANGID_MARGIN)
        )
        if not neutral and lang_ok:
            verdict = (
                "rescued-by-function-words"
                if fw >= FUNCTION_WORD_MIN
                else "rescued-by-en-near-tie"
            )

        detail[name] = {
            "lang": lang,
            "score": round(float(score), 1),
            "nonlatin": round(ratio, 5),
            "fw": round(fw, 3),
            "en_margin": margin,
            "via": verdict,
            "ok": script_ok and lang_ok,
        }
        english = english and detail[name]["ok"]
    return english, detail


def main():
    argv = set(sys.argv[1:])
    report_only = "--report" in argv
    diag = "--diag" in argv

    with open(SRC, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames
        rows = list(reader)

    keep, drop = [], []
    for i, row in enumerate(rows):
        ok, detail = judge(row)
        (keep if ok else drop).append((i, row, detail))

    print(f"input:   {len(rows)} rows")
    print(f"english: {len(keep)} rows")
    print(f"dropped: {len(drop)} rows\n")

    print("--- dropped ---")
    for i, row, detail in drop:
        bad = {
            f: f"{d['lang']}(script={d['nonlatin']}, fw={d['fw']}, en_margin={d['en_margin']})"
            for f, d in detail.items()
            if not d.get("ok", True)
        }
        print(f"  row {i:>3}  {row['source']:<40} {bad}")

    rescued = [
        (i, f, d)
        for i, _, detail in keep
        for f, d in detail.items()
        if str(d.get("via", "")).startswith("rescued")
    ]
    if rescued:
        print("\n--- kept despite non-'en' langid verdict (false positives) ---")
        for i, f, d in rescued:
            print(
                f"  row {i:>3}  {f:<8} langid={d['lang']:<4} fw={d['fw']:<6}"
                f" en_margin={str(d['en_margin']):<7} via={d['via']}"
            )

    kept_by_source = Counter(r["source"] for _, r, _ in keep)
    print("\n--- kept per source ---")
    for src in sorted(set(r["source"] for r in rows)):
        print(f"  {src:<40} {kept_by_source.get(src, 0):>3}/20")

    if diag:
        print("\n--- all rows ---")
        for i, row, detail in sorted(keep + drop):
            marks = " ".join(
                f"{f[0]}:{d['lang']}/{d['fw']}" for f, d in detail.items()
            )
            print(f"  {i:>3} {'EN ' if detail else '':<3} {marks}")

    if report_only:
        print("\n(--report: no files written)")
        return

    for path, subset in ((DST, keep), (DST_DROPPED, drop)):
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=header)
            writer.writeheader()
            writer.writerows(row for _, row, _ in subset)
        print(f"\nwrote {path}  ({len(subset)} rows)")


if __name__ == "__main__":
    main()
