# Q-matrix human review

Send each reviewer only their matching `reviewer_*.html` file. Do not send
`coordinator_manifest.json`; it contains the AI mappings and would unblind the review.

Each reviewer opens the HTML locally, completes 20 criteria, and clicks **Validate and
download CSV**. Progress is saved in that browser. The blank CSV is only a spreadsheet
fallback.

Return the three exported files as `qmatrix_review_A.csv`, `qmatrix_review_B.csv`, and
`qmatrix_review_C.csv`. Every sampled criterion has at least two ratings; the ten CORE
criteria have three ratings. Resolve disagreements only after all independent reviews are
complete.
