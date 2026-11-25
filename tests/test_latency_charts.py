import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import latency_charts  # noqa: E402

SVG_NS = "{http://www.w3.org/2000/svg}svg"

HISTORY = {
    "probe_runs": [
        {"label": "run <&> one", "date": "11-24 08:12", "p50": 2100, "p95": 4200},
        {"label": "second run", "date": "11-24 09:00", "p50": 1800, "p95": 3600},
    ],
    "worker_stages_ms": {
        "before (#264 baseline)": {
            "end of turn": 577,
            "turn start": 405,
            "command": 535,
            "model first chunk": 643,
            "tts": 170,
        },
        "now": {
            "end of turn": 577,
            "turn start": 388,
            "command": 535,
            "model first chunk": 643,
            "tts": 170,
        },
    },
    "targets": {"p50": 1500, "p95": 2000, "interim_p50": 2000, "interim_p95": 4000},
}


def _render(tmp_path: Path, payload: object) -> Path:
    data = tmp_path / "data.json"
    data.write_text(json.dumps(payload), encoding="utf-8")
    out_dir = tmp_path / "img"
    assert latency_charts.main(["--data", str(data), "--out-dir", str(out_dir)]) == 0
    return out_dir


def _text_of(path: Path) -> str:
    return "".join(ET.parse(path).getroot().itertext())


def test_both_svg_files_are_valid_xml(tmp_path):
    out_dir = _render(tmp_path, HISTORY)
    for name in ("latency-history.svg", "latency-stages.svg"):
        path = out_dir / name
        assert path.is_file()
        root = ET.parse(path).getroot()
        assert root.tag == SVG_NS
        assert root.get("viewBox")


def test_every_run_label_appears_and_text_is_escaped(tmp_path):
    out_dir = _render(tmp_path, HISTORY)
    history_path = out_dir / "latency-history.svg"
    raw = history_path.read_text(encoding="utf-8")
    text = _text_of(history_path)

    for run in HISTORY["probe_runs"]:
        assert run["label"] in text
    assert "&lt;&amp;&gt;" in raw
    assert "<&>" not in raw


def test_stage_labels_and_targets_appear(tmp_path):
    out_dir = _render(tmp_path, HISTORY)
    stages_text = _text_of(out_dir / "latency-stages.svg")
    for bar_label in HISTORY["worker_stages_ms"]:
        assert bar_label in stages_text
    for stage in latency_charts.STAGE_ORDER:
        assert stage in stages_text

    history_text = _text_of(out_dir / "latency-history.svg")
    assert "interim p50 target" in history_text
    assert "interim p95 target" in history_text
    assert "1500" in history_text
    assert "2000" in history_text


def test_output_is_deterministic(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    data = tmp_path / "data.json"
    data.write_text(json.dumps(HISTORY), encoding="utf-8")

    assert latency_charts.main(["--data", str(data), "--out-dir", str(first_dir)]) == 0
    assert latency_charts.main(["--data", str(data), "--out-dir", str(second_dir)]) == 0
    for name in ("latency-history.svg", "latency-stages.svg"):
        assert (first_dir / name).read_bytes() == (second_dir / name).read_bytes()


def test_invalid_json_returns_2(tmp_path, capsys):
    data = tmp_path / "bad.json"
    data.write_text("{", encoding="utf-8")
    result = latency_charts.main(["--data", str(data), "--out-dir", str(tmp_path / "img")])
    assert result == 2
    assert "invalid JSON" in capsys.readouterr().err


def test_missing_data_returns_2(tmp_path, capsys):
    result = latency_charts.main(["--data", str(tmp_path / "missing.json"), "--out-dir", str(tmp_path / "img")])
    assert result == 2
    assert "cannot read data file" in capsys.readouterr().err


def test_worker_chart_written_from_the_repo_data(tmp_path):
    assert latency_charts.main(["--out-dir", str(tmp_path)]) == 0
    assert "Worker-side TTFA" in (tmp_path / "latency-worker.svg").read_text(encoding="utf-8")
