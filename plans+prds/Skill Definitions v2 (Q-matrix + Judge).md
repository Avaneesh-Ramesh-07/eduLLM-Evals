# Skill Definitions v2 — Content / Diagnosis / Scaffolding

**Status:** canonical. This is the single source of truth for the three latent tutoring
skills used across (a) Q-matrix generation & verification, (b) human Q-matrix review, and
(c) the LLM judge. When any prompt or script defines these skills, it must match this file.

**Why v2:** the blind 3-reviewer Q-matrix audit (`qmatrix_human_review/`) exposed that the
v1 definitions were reliable for `content` (Fleiss κ = 0.85) and `scaffolding` (κ = 0.59)
but **not** for `diagnosis` (κ = 0.365, 64% unanimous). Almost every `diagnosis`
disagreement traced to one ambiguity: whether *acknowledging* a student's confusion counts
as diagnosing it. v2 resolves that and a handful of related edge cases with explicit rules
and worked examples drawn from the adjudicated criteria.

**Sync targets (keep identical to this file):**
- `scripts/generate_qmatrix.py` → `SYSTEM_PROMPT` "THE THREE SKILLS" block (also used by
  `scripts/verify_qmatrix.py`).
- `scripts/prepare_qmatrix_human_review.py` → `SKILL_DEFINITIONS`.
- The LLM judge prompt (when frozen for calibration).

---

## The core decision rule (unchanged)

> Mark a skill **1** only if a competent tutor **could not reliably satisfy the criterion
> without exercising that skill.** Default to **0**. Apply the counterfactual test: *"Could
> a tutor lacking only this skill still satisfy the criterion as written?"* If yes → 0.

A criterion may load several skills, or **none** (all-zero). Judge the criterion **as
literally written**, not the ideal tutoring behavior it evokes.

---

## 1-placement policy (conservative default + data-driven promotion)

**Decision:** stay **conservative** when labeling; do **not** blanket-liberalize `1`s. Recover
missed loadings from the data, not from guessing. Rationale — the two error types are *not*
symmetric at our calibration N (~100 common-person models):

- A **false 0** fixes that discrimination cell to exactly 0, so the item can't inform the
  dimension — but it is **recoverable through a well-lit channel**: exploratory factor analysis
  (free loadings) and modification-index / item-misfit tests flag an item that wants to load on
  a suppressed dimension. You flip `0→1` and re-fit (cheap; reuses all tutor + judge data).
- A **false 1** adds a discrimination parameter estimated from only ~100 observations. At that
  N, power to confirm a spurious `a ≈ 0` is low, so "prune it later" is unreliable; meanwhile it
  **worsens identifiability/collinearity** (pushes the latent correlations toward 1) and
  contaminates the θ estimates. Its damage is **systemic and hard to detect**.

So: false-0 damage is local and easy to catch; false-1 damage is systemic and hard to catch.
The instrument stays cleaner if we start conservative and let evidence promote `0→1`.

**Operating rules:**

1. **Default conservative.** Apply the counterfactual test; when a skill is not genuinely
   required, mark `0`.
2. **False-0 insurance = the data loop, not pre-emptive inclusion.** After the confirmatory
   fit, run EFA + misfit diagnostics, promote the flagged `0→1` cells, and re-fit.
3. **"Tie → 1" only on genuine coin-flips, and skill-aware:**
   - `scaffolding` — most headroom to lean `1` on real borderlines (least entangled,
     best-separated dimension).
   - `content` / `diagnosis` — stay **strict** on borderlines; do not add cross-loadings that
     worsen the content↔diagnosis collinearity (already ~82% overlap).
4. **Don't zealously zero a genuinely-implicated skill.** The asymmetry cuts against stripping,
   e.g., `diagnosis` off a real error-engagement item just to keep rows tidy — that is a false 0.

---

## The three skills

### `content` — subject-matter correctness
The criterion turns on accurate domain knowledge: correct facts, definitions, computations,
formulas, units, or solution steps.

- **Load 1 when** the criterion checks that a domain claim is *correct* — including when the
  tutor must supply a correct answer, a correct hint, a correct formula, or a correct
  *correction* of the student.
- **Do not load** for tone, formatting, or conversational moves that don't hinge on any
  domain fact.

### `diagnosis` — reading the student's *specific* error
Identifying, reasoning about, or acting on **this student's** particular misconception, error,
knowledge gap, or state of understanding, inferred from what they said or did.

