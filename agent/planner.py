"""planner.py — 把自然语言问题变成工具调用计划。

默认是确定性规则规划器（不需要 API key、不产生幻觉）。
设置 OPENAI_API_KEY + --llm 时才走 LLM 规划，且 LLM 只能从工具清单里选，
不能自己写数字；任何解析失败都回退到规则规划。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from tools import panel

BK_WORDS = {
    "国民银行": "N", "州成员银行": "SM", "州非成员银行": "NM",
    "储蓄机构": "SA", "储蓄银行": "SB", "外国银行": "OI",
}
REGION_WORDS = {"东北": "northeast", "中西部": "midwest", "南部": "south", "西部": "west"}

TOOL_CATALOG = [
    ("panel_overview", "数据总览：网点数、银行数、观测窗、总体关停率"),
    ("panel_query", "按年份/BKCLASS/CERT/年龄/邻居数/区域筛样本，返回关停率与 Wilson 区间"),
    ("panel_km", "Kaplan-Meier 生存曲线（按 BKCLASS 或单家银行）"),
    ("panel_neighbor_density", "按邻居数分桶看关停率"),
    ("panel_bank_profile", "单家银行画像：网点数、关停数、脆弱度分层"),
    ("engine_risk", "网点关停风险打分（cloglog，模型域 1994–2015）"),
    ("engine_impact", "网点关闭的空间冲击/溢出评估"),
    ("engine_grade", "结论强度评级 A/B/C"),
    ("engine_caliber", "空间口径阶梯 L0–L3"),
    ("engine_intake", "数据准入体检：这套数据能不能做因果评估"),
]


def _years(q: str) -> list[int]:
    return [int(y) for y in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", q)]


def _bkclass(q: str) -> str | None:
    for word, code in BK_WORDS.items():
        if word in q:
            return code
    m = re.search(r"\b(NM|SM|SA|SB|SL|OI|N)\b", q)
    return m.group(1) if m else None


def _cert(q: str):
    m = re.search(r"(?:CERT|cert|银行编号|银行)\s*[:：]?\s*(\d{1,6})(?!\d)", q)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{4,6})\s*号", q)
    return int(m.group(1)) if m else None


def _region(q: str):
    for word, code in REGION_WORDS.items():
        if word in q:
            return code
    return None


def _strength(q: str):
    m = re.search(r"强度\s*[=:：]?\s*([0-9]+(?:\.[0-9]+)?)", q)
    return float(m.group(1)) if m else None


def _fragility(q: str):
    m = re.search(r"\bL([012])\b", q)
    if not m:
        return None
    return {"0": "L0_单网点", "1": "L1_多网点高集中", "2": "L2_多网点地理分散"}[m.group(1)]


def _panel_defaults() -> dict:
    """未指定参数时用面板中位数代参——必须在报告里明示。"""
    df = panel.load_panel()
    return {
        "age": float(df["age"].median()),
        "log_depsumbr": float(__import__("math").log1p(df["DEPSUMBR_last"].median())),
        "neighbor_count": float(df["neighbor_count"].median()),
        "lat": float(df["lat"].median()),
        "lng": float(df["lng"].median()),
        "bank_closed_rate": float((df["event"] == "closed").mean()),
    }


def rule_plan(question: str) -> dict:
    q = question or ""
    years = _years(q)
    y_from, y_to = (min(years), max(years)) if years else (None, None)
    bk, cert, region = _bkclass(q), _cert(q), _region(q)
    assumptions: list[str] = []

    def has(*words):
        return any(w in q for w in words)

    # 单家银行：先画像，再打分（真·多步）
    if cert is not None:
        steps = [{"tool": "panel_bank_profile", "args": {"cert": cert}}]
        if has("风险", "打分", "关停概率"):
            year = y_to if (y_to and 1994 <= y_to <= 2015) else 2010
            if y_to and y_to > 2015:
                assumptions.append(f"问题里的 {y_to} 超出模型可用域 1994–2015，风险打分改用 2010")
            else:
                assumptions.append(f"未指定打分年份，按 {year}（模型可用域内）；参数取该银行网点的中位数")
            steps.append({"tool": "engine_risk", "args": {"cert": cert, "year": year}})
        return {"intent": "bank_profile", "steps": steps,
                "assumptions": assumptions, "clarify": []}

    if has("总览", "概览", "有多少", "数据多大", "样本量多少"):
        return {"intent": "overview", "steps": [{"tool": "panel_overview", "args": {}}],
                "assumptions": assumptions, "clarify": []}

    if has("生存", "存活", "寿命", "KM", "曲线", "活多久"):
        args = {"group_by": "bkclass", "cohort": "balanced"}
        if cert is not None:
            args = {"group_by": "cert", "value": cert, "cohort": "balanced"}
        elif bk:
            args["value"] = bk
        return {"intent": "km", "steps": [{"tool": "panel_km", "args": args}],
                "assumptions": assumptions, "clarify": []}

    if has("密集", "密度", "邻居", "扎堆", "聚集"):
        return {"intent": "neighbor_density",
                "steps": [{"tool": "panel_neighbor_density",
                           "args": {"year_from": y_from, "year_to": y_to, "bkclass": bk}}],
                "assumptions": assumptions, "clarify": []}

    if has("冲击", "溢出", "关掉一家", "关闭一家", "外溢"):
        strength = _strength(q)
        if strength is None:
            strength = 1.0
            assumptions.append("未给暴露强度，按 strength=1.0 演示；结论随输入连续变化")
        return {"intent": "impact",
                "steps": [{"tool": "engine_impact",
                           "args": {"strength": strength, "event_year": y_to}}],
                "assumptions": assumptions, "clarify": []}

    if has("证据等级", "结论强度", "能不能下结论", "可信吗", "A级", "B级", "C级"):
        return {"intent": "grade", "steps": [{"tool": "engine_grade", "args": {}}],
                "assumptions": assumptions, "clarify": []}

    if has("口径", "阶梯", "L0", "L3", "精度"):
        return {"intent": "caliber",
                "steps": [{"tool": "engine_caliber", "args": {"dataset": "fdic"}}],
                "assumptions": assumptions, "clarify": []}

    if has("准入", "能不能做", "数据够不够", "可行性"):
        return {"intent": "intake", "steps": [], "assumptions": assumptions,
                "clarify": ["准入体检需要 7 个字段：stableId / idMatchRate / coordNullRate / "
                            "outcomeLevel / years / eventDefined / exogenous"]}

    if has("风险", "打分", "关停概率", "违约"):
        d = _panel_defaults()
        args = dict(d)
        args.update({"year": y_to or 2010, "bkclass": bk or "N",
                     "fragility": _fragility(q) or "L0_单网点"})
        assumptions.append("未指定的风险参数用面板中位数代参（age / 存款 / 邻居数 / 经纬度 / 关停率）")
        return {"intent": "risk", "steps": [{"tool": "engine_risk", "args": args}],
                "assumptions": assumptions, "clarify": []}

    if has("关停", "关闭", "退出", "倒闭", "存活率"):
        args = {"year_from": y_from, "year_to": y_to, "bkclass": bk,
                "cert": cert, "region": region}
        return {"intent": "closure_rate",
                "steps": [{"tool": "panel_query", "args": args}],
                "assumptions": assumptions, "clarify": []}

    return {"intent": "unknown", "steps": [], "assumptions": assumptions,
            "clarify": ["没听懂要算什么。可问：关停率、生存曲线、邻域密度、单家银行画像、"
                        "风险打分、空间冲击、结论等级、口径阶梯"]}


def llm_plan(question: str) -> dict:
    """可选：用 OpenAI 兼容接口做工具选择。失败一律回退规则规划。"""
    key = os.environ.get("OPENAI_API_KEY")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    if not key:
        return rule_plan(question)

    schema = "\n".join(f"- {name}: {desc}" for name, desc in TOOL_CATALOG)
    sys_prompt = (
        "你是规划器，只能从工具清单里选工具并填参数，不能编造任何数字。"
        "数字参数只能来自用户输入；缺参数就不要填。"
        "输出严格 JSON：{\"intent\":str,\"steps\":[{\"tool\":str,\"args\":{}}],\"clarify\":[str]}"
        f"\n工具清单：\n{schema}"
    )
    body = json.dumps({
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": question}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        plan = json.loads(payload["choices"][0]["message"]["content"])
    except Exception as exc:  # 网络/解析/格式问题都回退
        fallback = rule_plan(question)
        fallback.setdefault("assumptions", []).append(f"LLM 规划失败，已回退规则规划：{exc}")
        return fallback

    allowed = {name for name, _ in TOOL_CATALOG}
    steps = [s for s in plan.get("steps", []) if s.get("tool") in allowed]
    if not steps:
        return rule_plan(question)
    plan["steps"] = steps
    plan.setdefault("assumptions", []).append("本计划由 LLM 选择工具；所有数字仍由工具计算")
    plan.setdefault("clarify", [])
    return plan