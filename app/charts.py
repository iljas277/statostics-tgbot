from __future__ import annotations

from io import BytesIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def render_comments_trend_png(trend_rows: list[dict], days: int) -> bytes:
    days = max(1, int(days))
    labels = [row.get("day", "") for row in trend_rows]
    values = [int(row.get("comments", 0)) for row in trend_rows]

    if not labels:
        labels = [f"last {days}d"]
        values = [0]

    fig, ax = plt.subplots(figsize=(10, 4), dpi=150)
    fig.patch.set_facecolor("#f7f9fc")
    ax.set_facecolor("#ffffff")

    ax.plot(labels, values, color="#0b84f3", linewidth=2.5, marker="o", markersize=4)
    ax.fill_between(labels, values, color="#d5e9ff", alpha=0.6)

    ax.set_title(f"Comments Trend ({days} days)", fontsize=12, fontweight="bold")
    ax.set_ylabel("Comments")
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    if len(labels) > 12:
        step = max(1, len(labels) // 8)
        ticks = list(range(0, len(labels), step))
        ax.set_xticks(ticks)
        ax.set_xticklabels([labels[i] for i in ticks], rotation=25, ha="right")
    else:
        ax.tick_params(axis="x", rotation=25)

    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
