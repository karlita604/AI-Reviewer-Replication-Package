"""
Analysis for Phase 6b experiment 1 (the direct bug question).

Why this exists. The review data cannot tell "never noticed the bug" apart
from "noticed it but stayed silent" - the review text is generated together
with the verdict. Experiment 1 removes the verdict: the model is asked only
"does this diff contain a bug?" and we read the probability it assigns to
yes vs no. If that probability falls under the unsupported-claims
description, the claims affect detection itself (blind), not just reporting.

Reads <scores-dir>/scores__*.csv (written by mode=bug_question).
Writes <out-dir>/analysis.txt and per_patch_drops.csv.

Usage:
    python src/bug_question_analysis.py                       # Phase 6 (70B)
    python src/bug_question_analysis.py \\
        --scores-glob 'results/phase8/bug_question/scores__*.csv' \\
        --out-dir results/phase8/bug_question                # Phase 8
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

import pandas as pd
from scipy import stats

SCORES_GLOB = "results/bug_question/scores__*.csv"
OUT_DIR = Path("results/bug_question")
CONDITIONS = ["hedged", "confident", "confident_extra_neutral", "unsupported_claims"]


def main() -> None:
    global SCORES_GLOB, OUT_DIR
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scores-glob", default=SCORES_GLOB,
                        help="glob for the per-model bug-question scores CSVs")
    parser.add_argument("--out-dir", default=str(OUT_DIR),
                        help="folder for analysis.txt and per_patch_drops.csv")
    args = parser.parse_args()
    SCORES_GLOB = args.scores_glob
    OUT_DIR = Path(args.out_dir)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.concat([pd.read_csv(f) for f in glob.glob(SCORES_GLOB)], ignore_index=True)
    lines: list[str] = []
    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    expected = df["model"].nunique() * len(CONDITIONS) * 50
    emit(f"Trials: {len(df)} (expected {expected}). "
         f"Coverage min={df['coverage'].min():.4f} mean={df['coverage'].mean():.4f} "
         f"- near 1 means the model answered yes/no and nothing else.")

    emit("\nMean P(yes | 'does this diff contain a bug?') per condition:")
    table = df.pivot_table(index="model", columns="condition",
                           values="p_yes_renorm")[CONDITIONS]
    emit(table.round(3).to_string())

    piv = df.pivot_table(index=["model", "patch_id"], columns="condition",
                         values="p_yes_renorm")
    rows = []
    emit("\nPaired per-patch comparisons against hedged (Wilcoxon signed-rank):")
    for model_name, g in piv.groupby(level="model"):
        short = model_name.split("/")[-1]
        for cond in CONDITIONS[1:]:
            diff = g["hedged"] - g[cond]
            w, p = stats.wilcoxon(g["hedged"], g[cond])
            emit(f"  {short:<26} hedged vs {cond:<24} "
                 f"mean drop={diff.mean():+.4f}  median={diff.median():+.4f}  p={p:.5f}")
        drop = (g["hedged"] - g["unsupported_claims"]).droplevel("model")
        emit(f"  {short}: claims drop is concentrated - "
             f"{(drop > 0.05).sum()} of {len(drop)} patches drop more than 0.05, "
             f"{(drop > 0.2).sum()} drop more than 0.2.")
        for patch_id, d in drop.items():
            rows.append({"model": model_name, "patch_id": patch_id,
                         "p_yes_hedged": g.loc[(model_name, patch_id), "hedged"],
                         "p_yes_claims": g.loc[(model_name, patch_id), "unsupported_claims"],
                         "drop": d})

    emit("\nReading: the probability the model assigns to 'this diff contains a "
         "bug' falls under the fabricated claims even though no verdict is "
         "requested - the claims affect detection itself (the blind account), "
         "concentrated in a minority of patches. Whether a silent component "
         "exists on top is what experiment 2 (problems before verdict) tests.")

    pd.DataFrame(rows).sort_values(["model", "drop"], ascending=[True, False]).to_csv(
        OUT_DIR / "per_patch_drops.csv", index=False)
    (OUT_DIR / "analysis.txt").write_text("\n".join(lines) + "\n")
    print(f"\n[saved] {OUT_DIR}/analysis.txt, per_patch_drops.csv")


if __name__ == "__main__":
    main()
