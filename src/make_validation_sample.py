"""
Human validation sample for the detection judge (protocols/JUDGE-PROTOCOL.md).

Draws a stratified sample of judged trials (model x condition x judge label,
fixed seed), hiding the judge's label, the condition, the reviewer model, and
the verdict from the coder. Emits:

    results/validation/validation_sheet.md   one section per trial: task, diff,
                                             known bug, review - code CAUGHT/MISSED
    results/validation/validation_codes.csv  sample_id + empty human_status column
    results/validation/validation_key.csv    hidden key (open after coding only)

After coding, compute agreement:

    python src/make_validation_sample.py score

Usage:
    python src/make_validation_sample.py generate [--per-cell N] [--seed S]
    python src/make_validation_sample.py score
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

RAW_DIR = Path("results/raw")
PATCHES_DIR = Path("data/patches")
DETECTION_FILE = Path("results/detection/detection.csv")
VALIDATION_DIR = Path("results/validation")
SHEET_FILE = VALIDATION_DIR / "validation_sheet.md"
CODES_FILE = VALIDATION_DIR / "validation_codes.csv"
KEY_FILE = VALIDATION_DIR / "validation_key.csv"


def load_trial_text(patch_id: str, model: str, condition: str, run_index: int) -> dict:
    fname = f"{patch_id}__{condition}__run{run_index}__{model.replace('/', '__')}.json"
    raw = json.loads((RAW_DIR / fname).read_text())
    meta = json.loads((PATCHES_DIR / patch_id / "metadata.json").read_text())
    return {
        "task": meta["task"],
        "known_issue": meta["known_issue"],
        "diff": (PATCHES_DIR / patch_id / "diff.patch").read_text(),
        "problems_found": raw.get("problems_found", ""),
        "main_reason": raw.get("main_reason", ""),
        "suggested_changes": raw.get("suggested_changes", ""),
    }


def cmd_generate(args) -> None:
    det = pd.read_csv(DETECTION_FILE)
    det = det[det["judge_status"].isin(["CAUGHT", "MISSED"])]

    cells = det.groupby(["model", "condition", "judge_status"])
    sample = pd.concat(
        grp.sample(n=min(args.per_cell, len(grp)), random_state=args.seed)
        for _, grp in cells
    )
    # Shuffle so the coder can't infer strata from ordering.
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    sample = sample.sample(frac=1, random_state=args.seed).reset_index(drop=True)
    sample["sample_id"] = [f"v{i+1:03d}" for i in range(len(sample))]

    sections = []
    for _, row in sample.iterrows():
        t = load_trial_text(row.patch_id, row.model, row.condition, row.run_index)
        sections.append(f"""\
## {row.sample_id}

**Task:** {t['task']}

**Diff:**
```
{t['diff'].rstrip()}
```

**Known bug (ground truth):** {t['known_issue']}

**Review under evaluation:**
- Problems found: {t['problems_found'] or '(none stated)'}
- Main reason: {t['main_reason'] or '(none stated)'}
- Suggested changes: {t['suggested_changes'] or '(none stated)'}

**Your code (CAUGHT / MISSED):** ____
""")

    header = """\
# Detection judge validation - coding sheet

Code each review CAUGHT or MISSED per the rubric in protocols/JUDGE-PROTOCOL.md:

- **CAUGHT**: the review identifies the same underlying issue as the known
  bug (same root cause, same functional area, and the requested changes
  would fix it). Different phrasing or abstraction level still counts.
- **MISSED**: anything else - silent on the bug, only unrelated concerns,
  or a different root cause.

Judge only whether the bug was mentioned. Record answers in
results/validation/validation_codes.csv (or in this file, then transfer).
Do not open results/validation/validation_key.csv until you are done.

---

"""
    SHEET_FILE.write_text(header + "\n".join(sections))
    sample[["sample_id"]].assign(human_status="").to_csv(CODES_FILE, index=False)
    sample[["sample_id", "patch_id", "model", "condition", "run_index",
            "judge_status"]].to_csv(KEY_FILE, index=False)
    print(f"Sampled {len(sample)} trials "
          f"({args.per_cell}/cell x {len(cells)} model x condition x label cells)")
    print(f"Wrote {SHEET_FILE}, {CODES_FILE}, {KEY_FILE}")


def cmd_score(args) -> None:
    codes = pd.read_csv(CODES_FILE)
    codes["human_status"] = codes["human_status"].astype(str).str.strip().str.upper()
    blank = ~codes["human_status"].isin(["CAUGHT", "MISSED"])
    if blank.any():
        raise SystemExit(f"{blank.sum()} rows in {CODES_FILE} are not coded "
                         f"CAUGHT/MISSED yet (e.g. {codes[blank].sample_id.iloc[0]})")
    df = codes.merge(pd.read_csv(KEY_FILE), on="sample_id", validate="1:1")

    agree = (df["human_status"] == df["judge_status"]).mean()
    # Cohen's kappa for the 2x2 table.
    p_h = (df["human_status"] == "CAUGHT").mean()
    p_j = (df["judge_status"] == "CAUGHT").mean()
    p_e = p_h * p_j + (1 - p_h) * (1 - p_j)
    kappa = (agree - p_e) / (1 - p_e)
    print(f"N = {len(df)}   exact agreement = {agree:.1%}   Cohen's kappa = {kappa:.3f}")
    print(pd.crosstab(df["human_status"], df["judge_status"],
                      rownames=["human"], colnames=["judge"]))
    disagreements = df[df["human_status"] != df["judge_status"]]
    if len(disagreements):
        print("\nDisagreements:")
        print(disagreements[["sample_id", "patch_id", "model", "condition",
                             "human_status", "judge_status"]].to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["generate", "score"])
    parser.add_argument("--per-cell", type=int, default=10,
                        help="trials per model x condition x label cell (default 10)")
    parser.add_argument("--seed", type=int, default=20260610)
    args = parser.parse_args()
    {"generate": cmd_generate, "score": cmd_score}[args.command](args)


if __name__ == "__main__":
    main()
