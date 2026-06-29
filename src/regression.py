"""
Trial-level regression analysis (Phase 6, step 3) - replaces McNemar as the
primary inference.

Design note. NEXT-PHASES.md calls for a mixed-effects logistic regression
(random intercept per patch). That model is not usable here: many patches are
near-deterministic (always approved or always rejected within a condition),
so within-patch flips are one-directional and conditional/subject-specific
estimates quasi-separate (ORs in the hundreds-to-thousands with CIs spanning
orders of magnitude; statsmodels' variational mixed GLM additionally
underestimates posterior SDs in this regime). We therefore report:

  PRIMARY      GEE logistic regression, exchangeable working correlation
               within patch (population-averaged ORs, robust Wald inference).
               Interpretation: "across patches, the odds of approval under
               condition X are OR times the hedged odds."
  SENSITIVITY  Conditional (patch-stratified) logit - direction/significance
               only; magnitudes inflated by quasi-separation.

Holm correction within each outcome family (3 contrasts x n models — scales
automatically with however many models are in the verdicts file).

Moderators: claims x difficulty, x patch source, x diff length (GEE
interactions on the hedged vs unsupported_claims subset, Holm-corrected).
bug_type is NOT testable: 47 distinct types across 50 patches (nearly one patch
per type), fully confounded with patch identity.

Outputs (--out-dir, default results/analysis/):
    regression_odds_ratios.csv   per-contrast ORs, CIs, p, Holm-adjusted p
    regression_moderators.csv    interaction tests, Holm-adjusted
    regression_summary.txt       readable summary

Approval-only mode (--no-detection). Good patches have no planted bug, so
there is no detection outcome and no detection.csv to merge. With
--no-detection the detection merge and the detected-outcome regression are
skipped; only the approval regression runs. Everything else (the approval
GEE, the conditional-logit sensitivity check, the moderators) is unchanged.

Usage:
    python src/regression.py                       # Phase 6 (70B) defaults
    python src/regression.py \\
        --verdicts results/phase8/verdicts.csv \\
        --detection results/phase8/detection/detection.csv \\
        --out-dir results/phase8/analysis          # Phase 8 small models
    python src/regression.py \\
        --verdicts results/good_verdicts.csv --no-detection \\
        --patches-dir data/goodpatches \\
        --out-dir results/analysis/good_verdicts   # good patches (approval only)
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.discrete.conditional_models import ConditionalLogit
from statsmodels.stats.multitest import multipletests

VERDICTS_FILE = Path("results/verdicts.csv")
DETECTION_FILE = Path("results/detection/detection.csv")
PATCHES_DIR = Path("data/patches")
OUT_DIR = Path("results/analysis")
NO_DETECTION = False  # set by --no-detection: approval-only (good patches)

CONDITIONS = ["hedged", "confident", "confident_extra_neutral", "unsupported_claims"]
DIFFICULTY_ORD = {"easy": 0, "medium": 1, "hard": 2}


def load_data() -> pd.DataFrame:
    ver = pd.read_csv(VERDICTS_FILE)
    if NO_DETECTION:
        df = ver.copy()
    else:
        det = pd.read_csv(DETECTION_FILE)
        df = ver.merge(det[["patch_id", "model", "condition", "run_index", "detected"]],
                       on=["patch_id", "model", "condition", "run_index"], validate="1:1")
    df["approved"] = (df["verdict"] == "approve").astype(int)

    meta_rows = []
    for d in sorted(PATCHES_DIR.iterdir()):
        # Only patch_NNN dirs; skip stray dirs (e.g. an old data/goodpatches/v1)
        # and files, matching the loader guard in src/patches.py.
        if not d.is_dir() or not re.fullmatch(r"patch_\d{3}", d.name):
            continue
        meta = json.loads((d / "metadata.json").read_text())
        meta_rows.append({
            "patch_id": d.name,
            "difficulty": DIFFICULTY_ORD[meta["difficulty"]],
            "source_injected": int(meta["source"] == "injected"),
            "diff_lines": len((d / "diff.patch").read_text().splitlines()),
        })
    meta_df = pd.DataFrame(meta_rows)
    meta_df["diff_lines_z"] = ((meta_df["diff_lines"] - meta_df["diff_lines"].mean())
                               / meta_df["diff_lines"].std())
    return df.merge(meta_df, on="patch_id", validate="m:1")


def condition_dummies(d: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({f"cond[{c}]": (d["condition"] == c).astype(float)
                         for c in CONDITIONS[1:]}, index=d.index)


def fit_gee(d: pd.DataFrame, outcome: str, X: pd.DataFrame) -> pd.DataFrame:
    Xc = sm.add_constant(X)
    fit = sm.GEE(d[outcome].values, Xc, groups=d["patch_id"].values,
                 family=sm.families.Binomial(),
                 cov_struct=sm.cov_struct.Exchangeable()).fit()
    ci = fit.conf_int()
    return pd.DataFrame({
        "term": Xc.columns, "or": np.exp(fit.params),
        "or_lo": np.exp(ci[:, 0] if isinstance(ci, np.ndarray) else ci.iloc[:, 0]),
        "or_hi": np.exp(ci[:, 1] if isinstance(ci, np.ndarray) else ci.iloc[:, 1]),
        "p": fit.pvalues,
    }).query("term != 'const'").reset_index(drop=True)


def fit_conditional(d: pd.DataFrame, outcome: str) -> pd.DataFrame:
    X = condition_dummies(d)
    fit = ConditionalLogit(d[outcome].values, X,
                           groups=d["patch_id"].values).fit(disp=0)
    return pd.DataFrame({"term": X.columns, "or": np.exp(fit.params),
                         "p": fit.pvalues}).reset_index(drop=True)


def main() -> None:
    global VERDICTS_FILE, DETECTION_FILE, PATCHES_DIR, OUT_DIR, NO_DETECTION
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verdicts", default=str(VERDICTS_FILE),
                        help="consolidated verdicts CSV")
    parser.add_argument("--detection", default=str(DETECTION_FILE),
                        help="detection.csv from the judge")
    parser.add_argument("--no-detection", action="store_true",
                        help="approval-only: no planted bug (good patches), "
                             "skip the detection merge and detected-outcome regression")
    parser.add_argument("--patches-dir", default=str(PATCHES_DIR),
                        help="patch metadata dir (shared across phases)")
    parser.add_argument("--out-dir", default=str(OUT_DIR),
                        help="folder for regression_*.csv and summary")
    args = parser.parse_args()
    VERDICTS_FILE = Path(args.verdicts)
    DETECTION_FILE = Path(args.detection)
    PATCHES_DIR = Path(args.patches_dir)
    OUT_DIR = Path(args.out_dir)
    NO_DETECTION = args.no_detection
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    df = load_data()
    lines: list[str] = []
    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    emit("Primary inference: GEE logistic regression, patch-clustered "
         "(population-averaged ORs).")
    emit("Sensitivity: conditional (patch-stratified) logit - direction only; "
         "magnitudes quasi-separated.")
    emit("Reference condition: hedged.")
    if NO_DETECTION:
        emit("Approval-only mode: good patches have no planted bug; "
             "detection outcome skipped.")

    # -- Primary: condition effects per outcome per model --
    outcomes = ("approved",) if NO_DETECTION else ("approved", "detected")
    or_tables = []
    for outcome in outcomes:
        emit(f"\n=== Outcome: {outcome} ===")
        fam_rows = []
        for model_name, d in df.groupby("model"):
            gee = fit_gee(d, outcome, condition_dummies(d))
            cond = fit_conditional(d, outcome).set_index("term")
            gee["cl_or"] = gee["term"].map(cond["or"])
            gee["cl_p"] = gee["term"].map(cond["p"])
            gee.insert(0, "outcome", outcome)
            gee.insert(1, "model", model_name)
            fam_rows.append(gee)
        fam = pd.concat(fam_rows, ignore_index=True)
        fam["p_holm"] = multipletests(fam["p"], method="holm")[1]
        or_tables.append(fam)
        for _, r in fam.iterrows():
            emit(f"{r['model'].split('/')[-1]:<28} {r['term']:<31} "
                 f"OR={r['or']:.2f} [{r['or_lo']:.2f}, {r['or_hi']:.2f}]  "
                 f"p={r['p']:.4f}  p_holm={r['p_holm']:.4f}  "
                 f"(cond.logit p={r['cl_p']:.4f})")
    pd.concat(or_tables, ignore_index=True).to_csv(
        OUT_DIR / "regression_odds_ratios.csv", index=False)

    # -- Moderators of the claims effect (approval outcome) --
    emit("\n=== Moderators of the claims effect (hedged vs unsupported_claims, approval) ===")
    emit("(bug_type not testable: 47 types / 50 patches, confounded with patch identity)")
    sub = df[df["condition"].isin(["hedged", "unsupported_claims"])].copy()
    sub["uc"] = (sub["condition"] == "unsupported_claims").astype(float)
    mod_rows = []
    for mod in ("difficulty", "source_injected", "diff_lines_z"):
        for model_name, d in sub.groupby("model"):
            X = pd.DataFrame({"uc": d["uc"], "mod": d[mod].astype(float),
                              "uc_x_mod": d["uc"] * d[mod].astype(float)}, index=d.index)
            res = fit_gee(d, "approved", X).set_index("term").loc["uc_x_mod"]
            mod_rows.append({"moderator": mod, "model": model_name,
                             "or": res["or"], "or_lo": res["or_lo"],
                             "or_hi": res["or_hi"], "p": res["p"]})
    mod_table = pd.DataFrame(mod_rows)
    mod_table["p_holm"] = multipletests(mod_table["p"], method="holm")[1]
    mod_table.to_csv(OUT_DIR / "regression_moderators.csv", index=False)
    for _, r in mod_table.iterrows():
        emit(f"{r['model'].split('/')[-1]:<28} claims x {r['moderator']:<16} "
             f"interaction OR={r['or']:.2f} [{r['or_lo']:.2f}, {r['or_hi']:.2f}]  "
             f"p={r['p']:.4f}  p_holm={r['p_holm']:.4f}")

    (OUT_DIR / "regression_summary.txt").write_text("\n".join(lines) + "\n")
    print(f"\n[saved] {OUT_DIR}/regression_odds_ratios.csv, regression_moderators.csv, "
          f"regression_summary.txt")


if __name__ == "__main__":
    main()
