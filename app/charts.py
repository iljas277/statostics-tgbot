from __future__ import annotations

from io import BytesIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


def _format_thousands(value: float, _pos: int) -> str:
    """Format tick values with space-separated thousands for Y-axis labels."""
    return f"{int(value):,}".replace(",", " ")


def _style_axis(ax, labels: list[str]) -> None:
    """Apply shared axis styling, grid, tick formatting and X-label density handling."""
    ax.grid(axis="y", linestyle="--", alpha=0.28)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_alpha(0.25)
    ax.spines["bottom"].set_alpha(0.25)
    ax.yaxis.set_major_formatter(FuncFormatter(_format_thousands))

    if len(labels) > 12:
        step = max(1, len(labels) // 8)
        ticks = list(range(0, len(labels), step))
        ax.set_xticks(ticks)
        ax.set_xticklabels([labels[i] for i in ticks], rotation=25, ha="right")
    else:
        ax.tick_params(axis="x", rotation=25)


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

    ax.plot(labels, values, color="#0b84f3", linewidth=2.8, marker="o", markersize=4.5)
    ax.fill_between(labels, values, color="#d5e9ff", alpha=0.5)

    ax.set_title(f"Comments Trend ({days} days)", fontsize=12, fontweight="bold")
    ax.set_ylabel("Comments")
    _style_axis(ax=ax, labels=labels)

    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def render_metric_trend_png(
    trend_rows: list[dict],
    days: int,
    title: str,
    y_label: str,
    value_field: str = "value",
    line_color: str = "#8f4cf0",
    fill_color: str = "#ece1ff",
) -> bytes:
    days = max(1, int(days))
    labels = [row.get("day", "") for row in trend_rows]
    values = [int(row.get(value_field, 0) or 0) for row in trend_rows]

    if not labels:
        labels = [f"last {days}d"]
        values = [0]

    fig, ax = plt.subplots(figsize=(10, 4), dpi=150)
    fig.patch.set_facecolor("#f7f9fc")
    ax.set_facecolor("#ffffff")

    ax.plot(labels, values, color=line_color, linewidth=2.8, marker="o", markersize=4.5)
    ax.fill_between(labels, values, color=fill_color, alpha=0.5)

    ax.set_title(f"{title} ({days} days)", fontsize=12, fontweight="bold")
    ax.set_ylabel(y_label)
    _style_axis(ax=ax, labels=labels)

    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
