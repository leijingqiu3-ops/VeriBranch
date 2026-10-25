"""trace.py — 每次工具调用都留痕，并用哈希链把记录串起来（防篡改）。

不是公钥签名：没有第三方可信时间戳，只能证明「这份日志自写出后没被改过」。
这一点在 README 的 Limitations 里写清楚，不夸大成"区块链存证"。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
GENESIS = "0" * 64


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str,
                      separators=(",", ":"))


def _digest(prev_hash: str, entry: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(entry)).encode("utf-8")).hexdigest()


class Tracer:
    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.dir = RUNS / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "trace.jsonl"
        self.prev_hash = GENESIS
        self.seq = 0
        self._fh = open(self.path, "a", encoding="utf-8")

    def log(self, kind: str, payload: dict) -> dict:
        self.seq += 1
        entry = {
            "seq": self.seq,
            "ts": round(time.time(), 3),
            "run_id": self.run_id,
            "kind": kind,
            "payload": payload,
        }
        h = _digest(self.prev_hash, entry)
        record = dict(entry, prev_hash=self.prev_hash, hash=h)
        self._fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self.prev_hash = h
        return record

    @property
    def head(self) -> str:
        return self.prev_hash

    def close(self):
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def verify(path: str | Path) -> dict:
    """逐条重算哈希链，返回是否完整、断点位置。"""
    path = Path(path)
    prev = GENESIS
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        body = {k: rec[k] for k in ("seq", "ts", "run_id", "kind", "payload")}
        expected = _digest(prev, body)
        if rec.get("prev_hash") != prev or rec.get("hash") != expected:
            return {"ok": False, "checked": count, "broken_at": rec.get("seq"), "path": str(path)}
        prev = rec["hash"]
        count += 1
    return {"ok": True, "checked": count, "head": prev, "path": str(path)}
if __name__ == "__main__":
    import sys as _sys
    print(json.dumps(verify(_sys.argv[1]), ensure_ascii=False, indent=2))
