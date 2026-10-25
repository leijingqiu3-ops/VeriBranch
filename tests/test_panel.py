import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import panel  # noqa: E402


def test_overview_matches_csv():
    s = panel.overview()["stats"]
    assert s["branches"] == 152_538
    assert s["banks"] == 15_505
    assert s["closed"] == 80_695
    assert (s["year_min"], s["year_max"]) == (1994, 2025)


def test_wilson_interval_sane():
    lo, hi = panel.wilson(50, 100)
    assert lo < 0.5 < hi
    assert 0.39 < lo < 0.41 and 0.59 < hi < 0.61
    assert panel.wilson(0, 0) is None


def test_query_empty_result_is_failure():
    r = panel.query(cert=999_999_999)
    assert r["ok"] is False and "为空" in r["reason"]


def test_query_rejects_unknown_bkclass():
    r = panel.query(bkclass="ZZ")
    assert r["ok"] is False and "BKCLASS" in r["reason"]


def test_query_range_mismatch():
    r = panel.query(year_from=2020, year_to=2010)
    assert r["ok"] is False


def test_query_ci_contains_point_estimate():
    s = panel.query(bkclass="SB", year_from=2010, year_to=2015)["stats"]
    assert 0 <= s["closure_rate"] <= 1
    assert s["ci95"][0] <= s["closure_rate"] <= s["ci95"][1]


def test_km_monotonic_and_bounded():
    r = panel.km(group_by="bkclass", value="SB", cohort="balanced")
    assert r["ok"] is True
    surv = [p["survival"] for p in r["groups"]["SB"]["points"]]
    assert all(0 <= x <= 1 for x in surv)
    assert all(b <= a + 1e-9 for a, b in zip(surv, surv[1:]))


def test_neighbor_buckets_cover_sample():
    r = panel.neighbor_density()
    assert r["ok"] is True
    assert sum(x["n"] for x in r["rows"]) == 152_538


def test_bank_profile_unknown_cert():
    assert panel.bank_profile(999_999_999)["ok"] is False


def test_bank_profile_cert8():
    r = panel.bank_profile(8)
    assert r["ok"] is True
    assert r["stats"]["n"] == 101 and r["stats"]["closed"] == 98
    assert r["stats"]["fragility_tier"] in {"L0_单网点", "L1_多网点高集中", "L2_多网点地理分散"}