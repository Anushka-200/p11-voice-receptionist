"""A tiny in-memory clinic that stands in for the real tool server until Phase 2.

Why: the harness and grader must be proven BEFORE the real tools exist. This stub has the same tool
names and the same basic rules (valid phone, known slot, no double booking), so a wrong call fails here
the way it will fail against the real server. Phase 2 replaces it with the FastAPI tool server.
"""

import re
from typing import Any

from evals.scenario_schema import Setup

E164 = re.compile(r"^\+1\d{10}$")

SLOTS = {
    "slot_20261015_1400_p1": "Thursday, October fifteenth at two PM",
    "slot_20261016_0900_p1": "Friday, October sixteenth at nine AM",
    "slot_20261023_0900_p1": "Friday, October twenty-third at nine AM",
}
APPOINTMENT_TYPES = {"cleaning", "checkup", "emergency", "filling", "whitening"}
DESTINATIONS = {"on_call", "front_desk"}
FAQ = {  # topic -> keywords (substring match, first topic wins)
    "hours": ("hours", "open", "clos"),
    "insurance": ("insurance", "delta", "cigna", "aetna"),
    "parking": ("parking", "park"),
    "address": ("address", "where", "located"),
}


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


class StubClinic:
    def __init__(self, setup: Setup) -> None:
        self.setup = setup
        self.bookings: list[dict[str, Any]] = []
        self.cancelled = 0
        self.rescheduled = 0
        self.transferred_to: str | None = None
        self.ended = False  # set by a transfer or end_call
        self.faq_topics: list[str] = []
        self._last_type: str | None = None  # what check_availability was last asked for
        eb = setup.existing_booking
        if eb:
            self.bookings.append(
                {"id": eb["id"], "type": eb["type"], "slot_id": None, "label": eb["label"], "active": True}
            )

    # -- the one entry point the harness uses --
    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return _err("unknown_tool", f"no tool named {name}")
        try:
            return handler(**args)
        except TypeError as exc:  # missing or unexpected argument
            return _err("bad_arguments", str(exc))

    def final_state(self) -> dict[str, Any]:
        active = [b for b in self.bookings if b["active"] and b["slot_id"] is not None]
        return {
            "bookings_active": len([b for b in self.bookings if b["active"]]),
            "booking_type": active[-1]["type"] if active else None,
            "booked_phone": active[-1].get("phone") if active else None,
            "cancelled": self.cancelled,
            "rescheduled": self.rescheduled,
            "transferred_to": self.transferred_to,
            "faq_topics": sorted(set(self.faq_topics)),
        }

    # -- tools --
    def _tool_check_availability(self, when_text: str, appointment_type: str) -> dict[str, Any]:
        if appointment_type not in APPOINTMENT_TYPES:
            return _err("unknown_type", "that appointment type does not exist")
        self._last_type = appointment_type
        return {"ok": True, "slots": [{"slot_id": k, "say": v} for k, v in SLOTS.items()]}

    def _slot_taken(self, slot_id: str) -> bool:
        return any(b["active"] and b["slot_id"] == slot_id for b in self.bookings)

    def _tool_book_appointment(self, slot_id: str, name: str, phone: str, reason: str) -> dict[str, Any]:
        if slot_id not in SLOTS:
            return _err("unknown_slot", "that slot was not offered")
        if not name.strip():
            return _err("bad_name", "a name is required")
        if not E164.match(phone):
            return _err("bad_phone", "phone must be +1 and ten digits")
        if self._slot_taken(slot_id):
            return _err("slot_taken", "that slot was just taken")
        booking = {
            "id": 100 + len(self.bookings),
            "type": self._last_type or "checkup",
            "slot_id": slot_id,
            "label": SLOTS[slot_id],
            "phone": phone,
            "active": True,
        }
        self.bookings.append(booking)
        return {"ok": True, "booking_id": booking["id"], "say": SLOTS[slot_id]}

    def _tool_find_my_appointment(self, phone: str) -> dict[str, Any]:
        if not E164.match(phone):
            return _err("bad_phone", "phone must be +1 and ten digits")
        if phone != self.setup.patient_phone:
            return {"ok": True, "bookings": []}
        found = [
            {"booking_id": b["id"], "type": b["type"], "when": b["label"]}
            for b in self.bookings
            if b["active"]
        ]
        return {"ok": True, "bookings": found}

    def _find(self, booking_id: int) -> dict[str, Any] | None:
        return next((b for b in self.bookings if b["id"] == booking_id and b["active"]), None)

    def _tool_reschedule_appointment(self, booking_id: int, new_slot_id: str) -> dict[str, Any]:
        booking = self._find(booking_id)
        if booking is None:
            return _err("no_such_booking", "no active booking with that id")
        if new_slot_id not in SLOTS or self._slot_taken(new_slot_id):
            return _err("unknown_slot", "that slot is not available")
        booking.update(slot_id=new_slot_id, label=SLOTS[new_slot_id])
        self.rescheduled += 1
        return {"ok": True, "say": SLOTS[new_slot_id]}

    def _tool_cancel_appointment(self, booking_id: int) -> dict[str, Any]:
        booking = self._find(booking_id)
        if booking is None:
            return _err("no_such_booking", "no active booking with that id")
        booking["active"] = False
        self.cancelled += 1
        return {"ok": True}

    def _tool_faq_lookup(self, question: str) -> dict[str, Any]:
        q = question.lower()
        for topic, keywords in FAQ.items():
            if any(k in q for k in keywords):
                self.faq_topics.append(topic)
                return {"ok": True, "topic": topic}
        return {"ok": True, "topic": None}

    def _tool_transfer_call(self, destination: str) -> dict[str, Any]:
        if destination not in DESTINATIONS:
            return _err("bad_destination", "destination must be on_call or front_desk")
        self.transferred_to = destination
        self.ended = True
        return {"ok": True}

    def _tool_end_call(self) -> dict[str, Any]:
        self.ended = True
        return {"ok": True}
