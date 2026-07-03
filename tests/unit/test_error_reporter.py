"""
tests/unit/test_error_reporter.py — Tests du reporting d'exceptions avalées (Phase 1, #10).
"""

from core import error_reporter as er


def setup_function():
    er.clear_recent()


def test_report_records_in_buffer(caplog):
    try:
        raise ValueError("boom test")
    except Exception as e:
        er.report_swallowed("unit.test_site", e, level="warning")

    recent = er.get_recent_errors()
    assert len(recent) == 1
    assert recent[0]["context"] == "unit.test_site"
    assert recent[0]["type"] == "ValueError"
    assert "boom test" in recent[0]["message"]


def test_recent_is_most_recent_first():
    for i in range(3):
        try:
            raise RuntimeError(f"err{i}")
        except Exception as e:
            er.report_swallowed(f"site{i}", e, level="debug")
    recent = er.get_recent_errors()
    assert [r["context"] for r in recent] == ["site2", "site1", "site0"]


def test_buffer_is_bounded():
    for i in range(er._MAX_RECENT + 50):
        try:
            raise KeyError(i)
        except Exception as e:
            er.report_swallowed("flood", e, level="debug")
    assert len(er.get_recent_errors(limit=10_000)) == er._MAX_RECENT


def test_unknown_level_defaults_gracefully():
    try:
        raise OSError("io")
    except Exception as e:
        er.report_swallowed("weird", e, level="not_a_level")
    assert er.get_recent_errors()[0]["context"] == "weird"
