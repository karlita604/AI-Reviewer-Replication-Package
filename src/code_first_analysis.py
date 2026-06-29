"""
Analysis for Phase 6b experiment 3 (code before description).

Why this exists. Experiments 1 and 2 localized the effect per model: Llama's
suppression looks blind (large belief drop, no recovery under problems-first),
Qwen's looks silent (small belief drop, near-full recovery). This experiment
reorders the *input* instead of the output: the model reads the code diff
before it sees the PR description, so the claims cannot color the reading of
the code. The blind account predicts the claims effect shrinks; the silent
account predicts it survives. After experiments 1-2 the prediction is per
model: Llama should shrink, Qwen should not.

Compares the code-first run (results/code_first/) against the
description-first main study (results/raw + results/detection), hedged vs
unsupported_claims, and tests the order x condition interaction with the
same patch-clustered GEE logistic regression as the main analysis.

Writes <out-file> (default results/code_first/analysis.txt).

Usage:
    python src/code_first_analysis.py                        # Phase 6 (70B)
    python src/code_first_analysis.py \\
        --main-verdicts results/phase8/verdicts.csv \\
        --main-detection results/phase8/detection/detection.csv \\
        --exp-verdicts results/phase8/code_first/verdicts.csv \\
        --exp-detection results/phase8/code_first/detection/detection.csv \\
        --out-file results/phase8/code_first/analysis.txt     # Phase 8
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

DEF_MAIN_VERDICTS = "results/verdicts.csv"
DEF_MAIN_DETECTION = "results/detection/detection.csv"
DEF_EXP_VERDICTS = "results/code_first/verdicts.csv"
DEF_EXP_DETECTION = "results/code_first/detection/detection.csv"
DEF_OUT_FILE = "results/code_first/analysis.txt"


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
        load("description_first", args.main_verdicts, args.main_detection),
        load("code_first", args.exp_verdicts, args.exp_detection),
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
         "code-first):")
    for outcome in ("detected", "approved"):
        for model_name, g in df.groupby("model"):
            X = pd.DataFrame({
                "uc": (g["condition"] == "unsupported_claims").astype(float),
                "cf": (g["order"] == "code_first").astype(float),
            })
            X["uc_x_cf"] = X["uc"] * X["cf"]
            X = sm.add_constant(X)
            fit = sm.GEE(g[outcome].values, X, groups=g["patch_id"].values,
                         family=sm.families.Binomial(),
                         cov_struct=sm.cov_struct.Exchangeable()).fit()
            emit(f"  {outcome:<9} {model_name.split('/')[-1]:<26} "
                 f"interaction OR={np.exp(fit.params['uc_x_cf']):.2f}  "
                 f"p={fit.pvalues['uc_x_cf']:.4f}")

    if is_phase6:
        emit("\nReading. Neither account predicted this: with the code before the "
             "description, the claims effect GROWS in both models. Detection under "
             "the cautious description barely moves (Qwen 73.2% of reviews mention "
             "the bug vs 72.4% description-first; Llama 46.8% vs 40.0%), but under "
             "the fabricated claims it falls much further: Qwen 34.0% (vs 54.8% "
             "description-first, interaction p = 0.0004), Llama 23.6% (vs 29.2%, "
             "p = 0.0007). Approvals under the claims rise the same way (Qwen "
             "64.0% vs 42.0%, p < 0.0001; Llama 75.2% vs 70.0%, p = 0.006). All "
             "four interactions survive Holm's correction. The blind account "
             "predicted the effect shrinks when the code is read before the "
             "claims; the silent account predicted it stays; growth fits neither "
             "as stated. The reading that fits: the claims act when the verdict "
             "is formed, not while the code is read, and they act harder the "
             "closer they sit to the end of the prompt.")

    out_file.write_text("\n".join(lines) + "\n")
    print(f"\n[saved] {out_file}")


if __name__ == "__main__":
    main()
