"""
Analysis for the two main research questions.

Baseline condition is "hedged" — variance across its 5 runs measures natural
model randomness. Framing effect is measured against that baseline.

Usage:
    python src/analyze.py results/verdicts.csv
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pandas as pd
from scipy import stats


BASELINE_CONDITION = "hedged"


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["approved"] = (df["verdict"] == "approve").astype(int)
    return df


# ── RQ1: framing effect beyond baseline randomness ────────────────────────────

def baseline_change_rate(df: pd.DataFrame) -> float:
    """
    How often does the verdict change across repeated runs of the hedged condition?
    This measures natural model randomness — the floor we compare framing against.
    """
    rates = []
    baseline = df[df["condition"] == BASELINE_CONDITION]
    for _, grp in baseline.groupby(["patch_id", "model"]):
        verdicts = grp["verdict"].tolist()
        pairs = list(itertools.combinations(verdicts, 2))
        if pairs:
            rates.append(sum(a != b for a, b in pairs) / len(pairs))
    return pd.Series(rates).mean() if rates else float("nan")


def framing_change_rate(df: pd.DataFrame, cond_a: str, cond_b: str) -> float:
    """
    For each patch, compare the most common verdict under cond_a vs cond_b.
    Returns the proportion of patches where the modal verdict differs.
    """
    modal = (
        df[df["condition"].isin([cond_a, cond_b])]
        .groupby(["patch_id", "model", "condition"])["verdict"]
        .agg(lambda x: x.mode()[0])
        .reset_index()
    )
    a = modal[modal["condition"] == cond_a].set_index(["patch_id", "model"])["verdict"]
    b = modal[modal["condition"] == cond_b].set_index(["patch_id", "model"])["verdict"]
    common = a.index.intersection(b.index)
    if not len(common):
        return float("nan")
    return sum(a[i] != b[i] for i in common) / len(common)


# ── RQ2/3: false approval rates ───────────────────────────────────────────────

def approval_rates(df: pd.DataFrame) -> pd.DataFrame:
    """Approval rate per condition and model, with 95% CI."""
    rows = []
    for (model, condition), grp in df.groupby(["model", "condition"]):
        n = len(grp)
        k = grp["approved"].sum()
        rate = k / n
        # Wilson score CI
        z = 1.96
        denom = 1 + z**2 / n
        centre = (rate + z**2 / (2 * n)) / denom
        margin = z * ((rate * (1 - rate) / n + z**2 / (4 * n**2)) ** 0.5) / denom
        rows.append(dict(model=model.split("/")[-1], condition=condition,
                         n=n, n_approved=int(k), approval_rate=round(rate, 3),
                         ci_lower=round(centre - margin, 3),
                         ci_upper=round(centre + margin, 3)))
    return pd.DataFrame(rows).sort_values(["model", "condition"])


def mcnemar(df: pd.DataFrame, model: str, cond_a: str, cond_b: str) -> float | None:
    """Paired McNemar test on per-patch modal verdicts."""
    modal = (
        df[(df["model"] == model) & (df["condition"].isin([cond_a, cond_b]))]
        .groupby(["patch_id", "condition"])["approved"]
        .agg(lambda x: int(x.mean() >= 0.5))  # majority vote
        .reset_index()
    )
    a = modal[modal["condition"] == cond_a].set_index("patch_id")["approved"]
    b = modal[modal["condition"] == cond_b].set_index("patch_id")["approved"]
    common = a.index.intersection(b.index)
    n10 = sum((a[i] == 1) and (b[i] == 0) for i in common)
    n01 = sum((a[i] == 0) and (b[i] == 1) for i in common)
    if n10 + n01 == 0:
        return None
    return stats.binomtest(n01, n10 + n01, p=0.5).pvalue


# ── Main ──────────────────────────────────────────────────────────────────────

def run(verdicts_file: str, out_dir: str | None = None) -> None:
    df = load(verdicts_file)
    out = Path(out_dir) if out_dir else Path(verdicts_file).resolve().parent / "analysis"
    out.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    # ── RQ1 ──
    n_runs = df[df['condition'] == BASELINE_CONDITION]['run_index'].nunique()
    emit("=== RQ1: Framing effect vs baseline randomness ===")
    rq1_rows = [dict(comparison=f"baseline ({BASELINE_CONDITION}, {n_runs} runs)",
                     change_rate=round(baseline_change_rate(df), 3))]
    emit(f"Baseline change rate (hedged, {n_runs} runs): {baseline_change_rate(df):.3f}")
    for a, b in [("hedged", "confident"),
                 ("hedged", "confident_extra_neutral"),
                 ("hedged", "unsupported_claims")]:
        rate = framing_change_rate(df, a, b)
        rq1_rows.append(dict(comparison=f"{a} vs {b}", change_rate=round(rate, 3)))
        emit(f"Framing change rate ({a} vs {b}): {rate:.3f}")

    # ── RQ2/3 approval rates ──
    emit("\n=== RQ2/3: Approval rates by condition (with 95% CI) ===")
    appr = approval_rates(df)
    emit(appr.to_string(index=False))

    # ── McNemar ──
    emit("\n=== Significance (McNemar, paired by patch) ===")
    mc_rows = []
    for model in df["model"].unique():
        for a, b in [("hedged", "confident"),
                     ("hedged", "confident_extra_neutral"),
                     ("hedged", "unsupported_claims"),
                     ("confident", "unsupported_claims")]:
            p = mcnemar(df, model, a, b)
            label = model.split("/")[-1]
            mc_rows.append(dict(model=label, cond_a=a, cond_b=b,
                                p_value=round(p, 4) if p is not None else None))
            emit(f"{label}  {a} vs {b}:  " + (f"p={p:.4f}" if p is not None else "insufficient data"))

    # ── Persist ──
    appr.to_csv(out / "approval_rates.csv", index=False)
    pd.DataFrame(rq1_rows).to_csv(out / "rq1_change_rates.csv", index=False)
    pd.DataFrame(mc_rows).to_csv(out / "mcnemar.csv", index=False)
    (out / "analysis_summary.txt").write_text("\n".join(lines) + "\n")
    print(f"\n[saved] {out/'analysis_summary.txt'}, approval_rates.csv, "
          f"rq1_change_rates.csv, mcnemar.csv")


if __name__ == "__main__":
    verdicts = sys.argv[1] if len(sys.argv) > 1 else "results/verdicts.csv"
    out_dir = sys.argv[2] if len(sys.argv) > 2 else None
    run(verdicts, out_dir)
