import pytest

from market_risk.quality.checks import CheckResult, blocking_failures, compile_check, load_checks


def render(chk, today="current_date()"):
    return chk.render(lambda t: f"cat.sch.{t}", today)


def test_unique():
    sql = render(compile_check("t", {"type": "unique", "columns": ["a", "b"]}))
    assert sql == "SELECT a, b FROM cat.sch.t GROUP BY a, b HAVING count(*) > 1"


def test_not_null():
    sql = render(compile_check("t", {"type": "not_null", "columns": ["a", "b"]}))
    assert sql.endswith("WHERE a IS NULL OR b IS NULL")


def test_accepted_values_escapes_strings():
    sql = render(compile_check("t", {"type": "accepted_values", "column": "c", "values": ["X", "O'K"]}))
    assert "NOT IN ('X', 'O\\'K')" in sql


def test_relationships_resolves_both_tables():
    chk = compile_check("child", {"type": "relationships", "column": "fk", "to": "parent", "field": "pk"})
    assert chk.referenced_tables() == {"child", "parent"}
    sql = render(chk)
    assert "FROM cat.sch.child c LEFT JOIN cat.sch.parent p ON p.pk = c.fk" in sql


def test_freshness_uses_today_placeholder():
    chk = compile_check("t", {"type": "freshness", "column": "d", "max_age_days": 3})
    assert "datediff(DATE'2025-01-10', max(d)) > 3" in render(chk, "DATE'2025-01-10'")


def test_custom_sql_and_names():
    chk = compile_check("t", {"type": "sql", "name": "my_check", "sql": "SELECT 1 FROM {t} JOIN {u}"})
    assert chk.check_id == "t.my_check"
    assert render(chk) == "SELECT 1 FROM cat.sch.t JOIN cat.sch.u"


def test_invalid_specs():
    with pytest.raises(ValueError):
        compile_check("t", {"type": "nope"})
    with pytest.raises(ValueError):
        compile_check("t", {"type": "unique", "columns": ["a"], "severity": "fatal"})


def test_packaged_checks_load_and_are_unique():
    checks = load_checks()
    assert len(checks) >= 40
    assert len({c.check_id for c in checks}) == len(checks)


def test_blocking_failures():
    def res(status, severity):
        return CheckResult("t.c", "t", "unique", severity, status, 1, "", "")

    results = [res("PASS", "error"), res("FAIL", "warn"), res("FAIL", "error"), res("ERROR", "warn")]
    assert [r.status for r in blocking_failures(results)] == ["FAIL", "ERROR"]
