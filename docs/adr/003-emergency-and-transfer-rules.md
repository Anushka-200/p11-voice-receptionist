# ADR 003: Emergency and transfer rules

Status: accepted (2026-10-12); layer 2 depends on a Phase 3 spike

## Context
A caller with facial swelling, a knocked-out tooth, jaw trauma, bleeding that won't stop or trouble breathing or
swallowing must reach a human immediately. In P03 (text) the emergency path ran before the LLM. In a live call
the LLM is in the loop for every turn, so we can't fully bypass it the same way. A missed emergency is the worst
failure this system can have. A receptionist must also never give medical advice.

## Options
- Prompt rule only: simple, but fails silently on odd phrasing or a model slip.
- **Prompt rule + server-side safety net:** our server watches live transcript events with deterministic regexes
  and forces a transfer through Vapi live call control if the LLM didn't transfer.
- Route every call through a human first: defeats the purpose.

## Decision
- **Fixed destinations:** `on_call` (dentist on call) and `front_desk`, defined in the assistant config. The LLM
  chooses *which* destination, never the number.
- **Layer 1:** a short, explicit prompt rule plus the `transferCall` tool. Breathing or swallowing trouble means
  "call 911 now", then transfer.
- **Layer 2:** `config/emergency.yaml` regexes (adapted from P03) run on transcript events. A match without a
  transfer within N seconds forces one.
  - Timeboxed spike: 1.5 h.
  - If Vapi can't do it, this ADR records that, and layer 1 must score 100% in the evals.
- **"Talk to a human"** → `front_desk` transfer at any point, with no pushback.
- **No medical advice:** fixed wording that offers to book or connect.
- When in doubt, transfer. A false alarm costs a staff minute.

## Consequences
- **Measured:**
  - Emergency phrasings in the text eval (target 100% transferred).
  - One live emergency call.
  - A negation set ("no swelling, just a cleaning") to report false positives.
- Web calls can't ring a real phone. Success means `transferCall` was invoked with the right destination; the
  real transfer is a stated limitation.

## Update 2026-10-13 (after the first live calls)
- **Built and measured:** layer 1 only (prompt rule plus the transfer tool). The text eval handled all 9 emergency runs (3 scenarios x 3)
  in each of the last two full evals, 18 of 18; one live emergency call initiated the transfer to the on-call number.
- **Layer 2 (the server forcing a transfer from transcript regexes) was NOT built.** It was the first item on the cut list. It
  remains the right next step, because layer 1 depends on the model.
- **How a transfer appears in Vapi's report:** a `transferCall` tool call whose argument is the dialled NUMBER (not our label),
  plus `artifact.transfers[].status`. The post-call code maps number to `on_call` / `front_desk`. On a browser call the status
  is `failed` and `endedReason` is `call.in-progress.error-transfer-failed`: expected, and flagged `transfer_failed`.
- **The transfer tool announces itself** ("Connecting you to our on-call dentist now."), so the prompt tells the model never to say it.
- **Found:** the voice dropped the word "AI" in the greeting once in three calls. The disclosure is checked on every call.
