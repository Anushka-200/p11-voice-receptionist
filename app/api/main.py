"""The tool server. Phase 0 spike version: health, one hello tool, and an events endpoint that saves payloads.

Phase 2 replaces `hello` with the real tools (availability, book, ...) behind the same dispatcher.
"""

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request

from app.api.vapi_auth import require_vapi_secret
from app.log import setup_logging
from app.settings import get_settings

logger = logging.getLogger(__name__)


def _tool_call_parts(call: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Vapi's docs show two shapes (flat name/parameters, or OpenAI-style function.arguments): accept both."""
    fn = call.get("function") or {}
    args = call.get("parameters")
    if args is None:
        raw = fn.get("arguments", {})
        args = json.loads(raw) if isinstance(raw, str) else raw
    return call.get("id", ""), call.get("name") or fn.get("name", ""), args or {}


def hello(args: dict[str, Any]) -> str:
    return f"Hello {args.get('name', 'there')}, nice to meet you."


TOOLS = {"hello": hello}


def _save_payload(kind: str, body: dict[str, Any]) -> None:
    folder = Path(get_settings().spike_payload_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    (folder / f"{stamp}_{kind}.json").write_text(json.dumps(body, indent=2))


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="P11 tool server")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/vapi/tools", dependencies=[Depends(require_vapi_secret)])
    async def tools(request: Request) -> dict[str, Any]:
        body = await request.json()
        _save_payload("tool-calls", body)
        message = body.get("message", {})
        results = []
        for call in message.get("toolCallList") or []:
            call_id, name, args = _tool_call_parts(call)
            started = time.perf_counter()
            tool = TOOLS.get(name)
            # Vapi wants HTTP 200 always; a failure is reported inside the result as a plain string.
            key, text = ("result", tool(args)) if tool else ("error", f"unknown tool {name}")
            logger.info(
                "tool call",
                extra={
                    "tool": name,
                    "ok": tool is not None,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            results.append({"toolCallId": call_id, key: text})
        return {"results": results}

    @app.post("/vapi/events", dependencies=[Depends(require_vapi_secret)])
    async def events(request: Request) -> dict[str, bool]:
        body = await request.json()
        kind = str(body.get("message", {}).get("type", "unknown"))
        _save_payload(kind, body)
        logger.info("vapi event", extra={"type": kind})
        return {"ok": True}

    return app
