"""Render per-turn latency timelines from turn-metric JSON lines.

The module is pure: the CLI reads records from disk, every timeline decision and
both renderers are data in, strings out.  The origin ``t=0`` is the end of the
user's speech.  Stage offsets captured from the hook start are shifted by
``eot_ms`` because the hook starts that many milliseconds after the origin.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

STAGE_PREFIX = "t_"
STAGE_SUFFIX = "_ms"

CRITICAL_CANDIDATES = frozenset({"turn start", "command", "model first chunk"})

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #ffffff;
  --fg: #1a1a1a;
  --muted: #666666;
  --track: #ececec;
  --bar: #3b6fd8;
  --critical: #d9480f;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111111;
    --fg: #f2f2f2;
    --muted: #a0a0a0;
    --track: #2a2a2a;
    --bar: #6ea8fe;
    --critical: #ff8c42;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 24px;
  background: var(--bg);
  color: var(--fg);
  font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif;
}
h1 { margin: 0 0 20px; font-size: 18px; }
h2 { margin: 0 0 10px; font-size: 15px; font-weight: 600; }
.ttfa { color: var(--muted); font-weight: 400; margin-left: 8px; }
.turn { padding: 14px 0; border-top: 1px solid var(--track); }
.track {
  position: relative;
  height: 24px;
  margin-bottom: 4px;
  background: var(--track);
  border-radius: 4px;
  overflow: hidden;
}
.bar {
  position: absolute;
  top: 0;
  bottom: 0;
  background: var(--bar);
  border-radius: 3px;
  overflow: hidden;
  white-space: nowrap;
}
.bar.critical { background: var(--critical); }
.bar-label {
  display: block;
  padding: 4px 6px;
  color: #ffffff;
  font-size: 12px;
  line-height: 16px;
}
"""


@dataclass(frozen=True)
class Segment:
    """One horizontal span of a turn timeline, in milliseconds from t=0."""

    name: str
    start_ms: int
    end_ms: int
    critical: bool = False


def parse_turn(record: Mapping[str, object]) -> list[Segment]:
    """Turn one turn-metric record into absolute-time segments."""
    eot_ms = _as_int(record.get("eot_ms"))
    stages = record.get("stages")
    stage_map: Mapping[str, object] = stages if isinstance(stages, Mapping) else {}
    base = eot_ms if eot_ms is not None else 0

    segments: list[Segment] = []
    if eot_ms is not None:
        segments.append(Segment("end of turn", 0, eot_ms))
    _add_pair(segments, "turn start", stage_map, base, "t_turn_start_begin_ms", "t_turn_start_end_ms")
    _add_pair(segments, "command", stage_map, base, "t_command_begin_ms", "t_command_end_ms")
    _add_pair(segments, "model first chunk", stage_map, base, "t_model_request_ms", "t_model_first_chunk_ms")
    _add_pair(segments, "tool", stage_map, base, "t_tool_start_ms", "t_tool_end_ms")

    chunk = _stage_ms(stage_map, "t_model_first_chunk_ms")
    tts = _stage_ms(stage_map, "t_tts_first_ms")
    if chunk is not None and tts is not None and tts > chunk:
        # The gap between the model's first words and their release to TTS (budget take), not synthesis time.
        segments.append(Segment("to tts", base + chunk, base + tts))
    ttfa = _as_int(record.get("ttfa_ms"))
    if tts is not None and ttfa is not None and base + tts < ttfa:
        segments.append(Segment("tts to audio", base + tts, ttfa))

    candidates = [segment for segment in segments if segment.name in CRITICAL_CANDIDATES]
    if not candidates:
        return segments
    latest = max(candidates, key=lambda segment: segment.end_ms)
    return [replace(segment, critical=segment is latest) for segment in segments]


def render_text(
    turn_id: str,
    segments: Sequence[Segment],
    ttfa_ms: int | None,
    width: int = 60,
) -> str:
    """Render one turn as an ASCII Gantt chart, one row per segment."""
    ttfa_label = "n/a" if ttfa_ms is None else str(ttfa_ms)
    lines = [f"{turn_id}  ttfa_ms={ttfa_label}"]
    if not segments:
        lines.append("  (no segments)")
        return "\n".join(lines)

    scale = max([segment.end_ms for segment in segments] + ([ttfa_ms] if ttfa_ms is not None else []))
    scale = max(scale, 1)
    for segment in segments:
        bar = _text_bar(segment, scale, width)
        marker = "  critical" if segment.critical else ""
        lines.append(
            f"  {segment.name:<18} {segment.start_ms:>7}..{segment.end_ms:>7} ms  [{bar}]{marker}",
        )
    return "\n".join(lines)


