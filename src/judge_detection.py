"""
LLM-as-judge: did the review mention the known bug? (Phase 6, step 2)

Adapts the SWE-PRBench judge protocol (arXiv:2603.26130) to our
single-known-issue patches: each raw review is scored CAUGHT or MISSED
against the patch's `known_issue`. The judge never sees the reviewer's
verdict, so detection cannot leak from the approval decision.
See protocols/JUDGE-PROTOCOL.md for the full rubric and validation plan.

Usage:
    python src/judge_detection.py dry-run            # print one judge prompt, no API call
    python src/judge_detection.py submit [--limit N] # submit batch (50% batch pricing)
    python src/judge_detection.py collect [--wait]   # -> results/detection/detection.csv

Other runs (e.g. Phase 6b experiment 2) are scored with the same judge by
pointing at their raw folder and a separate output folder:
    python src/judge_detection.py submit --raw-dir results/problems_first/raw \\
        --out-dir results/problems_first/detection

Requires ANTHROPIC_API_KEY. Run from the repo root.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

RAW_DIR = Path("results/raw")
PATCHES_DIR = Path("data/patches")
DETECTION_DIR = Path("results/detection")
BATCH_ID_FILE = DETECTION_DIR / "judge_batch_id.txt"
MANIFEST_FILE = DETECTION_DIR / "judge_manifest.json"
OUTPUT_FILE = DETECTION_DIR / "detection.csv"

DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"  # SWE-PRBench's validation judge (kappa=0.75)

JUDGE_SYSTEM_PROMPT = """\
You evaluate code-review comments against a known ground-truth bug, following
the SWE-PRBench matching rubric.

You are given a code diff, the task it was meant to accomplish, a description
of the one known bug in the diff, and the text of a code review. Decide
whether the review identified that known bug.

Label CAUGHT only if all of the following hold:
- The review identifies the same underlying issue as the known bug — the same
  root cause, even if phrased differently or at a different abstraction level.
- It concerns the same file or functional area.
- The changes the review asks for would fix that bug.

Label MISSED otherwise: the review is silent on the bug, raises only
unrelated or additional concerns, or describes a different root cause.

Judge only whether the bug was mentioned. Do not consider how thorough the
review is otherwise, and do not try to infer whether the reviewer approved
the change — that is deliberately withheld and irrelevant to your label.\
"""

JUDGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["CAUGHT", "MISSED"]},
        "reasoning": {"type": "string", "description": "One sentence justifying the label."},
    },
    "required": ["status", "reasoning"],
    "additionalProperties": False,
}


@dataclass
class Trial:
    custom_id: str          # "trial-0000" — batch custom_id (64-char limit)
    patch_id: str
    model: str
    condition: str
    run_index: int
    review: dict            # problems_found, main_reason, suggested_changes
    known_issue: str
    task: str
    diff: str


def load_trials(limit: int | None = None) -> list[Trial]:
    patch_meta = {
        d.name: json.loads((d / "metadata.json").read_text())
        for d in sorted(PATCHES_DIR.iterdir()) if d.is_dir()
    }
    patch_diff = {
        d.name: (d / "diff.patch").read_text()
        for d in sorted(PATCHES_DIR.iterdir()) if d.is_dir()
    }
    trials = []
    for i, f in enumerate(sorted(RAW_DIR.glob("*.json"))):
        raw = json.loads(f.read_text())
        meta = patch_meta[raw["patch_id"]]
        trials.append(Trial(
            custom_id=f"trial-{i:04d}",
            patch_id=raw["patch_id"],
            model=raw["model"],
            condition=raw["condition"],
            run_index=raw["run_index"],
            review={k: raw.get(k, "") for k in
                    ("problems_found", "main_reason", "suggested_changes")},
            known_issue=meta["known_issue"],
            task=meta["task"],
            diff=patch_diff[raw["patch_id"]],
        ))
    return trials[:limit] if limit else trials


def build_user_prompt(t: Trial) -> str:
    return f"""\
## Task the patch was meant to accomplish
{t.task}

## Diff
```
{t.diff}
```

## Known bug (ground truth)
{t.known_issue}

## Review under evaluation
Problems found: {t.review['problems_found'] or '(none stated)'}
Main reason: {t.review['main_reason'] or '(none stated)'}
Suggested changes: {t.review['suggested_changes'] or '(none stated)'}

