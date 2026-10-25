"""report.py — 把工具结果渲染成可交付的中文报告。

硬规则：报告里的每个数字都来自工具返回值，模板不做任何计算或补值。
"""
from __future__ import annotations

from agent import attest

TOOL_TITLES = {
    "panel_overview": "数据总览",
    "panel_query": "样本关停率",
    "panel_km": "Kaplan-Meier 生存曲线",
    "panel_neighbor_density": "邻域密度分层",
    "panel_bank_profile": "单家银行画像",
    "engine_risk": "关停风险打分",
    "engine_impact": "空间冲击评估",
    "engine_grade": "结论强度评级",
    "engine_caliber": "空间口径阶梯",
    "engine_intake": "数据准入体检",
}


def pct(x):
    return "—" if x is None else f"{x * 100:.2f}%"


def num(x, nd=3):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _table(headers, rows) -> list[str]:
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return out


def _filters_text(f: dict) -> str:
    if not f:
        return "无筛选（全样本）"
    names = {"year_from": "起始年", "year_to": "结束年", "bkclass": "BKCLASS",
             "cert": "CERT", "min_age": "最小年龄", "max_age": "最大年龄",
             "min_neighbors": "最少邻居数", "region": "区域"}
    return "；".join(f"{names.get(k, k)}={v}" for k, v in f.items())


def _section_for(r: dict) -> list[str]:
    tool = r.get("tool", "")
    L = [f"### {TOOL_TITLES.get(tool, tool)}", ""]

    if not r.get("ok"):
        L += [f"**未产出结果**：{r.get('reason', '未知原因')}", ""]
        return L

    if tool == "panel_overview":
        s = r["stats"]
        L += _table(["指标", "值"], [
            ["网点数", f"{s['branches']:,}"],
            ["银行数", f"{s['banks']:,}"],
            ["观测窗", f"{s['year_min']}–{s['year_max']}"],
            ["关停网点", f"{s['closed']:,}"],
            ["关停率", pct(s["closure_rate"])],
            ["95% CI", f"{pct(s['ci95'][0])} – {pct(s['ci95'][1])}"],
            ["存续年数中位数", num(s["median_alive_years"], 1)],
            ["BKCLASS 分布", "，".join(f"{k}:{v:,}" for k, v in s["bkclass_counts"].items())],
        ]) + [""]

    elif tool == "panel_query":
        s = r["stats"]
        L += [f"筛选：{_filters_text(r.get('filters', {}))}", ""]
        L += _table(["指标", "值"], [
            ["样本量 n", f"{s['n']:,}"],
            ["涉及银行", f"{s['banks']:,}"],
            ["关停数", f"{s['closed']:,}"],
            ["关停率", pct(s["closure_rate"])],
            ["95% Wilson CI", f"{pct(s['ci95'][0])} – {pct(s['ci95'][1])}"],
            ["存续年数中位数", num(s["median_alive_years"], 1)],
            ["年龄中位数", num(s.get("median_age"), 1)],
        ]) + [""]

    elif tool == "panel_km":
        L += [f"队列：`{r.get('cohort')}`（balanced = 排除左截断网点）", ""]
        for name, g in r["groups"].items():
            pts = {p["t"]: p["survival"] for p in g["points"]}
            marks = [f"t={t}: {pct(pts.get(t))}" for t in (5, 10, 20, 30) if t in pts]
            L += [f"- **{name}**（n={g['n']:,}）：" + "，".join(marks)]
        L += ["", "> 完整曲线在 JSON 结果的 `groups.<name>.points` 中。", ""]

    elif tool == "panel_neighbor_density":
        L += _table(["邻居数分桶", "n", "关停数", "关停率", "95% CI", "存续中位数"],
                    [[x["neighbor_bucket"], f"{x['n']:,}", f"{x['closed']:,}",
                      pct(x["closure_rate"]),
                      f"{pct(x['ci95'][0])} – {pct(x['ci95'][1])}",
                      num(x["median_alive_years"], 1)] for x in r["rows"]]) + [""]

    elif tool == "panel_bank_profile":
        s = r["stats"]
        L += _table(["指标", "值"], [
            ["CERT", r["cert"]],
            ["网点数", f"{s['n']:,}"],
            ["已关停", f"{s['closed']:,}"],
            ["关停率", pct(s["closure_rate"])],
            ["95% CI", f"{pct(s['ci95'][0])} – {pct(s['ci95'][1])}"],
            ["脆弱度分层", s["fragility_tier"] or "—"],
            ["BKCLASS", "，".join(s["bkclass"])],
            ["年龄中位数", num(s["median_age"], 1)],
            ["存款中位数（千美元）", num(s["median_depsumbr"], 0)],
        ]) + [""]

    elif tool == "engine_risk":
        L += _table(["指标", "值"], [
            ["关停风险 h", num(r.get("hazard"), 4)],
            ["剔除泄漏特征后（保守）", num(r.get("hazardConservative"), 4)],
            ["线性预测子 η", num(r.get("eta"), 4)],
            ["结论等级", (r.get("grade") or {}).get("level", "—")],
            ["是否超时序外推", "是" if r.get("outOfTime") else "否"],
        ]) + [""]
        cons = [c for c in r.get("contributions", []) if c.get("kind") != "base"][:6]
        if cons:
            L += ["主要贡献项（模型系数 × 输入，不是因果效应）：", ""]
            L += _table(["特征", "贡献"], [[c["name"], num(c.get("value"), 4)] for c in cons]) + [""]

    elif tool == "engine_impact":
        L += _table(["指标", "值"], [
            ["输入暴露强度", num(r.get("strengthInput"), 3)],
            ["观测支撑下界", num(r.get("supportLowerBound"), 3)],
            ["是否外推", "是" if r.get("isExtrapolation") else "否"],
            ["结论等级", (r.get("grade") or {}).get("level", "—")],
        ]) + [""]
        rows = r.get("byTau") or []
        if rows:
            L += ["按 τ 的效应（事件研究系数）：", ""]
            L += _table(["τ", "效应", "95% CI", "p"],
                        [[x.get("tau"), pct(x.get("effect")),
                          f"{pct(x.get('lo'))} – {pct(x.get('hi'))}", num(x.get("p"), 4)]
                         for x in rows]) + [""]

    elif tool == "engine_grade":
        L += _table(["项", "值"], [
            ["等级", r.get("level")],
            ["名称", r.get("name")],
            ["可用表述", r.get("use")],
        ]) + [""]
        for reason in r.get("reasons", []):
            L.append(f"- {reason}")
        L.append("")

    elif tool == "engine_caliber":
        rows = r.get("all") or []
        L += _table(["数据集", "格数", "点数", "Moran's I", "平均邻居", "孤岛"],
                    [[x.get("dataset"), f"{x.get('cells', 0):,}", f"{x.get('points', 0):,}",
                      num(x.get("moran"), 3), num(x.get("neighbors"), 2), f"{x.get('islands', 0):,}"]
                     for x in rows]) + [""]

    elif tool == "engine_intake":
        L += _table(["项", "值"], [
            ["评分", f"{round((r.get('scorePct') or 0) * 100)}/100"],
            ["结论上限", r.get("ceiling")],
            ["是否否决", "是" if r.get("hardFail") else "否"],
            ["预估工期", "–".join(str(w) for w in (r.get("weeks") or [])) + " 周"],
        ]) + [""]
        for g in r.get("gaps", []):
            L.append(f"- 缺口：{g}")
        for b in r.get("blockers", []):
            L.append(f"- 一票否决：{b}")
        L.append("")

    return L


