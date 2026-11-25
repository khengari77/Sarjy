"""Render latency history charts as self-contained SVG files.

The module uses only the standard library. It reads the probe-run history JSON
and writes a p50/p95 line chart plus a stacked worker-stage chart. Rendering is
pure (data in, strings out) and deterministic, and every text node is escaped.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "docs" / "data" / "latency-history.json"
DEFAULT_OUT_DIR = ROOT / "docs" / "img"

STAGE_ORDER = ("end of turn", "turn start", "command", "model first chunk", "tts")
STAGE_COLORS = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9")
P50_COLOR = "#0072B2"
P95_COLOR = "#D55E00"
INK = "#1a1a1a"
MUTED = "#4a4a4a"
GRID = "#e3e5e9"
AXIS = "#b8bcc4"
BACKGROUND = "#f8f9fb"
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"


@dataclass(frozen=True)
class ProbeRun:
    label: str
    date: str
    p50: float
    p95: float


@dataclass(frozen=True)
class StageBar:
    label: str
    segments: tuple[tuple[str, float], ...]

    @property
    def total(self) -> float:
        return sum(value for _, value in self.segments)


@dataclass(frozen=True)
class History:
    runs: tuple[ProbeRun, ...]
    worker_runs: tuple[ProbeRun, ...]
    stage_bars: tuple[StageBar, ...]
    interim_p50: float
    interim_p95: float
    goal_p50: float
    goal_p95: float


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where}: expected an object")
    return value


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: expected a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{where}: expected a finite non-negative number")
    return number


def _text_value(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty string")
    return value


def _parse_runs(raw_runs: Any, name: str) -> tuple[ProbeRun, ...]:
    if not isinstance(raw_runs, list) or not raw_runs:
        raise ValueError(f"{name}: expected a non-empty array")
    runs: list[ProbeRun] = []
    for index, item in enumerate(raw_runs):
        where = f"{name}[{index}]"
        entry = _mapping(item, where)
        label = _text_value(entry.get("label"), f"{where}.label")
        date = entry.get("date", "")
        if not isinstance(date, str):
            raise ValueError(f"{where}.date: expected a string")
        runs.append(
            ProbeRun(
                label=label,
                date=date,
                p50=_number(entry.get("p50"), f"{where}.p50"),
                p95=_number(entry.get("p95"), f"{where}.p95"),
            )
        )
    return tuple(runs)


def load_history(path: Path) -> History:
    """Load and validate the probe history; raises ValueError with a readable message."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read data file {path}: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc

    root = _mapping(payload, "root")
    runs = _parse_runs(root.get("probe_runs"), "probe_runs")
    worker_runs = _parse_runs(root["worker_runs"], "worker_runs") if "worker_runs" in root else ()

    raw_stages = _mapping(root.get("worker_stages_ms"), "worker_stages_ms")
    if not raw_stages:
        raise ValueError("worker_stages_ms: expected at least one entry")
    stage_bars: list[StageBar] = []
    for name, raw_segments in raw_stages.items():
        label = _text_value(name, "worker_stages_ms key")
        segments_map = _mapping(raw_segments, f"worker_stages_ms[{label!r}]")
        missing = [stage for stage in STAGE_ORDER if stage not in segments_map]
        unknown = [key for key in segments_map if key not in STAGE_ORDER]
        if missing or unknown:
            raise ValueError(
                f"worker_stages_ms[{label!r}]: expected stages {STAGE_ORDER}, missing {missing}, unknown {unknown}"
            )
        segments = tuple(
            (stage, _number(segments_map[stage], f"worker_stages_ms[{label!r}].{stage}")) for stage in STAGE_ORDER
        )
        stage_bars.append(StageBar(label=label, segments=segments))

    targets = _mapping(root.get("targets"), "targets")
    return History(
        runs=runs,
        worker_runs=worker_runs,
        stage_bars=tuple(stage_bars),
        interim_p50=_number(targets.get("interim_p50"), "targets.interim_p50"),
        interim_p95=_number(targets.get("interim_p95"), "targets.interim_p95"),
        goal_p50=_number(targets.get("p50"), "targets.p50"),
        goal_p95=_number(targets.get("p95"), "targets.p95"),
    )


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _coord(value: float) -> str:
    text = f"{value + 0.0:.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _text_on(colour: str) -> str:
    channels = [int(colour[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    ink_luminance = 0.0103
    dark_contrast = (luminance + 0.05) / (ink_luminance + 0.05)
    light_contrast = 1.05 / (luminance + 0.05)
    return INK if dark_contrast >= light_contrast else "#ffffff"


def _nice_step(max_value: float) -> float:
    target = max_value / 8 if max_value > 0 else 1
    for step in (50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000):
        if step >= target:
            return float(step)
    return 20000.0


def _axis_max(max_value: float, step: float) -> float:
    return max(step, float(math.ceil(max_value * 1.04 / step) * step))


def _text(
    x: float,
    y: float,
    value: str,
    *,
    size: float = 12,
    anchor: str = "start",
    fill: str = INK,
    weight: str | None = None,
    transform: str | None = None,
) -> str:
    attrs = [
        f'x="{_coord(x)}"',
        f'y="{_coord(y)}"',
        f'font-size="{_coord(size)}"',
        f'text-anchor="{anchor}"',
        f'fill="{fill}"',
    ]
    if weight is not None:
        attrs.append(f'font-weight="{weight}"')
    if transform is not None:
        attrs.append(f'transform="{_escape(transform)}"')
    return f"    <text {' '.join(attrs)}>{_escape(value)}</text>"


def _svg_document(width: int, height: int, title: str, body: Sequence[str]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" aria-labelledby="chart-title">'
        ),
        f'  <title id="chart-title">{_escape(title)}</title>',
        f'  <rect x="0" y="0" width="{width}" height="{height}" fill="{BACKGROUND}"/>',
        f'  <g font-family="{_escape(FONT)}" fill="{INK}">',
        *body,
        "  </g>",
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


def _legend_swatch(x: float, y: float, colour: str, label: str, dashed: bool) -> list[str]:
    dash = ' stroke-dasharray="8 6"' if dashed else ""
    return [
        f'    <line x1="{_coord(x)}" y1="{_coord(y - 4)}" x2="{_coord(x + 24)}" y2="{_coord(y - 4)}" '
        f'stroke="{colour}" stroke-width="2.5"{dash}/>',
        _text(x + 32, y, label, size=12),
    ]


def render_history_svg(
    history: History,
    runs: Sequence[ProbeRun] | None = None,
    title: str = "Probe-run latency history",
    subtitle: str = "p50 and p95 end-to-end latency for each probe run, in milliseconds",
    axis_label: str = "Probe run (chronological order)",
    target_labels: bool = True,
) -> str:
    runs = history.runs if runs is None else runs
    width, height = 980, 580
    left, right, top, bottom = 96, 44, 112, 132
    plot_w = width - left - right
    plot_h = height - top - bottom
    plot_bottom = top + plot_h

    all_values = [run.p50 for run in runs]
    all_values += [run.p95 for run in runs]
    all_values += [history.interim_p50, history.interim_p95]
    step = _nice_step(max(all_values))
    y_max = _axis_max(max(all_values), step)
    run_count = len(runs)

    def x_at(index: int) -> float:
        if run_count == 1:
            return left + plot_w / 2
        return left + index * plot_w / (run_count - 1)

    def y_at(value: float) -> float:
        return top + plot_h * (1 - value / y_max)

    body: list[str] = [
        _text(32, 42, title, size=20, weight="700"),
        _text(32, 66, subtitle, size=13, fill=MUTED),
    ]
    body += _legend_swatch(32, 94, P50_COLOR, "p50", dashed=False)
    body += _legend_swatch(140, 94, P95_COLOR, "p95", dashed=False)
    body += _legend_swatch(248, 94, MUTED, "interim target", dashed=True)
    body.append(
        _text(
            width - 32,
            94,
            f"Long-term goals: p50 {_coord(history.goal_p50)} ms / p95 {_coord(history.goal_p95)} ms",
            size=12,
            anchor="end",
            fill=MUTED,
        )
    )

    tick_count = int(round(y_max / step))
    for tick in range(tick_count + 1):
        value = tick * step
        y = y_at(value)
        body.append(
            f'    <line x1="{_coord(left)}" y1="{_coord(y)}" x2="{_coord(left + plot_w)}" '
            f'y2="{_coord(y)}" stroke="{GRID}" stroke-width="1"/>'
        )
        body.append(_text(left - 12, y + 4, f"{value:,.0f}", size=11, anchor="end", fill=MUTED))

    body.append(
        f'    <line x1="{_coord(left)}" y1="{_coord(plot_bottom)}" x2="{_coord(left + plot_w)}" '
        f'y2="{_coord(plot_bottom)}" stroke="{AXIS}" stroke-width="1.5"/>'
    )
    body.append(_text(left + plot_w / 2, height - 34, axis_label, size=12.5, anchor="middle"))
    body.append(
        _text(
            30,
            top + plot_h / 2,
            "Latency (ms)",
            size=12.5,
            anchor="middle",
            transform=f"rotate(-90 30 {_coord(top + plot_h / 2)})",
        )
    )

    for value, colour, name in (
        (history.interim_p50, P50_COLOR, "interim p50 target"),
        (history.interim_p95, P95_COLOR, "interim p95 target"),
    ):
        y = y_at(value)
        body.append(
            f'    <line x1="{_coord(left)}" y1="{_coord(y)}" x2="{_coord(left + plot_w)}" '
            f'y2="{_coord(y)}" stroke="{colour}" stroke-width="1.5" stroke-dasharray="8 6" opacity="0.65"/>'
        )
        if not target_labels:
            continue
        body.append(
            _text(
                left + plot_w * 0.52,
                y - 6,
                f"{name} {_coord(value)} ms",
                size=11,
                anchor="middle",
                fill=colour,
                weight="600",
            )
        )

    for index, run in enumerate(runs):
        x = x_at(index)
        body.append(_text(x, plot_bottom + 26, run.label, size=11.5, anchor="middle", weight="600"))
        if run.date:
            body.append(_text(x, plot_bottom + 42, run.date, size=10, anchor="middle", fill=MUTED))

    for key, colour, offset, anchor in (("p50", P50_COLOR, -12, "middle"), ("p95", P95_COLOR, 19, "middle")):
        values = [getattr(run, key) for run in runs]
        points = " ".join(f"{_coord(x_at(index))},{_coord(y_at(value))}" for index, value in enumerate(values))
        body.append(
            f'    <polyline points="{points}" fill="none" stroke="{colour}" stroke-width="2.5" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for index, value in enumerate(values):
            x = x_at(index)
            y = y_at(value)
            body.append(f'    <circle cx="{_coord(x)}" cy="{_coord(y)}" r="4.5" fill="{colour}"/>')
            body.append(_text(x, y + offset, f"{value:,.0f}", size=10.5, anchor=anchor, fill=colour, weight="600"))

    body.append(
        _text(
            32,
            height - 12,
            "Source: docs/data/latency-history.json. Interim targets are dashed; long-term goals are 1500 / 2000 ms.",
            size=10.5,
            fill=MUTED,
        )
    )
    return _svg_document(width, height, title, body)


def render_stages_svg(history: History) -> str:
    width = 1080
    left, right, top = 250, 150, 118
    bar_h, gap = 46, 58
    plot_w = width - left - right
    entries = len(history.stage_bars)
    last_bottom = top + (entries - 1) * (bar_h + gap) + bar_h
    height = last_bottom + 112

    max_total = max((bar.total for bar in history.stage_bars), default=0.0)
    step = _nice_step(max_total)
    x_max = _axis_max(max_total, step)
    tick_count = int(round(x_max / step))

    body: list[str] = [
        _text(32, 42, "Worker latency by stage", size=20, weight="700"),
        _text(
            32,
            66,
            "Stacked worker stages for each worker_stages_ms entry, in milliseconds",
            size=13,
            fill=MUTED,
        ),
    ]

    for tick in range(tick_count + 1):
        x = left + (tick * step / x_max) * plot_w
        body.append(
            f'    <line x1="{_coord(x)}" y1="{_coord(top - 12)}" x2="{_coord(x)}" '
            f'y2="{_coord(last_bottom + 6)}" stroke="{GRID}" stroke-width="1"/>'
        )
        body.append(_text(x, last_bottom + 28, f"{tick * step:,.0f}", size=11, anchor="middle", fill=MUTED))
    body.append(
        f'    <line x1="{_coord(left)}" y1="{_coord(top - 12)}" x2="{_coord(left)}" '
        f'y2="{_coord(last_bottom + 6)}" stroke="{AXIS}" stroke-width="1.5"/>'
    )
    body.append(_text(left + plot_w / 2, last_bottom + 62, "Latency (ms)", size=12.5, anchor="middle"))

    for row, bar in enumerate(history.stage_bars):
        bar_y = top + row * (bar_h + gap)
        body.append(_text(left - 14, bar_y + bar_h / 2 + 4, bar.label, size=12.5, anchor="end", weight="600"))
        offset = 0.0
        for stage, value in bar.segments:
            seg_w = (value / x_max) * plot_w if x_max else 0.0
            seg_x = left + (offset / x_max) * plot_w if x_max else float(left)
            colour = STAGE_COLORS[STAGE_ORDER.index(stage)]
            if seg_w > 0:
                body.append(
                    f'    <rect x="{_coord(seg_x)}" y="{_coord(bar_y)}" width="{_coord(seg_w)}" '
                    f'height="{bar_h}" fill="{colour}" stroke="{BACKGROUND}" stroke-width="1"/>'
                )
            needed = len(stage) * 6.2 + 10
            if seg_w >= needed:
                body.append(
                    _text(
                        seg_x + seg_w / 2,
                        bar_y + bar_h / 2 + 4,
                        stage,
                        size=11,
                        anchor="middle",
                        fill=_text_on(colour),
                        weight="600",
                    )
                )
            else:
                body.append(
                    _text(
                        seg_x + seg_w / 2,
                        bar_y - 5,
                        stage,
                        size=10.5,
                        anchor="middle",
                        fill=INK,
                        weight="600",
                    )
                )
            offset += value
        body.append(
            _text(
                left + (bar.total / x_max) * plot_w + 10,
                bar_y + bar_h / 2 + 5,
                f"{bar.total:,.0f} ms",
                size=13,
                weight="700",
            )
        )

    body.append(
        _text(
            32,
            height - 12,
            "Source: docs/data/latency-history.json. Bar totals sum the worker stages.",
            size=10.5,
            fill=MUTED,
        )
    )
    return _svg_document(width, height, "Worker latency by stage", body)


def write_charts(history: History, out_dir: Path) -> tuple[Path, ...]:
    out_dir.mkdir(parents=True, exist_ok=True)
    history_path = out_dir / "latency-history.svg"
    stages_path = out_dir / "latency-stages.svg"
    history_path.write_text(render_history_svg(history), encoding="utf-8")
    stages_path.write_text(render_stages_svg(history), encoding="utf-8")
    written = [history_path, stages_path]
    if history.worker_runs:
        worker_path = out_dir / "latency-worker.svg"
        worker_path.write_text(
            render_history_svg(
                history,
                runs=history.worker_runs,
                title="Worker-side TTFA over time",
                subtitle="p50 and p95 time to first audio measured inside the worker (turn_metrics), per hour with 8+ turns; dashed lines are the interim targets (2,000 / 4,000 ms)",
                axis_label="Hour (UTC)",
                target_labels=False,
            ),
            encoding="utf-8",
        )
        written.append(worker_path)
    return tuple(written)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help=f"probe history JSON (default: {DEFAULT_DATA})")
    parser.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT_DIR, help=f"SVG output directory (default: {DEFAULT_OUT_DIR})"
    )
    args = parser.parse_args(argv)
    try:
        history = load_history(args.data)
        written = write_charts(history, args.out_dir)
    except (OSError, ValueError) as exc:
        print(f"latency_charts: {exc}", file=sys.stderr)
        return 2
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
