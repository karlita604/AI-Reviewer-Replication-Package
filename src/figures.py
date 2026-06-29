"""
Generate paper figures and a qualitative flip analysis from results/.

Outputs (to results/figures/):
    approval_by_condition.png   approval rate per condition, one panel per model, 95% CI bars
    change_rate.png             verdict-change rate: baseline vs each framing comparison

Also prints a qualitative summary: among patches whose modal verdict flips to
"approve" under unsupported_claims (but not under hedged), how often the model's
stated reason cites a description claim ("tests passed", "reviewed", "safe to merge").

Usage:
    python src/figures.py            # uses results/
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

CONDITIONS = ["hedged", "confident", "confident_extra_neutral", "unsupported_claims"]
NICE = {"hedged": "hedged\n(baseline)", "confident": "confident",
        "confident_extra_neutral": "confident\n+neutral", "unsupported_claims": "unsupported\nclaims"}
CLAIM_RE = re.compile(r"test|review|safe to merge|merge|senior|maintainer|passed|verifi", re.I)


def approval_figure(appr: pd.DataFrame, out: Path) -> None:
    models = sorted(appr["model"].unique())
    fig, axes = plt.subplots(1, len(models), figsize=(5 * len(models), 4.2), sharey=True)
    if len(models) == 1:
        axes = [axes]
    for ax, model in zip(axes, models):
        sub = appr[appr["model"] == model].set_index("condition").reindex(CONDITIONS)
        rates = sub["approval_rate"].values
        lo = rates - sub["ci_lower"].values
        hi = sub["ci_upper"].values - rates
        colors = ["#888888", "#4C72B0", "#55A868", "#C44E52"]
        ax.bar(range(len(CONDITIONS)), rates, yerr=[lo, hi], capsize=4, color=colors)
        ax.set_xticks(range(len(CONDITIONS)))
        ax.set_xticklabels([NICE[c] for c in CONDITIONS], fontsize=8)
        ax.set_title(model, fontsize=10)
        ax.set_ylim(0, 1)
        ax.axhline(sub.loc["hedged", "approval_rate"], ls="--", lw=0.8, color="#888888")
        for i, r in enumerate(rates):
            ax.text(i, r + hi[i] + 0.02, f"{r:.0%}", ha="center", fontsize=8)
    axes[0].set_ylabel("Approval rate on known-bad patches")
    fig.suptitle("False-approval rate by PR-description framing (95% CI)", fontsize=12)
    fig.tight_layout()
    fig.savefig(out / "approval_by_condition.png", dpi=150)
    plt.close(fig)


def change_rate_figure(rq1: pd.DataFrame, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    labels = [r.replace(" vs ", "\nvs ") for r in rq1["comparison"]]
    colors = ["#888888"] + ["#C44E52" if "unsupported" in c else "#4C72B0"
                            for c in rq1["comparison"][1:]]
    ax.bar(range(len(rq1)), rq1["change_rate"], color=colors)
    ax.set_xticks(range(len(rq1)))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Verdict-change rate")
    ax.set_title("RQ1: framing verdict-change vs baseline run-to-run noise")
    for i, v in enumerate(rq1["change_rate"]):
        ax.text(i, v + 0.004, f"{v:.3f}", ha="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "change_rate.png", dpi=150)
    plt.close(fig)


def qualitative_flips(df: pd.DataFrame) -> str:
    df = df.copy()
    df["approved"] = (df["verdict"] == "approve").astype(int)
    out_lines = []
    total_flips, cited = 0, 0
    for model, g in df.groupby("model"):
        label = model.split("/")[-1]
        flips = []
        for pid, gp in g.groupby("patch_id"):
            h = gp[gp["condition"] == "hedged"]["approved"].mean()
            u = gp[gp["condition"] == "unsupported_claims"]["approved"].mean()
            if h < 0.5 <= u:          # flipped to approve under claims
                flips.append(pid)
                texts = gp[(gp["condition"] == "unsupported_claims")]
                blob = " ".join((texts["main_reason"].fillna("") + " "
                                 + texts["problems_found"].fillna("")).tolist())
                if CLAIM_RE.search(blob):
                    cited += 1
                total_flips += 1
        out_lines.append(f"{label}: {len(flips)} patches flipped hedged->approve under unsupported_claims: {flips}")
    out_lines.append(f"Across both models: {total_flips} flip cases; "
                     f"{cited} ({cited/total_flips:.0%}) had a claims-related term in the stated reason."
                     if total_flips else "No flip cases.")
    return "\n".join(out_lines)


def run(results_dir: str = "results") -> None:
    res = Path(results_dir)
    figs = res / "figures"; figs.mkdir(parents=True, exist_ok=True)
    analysis = res / "analysis"
    appr = pd.read_csv(analysis / "approval_rates.csv")
    rq1 = pd.read_csv(analysis / "rq1_change_rates.csv")
    df = pd.read_csv(res / "verdicts.csv")

    approval_figure(appr, figs)
    change_rate_figure(rq1, figs)
    print(f"[figures] wrote {figs}/approval_by_condition.png, {figs}/change_rate.png")

    qual = qualitative_flips(df)
    print("\n=== Qualitative flip analysis ===")
    print(qual)
    (analysis / "qualitative_flips.txt").write_text(qual + "\n")
    print(f"\n[saved] {analysis}/qualitative_flips.txt")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "results")
