"""
Vague-rejection selection test (Phase 6b, experiment 0b). No new model runs.

Why this exists. Under the unsupported-claims description, Llama almost never
produces a vague rejection - a rejection whose complaints do not include the
planted bug (2 of its 75 claims-condition rejections, vs ~12% of its
rejections elsewhere). The selection account says the claims flip precisely
the weakly grounded rejections into approvals, leaving only well-grounded
rejections behind. If that is right, then patches whose hedged-condition
rejections were vague should be over-represented among the patches that flip
from majority-reject under hedged to majority-approve under the claims.

Method. For each model, take the patches that were majority-rejected (3+ of
5 runs) under the hedged description. For each such patch, record (a) whether
any of its hedged rejections was vague - rejected without mentioning the bug,
per the validated detection labels - and (b) whether the patch flips to
majority-approval (3+ of 5 runs) under unsupported claims. Test whether
vague-prone patches flip more often (Fisher's exact test on the 2x2 table),
and compare the share of vague hedged rejections between flipping and
non-flipping patches (Mann-Whitney).

Outputs (results/analysis/):
    vague_rejection_test.csv   one row per at-risk patch per model
    vague_rejection_test.txt   the 2x2 tables and test results

Usage:
    python src/vague_rejection_test.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from scipy import stats

VERDICTS_FILE = Path("results/verdicts.csv")
DETECTION_FILE = Path("results/detection/detection.csv")
OUT_DIR = Path("results/analysis")


def patch_table(d: pd.DataFrame) -> pd.DataFrame:
    """One row per patch: hedged rejection profile + claims outcome."""
    h = d[d["condition"] == "hedged"]
    c = d[d["condition"] == "unsupported_claims"]
    rows = []
    for patch_id, hp in h.groupby("patch_id"):
        rej = hp[hp["approved"] == 0]
        cp = c[c["patch_id"] == patch_id]
        rows.append({
            "patch_id": patch_id,
            "hedged_rejections": len(rej),
            "hedged_vague_rejections": int((rej["detected"] == 0).sum()),
            "vague_share": (rej["detected"] == 0).mean() if len(rej) else float("nan"),
            "any_vague": int((rej["detected"] == 0).any()),
            "claims_approvals": int(cp["approved"].sum()),
            "flipped": int(cp["approved"].sum() >= 3),
        })
    t = pd.DataFrame(rows)
    return t[t["hedged_rejections"] >= 3]  # majority-rejected under hedged


def main() -> None:
    ver = pd.read_csv(VERDICTS_FILE)
    det = pd.read_csv(DETECTION_FILE)
    df = ver.merge(det[["patch_id", "model", "condition", "run_index", "detected"]],
                   on=["patch_id", "model", "condition", "run_index"], validate="1:1")
    df["approved"] = (df["verdict"] == "approve").astype(int)

    lines: list[str] = []
    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    tables = []
    for model_name, d in df.groupby("model"):
        t = patch_table(d)
        t.insert(0, "model", model_name)
        tables.append(t)
        short = model_name.split("/")[-1]

        ct = pd.crosstab(t["any_vague"], t["flipped"])
        ct = ct.reindex(index=[0, 1], columns=[0, 1], fill_value=0)
        odds, p_fisher = stats.fisher_exact(ct.values)

        flip, stay = t[t["flipped"] == 1], t[t["flipped"] == 0]
        if len(flip) and len(stay):
            u, p_mw = stats.mannwhitneyu(flip["vague_share"], stay["vague_share"])
        else:
            p_mw = float("nan")

        emit(f"\n=== {short} ===")
        emit(f"Patches majority-rejected under hedged: {len(t)}; "
             f"of these, {t['flipped'].sum()} flip to majority-approval under claims.")
        emit(f"Patches with at least one vague hedged rejection: {t['any_vague'].sum()}.")
        emit("")
        emit("                        did not flip   flipped")
        emit(f"no vague hedged rej.        {ct.loc[0,0]:>4}        {ct.loc[0,1]:>4}")
        emit(f"some vague hedged rej.      {ct.loc[1,0]:>4}        {ct.loc[1,1]:>4}")
        emit("")
        fr = flip["vague_share"].mean() if len(flip) else float("nan")
        sr = stay["vague_share"].mean() if len(stay) else float("nan")
        emit(f"Mean share of hedged rejections that were vague: "
             f"flipping patches {fr:.1%} vs non-flipping {sr:.1%}.")
        emit(f"Fisher's exact (any vague x flipped): odds ratio {odds:.2f}, p = {p_fisher:.4f}.")
        emit(f"Mann-Whitney on vague share (flip vs stay): p = {p_mw:.4f}.")

    out = pd.concat(tables, ignore_index=True)
    out.to_csv(OUT_DIR / "vague_rejection_test.csv", index=False)
    (OUT_DIR / "vague_rejection_test.txt").write_text("\n".join(lines) + "\n")
    print(f"\n[saved] {OUT_DIR}/vague_rejection_test.csv, vague_rejection_test.txt")


if __name__ == "__main__":
    main()