def _conclusion(question: str, results: list[dict]) -> str:
    if not results:
        return "没有可执行的结果。"
    lines = [_sentence(r) for r in results[:3]]
    return "  \n".join(lines)


def _sentence(r: dict) -> str:
    tool = r.get("tool")
    if not r.get("ok"):
        return f"没有给出结论：{r.get('reason', '工具未产出结果')}。"

    if tool == "panel_query":
        s = r["stats"]
        return (f"在 {_filters_text(r.get('filters', {}))} 条件下，共 {s['n']:,} 个网点，"
                f"{s['closed']:,} 个关停，关停率 **{pct(s['closure_rate'])}**"
                f"（95% CI {pct(s['ci95'][0])}–{pct(s['ci95'][1])}）。这是样本内历史频率，不是未来概率。")
    if tool == "panel_overview":
        s = r["stats"]
        return (f"面板共 {s['branches']:,} 个网点、{s['banks']:,} 家银行，"
                f"观测窗 {s['year_min']}–{s['year_max']}，总体关停率 {pct(s['closure_rate'])}。")
    if tool == "panel_km":
        name, g = next(iter(r["groups"].items()))
        pts = {p["t"]: p["survival"] for p in g["points"]}
        return (f"{name} 组（n={g['n']:,}）的 10 年生存率 {pct(pts.get(10))}、"
                f"20 年 {pct(pts.get(20))}（朴素 KM，未做银行内聚类校正）。")
    if tool == "panel_neighbor_density":
        top = max(r["rows"], key=lambda x: x["n"])
        return (f"样本量最大的邻居数分桶是 {top['neighbor_bucket']}（n={top['n']:,}），"
                f"关停率 {pct(top['closure_rate'])}；各桶为描述性分层，不是密度效应。")
    if tool == "panel_bank_profile":
        s = r["stats"]
        return (f"CERT {r['cert']} 共 {s['n']:,} 个网点，已关停 {s['closed']:,} 个"
                f"（{pct(s['closure_rate'])}），脆弱度分层 {s['fragility_tier'] or '—'}。")
    if tool == "engine_risk":
        level = (r.get("grade") or {}).get("level", "—")
        return (f"以该银行中位网点代参，关停风险 h = **{num(r.get('hazard'), 4)}**"
                f"（剔除泄漏特征后 {num(r.get('hazardConservative'), 4)}），"
                f"模型内部结论等级 {level}。")
    if tool == "engine_impact":
        m = (r.get("marginal") or {}).get("atInput") or {}
        return (f"暴露强度 {num(r.get('strengthInput'), 3)} 下，效应点估计 "
                f"{pct(m.get('effect'))}（95% CI {pct((m.get('ci') or {}).get('lo'))}–"
                f"{pct((m.get('ci') or {}).get('hi'))}）。")
    if tool == "engine_grade":
        return f"结论强度 **{r.get('level')} 级（{r.get('name')}）**：{r.get('use')}"
    if tool == "engine_caliber":
        fdic = next((x for x in (r.get("all") or []) if x.get("dataset") == "fdic"), None)
        return (f"FDIC 口径：{fdic['cells']:,} 个格、{fdic['points']:,} 个点，"
                f"Moran's I = {num(fdic['moran'], 3)}。") if fdic else "未找到 fdic 口径。"
    if tool == "engine_intake":
        return (f"数据准入评分 {round((r.get('scorePct') or 0) * 100)}/100，"
                f"结论强度上限 {r.get('ceiling')} 级"
                + ("，存在一票否决项。" if r.get("hardFail") else "。"))
    return "已产出结果。"