def render_html(turns: Iterable[Mapping[str, Any] | object]) -> str:
    """Render all turns as one self-contained HTML document."""
    sections = [_render_html_turn(turn) for turn in turns]
    body = "\n".join(sections) if sections else '<p class="ttfa">No turns with stage data.</p>'
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Turn timeline</title>\n<style>"
        + _CSS
        + "</style>\n</head>\n<body>\n<h1>Turn timeline</h1>\n"
        + body
        + "\n</body>\n</html>\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render per-turn latency timelines from JSON lines.")
    parser.add_argument("file", type=Path, help="JSON-lines file of turn-metric records")
    parser.add_argument("--html", type=Path, help="write a self-contained HTML timeline to this path")
    parser.add_argument("--slowest", type=int, default=3, help="number of slowest turns to print (default 3)")
    args = parser.parse_args(argv)
    args.slowest = max(args.slowest, 0)

    turns = _load_turns(args.file)
    if not turns:
        print("no turns with stage data", file=sys.stderr)
        return 1

    if args.html is not None:
        try:
            args.html.write_text(render_html(turns), encoding="utf-8")
        except OSError as error:
            print(f"cannot write {args.html}: {error}", file=sys.stderr)
            return 2

    timed = [turn for turn in turns if turn["ttfa_ms"] is not None]
    if not timed:
        print("no turns have ttfa_ms data", file=sys.stderr)
        return 0

    ordered = sorted(timed, key=lambda turn: turn["ttfa_ms"])
    median = ordered[len(ordered) // 2]
    print("== median turn by ttfa_ms ==")
    print(render_text(median["turn_id"], median["segments"], median["ttfa_ms"]))

    slowest = sorted(timed, key=lambda turn: turn["ttfa_ms"], reverse=True)[: max(args.slowest, 0)]
    if slowest:
        print("\n== slowest turns ==")
    shown = False
    for turn in slowest:
        if turn is median:
            continue
        print(render_text(turn["turn_id"], turn["segments"], turn["ttfa_ms"]))
        print()
        shown = True
    if slowest and not shown:
        print("(median turn is also the only slowest turn)")
    return 0


def _add_pair(
    segments: list[Segment],
    name: str,
    stages: Mapping[str, object],
    base: int,
    begin_key: str,
    end_key: str,
) -> None:
    begin = _stage_ms(stages, begin_key)
    end = _stage_ms(stages, end_key)
    if begin is None or end is None:
        return
    segments.append(Segment(name, base + begin, base + end))


def _stage_ms(stages: Mapping[str, object], key: str) -> int | None:
    return _as_int(stages.get(key))


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value)


def _text_bar(segment: Segment, scale: int, width: int) -> str:
    start = _bar_index(segment.start_ms, scale, width)
    end = _bar_index(segment.end_ms, scale, width)
    if segment.end_ms > segment.start_ms and end <= start:
        end = min(start + 1, width)
    return "#" * start + "=" * max(end - start, 0) + " " * max(width - end, 0)


def _bar_index(value: int, scale: int, width: int) -> int:
    return max(0, min(width, round(value / scale * width)))


def _has_stage_keys(record: Mapping[str, object]) -> bool:
    stages = record.get("stages")
    if not isinstance(stages, Mapping):
        return False
    return any(str(key).startswith(STAGE_PREFIX) and str(key).endswith(STAGE_SUFFIX) for key in stages)


def _load_turns(path: Path) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as error:
                print(f"note: skipping line {line_number}: invalid JSON ({error.msg})", file=sys.stderr)
                continue
            if not isinstance(record, Mapping):
                print(f"note: skipping line {line_number}: record is not an object", file=sys.stderr)
                continue
            turn_id = str(record.get("turn_id", f"line-{line_number}"))
            if not _has_stage_keys(record):
                print(f"note: skipping {turn_id}: no t_* stage keys", file=sys.stderr)
                continue
            turns.append(
                {
                    "turn_id": turn_id,
                    "segments": parse_turn(record),
                    "ttfa_ms": _as_int(record.get("ttfa_ms")),
                },
            )
    return turns


def _render_html_turn(turn: Mapping[str, Any] | object) -> str:
    turn_id = html.escape(str(_turn_field(turn, "turn_id", "turn")))
    ttfa_ms = _as_int(_turn_field(turn, "ttfa_ms", None))
    segments = _turn_field(turn, "segments", [])
    segment_list = list(segments) if isinstance(segments, Iterable) and not isinstance(segments, str) else []

    ttfa_label = "n/a" if ttfa_ms is None else f"{ttfa_ms} ms"
    rows = [_html_bar(segment, segment_list, ttfa_ms) for segment in segment_list]
    return (
        f'<section class="turn">\n<h2>{turn_id}<span class="ttfa">ttfa {ttfa_label}</span></h2>\n'
        + ("\n".join(rows) if rows else '<p class="ttfa">(no segments)</p>')
        + "\n</section>"
    )


def _html_bar(segment: Segment, segments: Sequence[Segment], ttfa_ms: int | None) -> str:
    scale = max([item.end_ms for item in segments] + ([ttfa_ms] if ttfa_ms is not None else []))
    scale = max(scale, 1)
    left = max(0.0, min(100.0, segment.start_ms / scale * 100.0))
    right = max(left, min(100.0, segment.end_ms / scale * 100.0))
    width = max(right - left, 0.4 if segment.end_ms > segment.start_ms else 0.0)
    width = min(width, 100.0 - left)
    css_class = "bar critical" if segment.critical else "bar"
    label = html.escape(f"{segment.name} {segment.start_ms}..{segment.end_ms} ms")
    return (
        f'<div class="track"><div class="{css_class}" style="left: {left:.2f}%; width: {width:.2f}%">'
        f'<span class="bar-label">{label}</span></div></div>'
    )


def _turn_field(turn: Mapping[str, Any] | object, key: str, default: Any) -> Any:
    if isinstance(turn, Mapping):
        return turn.get(key, default)
    return getattr(turn, key, default)


if __name__ == "__main__":
    raise SystemExit(main())
