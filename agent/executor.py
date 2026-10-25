"""executor.py — 规划 → 校验 → 执行 → 归一 → 存证。"""
from __future__ import annotations

import time

from agent import attest, planner, report, trace
from tools import engines_bridge, panel, registry

DISPATCH = {
    "panel_overview": lambda a: panel.overview(),
    "panel_query": lambda a: panel.query(**a),
    "panel_km": lambda a: panel.km(**a),
    "panel_neighbor_density": lambda a: panel.neighbor_density(**a),
    "panel_bank_profile": lambda a: panel.bank_profile(**a),
    "engine_impact": lambda a: engines_bridge.impact(**a),
    "engine_grade": lambda a: engines_bridge.grade(**a),
    "engine_caliber": lambda a: engines_bridge.caliber(**a),
    "engine_intake": lambda a: engines_bridge.intake(**a),
    "engine_risk": lambda a: engines_bridge.risk(**a),
}


def _risk_from_profile(profile: dict, year: int = 2010) -> dict:
    """多步：单家银行画像 → 风险打分，用画像里的实测中位数代参。"""
    s = profile.get("stats", {})
    return {
        "age": s.get("median_age"),
        "log_depsumbr": s.get("log_depsumbr"),
        "neighbor_count": s.get("median_neighbors"),
        "lat": s.get("lat"), "lng": s.get("lng"),
        "bank_closed_rate": s.get("closure_rate"),
        "year": year,
        "bkclass": (s.get("bkclass") or ["N"])[0],
        "fragility": s.get("fragility_tier") or "L0_单网点",
    }


def _resolve(step: dict, results: list[dict]) -> dict:
    name, args = step["tool"], dict(step.get("args") or {})

    if name == "engine_risk" and "cert" in args and "age" not in args:
        prof = next((r for r in results if r.get("tool") == "panel_bank_profile"), None)
        if prof and prof.get("ok"):
            built = _risk_from_profile(prof)
            built.update({k: v for k, v in args.items() if k != "cert"})
            return built
    if name == "engine_risk":
        args.pop("cert", None)
    return args


def run(question: str, use_llm: bool = False, run: bool = True,
        disabled_rules: set[str] | None = None) -> dict:
    tr = trace.Tracer()
    t0 = time.time()

    tr.log("input", {"question": question, "llm": use_llm})
    pre = attest.scan_input(question, disabled_rules)
    tr.log("attest.pre", attest.summarize(pre))

    if any(f.severity == attest.BLOCK for f in pre):
        md = report.blocked(question, pre, tr)
        (tr.dir / "report.md").write_text(md, encoding="utf-8")
        tr.log("output", {"blocked": True, "head": tr.head})
        tr.close()
        return {"ok": False, "blocked": True, "question": question,
                "findings": [f.as_dict() for f in pre], "report": md,
                "trace": str(tr.path), "run_id": tr.run_id,
                "elapsed_s": round(time.time() - t0, 3)}

    plan = (planner.llm_plan if use_llm else planner.rule_plan)(question)
    tr.log("plan", plan)

    if not plan.get("steps"):
        md = report.clarify(question, plan, tr)
        (tr.dir / "report.md").write_text(md, encoding="utf-8")
        tr.log("output", {"blocked": False, "clarify": True})
        tr.close()
        return {"ok": True, "blocked": False, "clarify": plan.get("clarify", []),
                "question": question, "plan": plan, "report": md,
                "trace": str(tr.path), "run_id": tr.run_id,
                "elapsed_s": round(time.time() - t0, 3)}

    results, findings = [], list(attest.check_plan(plan["steps"]))
    for step in plan["steps"]:
        name = step["tool"]
        args = _resolve(step, results)
        ok, why = registry.validate(name, args)
        if not ok:
            findings.append(attest.Finding("A4-schema", attest.BLOCK,
                                           f"拒绝调用 {name}：{why}", {"args": args}))
            tr.log("attest.schema", {"tool": name, "ok": False, "reason": why})
            break

        res = DISPATCH[name](args)
        res.setdefault("ok", False)
        fs = attest.check_result(name, res, disabled=disabled_rules)
        findings.extend(fs)
        tr.log("tool_call", {"tool": name, "args": args,
                             "ok": bool(res.get("ok")),
                             "reason": res.get("reason"),
                             "attested": [f.as_dict() for f in fs],
                             "result_digest": _digest_view(res)})
        results.append({**res, "args": args})

        if any(f.severity == attest.BLOCK for f in fs):
            break

    grade_level = next((str((r.get("grade") or {}).get("level"))
                        for r in results
                        if r.get("tool") in ("engine_grade", "engine_impact")
                        and isinstance(r.get("grade"), dict)), None)

    md = report.build(question, plan, results, findings, tr, grade_level)
    (tr.dir / "report.md").write_text(md, encoding="utf-8")
    tr.log("output", {"blocked": False, "head": tr.head,
                      "grade_level": grade_level, "tools": [r["tool"] for r in results]})
    tr.close()

    return {"ok": all(r.get("ok") for r in results) if results else True,
            "blocked": False, "question": question, "plan": plan,
            "results": [{k: v for k, v in r.items() if k != "args"} for r in results],
            "findings": [f.as_dict() for f in findings],
            "grade_level": grade_level, "report": md,
            "trace": str(tr.path), "run_id": tr.run_id,
            "elapsed_s": round(time.time() - t0, 3)}


def _digest_view(res: dict) -> dict:
    """trace 里只存结果摘要，不复制整份数据。"""
    keep = ("ok", "reason", "stats", "n", "level", "hazard", "isExtrapolation")
    return {k: res[k] for k in keep if k in res}