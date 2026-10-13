"""The tool server Vapi calls during a live call.

`/vapi/tools` receives Vapi's `tool-calls` message and answers in Vapi's format, one result per call.
`/vapi/events` stores every other event (status, transcript, end-of-call-report) for the post-call worker (Phase 4).
Both need the shared secret. Vapi wants HTTP 200 always; a failure is a spoken message inside the result.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool

from app.adapters.fakes import FakeCrm
from app.api.vapi_auth import require_vapi_secret
from app.log import setup_logging
from app.postcall import ingest_report, process_call, process_crm_outbox
from app.settings import get_settings
from app.tools import execute, make_context

logger = logging.getLogger(__name__)


def _tool_call_parts(call: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Real payloads (recorded in tests/fixtures/vapi) carry function.arguments as a dict; accept a JSON string too."""
    fn = call.get("function") or {}
    args = call.get("parameters")
    if args is None:
        raw = fn.get("arguments", {})
        args = json.loads(raw) if isinstance(raw, str) else raw
    return call.get("id", ""), call.get("name") or fn.get("name", ""), args or {}


def _save_payload(kind: str, body: dict[str, Any]) -> None:
    """Raw events are kept on disk until the post-call worker (Phase 4) stores them in Postgres."""
    folder = Path(get_settings().spike_payload_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    (folder / f"{stamp}_{kind}.json").write_text(json.dumps(body, indent=2))


def _run_one(
    connect: Callable[[], psycopg.Connection], call_id: str, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    if name == "hello":  # Phase 0 spike tool, still published in the Vapi dashboard; removed in Phase 3
        return {"ok": True, "say": f"Hello {args.get('name', 'there')}, nice to meet you."}
    with connect() as conn:
        return execute(make_context(conn), call_id, name, args)


Analyze = Callable[[psycopg.Connection, str], None]


def _default_analyze(conn: psycopg.Connection, vapi_call_id: str) -> None:
    """Analyse one stored call and deliver its CRM note. Needs an LLM key; without one the call stays pending."""
    if not get_settings().anthropic_api_key.get_secret_value():
        logger.warning("no ANTHROPIC_API_KEY: call left pending for the worker", extra={"call": vapi_call_id})
        return
    process_call(conn, vapi_call_id)
    conn.commit()
    process_crm_outbox(conn, FakeCrm())  # the fake CRM until a real adapter exists (cut from v1, see PLAN)
    conn.commit()


def _ingest(connect: Callable[[], psycopg.Connection], body: dict[str, Any]) -> tuple[str, bool]:
    with connect() as conn:
        result = ingest_report(conn, body)
        conn.commit()
        return result


def _analyse_in_background(
    connect: Callable[[], psycopg.Connection], analyze: Analyze, vapi_call_id: str
) -> None:
    try:  # never let a failed analysis surface as an error: the report is already saved and the worker retries
        with connect() as conn:
            analyze(conn, vapi_call_id)
    except Exception:
        logger.exception("post-call analysis failed; the worker will retry", extra={"call": vapi_call_id})


def create_app(
    connect: Callable[[], psycopg.Connection] | None = None, analyze: Analyze | None = None
) -> FastAPI:
    setup_logging()
    app = FastAPI(title="P11 tool server")
    db = connect or (lambda: psycopg.connect(get_settings().database_url, connect_timeout=5))
    analyse = analyze or _default_analyze

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/vapi/tools", dependencies=[Depends(require_vapi_secret)])
    async def tools(request: Request) -> dict[str, Any]:
        body = await request.json()
        message = body.get("message", {})
        call_id = (message.get("call") or {}).get("id", "unknown-call")
        results = []
        for call in message.get("toolCallList") or []:
            tool_call_id, name, args = _tool_call_parts(call)
            result = await run_in_threadpool(_run_one, db, call_id, name, args)
            # Vapi expects `result` to be a flat string, so the structured result goes out as JSON text.
            results.append({"toolCallId": tool_call_id, "result": json.dumps(result)})
        return {"results": results}

    @app.post("/vapi/events", dependencies=[Depends(require_vapi_secret)])
    async def events(request: Request, background: BackgroundTasks) -> dict[str, Any]:
        body = await request.json()
        kind = str(body.get("message", {}).get("type", "unknown"))
        _save_payload(kind, body)
        logger.info("vapi event", extra={"type": kind})
        if kind == "end-of-call-report":
            # SAVE FIRST: the raw report is in Postgres before anything else can go wrong; a replay is a no-op.
            try:
                vapi_call_id, created = await run_in_threadpool(_ingest, db, body)
            except (
                KeyError,
                TypeError,
            ):  # a report without a call id cannot be filed; say so instead of a 500
                logger.warning("end-of-call-report without a call id was ignored")
                return {"ok": False, "error": "report has no call id"}
            if created:
                background.add_task(_analyse_in_background, db, analyse, vapi_call_id)
            return {"ok": True, "call": vapi_call_id, "stored": created}
        return {"ok": True}

    return app
