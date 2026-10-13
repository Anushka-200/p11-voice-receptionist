# ADR 004: Post-call analytics design

Status: accepted (2026-10-12)

## Context
After each call the clinic needs:
- a summary
- an outcome (booked / rescheduled / cancelled / faq / transferred_human / transferred_emergency / abandoned)
- sentiment
- a CRM note

We also want quality signals on *every* call (online evals), not only on test calls. Webhooks can arrive twice
or while our server is down.

## Options
- **Vapi's built-in analysis** (summary, structured data, success evaluation): zero code, but the prompt and model
  live in the vendor, there's no Langfuse trace, and it's hard to evaluate or port to Pipecat in v1.5.
- **Our own post-call LLM call** via LiteLLM with a Pydantic schema: traced, testable, portable, cheap-tier model.
- **Pure rules** from the tool log: reliable for outcome, but can't summarise or judge sentiment.

## Decision
- **Save first:** the end-of-call report is upserted raw into `calls` by `vapi_call_id`, so a replay is a no-op.
- **A worker runs one structured-output call** (LiteLLM, cheap tier) producing `CallAnalysis`:
  - summary, outcome, sentiment
  - online-eval checks: AI disclosure said, phone read back, date read back, task completed
  - Repair-then-retry once; still invalid → `needs_review`.
- **Code cross-checks the LLM:** outcome vs the tool log (e.g. "booked" requires a successful `book_appointment`;
  "transferred_emergency" requires a `transferCall` to `on_call`). Mismatches are flagged on the dashboard.
- **CRM note through `crm_outbox`** with retries. The fake adapter is the default and HubSpot is real only in an
  approved live run. A CRM outage never loses a call record.
- **Optional experiment E6:** compare with Vapi's built-in analysis on agreement and cost.

## Consequences
- About one cheap LLM call per call (cost reported per call).
- The same pipeline works for the v1.5 Pipecat build, because it consumes our own `calls` record.
- Transcripts contain names and phone numbers. They are masked in logs and traces, kept in Postgres only, and
  every record is a fake patient.

## Update 2026-10-13 (built)
- **The phone log wins.** The stored outcome is derived by code from what the tools completed (and the transfer target); the
  LLM's opinion is kept in `eval_scores.llm_outcome`, and any disagreement adds an `outcome_mismatch` flag.
- **Silent calls** (no caller audio) skip the LLM entirely and are marked `abandoned`.
- **Measured cost:** about $0.0001 per analysed call (a Haiku-class model, thinking off).
- **Provider failure:** two invalid replies in a row leave the call `needs_review` with the error; it is never guessed.
- **Not built:** the real HubSpot adapter (cut for v1); the CRM runs on a fake with the same outbox and retry behaviour.
