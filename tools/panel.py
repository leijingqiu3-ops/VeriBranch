"""panel.py — 在真实网点面板上做确定性计算。

数据源: project2_fdic_survival/output/data/branch_panel.csv（上游团队仓库产物）
字段: UNINUMBR, CERT, first_year, last_year, est_year, acq_year, DEPSUMBR_last,
      lat, lng, BKCLASS, left_truncated, right_censored, event, alive_years,
      age, grid, spatial_key, neighbor_count

设计原则与上游决策台一致:
  - 只算不猜: 取不到的字段返回 ok=false + reason
  - 每个结果都带 caveats[] / source[] / n
  - 不做任何"估算填充"
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PANEL = ROOT / "data" / "branch_panel.csv"
FRAGILITY = ROOT / "data" / "bank_fragility.csv"

SOURCE = "project2_fdic_survival/output/data/branch_panel.csv"

# 数据里实际出现过的 BKCLASS（含样本量），用于参数校验
BKCLASS_SUPPORTED = {"N", "NM", "SM", "SA", "SB", "SL", "OI"}

# 自定义矩形近似分区（非官方行政区划），仅用于粗粒度筛选
REGIONS = {
    "northeast": (37.0, 47.5, -80.5, -66.9),
    "midwest": (35.5, 49.5, -104.1, -80.4),
    "south": (24.4, 39.5, -106.7, -75.0),
    "west": (24.4, 49.5, -125.0, -102.0),
}

CAVEATS_COMMON = [
    "面板为网点-银行粒度（一行=一个网点），不是网点-年份面板；alive_years 为观测到的存续年数。",
    "美国 FDIC 数据，只覆盖美国境内网点；不含中国或任何非美国地区。",
    "观测窗 1994–2025，1994 年已在营的网点存在左截断（left_truncated=1）。",
]


@lru_cache(maxsize=1)
def load_panel() -> pd.DataFrame:
    if not PANEL.exists():
        raise FileNotFoundError(f"缺少面板数据: {PANEL}")
    df = pd.read_csv(PANEL)
    df["event"] = df["event"].astype(str).str.strip().str.lower()
    df["BKCLASS"] = df["BKCLASS"].astype(str).str.strip().str.upper()
    return df


@lru_cache(maxsize=1)
def load_fragility() -> pd.DataFrame:
    if not FRAGILITY.exists():
        raise FileNotFoundError(f"缺少银行脆弱度表: {FRAGILITY}")
    return pd.read_csv(FRAGILITY)


def wilson(k: int, n: int, z: float = 1.96):
    """二项比例的 Wilson 置信区间。"""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 6), round(min(1.0, centre + half), 6)]


def _rate(k: int, n: int) -> dict:
    return {
        "n": n,
        "closed": k,
        "closure_rate": round(k / n, 6) if n else None,
        "ci95": wilson(k, n),
    }


def _caveats(*extra: str):
    return CAVEATS_COMMON + [c for c in extra if c]


def overview() -> dict:
    df = load_panel()
    closed = int((df["event"] == "closed").sum())
    return {
        "ok": True,
        "tool": "panel_overview",
        "stats": {
            "branches": int(len(df)),
            "banks": int(df["CERT"].nunique()),
            "year_min": int(df["first_year"].min()),
            "year_max": int(df["last_year"].max()),
            "closed": closed,
            **_rate(closed, len(df)),
            "median_alive_years": float(df["alive_years"].median()),
            "median_neighbors": float(df["neighbor_count"].median()),
            "bkclass_counts": {k: int(v) for k, v in df["BKCLASS"].value_counts().items()},
        },
        "caveats": _caveats(),
        "source": [SOURCE],
    }


def _check_year_range(year_from, year_to):
    df = load_panel()
    lo, hi = int(df["first_year"].min()), int(df["last_year"].max())
    if year_from is not None and (year_from < lo or year_from > hi):
        return f"year_from={year_from} 超出面板观测窗 {lo}–{hi}"
    if year_to is not None and (year_to < lo or year_to > hi):
        return f"year_to={year_to} 超出面板观测窗 {lo}–{hi}"
    if year_from is not None and year_to is not None and year_from > year_to:
        return f"year_from={year_from} 大于 year_to={year_to}"
    return None


def query(year_from=None, year_to=None, bkclass=None, cert=None,
          min_age=None, max_age=None, min_neighbors=None, region=None) -> dict:
    """按条件筛网点，返回样本量、关停率与区间。"""
    err = _check_year_range(year_from, year_to)
    if err:
        return {"ok": False, "tool": "panel_query", "reason": err,
                "caveats": _caveats(), "source": [SOURCE]}

    if bkclass is not None and str(bkclass).upper() not in BKCLASS_SUPPORTED:
        return {"ok": False, "tool": "panel_query",
                "reason": f"未知 BKCLASS={bkclass!r}；本数据只支持 {sorted(BKCLASS_SUPPORTED)}",
                "caveats": _caveats(), "source": [SOURCE]}

    if region is not None and str(region).lower() not in REGIONS:
        return {"ok": False, "tool": "panel_query",
                "reason": f"未知 region={region!r}；支持 {sorted(REGIONS)}（自定义矩形近似，非官方区划）",
                "caveats": _caveats(), "source": [SOURCE]}

    df = load_panel()
    mask = pd.Series(True, index=df.index)
    if year_from is not None:
        mask &= df["last_year"] >= year_from
    if year_to is not None:
        mask &= df["first_year"] <= year_to
    if bkclass is not None:
        mask &= df["BKCLASS"] == str(bkclass).upper()
    if cert is not None:
        mask &= df["CERT"] == int(cert)
    if min_age is not None:
        mask &= df["age"] >= min_age
    if max_age is not None:
        mask &= df["age"] <= max_age
    if min_neighbors is not None:
        mask &= df["neighbor_count"] >= min_neighbors
    if region is not None:
        lat0, lat1, lng0, lng1 = REGIONS[str(region).lower()]
        mask &= df["lat"].between(lat0, lat1) & df["lng"].between(lng0, lng1)

    sub = df[mask]
    if len(sub) == 0:
        return {"ok": False, "tool": "panel_query",
                "reason": "筛选结果为空：给定条件下没有网点，无法给出比率（不做零样本推断）",
                "filters": _filters(locals()),
                "caveats": _caveats(), "source": [SOURCE]}

    closed = int((sub["event"] == "closed").sum())
    return {
        "ok": True,
        "tool": "panel_query",
        "filters": _filters(locals()),
        "stats": {
            **_rate(closed, len(sub)),
            "banks": int(sub["CERT"].nunique()),
            "median_alive_years": float(sub["alive_years"].median()),
            "median_age": float(sub["age"].median()) if sub["age"].notna().any() else None,
            "median_neighbors": float(sub["neighbor_count"].median()),
        },
        "caveats": _caveats(
            "关停率是样本内的历史频率，不是未来概率；未做随机化或外推验证。",
            "同一银行的多家网点不是独立样本，区间会偏窄。",
        ),
        "source": [SOURCE],
    }


def _filters(scope) -> dict:
    keys = ["year_from", "year_to", "bkclass", "cert", "min_age",
            "max_age", "min_neighbors", "region"]
    return {k: scope.get(k) for k in keys if scope.get(k) is not None}


def km(group_by="bkclass", value=None, year_from=None, year_to=None,
       cohort="balanced") -> dict:
    """Kaplan-Meier 生存曲线（离散年，朴素估计）。

    cohort='balanced' 只取 1994 年观测窗起点已在册、无左截断的网点，
    避免左截断把生存率抬高的偏差；'all' 使用全部样本。
    """
    if group_by not in {"bkclass", "cert", "all"}:
        return {"ok": False, "tool": "panel_km",
                "reason": "group_by 只支持 bkclass / cert / all"}
    if cohort not in {"balanced", "all"}:
        return {"ok": False, "tool": "panel_km", "reason": "cohort 只支持 balanced / all"}

    err = _check_year_range(year_from, year_to)
    if err:
        return {"ok": False, "tool": "panel_km", "reason": err}

    df = load_panel()
    if cohort == "balanced":
        df = df[df["left_truncated"] == 0]
    if year_from is not None:
        df = df[df["last_year"] >= year_from]
    if year_to is not None:
        df = df[df["first_year"] <= year_to]

    groups = {}
    if group_by == "all":
        groups["all"] = df
    elif group_by == "bkclass":
        if value is not None:
            v = str(value).upper()
            if v not in BKCLASS_SUPPORTED:
                return {"ok": False, "tool": "panel_km", "reason": f"未知 BKCLASS={value!r}"}
            groups[v] = df[df["BKCLASS"] == v]
        else:
            for k in sorted(BKCLASS_SUPPORTED):
                sub = df[df["BKCLASS"] == k]
                if len(sub) >= 100:
                    groups[k] = sub
    else:
        if value is None:
            return {"ok": False, "tool": "panel_km", "reason": "group_by=cert 时必须给 value（银行 CERT）"}
        groups[str(value)] = df[df["CERT"] == int(value)]

    curves, empty = {}, []
    for name, sub in groups.items():
        if len(sub) == 0:
            empty.append(name)
            continue
        curves[name] = {"n": int(len(sub)), "points": _km_points(sub)}

    if not curves:
        return {"ok": False, "tool": "panel_km",
                "reason": "分组后没有剩余样本，无法估计生存曲线",
                "caveats": _caveats(), "source": [SOURCE]}

    return {
        "ok": True,
        "tool": "panel_km",
        "cohort": cohort,
        "groups": curves,
        "empty_groups": empty,
        "caveats": _caveats(
            "朴素 KM：未处理银行内网点相关性，区间未做 bootstrap。",
            "balanced 队列排除 left_truncated=1 的网点，样本量更小但起点一致。",
        ),
        "source": [SOURCE],
    }


def _km_points(sub: pd.DataFrame) -> list:
    t_max = int(sub["alive_years"].max())
    closed = sub["event"] == "closed"
    survival, out = 1.0, []
    for t in range(0, t_max + 1):
        at_risk = int((sub["alive_years"] >= t).sum())
        deaths = int(((sub["alive_years"] == t) & closed).sum())
        if at_risk == 0:
            break
        if deaths:
            survival *= 1 - deaths / at_risk
        out.append({"t": t, "survival": round(survival, 6),
                    "at_risk": at_risk, "events": deaths})
    return out


def neighbor_density(year_from=None, year_to=None, bkclass=None) -> dict:
    """按邻居数分桶看关停率——回答「网点密集处是否更容易/更不容易关」。"""
    err = _check_year_range(year_from, year_to)
    if err:
        return {"ok": False, "tool": "panel_neighbor_density", "reason": err}
    if bkclass is not None and str(bkclass).upper() not in BKCLASS_SUPPORTED:
        return {"ok": False, "tool": "panel_neighbor_density", "reason": f"未知 BKCLASS={bkclass!r}"}

    df = load_panel()
    if year_from is not None:
        df = df[df["last_year"] >= year_from]
    if year_to is not None:
        df = df[df["first_year"] <= year_to]
    if bkclass is not None:
        df = df[df["BKCLASS"] == str(bkclass).upper()]

    if len(df) == 0:
        return {"ok": False, "tool": "panel_neighbor_density", "reason": "筛选结果为空"}

    bins = [-1, 0, 2, 5, 10, 10_000]
    labels = ["0", "1-2", "3-5", "6-10", "11+"]
    cut = pd.cut(df["neighbor_count"], bins=bins, labels=labels)
    rows = []
    for label in labels:
        sub = df[cut == label]
        if len(sub) == 0:
            continue
        closed = int((sub["event"] == "closed").sum())
        rows.append({"neighbor_bucket": label, **_rate(closed, len(sub)),
                     "median_alive_years": float(sub["alive_years"].median())})

    return {
        "ok": True,
        "tool": "panel_neighbor_density",
        "rows": rows,
        "caveats": _caveats(
            "neighbor_count 是上游按空间格计算的邻居数，分桶边界(0/1-2/3-5/6-10/11+)是本项目自定义口径。",
            "这是描述性分层，不是密度对关停的因果效应。",
        ),
        "source": [SOURCE],
    }


def bank_profile(cert: int) -> dict:
    """单家银行画像：网点数、关停数、脆弱度分层。"""
    df = load_panel()
    sub = df[df["CERT"] == int(cert)]
    if len(sub) == 0:
        return {"ok": False, "tool": "panel_bank_profile",
                "reason": f"CERT={cert} 不在面板中（不做猜测性画像）",
                "source": [SOURCE]}
    closed = int((sub["event"] == "closed").sum())
    frag = load_fragility()
    row = frag[frag["CERT"] == int(cert)]
    tier = None if row.empty else str(row.iloc[0]["fragility_tier"])
    return {
        "ok": True,
        "tool": "panel_bank_profile",
        "cert": int(cert),
        "stats": {
            **_rate(closed, len(sub)),
            "fragility_tier": tier,
            "median_age": float(sub["age"].median()),
            "median_neighbors": float(sub["neighbor_count"].median()),
            "median_depsumbr": float(sub["DEPSUMBR_last"].median()),
            "lat": float(sub["lat"].median()),
            "lng": float(sub["lng"].median()),
            "log_depsumbr": float(__import__("math").log1p(sub["DEPSUMBR_last"].median())),
            "bkclass": sorted(sub["BKCLASS"].unique().tolist()),
        },
        "caveats": _caveats(
            "fragility_tier 来自上游 bank_fragility.csv，口径为网点地理集中度分层，不是信用评级。",
        ),
        "source": [SOURCE, "project2_fdic_survival/output/data/bank_fragility.csv"],
    }