"""run_eval.py — 固定用例集上的评测。

测四件事：
  1. 拦截召回（该拦的拦住了多少）
  2. 误伤（不该拦的被拦了多少）
  3. 同问一致性（同一问题跑两次，数字是否完全一致）
  4. 延迟（P50 / P95）

外加消融：关掉某条规则，看召回掉多少、误伤变多少。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
os.environ.setdefault("VERIBRANCH_RUNS_DIR", str(EVAL / "runs"))
sys.path.insert(0, str(ROOT))

from agent import executor  # noqa: E402

RULES = ["A1-injection", "A2-out-of-time", "A3-out-of-domain"]


def load_cases(name: str = "cases.jsonl") -> list[dict]:
    lines = (EVAL / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines if x.strip()]


def observed(res: dict) -> str:
    if res.get("blocked"):
        return "block"
    if res.get("clarify"):
        return "clarify"
    return "pass" if res.get("ok") else "no_result"


def blocked_rules(res: dict) -> list[str]:
    return [f["rule"] for f in res.get("findings", []) if f.get("severity") == "block"]


def signature(res: dict) -> str:
    """只保留工具名 + 数字结果，用来判断两次运行是否给出同一套数。"""
    slim = []
    for r in res.get("results", []):
        item = {"tool": r.get("tool"), "ok": r.get("ok")}
        for k in ("stats", "hazard", "hazardConservative", "eta", "level", "rows"):
            if k in r:
                item[k] = r[k]
        slim.append(item)
    return json.dumps(slim, sort_keys=True, ensure_ascii=False, default=str)


def p95(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
    return s[idx]


def evaluate(disabled: set[str] | None = None, consistency: bool = True,
             cases_file: str = "cases.jsonl") -> dict:
    cases = load_cases(cases_file)
    rows, latencies = [], []

    for c in cases:
        t0 = time.time()
        res = executor.run(c["question"], disabled_rules=disabled)
        dt = time.time() - t0
        latencies.append(dt)

        obs = observed(res)
        rules = blocked_rules(res)
        row = {
            "id": c["id"], "expect": c["expect"], "question": c["question"],
            "observed": obs, "rules": rules, "elapsed": round(dt, 3),
            "ok": bool(res.get("ok")), "signature": signature(res),
        }
        if c.get("rule"):
            row["expect_rule"] = c["rule"]
            row["rule_hit"] = c["rule"] in rules
        rows.append(row)

    if consistency:
        for row in rows:
            if row["expect"] != "pass":
                continue
            q = next(c["question"] for c in cases if c["id"] == row["id"])
            again = executor.run(q)
            row["consistent"] = signature(again) == row["signature"]

    return {"rows": rows, "latencies": latencies}


def summarize(data: dict) -> dict:
    rows = data["rows"]
    by = lambda e: [r for r in rows if r["expect"] == e]
    blocked_cases = by("block")
    pass_cases = by("pass")

    caught = [r for r in blocked_cases if r["observed"] == "block"]
    rule_ok = [r for r in blocked_cases if r.get("rule_hit")]
    false_blocks = [r for r in pass_cases if r["observed"] == "block"]
    consistent = [r for r in pass_cases if r.get("consistent")]

    per_rule = {}
    for rule in RULES:
        rs = [r for r in blocked_cases if r.get("expect_rule") == rule]
        per_rule[rule] = {
            "n": len(rs),
            "caught": sum(1 for r in rs if r["observed"] == "block"),
            "exact": sum(1 for r in rs if r.get("rule_hit")),
            "missed": [r["id"] for r in rs if r["observed"] != "block"],
        }

    lat = data["latencies"]
    return {
        "total": len(rows),
        "block_cases": len(blocked_cases),
        "block_caught": len(caught),
        "block_recall": len(caught) / len(blocked_cases) if blocked_cases else 0,
        "block_recall_strict": len(rule_ok) / len(blocked_cases) if blocked_cases else 0,
        "pass_cases": len(pass_cases),
        "false_blocks": len(false_blocks),
        "false_block_ids": [r["id"] for r in false_blocks],
        "fp_rate": len(false_blocks) / len(pass_cases) if pass_cases else 0,
        "consistency": (len(consistent) / len(pass_cases)) if pass_cases else 0,
        "consistency_checked": len(pass_cases),
        "p50": round(statistics.median(lat), 3) if lat else 0,
        "p95": round(p95(lat), 3),
        "per_rule": per_rule,
    }


def render(summary: dict, data: dict, ablation: list[dict], cases_file: str) -> str:
    rows = data["rows"]
    n_pass = summary["pass_cases"]
    n_block = summary["block_cases"]

    L = ["# VeriBranch 评测报告", "",
         f"- 用例集：`eval/{cases_file}`，共 **{summary['total']}** 条"
         f"（pass {n_pass} / block {n_block}）",
         "- 判定：`pass` = 给出结果；`block` = 策略层拦截；`clarify` = 反问补参",
         ""]

    L += ["## 一、总览", "",
          "| 指标 | 值 |", "| --- | --- |",
          f"| 拦截召回 | {summary['block_caught']}/{n_block} = {summary['block_recall'] * 100:.1f}% |",
          f"| 规则精确命中 | {summary['block_recall_strict'] * 100:.1f}%（拦住了，但可能不是预期那条规则） |",
          f"| 误伤 | {summary['false_blocks']}/{n_pass} = {summary['fp_rate'] * 100:.1f}% |",
          f"| 同问一致性 | {summary['consistency'] * 100:.1f}%（{summary['consistency_checked']} 条 pass 用例各跑两遍） |",
          f"| 延迟 P50 / P95 | {summary['p50']}s / {summary['p95']}s |", ""]

    L += ["## 二、分规则", "",
          "| 规则 | 用例 | 拦住 | 规则精确命中 | 漏检 |", "| --- | --- | --- | --- | --- |"]
    for rule, s in summary["per_rule"].items():
        if not s["n"]:
            continue
        L.append(f"| {rule} | {s['n']} | {s['caught']} | {s['exact']} | "
                 f"{', '.join(s['missed']) or '—'} |")
    L.append("")

    misses = [r for r in rows if r["expect"] == "block" and r["observed"] != "block"]
    L += ["## 三、漏检明细", ""]
    L += [f"- `{r['id']}`（应拦 {r.get('expect_rule')}，实际 {r['observed']}）：{r['question']}"
          for r in misses] or ["- 无。"]
    L.append("")

    fbs = [r for r in rows if r["expect"] == "pass" and r["observed"] == "block"]
    L += ["## 四、误伤明细", ""]
    L += [f"- `{r['id']}`（{', '.join(r['rules'])}）：{r['question']}" for r in fbs] or ["- 无。"]
    L.append("")

    if ablation:
        L += ["## 五、规则消融", "",
              "关掉一条规则，看它到底在不在干活。", "",
              "| 关闭规则 | 该规则召回 | 误伤 | 变化 |", "| --- | --- | --- | --- |"]
        for a in ablation:
            delta = a["fp_rate"] - summary["fp_rate"]
            L.append(f"| {a['rule']} | {a['recall'] * 100:.1f}% | "
                     f"{a['false_blocks']}/{a['pass_cases']} = {a['fp_rate'] * 100:.1f}% | "
                     f"误伤 {delta * 100:+.1f}pp |")
        L += ["", "> A2 关掉后召回不为 0，是因为年份超窗会先被 A4 参数 schema 挡住——",
              "> 这是纵深防御，不是 A2 的功劳，消融表如实显示两者重叠。", ""]

    L += ["## 六、怎么复现", "", "```powershell",
          f"python eval/run_eval.py --cases {cases_file}", "```", "",
          "结果同时写入 `eval/results.json`。", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-consistency", action="store_true")
    ap.add_argument("--skip-ablation", action="store_true")
    ap.add_argument("--cases", default="cases.jsonl")
    ap.add_argument("--out", default="report.md")
    args = ap.parse_args()

    t0 = time.time()
    base = evaluate(consistency=not args.no_consistency, cases_file=args.cases)
    summary = summarize(base)

    ablation = []
    if not args.skip_ablation:
        for rule in RULES:
            s = summarize(evaluate(disabled={rule}, consistency=False, cases_file=args.cases))
            if s["per_rule"][rule]["n"]:
                ablation.append({
                    "rule": rule,
                    "recall": s["per_rule"][rule]["caught"] / s["per_rule"][rule]["n"],
                    "false_blocks": s["false_blocks"],
                    "pass_cases": s["pass_cases"],
                    "fp_rate": s["fp_rate"],
                })

    md = render(summary, base, ablation, args.cases)
    (EVAL / args.out).write_text(md, encoding="utf-8")
    results_name = "results.json" if args.out == "report.md" else args.out.replace("report.md", "results.json").replace(".md", "_results.json")
    (EVAL / results_name).write_text(
        json.dumps({"summary": summary, "ablation": ablation, "rows": base["rows"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print(md)
    print(f"\n总耗时 {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())