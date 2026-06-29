# Detection Judge Protocol (Phase 6, step 2)

*Did the review mention the known bug?* — scored separately from the verdict.

This protocol adapts the LLM-as-judge methodology of **SWE-PRBench**
(arXiv:2603.26130; harness `github.com/FoundryHQ-AI/swe-prbench`, pipeline
v0.4.1, MIT license). Their judge matches AI review comments against
human-written ground-truth comments and was validated against blind human
rubric application at **κ = 0.75** (N=30, 83.3% exact agreement), with a
second-judge cross-validation at κ = 0.616. Reusing their rubric and labels
keeps our method citable and keeps us protocol-compatible with the provenance
project, which shares the same benchmark.

## Adaptation to this study

SWE-PRBench scores many agent comments against many human comments
(CONFIRMED / PLAUSIBLE / FABRICATED per agent comment, CAUGHT / MISSED per
human comment). Our setting is simpler: every patch has exactly **one**
ground-truth issue (`known_issue` in `metadata.json`), so we keep only their
ground-truth-side labels:

| Label | Meaning (from SWE-PRBench RUBRIC.md, single-issue form) |
|-------|----------------------------------------------------------|
| `CAUGHT` | The review identifies the **same underlying issue** — same root cause, same file/functional area, and the changes it requests would fix that bug. Different phrasing or abstraction level still counts. |
| `MISSED` | Anything else: the review is silent on the bug, raises only unrelated concerns, or describes a different root cause. |

## What the judge sees — and does not see

**Sees:** the diff, the task description, `known_issue`, and the review's
`problems_found`, `main_reason`, and `suggested_changes` fields.

**Does not see:** the reviewer's `verdict` or `confidence`, the PR description
condition, or which model wrote the review. This prevents the approval
decision from leaking into the detection judgment — the dissociation between
the two is the result we are measuring.

## Judge model and settings

- Default judge: **`claude-sonnet-4-6`** — the model SWE-PRBench used for
  their κ = 0.75 validation pass, chosen for protocol fidelity. Also from a
  different model family than both reviewers (Llama, Qwen), avoiding
  self-preference bias. Overridable via `--model`.
- Temperature 0 (their convention), structured JSON output, one request per
  trial via the Message Batches API.
- Implementation: `src/judge_detection.py`; output: `results/detection.csv`.

## Validation plan (required before reporting)

SWE-PRBench's κ vouches for their data, not ours. Before the detection
numbers go into RESULTS.md:

1. Stratified sample of **~150 trials** (oversample the critical cell:
   unsupported_claims × approved), hidden judge labels.
2. One human codes CAUGHT/MISSED using the rubric above, blind.
3. Report Cohen's κ between human and judge. Target κ ≥ 0.7; below that,
   tighten the rubric and re-run.

## Provenance-project compatibility

Decisions the sister project should mirror (or veto with a reason):
single-issue CAUGHT/MISSED labels, verdict-blind judging, judge model +
temperature 0, and the κ-validation sample design above.
