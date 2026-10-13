# P11 Voice AI Receptionist

The shared engineering standard and my working style load automatically from `~/portfolio/CLAUDE.md`
(which imports `ai-portfolio-kit/01_SHARED_CLAUDE.md`). Don't duplicate them here.
The full project brief is `~/portfolio/ai-portfolio-kit/projects/P11_voice_receptionist.md`. The approved plan is `PLAN.md`.

## Project: P11 Voice AI Receptionist (dental clinic; from the brief)
Platform: Vapi (or Retell) + Twilio number. Backend: FastAPI tool server reusing P3's MCP/calendar tools.
Call flow: greet + recording disclosure → intent (book / reschedule / cancel / FAQ / talk to human / emergency) → for booking: collect name, phone (read back), reason, preferred time → check availability → confirm slot → book → SMS confirmation.
Rules: dental emergencies (swelling, trauma, bleeding) → immediate transfer to on-call number; never give medical advice; always confirm dates/phones by reading back.
Post-call webhook: store transcript, LLM summary, outcome (booked/rescheduled/FAQ/transfer/abandoned), sentiment → Postgres + HubSpot note. Streamlit call-analytics dashboard.
Eval: 15 scripted test calls (normal, fast talker, changes mind, wrong date "next Friday" ambiguity, background noise, asks for human, emergency, silent caller). Measure: task success, turn latency (p50/p95), interruptions handled, cost per minute.
Core (v1.5, see 'Senior Voice Engineer Extension'): same flow rebuilt on Pipecat with Sarvam STT/TTS as a drop-in module, compared against the Vapi build on latency, cost and call feel.

## Decisions that override the brief (2026-10-12, see PLAN.md)
- **$0 new spend:** Vapi free credit, browser (web) calls only, **no Twilio number**. SMS confirmations go to a
  dry-run outbox. Real phone transfers and 8 kHz phone audio are listed limitations.
- **Scope:** Vapi v1 first. The Pipecat + Sarvam extension is v1.5 and starts only after v1 ships.
- **Reuse P03** (`~/portfolio/p03-ai-operations-agent`) by **copying** modules with an "adapted from P03" header,
  not importing across repos.
- Dry-run by default; fakes for every external system; real adapters only in live runs I approve.
- Ports: API 8011, Postgres 5441, dashboard 8511.
- Never paste secrets into chat; never commit `.env`.
