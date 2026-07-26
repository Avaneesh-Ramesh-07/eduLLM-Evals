"""Rebuild rurbichub_v1_sample_220rows.csv as an ENGLISH-ONLY stratified subset.

Language detection is pushed into DuckDB's vectorized RE2 regex (Unicode script
classes) rather than a per-row Python UDF, which is orders of magnitude faster
over the 156k-row / 2 GB parquet. A row is "English" when its query carries
enough Latin letters and effectively no characters from the non-Latin scripts
that dominate the multilingual rows here (CJK, Cyrillic, Arabic, etc.).
"""
import duckdb
import os

SRC = "rurbichub_v1_6samples_156k_sft_data.parquet"
OUT = "rurbichub_v1_sample_220rows.csv"
TMP = "rurbichub_v1_sample_220rows.tmp.csv"

# scripts whose presence marks non-English text in this corpus
_NONLATIN = (
    r"[\p{Han}\p{Hiragana}\p{Katakana}\p{Hangul}\p{Cyrillic}"
    r"\p{Arabic}\p{Hebrew}\p{Thai}\p{Devanagari}]"
)
# judge on a 2000-char prefix; latin>=20 and non-latin fraction <2%
ENGLISH = (
    "length(regexp_replace(substr(query,1,2000), '[^A-Za-z]', '', 'g')) >= 20 "
    "AND (length(substr(query,1,2000)) "
    f"    - length(regexp_replace(substr(query,1,2000), '{_NONLATIN}', '', 'g'))) "
    "    < 0.02 * length(regexp_replace(substr(query,1,2000), '[^A-Za-z]', '', 'g'))"
)


def main() -> None:
    con = duckdb.connect()
    con.execute(f"""
      COPY (
        SELECT source, query, answer, sample_id,
               to_json(rubrics) AS rubrics,
               rubric_score,
               to_json(rubric_judge_details) AS rubric_judge_details
        FROM (
          SELECT *, row_number() OVER (PARTITION BY source ORDER BY sample_id) AS rn
          FROM read_parquet('{SRC}')
          WHERE {ENGLISH}
        )
        WHERE rn <= 20
        ORDER BY source, rn
      ) TO '{TMP}' (HEADER, DELIMITER ',', QUOTE '"', ESCAPE '"')
    """)
    rows = con.execute(f"""
      SELECT source, count(*) FROM (
        SELECT source, row_number() OVER (PARTITION BY source ORDER BY sample_id) AS rn
        FROM read_parquet('{SRC}') WHERE {ENGLISH}
      ) WHERE rn <= 20 GROUP BY source ORDER BY source
    """).fetchall()
    con.close()

    os.replace(TMP, OUT)
    total = sum(c for _, c in rows)
    print(f"wrote {OUT}: {total} English rows across {len(rows)} sources")
    for s, c in rows:
        print(f"  {c:3d}  {s}")
    print("size:", os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
