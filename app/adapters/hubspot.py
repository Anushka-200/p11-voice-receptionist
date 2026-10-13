# Adapted from P03 (p03-ai-operations-agent/app/adapters/hubspot.py): contacts and call NOTES (no deals).
"""HubSpot adapter (free developer account, private-app token): contact upsert and a note on the contact.

"Upsert" = update if it exists, else create, so calling twice never makes a duplicate contact. Reads (search) are always
allowed; writes only when dry_run is False. A service that is down raises AdapterUnavailable and the crm_outbox retries
later; a request HubSpot refuses (a wrong token, a missing scope) raises AdapterRejected and is not retried.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.http import AdapterRejected, call
from app.adapters.resilience import CircuitBreaker, retry

logger = logging.getLogger(__name__)
API = "https://api.hubapi.com"
NOTE_TO_CONTACT = {
    "associationCategory": "HUBSPOT_DEFINED",
    "associationTypeId": 202,
}  # per HubSpot's notes guide


class HubSpotCrm:
    def __init__(
        self,
        token: str,
        dry_run: bool = True,
        client: httpx.Client | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._token, self.dry_run = token, dry_run
        self.client = client or httpx.Client()
        self.breaker = breaker or CircuitBreaker("hubspot")
        self._retry_kw: dict[str, Any] = {"sleep": sleep} if sleep else {}
        self._now = now or (lambda: datetime.now(UTC))

    def _req(self, method: str, path: str, ok: tuple[int, ...] = (200, 201), **kw: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token}"}
        return retry(
            lambda: self.breaker.call(lambda: call(self.client, method, f"{API}{path}", name="hubspot", headers=headers, ok=ok, **kw)),
            **self._retry_kw,
        )  # fmt: skip

    def _search(self, prop: str, value: str) -> str | None:
        body = {
            "filterGroups": [{"filters": [{"propertyName": prop, "operator": "EQ", "value": value}]}],
            "limit": 1,
        }
        results = self._req("POST", "/crm/v3/objects/contacts/search", json=body).json().get("results", [])
        return str(results[0]["id"]) if results else None

    def upsert_contact(self, patient: dict[str, Any]) -> str:
        if self.dry_run:
            logger.info("crm dry-run: would upsert contact")
            return f"dryrun-contact-{patient.get('id', 'x')}"
        first, _, last = (patient.get("name") or "").partition(" ")
        props = {
            k: v
            for k, v in {"firstname": first, "lastname": last, "phone": patient.get("phone")}.items()
            if v
        }
        known = patient.get("crm_contact_id")
        if known and not known.startswith("dryrun-"):  # the stored id is the reliable key; search lags writes
            try:
                self._req("PATCH", f"/crm/v3/objects/contacts/{known}", json={"properties": props})
                return str(known)
            except (
                AdapterRejected
            ):  # e.g. 404: the contact was deleted in HubSpot; fall through to search/create
                pass
        found = self._search("phone", patient["phone"]) if patient.get("phone") else None
        if found:
            self._req("PATCH", f"/crm/v3/objects/contacts/{found}", json={"properties": props})
            return found
        return str(self._req("POST", "/crm/v3/objects/contacts", json={"properties": props}).json()["id"])

    def add_call_note(self, crm_contact_id: str, note: str) -> str:
        if self.dry_run or crm_contact_id.startswith("dryrun-"):
            logger.info("crm dry-run: would add a call note")
            return "dryrun-note"
        body = {
            "properties": {"hs_timestamp": self._now().isoformat(), "hs_note_body": note},
            "associations": [{"to": {"id": crm_contact_id}, "types": [NOTE_TO_CONTACT]}],
        }
        return str(self._req("POST", "/crm/v3/objects/notes", json=body).json()["id"])

    def delete(self, kind: str, object_id: str) -> None:
        """Used only by the live check to remove what it created (HubSpot archives the record)."""
        if not self.dry_run:
            self._req("DELETE", f"/crm/v3/objects/{kind}/{object_id}", ok=(200, 204))
