import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import trace  # noqa: E402


def _tracer(tmp_path, run_id):
    tr = trace.Tracer(run_id=run_id)
    tr.dir = tmp_path
    tr.path = tmp_path / "trace.jsonl"
    tr._fh.close()
    tr._fh = open(tr.path, "a", encoding="utf-8")
    return tr


def test_chain_verifies(tmp_path):
    tr = _tracer(tmp_path, "t1")
    tr.log("input", {"q": "hi"})
    tr.log("tool_call", {"tool": "panel_query", "ok": True})
    tr.close()
    v = trace.verify(tr.path)
    assert v["ok"] is True and v["checked"] == 2


def test_tamper_detected(tmp_path):
    tr = _tracer(tmp_path, "t2")
    tr.log("input", {"q": "hi"})
    tr.log("output", {"ok": True})
    tr.close()

    lines = tr.path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["q"] = "tampered"
    lines[0] = json.dumps(rec, ensure_ascii=False, sort_keys=True)
    tr.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    v = trace.verify(tr.path)
    assert v["ok"] is False and v["broken_at"] == 1


def test_hash_is_chained(tmp_path):
    tr = _tracer(tmp_path, "t3")
    a = tr.log("input", {"q": "a"})
    b = tr.log("input", {"q": "b"})
    tr.close()
    assert b["prev_hash"] == a["hash"]