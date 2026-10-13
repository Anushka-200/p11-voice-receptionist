"""The database enforces the rules we must never break, even if the application code has a bug."""

import psycopg
import pytest

from app.settings import get_settings

START = "2026-10-20 15:00+00"
END = "2026-10-20 16:00+00"


def _patient(conn) -> int:
    return conn.execute(
        "INSERT INTO patients (phone, name) VALUES ('+15125550100', 'Test Patient') RETURNING id"
    ).fetchone()[0]


def _book(conn, patient_id: int, provider_id: int, start: str, end: str) -> None:
    conn.execute(
        "INSERT INTO bookings (patient_id, provider_id, type_code, start_at, end_at)"
        " VALUES (%s, %s, 'checkup', %s, %s)",
        (patient_id, provider_id, start, end),
    )


def test_seed_data_is_present(conn):
    assert conn.execute("SELECT count(*) FROM providers").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM appointment_types").fetchone()[0] == 5
    assert conn.execute("SELECT count(*) FROM faq").fetchone()[0] >= 10
    # Exactly one dentist is on call: the emergency transfer target (ADR 003).
    assert conn.execute("SELECT count(*) FROM providers WHERE on_call").fetchone()[0] == 1


def test_database_refuses_overlapping_bookings_for_one_provider(conn):
    pid = _patient(conn)
    _book(conn, pid, 1, START, END)
    with pytest.raises(psycopg.errors.ExclusionViolation):
        _book(conn, pid, 1, "2026-10-20 15:30+00", "2026-10-20 16:30+00")


def test_different_provider_or_cancelled_booking_does_not_conflict(conn):
    pid = _patient(conn)
    _book(conn, pid, 1, START, END)
    _book(conn, pid, 2, START, END)  # another provider: fine
    conn.execute("UPDATE bookings SET status = 'cancelled' WHERE provider_id = 1")
    _book(conn, pid, 1, START, END)  # the old one is cancelled: the slot is free again


def test_replayed_vapi_call_id_is_rejected_by_unique_key(conn):
    conn.execute("INSERT INTO calls (vapi_call_id) VALUES ('call_1')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO calls (vapi_call_id) VALUES ('call_1')")


def test_app_role_cannot_change_table_structure(conn):
    # Least privilege: a bug or an injection in the app must not be able to drop or alter tables.
    assert get_settings().database_url.split("//")[1].startswith("p11_app")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DROP TABLE bookings")
