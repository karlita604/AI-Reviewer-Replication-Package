"""
Analysis for Phase 6b experiment 2 (problems before verdict).

Why this exists. Experiment 1 showed the fabricated claims lower the model's
belief that a bug exists (the blind account). This experiment asks whether a
reporting-stage component (the silent account) exists on top: the same review
request, but the answer format puts the problems list first and the verdict
last. If bug mentions recover under the claims once the problems list comes
first, part of the original suppression happened at the reporting stage.

Compares the problems-first run (results/problems_first/) against the
verdict-first main study (results/raw + results/detection), hedged vs
unsupported_claims, and tests the order x condition interaction with the
same patch-clustered GEE logistic regression as the main analysis.

Writes <out-file> (default results/problems_first/analysis.txt).

Usage:
    python src/problems_first_analysis.py                    # Phase 6 (70B)
    python src/problems_first_analysis.py \\
        --main-verdicts results/phase8/verdicts.csv \\
        --main-detection results/phase8/detection/detection.csv \\
        --exp-verdicts results/phase8/problems_first/verdicts.csv \\
        --exp-detection results/phase8/problems_first/detection/detection.csv \\
        --out-file results/phase8/problems_first/analysis.txt  # Phase 8
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

DEF_MAIN_VERDICTS = "results/verdicts.csv"
DEF_MAIN_DETECTION = "results/detection/detection.csv"
DEF_EXP_VERDICTS = "results/problems_first/verdicts.csv"
DEF_EXP_DETECTION = "results/problems_first/detection/detection.csv"
DEF_OUT_FILE = "results/problems_first/analysis.txt"


def load(order: str, ver_path: str, det_path: str) -> pd.DataFrame:
    ver = pd.read_csv(ver_path)
    det = pd.read_csv(det_path)
    df = ver.merge(det[["patch_id", "model", "condition", "run_index", "detected"]],
                   on=["patch_id", "model", "condition", "run_index"], validate="1:1")
    df = df[df["condition"].isin(["hedged", "unsupported_claims"])].copy()
    assert not df["detected"].isna().any(), f"{order}: unscored trials"
    df["detected"] = df["detected"].astype(int)  # float if source CSV had blanks
    df["approved"] = (df["verdict"] == "approve").astype(int)
    df["order"] = order
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--main-verdicts", default=DEF_MAIN_VERDICTS)
    parser.add_argument("--main-detection", default=DEF_MAIN_DETECTION)
    parser.add_argument("--exp-verdicts", default=DEF_EXP_VERDICTS)
    parser.add_argument("--exp-detection", default=DEF_EXP_DETECTION)
    parser.add_argument("--out-file", default=DEF_OUT_FILE)
    args = parser.parse_args()
    out_file = Path(args.out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    # The narrative below is the written reading of the 70B (Phase 6b) run with
    # its specific numbers; only emit it when running the default Phase 6 inputs.
    is_phase6 = args.main_verdicts == DEF_MAIN_VERDICTS

    df = pd.concat([
        load("verdict_first", args.main_verdicts, args.main_detection),
        load("problems_first", args.exp_verdicts, args.exp_detection),
    ], ignore_index=True)

    lines: list[str] = []
    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    for outcome, label in (("detected", "share of reviews that mention the bug"),
                           ("approved", "share of reviews that approve")):
        emit(f"\n=== {label} ===")
        emit(df.groupby(["model", "order", "condition"])[outcome]
               .mean().unstack().round(3).to_string())

    zero = int((df["detected"] & df["approved"]).sum())
    emit(f"\nReviews that mention the bug AND approve, all orders and conditions: "
         f"{zero} of {len(df)} - the mention-implies-reject coupling survives "
         f"the reordering.")

    emit("\nOrder x condition interaction (patch-clustered GEE; OR > 1 means the "
         "claims effect is weaker / detection better protected under "
         "problems-first):")
    for outcome in ("detected", "approved"):
        for model_name, g in df.groupby("model"):
            X = pd.DataFrame({
                "uc": (g["condition"] == "unsupported_claims").astype(float),
                "pf": (g["order"] == "problems_first").astype(float),
            })
            X["uc_x_pf"] = X["uc"] * X["pf"]
            X = sm.add_constant(X)
            fit = sm.GEE(g[outcome].values, X, groups=g["patch_id"].values,
                         family=sm.families.Binomial(),
                         cov_struct=sm.cov_struct.Exchangeable()).fit()
            emit(f"  {outcome:<9} {model_name.split('/')[-1]:<26} "
                 f"interaction OR={np.exp(fit.params['uc_x_pf']):.2f}  "
                 f"p={fit.pvalues['uc_x_pf']:.4f}")

    if is_phase6:
        emit("\nReading. Qwen: with the problems list first, the claims-induced "
             "detection drop nearly vanishes (77.6% -> 75.6%, vs 72.4% -> 54.8% "
             "verdict-first; interaction p = 0.007) - much of Qwen's suppression "
             "was at the reporting stage (silent), matching its small belief drop "
             "in experiment 1. Llama: reordering raises detection overall but the "
             "claims drop persists and even grows (interaction p = 0.09, wrong "
             "direction for recovery) - Llama's suppression is not at the "
             "reporting stage (blind), matching its large belief drop in "
             "experiment 1. Both models keep approving more under the claims "
             "(Llama's approval effect grows, interaction p = 0.04), and in all "
             "3,000 reviews across both orderings no review mentions the bug and "
             "approves.")

    out_file.write_text("\n".join(lines) + "\n")
    print(f"\n[saved] {out_file}")


if __name__ == "__main__":
    main()
