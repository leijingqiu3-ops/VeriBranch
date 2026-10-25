"""registry.py — 工具白名单与参数 schema。

Attest 层只放行这里注册过的工具与参数，模型说破天也调不到别的。
"""
from __future__ import annotations

TOOLS = {
    "panel_overview": {"desc": "数据总览", "params": {}},
    "panel_query": {"desc": "筛选样本并计算关停率", "params": {
        "year_from": {"type": "int", "min": 1994, "max": 2025},
        "year_to": {"type": "int", "min": 1994, "max": 2025},
        "bkclass": {"type": "str", "enum": ["N", "NM", "SM", "SA", "SB", "SL", "OI"]},
        "cert": {"type": "int", "min": 1},
        "min_age": {"type": "int", "min": 0},
        "max_age": {"type": "int", "min": 0},
        "min_neighbors": {"type": "int", "min": 0},
        "region": {"type": "str", "enum": ["northeast", "midwest", "south", "west"]},
    }},
    "panel_km": {"desc": "Kaplan-Meier 生存曲线", "params": {
        "group_by": {"type": "str", "enum": ["bkclass", "cert", "all"]},
        "value": {},
        "year_from": {"type": "int", "min": 1994, "max": 2025},
        "year_to": {"type": "int", "min": 1994, "max": 2025},
        "cohort": {"type": "str", "enum": ["balanced", "all"]},
    }},
    "panel_neighbor_density": {"desc": "邻居数分桶关停率", "params": {
        "year_from": {"type": "int", "min": 1994, "max": 2025},
        "year_to": {"type": "int", "min": 1994, "max": 2025},
        "bkclass": {"type": "str", "enum": ["N", "NM", "SM", "SA", "SB", "SL", "OI"]},
    }},
    "panel_bank_profile": {"desc": "单家银行画像", "params": {
        "cert": {"type": "int", "min": 1, "required": True},
    }},
    "engine_risk": {"desc": "网点关停风险打分", "params": {
        "age": {"type": "float", "min": 0},
        "log_depsumbr": {"type": "float"},
        "neighbor_count": {"type": "float", "min": 0},
        "lat": {"type": "float", "min": 15, "max": 72},
        "lng": {"type": "float", "min": -180, "max": -60},
        "bank_closed_rate": {"type": "float", "min": 0, "max": 1},
        "year": {"type": "int", "min": 1994, "max": 2015},
        "bkclass": {"type": "str", "enum": ["N", "NM", "SM", "SA", "SB", "SL", "OI"]},
        "fragility": {"type": "str", "enum": ["L0_单网点", "L1_多网点高集中", "L2_多网点地理分散"]},
        "cert": {"type": "int", "min": 1},
    }},
    "engine_impact": {"desc": "空间冲击评估", "params": {
        "strength": {"type": "float", "min": 0},
        "event_year": {"type": "int", "min": 1994, "max": 2025},
    }},
    "engine_grade": {"desc": "结论强度评级", "params": {
        "exogenous": {"type": "bool"},
    }},
    "engine_caliber": {"desc": "空间口径阶梯", "params": {
        "dataset": {"type": "str", "enum": ["fdic", "sz_bike", "snap_brightkite", "snap_gowalla"]},
    }},
    "engine_intake": {"desc": "数据准入体检", "params": {
        "stableId": {"type": "bool"},
        "idMatchRate": {"type": "float", "min": 0, "max": 1},
        "coordNullRate": {"type": "float", "min": 0, "max": 1},
        "outcomeLevel": {"type": "str"},
        "years": {"type": "int", "min": 1},
        "eventDefined": {"type": "bool"},
        "exogenous": {"type": "bool"},
    }},
}


def validate(name: str, args: dict) -> tuple[bool, str]:
    if name not in TOOLS:
        return False, f"工具 {name!r} 不在白名单"
    spec = TOOLS[name]["params"]
    unknown = set(args) - set(spec)
    if unknown:
        return False, f"未知参数: {sorted(unknown)}"
    for key, rule in spec.items():
        if rule.get("required") and key not in args:
            return False, f"缺少必填参数 {key}"
        if key not in args or args[key] is None:
            continue
        val = args[key]
        t = rule.get("type")
        if t == "int" and not isinstance(val, int):
            return False, f"{key} 必须是 int"
        if t == "float" and not isinstance(val, (int, float)):
            return False, f"{key} 必须是数字"
        if t == "str" and not isinstance(val, str):
            return False, f"{key} 必须是 str"
        if t == "bool" and not isinstance(val, bool):
            return False, f"{key} 必须是 bool"
        if "min" in rule and val < rule["min"]:
            return False, f"{key}={val} 小于下界 {rule['min']}"
        if "max" in rule and val > rule["max"]:
            return False, f"{key}={val} 大于上界 {rule['max']}"
        if "enum" in rule and val not in rule["enum"]:
            return False, f"{key}={val!r} 不在 {rule['enum']}"
    return True, "ok"