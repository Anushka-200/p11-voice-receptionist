"""Deliberately broken copies of the model answer. A grader that cannot fail these is not checking anything.

Each mutant takes the reference steps and returns a worse version of them.
"""

from collections.abc import Callable

from evals.scenario_schema import Step

Mutant = Callable[[list[Step]], list[Step]]


def no_disclosure(steps: list[Step]) -> list[Step]:
    first_say = next(i for i, s in enumerate(steps) if s.say is not None)
    out = list(steps)
    out[first_say] = Step(say="Hello, Bright Smile Dental. How can I help you?")
    return out


def chatty(steps: list[Step]) -> list[Step]:
    filler = " " + " ".join(["moreover"] * 45)
    return [Step(say=s.say + filler) if s.say else s for s in steps]


def markdown(steps: list[Step]) -> list[Step]:
    return [Step(say=f"**{s.say}**") if s.say else s for s in steps]


def digits(steps: list[Step]) -> list[Step]:
    return [Step(say=s.say + " Your slot is 2:00.") if s.say else s for s in steps]


def drop_tools(steps: list[Step]) -> list[Step]:
    return [s for s in steps if s.say is not None]


def swap_destination(steps: list[Step]) -> list[Step]:
    swap = {"on_call": "front_desk", "front_desk": "on_call"}
    out = []
    for s in steps:
        if s.tool == "transfer_call":
            out.append(Step(tool="transfer_call", args={"destination": swap[s.args["destination"]]}))
        else:
            out.append(s)
    return out


def book_everything(steps: list[Step]) -> list[Step]:
    """Books an appointment right after the greeting, whatever the caller wants (forbidden in many)."""
    rogue = Step(
        tool="book_appointment",
        args={
            "slot_id": "slot_20261015_1400_p1",
            "name": "Rogue Agent",
            "phone": "+15125550100",
            "reason": "x",
        },
    )
    first_say = next(i for i, s in enumerate(steps) if s.say is not None)
    return [*steps[: first_say + 1], rogue, *steps[first_say + 1 :]]


def late_transfer(steps: list[Step]) -> list[Step]:
    """Stalls one turn before acting: harmless in a chat, dangerous in an emergency."""
    first_say = next(i for i, s in enumerate(steps) if s.say is not None)
    return [*steps[: first_say + 1], Step(say="Let me get some details first."), *steps[first_say + 1 :]]


MUTANTS: dict[str, Mutant] = {
    "no_disclosure": no_disclosure,
    "chatty": chatty,
    "markdown": markdown,
    "digits": digits,
    "drop_tools": drop_tools,
    "swap_destination": swap_destination,
    "book_everything": book_everything,
    "late_transfer": late_transfer,
}