- **Load 1 when** the criterion requires pinpointing, *or* correcting/addressing, the student's
  specific mistake or misunderstanding (e.g., "identify that the student added the
  denominators", "correct the student's error in …", "recheck the abbreviations *they* used").
- **Broad "address-the-error" rule (adopted).** *Any* phrasing that asks the tutor to correct
  or address **the student's** error loads `diagnosis` — even when the criterion does not spell
  out the specific misconception. Engaging the student's error at all presupposes reading it. We
  deliberately accept the resulting higher content↔diagnosis entanglement rather than risk false
  0s on genuine error-engagement items. **Contrast:** a *generic* "provide the correct solution"
  with no reference to the student's mistake is `content` only, not `diagnosis`.
- **Do NOT load for generic empathy or acknowledgement.** Merely acknowledging that the
  student is confused / worried / frustrated — without engaging *what specifically* they got
  wrong — is a conversational/affective move and requires **no skill** (all-zero). This is the
  single most important v2 change.
- Solving the problem correctly, with no reference to the student's specific error, is
  `content`, not `diagnosis`.

### `scaffolding` — pedagogical structuring of the help
Structuring guidance so the student makes progress: hints instead of answers, deliberately
withholding the solution, decomposing into steps, asking guiding questions, sequencing
support, "explain (not just state) why …".

- **Load 1 when** the criterion requires the *form* of the help to be pedagogically
  structured (a hint/question rather than the full answer; withholding; a worked
  decomposition; an explanation rather than a bare assertion).
- **Do not load** for merely stating a correct fact, nor for an **optional** enrichment
  check ("*can* include an additional check"). Correcting an error is not, by itself,
  scaffolding.

### all-zero — no skill strictly required
Tone/affect/empathy, formatting, spelling/orthography, and conversational moves
(acknowledge confusion, check for understanding, offer further help, be encouraging) load
**no** skill. `primary_skill` is `null`. These items are kept for critical-failure
monitoring but do not discriminate among the three abilities in MIRT.

### `primary_skill`
The single most central skill among those marked 1. It **must** be one of the marked skills,
and is `null` **iff** all three are 0.

---

## Worked examples (from the adjudicated audit)

| Criterion (gist) | content | diagnosis | scaffolding | Why |
|---|:--:|:--:|:--:|---|
| "acknowledge the student's confusion" (`tb_0113`, `tb_0125`, `tb_0120`, `tb_0129`, `tb_0268`) | 0 | **0** | 0 | Generic acknowledgement/empathy — no specific error read. All-zero. |
| "check for understanding / offer further help" (`tb_0006`) | 0 | 0 | 0 | Conversational closing move. All-zero. |
| "correct spelling of *Le Chatelier's*" (`tb_0464`) | 0 | 0 | 0 | Orthography check. All-zero. |
| "state the ionic dissociation of both reactants" (`tb_0016`) | **1** | 0 | 0 | Stating a correct fact is content, not scaffolding. |
| "*can* include an additional conceptual check" (`tb_0056`) | **1** | 0 | 0 | Optional content enrichment; "can" ≠ required scaffolding. |
| "give a hint (question/suggestion) to consider each partition component" (`tb_0576`) | 0 | 0 | **1** | Only the hint *structure* is required; domain correctness not checked. |
| "explain, **not just state**, why zero relative velocity" (`tb_0215`) | **1** | 0 | **1** | Correct physics (content) delivered as an explanation (scaffolding). |
| "hint so the student can **identify** what the type error means" (`tb_0620`) | **1** | **1** | **1** | Hint (scaffolding) that targets the student's specific error (diagnosis) and must be correct (content). |
| "**correct the student's** wrong y-intercept term → a(V−b)/(RV²)" (`tb_0047`) | **1** | **1** | 0 | Right term (content) + pinpoint the specific error (diagnosis); correcting isn't scaffolding. |
| "suggest the student **recheck the abbreviations they used**" (`tb_0554`) | **1** | **1** | **1** | Know the correct abbreviations (content), spot the student's wrong ones (diagnosis), nudge via suggestion (scaffolding). |

---

## Quick decision aids

**diagnosis in one line:** *does the criterion require reading THIS student's specific
mistake?* Acknowledging a feeling ≠ diagnosing an error.

**scaffolding in one line:** *does the criterion constrain the FORM of the help* (hint /
withhold / steps / guiding question / explain-not-state)? A bare correct statement isn't
scaffolding; an *optional* addition isn't required.

**content in one line:** *does correctness of a domain claim get checked?* Correct answers,
correct hints, and correct corrections all count.

---

## Paste-ready block A — Q-matrix generator / verifier (`SYSTEM_PROMPT`)

Replaces the "THE THREE SKILLS" section in `scripts/generate_qmatrix.py:SYSTEM_PROMPT`.
Bump `PROMPT_VERSION` when adopted (e.g. `qmatrix-v4-3skill-diagnosis-sharpened`).

```text
THE THREE SKILLS (mark 1 only if the criterion cannot be satisfied without the skill; default 0)
- content: Subject-matter correctness -- correct facts, definitions, computations,
  formulas, or solution steps. Load it whenever the criterion checks that a domain claim is
  correct, INCLUDING supplying a correct answer, a correct hint, or a correct correction.
    Positive: "The response correctly computes the second derivative."
    Negative: a criterion purely about tone/formatting with no domain fact at stake.
- diagnosis: Reading, or acting on, THIS student's SPECIFIC error, misconception, knowledge
  gap, or state of understanding from what they said or did.
    Positive: "The response identifies that the student added the denominators."
    Positive (broad address-the-error rule): ANY criterion asking the tutor to correct or
    address THE STUDENT'S error loads diagnosis, even if it does not name the specific
    misconception (engaging the student's error presupposes reading it).
    Negative (content only): a GENERIC "provide the correct solution" with no reference to the
    student's mistake is content, not diagnosis.
    Negative (all-zero): "acknowledge the student's confusion" -- generic empathy that does
    NOT engage a specific mistake requires NO skill. Merely acknowledging a feeling is not
    diagnosis.
- scaffolding: Pedagogical structuring of the help -- hints instead of answers, withholding
  the solution, decomposing into steps, guiding questions, or "explain (not just state) why".
    Positive: "The response gives a hint without revealing the full solution."
    Negative: "The response states the correct fact" (content, not structuring); an OPTIONAL
    extra check ("can include ...") is not required scaffolding; correcting an error is not,
    by itself, scaffolding.

PLACEMENT: default to 0. Be conservative -- missed loadings are recovered later from the
calibration data (EFA / misfit), whereas spurious 1s hurt dimensional separability. On a
genuine coin-flip you may lean 1 for scaffolding, but stay strict for content/diagnosis.

A criterion may load several skills or NONE. All-zero cases include tone/affect/empathy,
formatting, spelling, and conversational moves (acknowledge confusion, check for
understanding, offer further help).
```

## Paste-ready block B — LLM judge

The judge grades each criterion **pass/fail on the criterion as written** and does not need
to output a skill label. Include this only so borderline criteria are interpreted
consistently with the Q-matrix instrument:

```text
Interpret each criterion literally and score PASS only if the response satisfies exactly
what the criterion asks. Do not import extra tutoring expectations. In particular:
- A criterion that only asks the tutor to acknowledge the student's confusion/feeling passes
  on the acknowledgement alone; it does NOT require identifying the student's specific error.
- A criterion asking for a hint/question (rather than the full answer) fails if the response
  reveals the full answer instead of the requested structured help.
- A criterion asking to correct the student's specific error requires addressing THAT error,
  not a generic restatement of the correct solution.
```

---

## Changelog

- **v2.1** (this file): adopted the **broad "address-the-error" rule** — any criterion asking
  the tutor to correct/address the student's error loads `diagnosis` (a generic correct answer
  with no reference to the student's mistake stays `content` only). Added the **1-placement
  policy**: conservative default, false-0s recovered via the EFA/misfit data loop + cheap
  re-fit, skill-aware "tie→1" (lenient `scaffolding`, strict `content`/`diagnosis`). Grounded in
  the observed content↔diagnosis collinearity (~82%) at the ~100-model calibration N.
- **v2**: `diagnosis` sharpened — acknowledging confusion/affect is all-zero, not
  diagnosis; clarified content applies to correct hints/corrections; clarified scaffolding
  excludes bare statements and optional enrichment; added all-zero coverage for
  spelling/conversational moves. Grounded in the `human_review_v1` adjudication and the
  `qmatrix_rulefix_v1` sweep (`qmatrix_human_review/patch_report.md`).
- **v1**: original 3-skill definitions in `generate_qmatrix.py` (`qmatrix-v3-3skill-primary`).
