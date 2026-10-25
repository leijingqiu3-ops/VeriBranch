import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import engines_bridge  # noqa: E402

ready, why = engines_bridge.available()
pytestmark = pytest.mark.skipif(not ready, reason=f"上游引擎不可用: {why}")


def test_grade_has_level():
    r = engines_bridge.grade()
    assert r["ok"] is True and r["level"] in {"A", "B", "C"}


def test_risk_rejects_unknown_bkclass():
    r = engines_bridge.risk(25, 10, 5, 38, -86, 0.5, 2010, "ZZ", "L0_单网点")
    assert r["ok"] is False and r["attest"] == "arg_validation"


def test_risk_rejects_non_us_coords():
    r = engines_bridge.risk(25, 10, 5, 30.2, 120.1, 0.5, 2010, "N", "L0_单网点")
    assert r["ok"] is False


def test_risk_out_of_model_domain():
    r = engines_bridge.risk(25, 10, 5, 38, -86, 0.5, 2018, "N", "L0_单网点")
    assert r["ok"] is False


def test_impact_returns_caveats_and_source():
    r = engines_bridge.impact(1.0)
    assert r["ok"] is True
    assert r.get("caveats") and r.get("source")