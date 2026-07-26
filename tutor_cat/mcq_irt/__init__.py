"""Unidimensional 2PL calibration + CAT for MCQ benchmarks, built directly from
the existing loglikelihood right/wrong grids in the mcq/ CSVs.

Pure-analysis package (numpy/scipy/pandas/girth, no torch/vllm/GPU): read a
per-benchmark model x item 0/1 matrix, calibrate a 2PL item bank, and run a
Fisher-information CAT on a held-out set of models.

Layering: `ability` and `cat` are pure numpy (unit-testable with no I/O);
`matrix` reads the CSVs; `calibrate` wraps girth's 2PL MML; `pipeline` wires
the stages end to end.
"""