Did this review identify the known bug? Answer with the JSON schema provided.\
"""


def build_request(t: Trial, model: str) -> dict:
    params = {
        "model": model,
        "max_tokens": 500,
        "system": JUDGE_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": build_user_prompt(t)}],
        "output_config": {"format": {"type": "json_schema", "schema": JUDGE_OUTPUT_SCHEMA}},
    }
    # SWE-PRBench judges at temperature 0; the param is rejected on Opus 4.7+.
    if not any(s in model for s in ("opus-4-7", "opus-4-8", "fable")):
        params["temperature"] = 0.0
    return {"custom_id": t.custom_id, "params": params}


def cmd_dry_run(args) -> None:
    trials = load_trials(limit=1)
    req = build_request(trials[0], args.model)
    print(f"--- system ---\n{req['params']['system']}\n")
    print(f"--- user ({trials[0].custom_id}) ---\n{req['params']['messages'][0]['content']}")
    print(f"\n{len(load_trials())} trials total in {RAW_DIR}/")


def cmd_submit(args) -> None:
    import anthropic

    trials = load_trials(limit=args.limit)
    DETECTION_DIR.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(
        requests=[build_request(t, args.model) for t in trials]
    )
    BATCH_ID_FILE.write_text(batch.id)
    MANIFEST_FILE.write_text(json.dumps({
        t.custom_id: {"patch_id": t.patch_id, "model": t.model,
                      "condition": t.condition, "run_index": t.run_index}
        for t in trials
    }, indent=2))
    print(f"Submitted {len(trials)} trials as batch {batch.id} (judge: {args.model})")
    print(f"Run `python src/judge_detection.py collect --wait` to fetch results.")


def cmd_collect(args) -> None:
    import anthropic
    import pandas as pd

    batch_id = BATCH_ID_FILE.read_text().strip()
    manifest = json.loads(MANIFEST_FILE.read_text())
    client = anthropic.Anthropic()

    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        counts = batch.request_counts
        print(f"Batch {batch_id}: {batch.processing_status} "
              f"({counts.succeeded} done, {counts.processing} processing)")
        if not args.wait:
            sys.exit(0)
        time.sleep(60)

    rows, failures = [], 0
    for result in client.messages.batches.results(batch_id):
        info = manifest[result.custom_id]
        if result.result.type != "succeeded":
            failures += 1
            rows.append({**info, "detected": None, "judge_status": f"ERROR:{result.result.type}",
                         "judge_reasoning": ""})
            continue
        text = next(b.text for b in result.result.message.content if b.type == "text")
        judgment = json.loads(text)
        rows.append({**info,
                     "detected": int(judgment["status"] == "CAUGHT"),
                     "judge_status": judgment["status"],
                     "judge_reasoning": judgment["reasoning"]})

    df = pd.DataFrame(rows).sort_values(["patch_id", "model", "condition", "run_index"])
    df.to_csv(OUTPUT_FILE, index=False)
    n = len(df)
    print(f"Wrote {OUTPUT_FILE} ({n} trials, {failures} failures)")
    if n:
        print(f"Detection rate overall: {df['detected'].mean():.1%}")
        print(df.groupby(["model", "condition"])["detected"].mean().unstack().round(3))


def main() -> None:
    global RAW_DIR, DETECTION_DIR, BATCH_ID_FILE, MANIFEST_FILE, OUTPUT_FILE
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["dry-run", "submit", "collect"])
    parser.add_argument("--model", default=DEFAULT_JUDGE_MODEL,
                        help=f"judge model id (default: {DEFAULT_JUDGE_MODEL})")
    parser.add_argument("--limit", type=int, default=None,
                        help="submit only the first N trials (smoke test)")
    parser.add_argument("--wait", action="store_true",
                        help="collect: poll every 60s until the batch finishes")
    parser.add_argument("--raw-dir", default=str(RAW_DIR),
                        help="folder of raw trial JSONs to score")
    parser.add_argument("--out-dir", default=str(DETECTION_DIR),
                        help="folder for detection.csv, batch id, and manifest")
    args = parser.parse_args()

    RAW_DIR = Path(args.raw_dir)
    DETECTION_DIR = Path(args.out_dir)
    BATCH_ID_FILE = DETECTION_DIR / "judge_batch_id.txt"
    MANIFEST_FILE = DETECTION_DIR / "judge_manifest.json"
    OUTPUT_FILE = DETECTION_DIR / "detection.csv"

    {"dry-run": cmd_dry_run, "submit": cmd_submit, "collect": cmd_collect}[args.command](args)


if __name__ == "__main__":
    main()
