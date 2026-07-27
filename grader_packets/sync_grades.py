#!/usr/bin/env python3
"""Sync P/F grades and notes from a grader packet .md into its companion .csv.

The .md is the human-readable grading surface; the .csv is the official sheet.
Grades are matched by criterion_id (the `#### tb_...` headers in the md map to the
`criterion_id` column in the csv), so row order does not matter.

Usage:
    python3 sync_grades.py grader_03            # dry run: report only, no changes
    python3 sync_grades.py grader_03 --write    # write grades into grader_03.csv
    python3 sync_grades.py --all                # dry run over every grader_*.md
    python3 sync_grades.py --all --write
"""
import csv
import re
import sys
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

CRITERION_BLOCK = re.compile(r"^####\s+(\S+)\s*$(.*?)(?=^#{1,4}\s|\Z)", re.MULTILINE | re.DOTALL)
GRADE_LINE = re.compile(r"^-\s*Grade\s*\(P/F\):\s*(.*)$", re.MULTILINE)
NOTES_LINE = re.compile(r"^-\s*Notes:\s*(.*)$", re.MULTILINE)


def _blank(value: str) -> bool:
    """True for empty or placeholder underscores like ____."""
    return value == "" or set(value) <= {"_"}


def parse_md(md_path: pathlib.Path) -> dict:
    text = md_path.read_text(encoding="utf-8")
    out = {}
    for m in CRITERION_BLOCK.finditer(text):
        cid = m.group(1).strip()
        block = m.group(2)
        gm = GRADE_LINE.search(block)
        nm = NOTES_LINE.search(block)
        grade = gm.group(1).strip() if gm else ""
        notes = nm.group(1).strip() if nm else ""
        if _blank(grade):
            grade = ""
        if _blank(notes):
            notes = ""
        out[cid] = {"grade": grade, "notes": notes}
    return out


def sync(stem: str, write: bool) -> None:
    md_path = HERE / f"{stem}.md"
    csv_path = HERE / f"{stem}.csv"
    if not md_path.exists() or not csv_path.exists():
        print(f"{stem}: SKIP (missing {'md' if not md_path.exists() else 'csv'})")
        return

    md_grades = parse_md(md_path)

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    csv_ids = {row["criterion_id"] for row in rows}
    filled, still_blank, bad = 0, [], []
    for row in rows:
        cid = row["criterion_id"]
        g = md_grades.get(cid)
        if g is None or not g["grade"]:
            still_blank.append(cid)
            continue
        grade = g["grade"].upper()
        if grade not in ("P", "F"):
            bad.append((cid, g["grade"]))
            continue
        row["grade"] = grade
        row["notes"] = g["notes"]
        filled += 1

    print(f"{stem}: {filled}/{len(rows)} graded, {len(still_blank)} still blank")
    only_in_md = sorted(set(md_grades) - csv_ids)
    if only_in_md:
        print(f"  WARNING: in md but not in csv: {only_in_md}")
    if bad:
        print(f"  WARNING: unrecognized grades (expected P or F): {bad}")
    if still_blank and len(still_blank) <= 60:
        print(f"  not yet graded: {still_blank}")

    if write:
        if bad:
            print("  -> NOT writing: fix the unrecognized grades above first.")
            return
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        print(f"  -> wrote {filled} grades into {csv_path.name}")


def main(argv):
    args = [a for a in argv if not a.startswith("-")]
    write = "--write" in argv
    if "--all" in argv:
        stems = sorted({p.stem for p in HERE.glob("grader_*.md")})
    elif args:
        stems = [a.removesuffix(".md").removesuffix(".csv") for a in args]
    else:
        print(__doc__)
        return
    for stem in stems:
        sync(stem, write)


if __name__ == "__main__":
    main(sys.argv[1:])
