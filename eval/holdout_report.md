# VeriBranch 评测报告

- 用例集：`eval/holdout.jsonl`，共 **14** 条（pass 4 / block 10）
- 判定：`pass` = 给出结果；`block` = 策略层拦截；`clarify` = 反问补参

## 一、总览

| 指标 | 值 |
| --- | --- |
| 拦截召回 | 10/10 = 100.0% |
| 规则精确命中 | 100.0%（拦住了，但可能不是预期那条规则） |
| 误伤 | 0/4 = 0.0% |
| 同问一致性 | 100.0%（4 条 pass 用例各跑两遍） |
| 延迟 P50 / P95 | 0.005s / 0.055s |

## 二、分规则

| 规则 | 用例 | 拦住 | 规则精确命中 | 漏检 |
| --- | --- | --- | --- | --- |
| A1-injection | 5 | 5 | 5 | — |
| A2-out-of-time | 3 | 3 | 3 | — |
| A3-out-of-domain | 2 | 2 | 2 | — |

## 三、漏检明细

- 无。

## 四、误伤明细

- 无。

## 六、怎么复现

```powershell
python eval/run_eval.py --cases holdout.jsonl
```

结果同时写入 `eval/results.json`。
