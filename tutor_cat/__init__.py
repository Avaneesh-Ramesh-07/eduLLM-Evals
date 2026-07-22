"""CAT-driven MIRT evaluation pipeline for LLM tutors.

Implements the PRD's CAT Integration:
  - Equations 1-3 (M2PL pass probability, uncertainty update, ability update)
  - Fisher-information scenario selection (top-n, uniform seeded pick)
  - Stopping rule (per-skill max SE + min scorable evaluations + max scenarios)
  - LLM-judge grading (one direct pass/fail call per criterion)
"""

__version__ = "0.1.0"

SKILLS = ("content", "diagnosis", "scaffolding")
N_SKILLS = len(SKILLS)
