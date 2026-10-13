import psycopg

from app.settings import get_settings
from scripts.demo import PHONE, main


def test_the_demo_runs_end_to_end_and_leaves_the_database_as_it_found_it(capsys):
    with psycopg.connect(get_settings().database_url) as c:
        before = c.execute(
            "SELECT (SELECT count(*) FROM calls), (SELECT count(*) FROM bookings), (SELECT count(*) FROM patients)"
        ).fetchone()
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "replayed=True" in out and "delivery 2: stored=False" in out and "outcome=booked" in out
    with psycopg.connect(get_settings().database_url) as c:
        after = c.execute(
            "SELECT (SELECT count(*) FROM calls), (SELECT count(*) FROM bookings), (SELECT count(*) FROM patients)"
        ).fetchone()
        assert c.execute("SELECT count(*) FROM patients WHERE phone = %s", (PHONE,)).fetchone()[0] == 0
    assert before == after
