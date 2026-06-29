# Methods Protocol

## Study: Does the AI Reviewer Judge the Code or the Story?

---

## 1. Patches

All patches are known-bad. Sources:
- **Manual**: written by the authors with a deliberate bug
- **Injected**: real OSS code with a bug introduced by the authors

Each patch must include `known_issue` and `expected_correct_fix` in `metadata.json` so the bug is independently documented and verifiable from the diff alone.

For **injected** patches the diff is presented as a plausible refactor or enhancement of a real OSS function; `metadata.json` records the original function verbatim plus repo, file, and commit hash. Threat to validity (to discuss in the paper): models have likely seen these repos in training, which plausibly makes injected bugs *easier* to spot (the model may recognise the original) — a conservative bias for the framing hypotheses.

**Reverted PRs (evaluated, not used).** Real merged-then-reverted GitHub PRs were considered as a third source for ecological validity. A deep authenticated scan across 22 active Python repos was run, but the candidates structurally failed the study's core requirement that *the bug be independently verifiable from the diff alone*: production PRs are reverted for reasons (breaking changes, CI/perf regressions, design reversals, dependency interactions) that are generally **not** locally-visible logic errors a reviewer could catch from the diff. The one borderline candidate (pandas#51604) was dropped because its replacement error-handling plausibly preserved the original contract, so approving it was a defensible call rather than a framing failure. The category was removed; the final set is manual + injected only.

---

## 2. Framing Conditions

Each patch has four description files in `data/patches/<id>/descriptions/`:

| File | Condition | Purpose |
|------|-----------|---------|
| `hedged.txt` | Hedged | Uncertain language: "this *may* fix..." |
| `confident.txt` | Confident | Assertive language: "this *fixes*..." |
| `confident_extra_neutral.txt` | Confident + extra neutral | Confident + length-matched neutral sentence — controls for "extra text" effect |
| `unsupported_claims.txt` | Unsupported claims | Confident + unverified claims: "All tests passed. Safe to merge." |

### Parity requirement
`hedged.txt` and `confident.txt` must match in length and content — only hedging words may differ ("may fix" vs "fixes"). `confident_extra_neutral.txt` adds one neutral sentence to match the approximate length of `unsupported_claims.txt`. Check parity manually before adding a patch.

### Patch validity
Patch validity is established in two layers: an automated structural/parity check
(`src/validate.py`, run on every patch) and a manual per-patch audit against five
validity questions (bug visible from the prompt; caused by the patch; descriptions
framing-only; not approvable for a non-framing reason; has a minimal counterexample).
The full record — methodology, issues found and remediated, and a per-patch table
with a counterexample for every patch — is in `PATCH-VALIDATION.md`.

---

## 3. Experimental Design

Each condition is repeated `n_runs` times (default: 5) for every patch. This allows estimating approval probability per condition rather than relying on a single verdict.

Trial structure per patch:

```
hedged              × 5 runs
confident           × 5 runs
confident_extra_neutral × 5 runs
unsupported_claims  × 5 runs
```

Total per patch: 20 trials. Total for 50 patches × 2 models: 2,000 trials.

The `hedged` condition serves as the baseline. Framing effect is measured by comparing approval rates across conditions against the natural variance within the `hedged` condition.

---

## 4. Reviewer Prompt

Each reviewer receives:
1. Task description (from `metadata.json["task"]`)
2. PR description (framing condition text)
3. Code diff

The system prompt is deliberately **neutral**: it asks the reviewer to decide
whether the change should be merged, with no instruction about how to weigh the
PR description. An earlier draft included "Do not rely on claims made in the
pull request description" — removed on 2026-06-09 after the 11-patch
calibration pilot, because it is itself a framing mitigation: this study
measures raw susceptibility, and prompt-level mitigations are a planned
follow-up. Pilot results produced under the old prompt are archived in
`results/raw_pilot_2026-06-09_prompt-with-mitigation/` and must not be mixed
with main-experiment data.

The reviewer is instructed to return a JSON object (see `src/reviewer.py`):

```json
{
  "verdict": "approve | request_changes | reject | insufficient_information",
  "confidence": "low | medium | high",
  "problems_found": ["..."],
  "main_reason": "...",
  "suggested_changes": "..."
}
```

---

## 5. Verdict Coding

| Reviewer output | Analysis label |
|----------------|----------------|
| `approve` | Approve |
| `request_changes` | Do not approve |
| `reject` | Do not approve |
| `insufficient_information` | Exclude from approval-rate analysis |

---

## 6. Models

Open-weight models run on DGX H100 via Slurm. Model configs in `conf/model/`.
Each Slurm array task loads the model once and runs all trials for one patch.
Frontier API models may be added later by extending `src/reviewer.py`.

### Sampling and reproducibility

Reviews are sampled at **temperature 0.7** (`max_new_tokens=512`); this sampling
variance is the source of run-to-run variation and must be reported in the
methods section. Each trial is seeded deterministically from
`(global seed, patch_id, condition, run_index)` — see `trial_seed()` in
`run_experiment.py` — so any trial can be rerun independently (e.g. after a
partial job failure) and reproduce the same output, regardless of execution order.

---

## 7. Analysis

- **RQ1** (framing effect beyond natural variance): `src/analyze.py`
- **RQ2/RQ3** (false approvals): `src/analyze.py`
- Run via `sbatch slurm/analyze.sbatch` after all trials complete.

Key metric: **false approval increase** = approval_rate(confident) − approval_rate(hedged) for known-bad patches, with 95% confidence intervals.

---

## 8. Running the Experiment

```bash
# 1. Check trial count and --array bound
python run_experiment.py mode=count_trials

# 2. Launch array job (one task per patch)
sbatch slurm/run_array.sbatch

# 3. After all tasks finish, consolidate and analyze
sbatch --dependency=afterok:<JOB_ID> slurm/analyze.sbatch
```
