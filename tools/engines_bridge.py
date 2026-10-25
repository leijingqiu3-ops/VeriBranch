"""engines_bridge.py — 调用上游决策台的确定性引擎（node 子进程）。

上游把 6 个引擎写成浏览器脚本 portal/assets/engines.js，
通过 globalThis.SDP_ENGINE 导出。这里用一个 node 子进程桥接，
不复制、不改写上游代码；上游更新后本层自动跟随。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "tools" / "bridge.js"

DEFAULT_REPOS = [
    Path(r"D:\网点项目\UCP-and-IS_Spatial-Big-Data-Analysis"),
    ROOT.parent / "UCP-and-IS_Spatial-Big-Data-Analysis",
]

FRAGILITY_TIERS = {"L0_单网点", "L1_多网点高集中", "L2_多网点地理分散"}


def repo_path() -> Path:
    env = os.environ.get("BRANCH_AGENT_REPO")
    if env:
        return Path(env)
    for p in DEFAULT_REPOS:
        if (p / "portal" / "assets" / "engines.js").exists():
            return p
    return DEFAULT_REPOS[0]


def available() -> tuple[bool, str]:
    if shutil.which("node") is None:
        return False, "找不到 node，无法调用上游引擎"
    if not BRIDGE.exists():
        return False, f"缺少桥接脚本: {BRIDGE}"
    repo = repo_path()
    if not (repo / "portal" / "assets" / "engines.js").exists():
        return False, f"上游仓库不完整: {repo}/portal/assets/engines.js 不存在（先 git pull）"
    if not (repo / "portal" / "data" / "portal_data.json").exists():
        return False, f"缺少数据层: {repo}/portal/data/portal_data.json"
    return True, "ok"


def call(tool: str, payload: dict | None = None, timeout: int = 60) -> dict:
    ok, why = available()
    if not ok:
        return {"ok": False, "reason": why, "tool": f"engine_{tool}",
                "source": ["portal/assets/engines.js"]}
    try:
        proc = subprocess.run(
            ["node", str(BRIDGE), str(repo_path()), tool,
             json.dumps(payload or {}, ensure_ascii=False)],
            capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"引擎调用超时（>{timeout}s）", "tool": f"engine_{tool}"}
    except OSError as exc:
        return {"ok": False, "reason": f"无法启动 node: {exc}", "tool": f"engine_{tool}"}

    out = proc.stdout.decode("utf-8", errors="replace").strip()
    if not out:
        err = proc.stderr.decode("utf-8", errors="replace")[:500]
        return {"ok": False, "reason": f"引擎无输出: {err}", "tool": f"engine_{tool}"}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "reason": f"引擎输出不是 JSON: {out[:300]}", "tool": f"engine_{tool}"}
    data.setdefault("ok", True)  # gradeFromData 等不带 ok 字段
    data.setdefault("tool", f"engine_{tool}")
    if "source" not in data:
        data["source"] = ["portal/assets/engines.js", "portal/data/portal_data.json"]
    return data


# --------------------------------------------------------------------------
# 参数校验：上游引擎对未知 BKCLASS / fragility 会静默返回结果，
# 这里在调用前挡住——这是本 Agent 相对裸引擎多出来的一层。
# --------------------------------------------------------------------------

class ArgError(ValueError):
    pass


def risk(age, log_depsumbr, neighbor_count, lat, lng, bank_closed_rate,
         year, bkclass, fragility) -> dict:
    problems = []
    if bkclass not in {"N", "NM", "SM", "SA", "SB", "SL", "OI"}:
        problems.append(f"BKCLASS={bkclass!r} 不在已知集合")
    if fragility not in FRAGILITY_TIERS:
        problems.append(f"fragility={fragility!r} 不在已知分层 {sorted(FRAGILITY_TIERS)}")
    if not 1994 <= int(year) <= 2025:
        problems.append(f"year={year} 超出面板观测窗 1994–2025")
    if not (0 <= float(bank_closed_rate) <= 1):
        problems.append("bank_closed_rate 必须在 [0,1]")
    if float(age) < 0 or float(neighbor_count) < 0:
        problems.append("age / neighbor_count 不能为负")
    if not (-180 <= float(lng) <= -60 and 15 <= float(lat) <= 72):
        problems.append("lat/lng 不在美国本土经纬度范围内")
    if problems:
        return {"ok": False, "tool": "engine_risk",
                "reason": "参数校验未通过: " + "；".join(problems),
                "attest": "arg_validation"}

    return call("risk", {
        "age": float(age), "log_depsumbr": float(log_depsumbr),
        "neighbor_count": float(neighbor_count),
        "lat": float(lat), "lng": float(lng),
        "bank_closed_rate": float(bank_closed_rate),
        "year": int(year), "bkclass": bkclass, "fragility": fragility,
    })


def impact(strength: float, event_year: int | None = None) -> dict:
    if strength < 0:
        return {"ok": False, "tool": "engine_impact", "reason": "strength 不能为负",
                "attest": "arg_validation"}
    payload = {"strength": float(strength)}
    if event_year is not None:
        payload["eventYear"] = int(event_year)
    return call("impact", payload)


def grade(exogenous: bool | None = None) -> dict:
    payload = {"exogenous": exogenous}
    return call("grade", payload)


def caliber(dataset: str = "fdic") -> dict:
    if dataset not in {"fdic", "sz_bike", "snap_brightkite", "snap_gowalla"}:
        return {"ok": False, "tool": "engine_caliber",
                "reason": f"未知 dataset={dataset!r}", "attest": "arg_validation"}
    return call("caliber", {"dataset": dataset})


def intake(answers: dict) -> dict:
    allowed = {"stableId", "idMatchRate", "coordNullRate", "outcomeLevel",
               "years", "eventDefined", "exogenous"}
    unknown = set(answers) - allowed
    if unknown:
        return {"ok": False, "tool": "engine_intake",
                "reason": f"未知字段: {sorted(unknown)}；可用: {sorted(allowed)}",
                "attest": "arg_validation"}
    return call("intake", answers)