def _merge(results, key):
    seen, out = set(), []
    for r in results:
        for item in r.get(key) or []:
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def build(question, plan, results, findings, tracer, grade_level=None) -> str:
    conclusion = _conclusion(question, results)
    if grade_level == "C":
        conclusion, removed = attest.strip_causal_language(conclusion)
        if removed:
            conclusion += f"\n\n> 措辞已降级：C 级结论不允许因果动词（替换 {', '.join(removed)}）。"

    cn = "一二三四五六七八九"
    counter = {"i": 0}

    def sec(title):
        i = counter["i"]
        counter["i"] += 1
        return f"## {cn[i]}、{title}"

    L = ["# 网点关停决策评估报告", "",
         f"- **问题**：{question}",
         f"- **运行 ID**：`{tracer.run_id}`",
         f"- **规划意图**：`{plan.get('intent')}`",
         "", sec("结论"), "", conclusion, "", sec("证据"), ""]

    for r in results:
        L += _section_for(r)

    warns = [f for f in findings if f.severity == attest.WARN]
    blocks = [f for f in findings if f.severity == attest.BLOCK]
    L += [sec("边界与告警"), ""]
    if not warns and not blocks:
        L += ["- 本次调用未触发告警。", ""]
    for f in blocks + warns:
        L.append(f"- **[{f.rule}]** {f.message}")
    if warns or blocks:
        L.append("")

    caveats = _merge(results, "caveats")
    L += [sec("口径与限制"), ""]
    L += [f"- {c}" for c in caveats] or ["- 无。"]
    L.append("")

    if plan.get("assumptions"):
        L += [sec("本次使用的假设"), ""]
        L += [f"- {a}" for a in plan["assumptions"]]
        L.append("")

    L += [sec("数据血缘"), ""]
    L += [f"- `{s}`" for s in _merge(results, "source")] or ["- 无。"]
    L += ["", sec("存证"), "",
          f"- 本次 trace：`{tracer.path}`",
          f"- 哈希链 head：`{tracer.head}`",
          "- 校验：`python -m agent.trace <trace.jsonl>`",
          "- 说明：哈希链只能证明日志写出后未被改写，不等同于第三方可信时间戳或公钥签名。",
          ""]
    return "\n".join(L)


def blocked(question, findings, tracer) -> str:
    L = ["# 请求被拦截", "",
         f"- **问题**：{question}",
         f"- **运行 ID**：`{tracer.run_id}`", "",
         "## 拦截原因", ""]
    seen = set()
    for f in findings:
        if f.severity == attest.BLOCK and (f.rule, f.message) not in seen:
            seen.add((f.rule, f.message))
            L.append(f"- **[{f.rule}]** {f.message}")
    L += ["", "## 为什么不做", "",
          "这个 Agent 的定位是「只给可核验的答案」。检测到越域、注入或超时序外推时，",
          "直接拒绝比给一个看起来合理但没有证据的数字更安全。", "",
          "## 存证", "",
          f"- trace：`{tracer.path}`",
          f"- head：`{tracer.head}`", ""]
    return "\n".join(L)


def clarify(question, plan, tracer) -> str:
    L = ["# 需要补充信息", "", f"- **问题**：{question}", ""]
    L += [f"- {c}" for c in plan.get("clarify", [])]
    L += ["", f"（trace: `{tracer.path}`）", ""]
    return "\n".join(L)