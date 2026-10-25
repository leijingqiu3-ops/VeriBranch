"""attest.py — 调用前/后的策略校验层。

分工：
  - 引擎负责「算得对」（系数、口径、适用域）
  - 本层负责「该不该算、能不能这么说」（越权、越域、注入、因果措辞）

每条规则都有 id，命中即写入 trace，便于事后回放。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BLOCK = "block"
WARN = "warn"

# 观测窗与模型可用域（来自上游：数据 1994–2025；cloglog 2016+ 完全分离）
DATA_YEARS = (1994, 2025)
MODEL_YEARS = (1994, 2015)

US_STATES_HINT = re.compile(r"\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b")

CN_GEO = [
    "中国", "国内", "杭州", "上海", "北京", "南京", "苏州", "深圳", "广州", "成都",
    "浙江", "江苏", "广东", "长三角", "江浙沪", "武汉", "西安", "天津", "重庆",
]

INJECTION_PATTERNS = [
    r"忽略(之前|以上|前面|上面)的?(规则|指令|限制|要求)",
    r"(ignore|disregard)\s+(all\s+)?(previous|above|prior)\s+(instructions?|rules?)",
    r"你现在是|从现在开始你",
    r"不需要(审计|校验|验证|检查)",
    r"(绕过|跳过|关闭)(审计|校验|验证|规则|限制|护栏)",
    r"直接(给我|告诉我|输出)(一个|个)?(正|好|乐观|肯定)的?(结论|答案|数)",
    r"(输出|打印|泄露|告诉我)(你的)?(系统)?(提示词|prompt|规则)",
    r"system\s*prompt",
    r"当作(我|用户)说(过)?",
]

FUTURE_PATTERNS = [
    r"(预测|预估|推算|展望|forecast|predict)[^。？\n]{0,20}(未来|明年|后年|20[3-9]\d)",
    r"(未来|明年|后年)[^。？\n]{0,10}(会|将|是否)(关停|倒闭|关闭)",
    r"20[3-9]\d\s*年",
]

CAUSAL_VERBS = ["导致", "造成", "引起", "使得", "带来", "提升了", "降低了", "将会使", "必然"]


@dataclass
class Finding:
    rule: str
    severity: str          # block | warn
    message: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity,
                "message": self.message, "detail": self.detail}


def scan_input(question: str) -> list[Finding]:
    out: list[Finding] = []
    q = question or ""

    for pat in INJECTION_PATTERNS:
        if re.search(pat, q, flags=re.IGNORECASE):
            out.append(Finding("A1-injection", BLOCK,
                               "检测到提示词注入：要求绕过审计/校验，或索取系统规则",
                               {"pattern": pat}))
            break

    for pat in FUTURE_PATTERNS:
        m = re.search(pat, q)
        if m:
            out.append(Finding("A2-out-of-time", BLOCK,
                               "本系统没有时序外推验证，不能预测未来年份的关停/倒闭",
                               {"matched": m.group(0)}))
            break

    hit = [c for c in CN_GEO if c in q]
    if hit:
        out.append(Finding("A3-out-of-domain", BLOCK,
                           f"命中非美国地区关键词 {hit}；面板是美国 FDIC 网点数据，不含中国网点",
                           {"hits": hit}))
    elif re.search(r"\b(上海的?某|杭州的?某)\b", q):
        out.append(Finding("A3-out-of-domain", BLOCK, "面板不含中国网点", {}))

    years = [int(y) for y in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", q)]
    beyond = [y for y in years if y > DATA_YEARS[1]]
    if beyond:
        out.append(Finding("A2-out-of-time", BLOCK,
                           f"问题里的年份 {beyond} 超出面板观测窗 {DATA_YEARS[0]}–{DATA_YEARS[1]}",
                           {"years": beyond}))

    return out


def check_plan(plan: list[dict]) -> list[Finding]:
    out: list[Finding] = []
    if not plan:
        out.append(Finding("A5-empty-plan", WARN, "没有匹配到可执行的工具，需要向用户澄清"))
    return out


def check_result(tool: str, result: dict, context: dict | None = None) -> list[Finding]:
    out: list[Finding] = []
    ctx = context or {}

    if not result.get("ok"):
        out.append(Finding("A6-missing-data", BLOCK,
                           f"{tool} 无法给出结果：{result.get('reason', '未知原因')}",
                           {"tool": tool}))
        return out

    if result.get("isExtrapolation"):
        out.append(Finding("A7-extrapolation", WARN,
                           f"输入强度 {result.get('strengthInput')} 低于观测支撑下界 "
                           f"{result.get('supportLowerBound')}，属于外推区",
                           {"supportLowerBound": result.get("supportLowerBound")}))

    if tool == "engine_impact":
        grade = result.get("grade") or {}
        if str(grade.get("level")) == "C":
            out.append(Finding("A8-causal-language", WARN,
                               "结论强度为 C：只能做描述性表述，禁止因果动词",
                               {"level": "C", "reasons": grade.get("reasons", [])}))

    if tool == "engine_risk" and result.get("outOfTime"):
        out.append(Finding("A2-out-of-time", WARN,
                           "该模型没有通过时序外推验证，分数只能用于历史归因与排序，不能当作未来概率", {}))

    if tool == "panel_query":
        stats = result.get("stats") or {}
        n = stats.get("n") or 0
        if n and n < 100:
            out.append(Finding("A9-small-sample", WARN,
                               f"样本量仅 {n}，比率不稳定，不应据此下结论", {"n": n}))

    if ctx.get("grade_level") == "C":
        out.append(Finding("A8-causal-language", WARN,
                           "当前结论等级为 C，报告中禁止出现因果动词", {}))

    return out


def strip_causal_language(text: str) -> tuple[str, list[str]]:
    """C 级结论下的措辞降级：把因果动词换成描述性表述。"""
    removed = []
    mapping = {
        "导致": "同时出现", "造成": "同时出现", "引起": "同时出现",
        "使得": "并与", "带来": "伴随", "提升了": "高于", "降低了": "低于",
        "将会使": "可能伴随", "必然": "在样本内通常",
    }
    for bad, good in mapping.items():
        if bad in text:
            removed.append(bad)
            text = text.replace(bad, good)
    return text, removed


def summarize(findings: list[Finding]) -> dict:
    blocks = [f for f in findings if f.severity == BLOCK]
    warns = [f for f in findings if f.severity == WARN]
    return {
        "blocked": bool(blocks),
        "block_count": len(blocks),
        "warn_count": len(warns),
        "findings": [f.as_dict() for f in findings],
    }