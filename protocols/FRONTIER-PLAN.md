# Frontier Reviewer — Design and Execution Plan

This document records the design decisions for the closed-model (frontier) reviewer
extension, which runs Claude Sonnet through the Anthropic API.

---

## 1. Why a closed reviewer

The main study uses open-weight models whose weights, sampling parameters, and random
seeds can be fully controlled. The frontier extension tests whether the same framing
effect appears in a production-grade API model — a setting closer to real-world
deployment, where the reviewer is a black box.

---

## 2. Model and configuration

| Setting | Value |
|---------|-------|
| Model | Claude Sonnet (`claude-sonnet-4-6`) |
| Inference | Anthropic Messages API |
| System prompt | Same neutral reviewer prompt as the main study (`SYSTEM_PROMPT` in `src/reviewer.py`) |
| Conditions | All four: `hedged`, `confident`, `confident_extra_neutral`, `unsupported_claims` |
| Runs per cell | 5 |
| Temperature | 0.7 |
| Max tokens | 512 |

Configuration: `conf/model/claude_sonnet.yaml`.

---

## 3. Differences from the open-weight setup

**No seed control.** The Anthropic API does not accept a random seed, so individual
trials are not bit-reproducible. The five runs per cell still estimate per-cell
approval probability and separate framing from sampling noise. The full raw response
is stored to the raw JSON tree for auditability.

**No bug-question probe.** The probe reads token log-probabilities for "yes"/"no"
after a single forward pass (`src/reviewer.py BugQuestionScorer`). The Anthropic API
does not expose log-probabilities, so the probe is not run for the frontier reviewer.

**Retry logic.** The API reviewer owns its own retry loop (exponential backoff with
jitter) for transient failures (connection drops, 429 rate limits, 5xx). Terminal
errors (401 auth, 400 credit balance) propagate immediately. See
`src/api_reviewer.py APIReviewer._create_with_retry`.

**No mitigation arms.** The prompt-variant and diff-only arms (RQ4) are open-weight
experiments. The frontier extension uses the main-study neutral prompt only.

---

## 4. Running the frontier reviewer

Requires `pip install anthropic` and `ANTHROPIC_API_KEY` in the environment.

```bash
# Run all patches, all conditions, 5 runs each (one sequential process):
python run_experiment.py model=claude_sonnet data=frontier_bad mode=run_all
python run_experiment.py model=claude_sonnet data=frontier_good mode=run_all

# Consolidate into verdicts:
python run_experiment.py model=claude_sonnet data=frontier_bad mode=consolidate
python run_experiment.py model=claude_sonnet data=frontier_good mode=consolidate
```

Results land in `results/frontier/` as configured in
`conf/data/frontier_bad.yaml` and `conf/data/frontier_good.yaml`.

---

## 5. Analysis

The frontier verdicts are analysed alongside the open-weight results in
`src/sdt.py` and `src/analyze.py`. Processed outputs are in `results/frontier/`.
