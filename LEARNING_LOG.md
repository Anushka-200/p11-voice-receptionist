# LEARNING LOG: P11 Voice AI Receptionist

Your quiz answers and the grading, kept so you can revisit weak spots after a day, a week and a month.

## Prompt 0: voice AI concepts (about 12.5/21)
Weakest: Q5 (filler speech while a tool runs; retry once silently, then apologise and offer a human) and Q7
(the four cost parts are telephony, STT, LLM, TTS plus the platform fee; "inbound-only" means the caller dials us).

## Phase 0 quiz (1/3)
- Why a separate app role: the app can read and write rows but cannot change table structure. Limits what a bug or injection can do.
- Why enforce no-double-booking in Postgres: tool checks can race, a database constraint is atomic. Defence in depth.
- Replayed webhook: `calls.vapi_call_id` is UNIQUE, so the second delivery is an upsert that changes nothing.

## Phase 1 quiz (about 1.75/3)
1. Stalling the emergency transfer: **no answer given**. The grader has a deadline (`tool_by_agent_turn`: transfer by
   agent turn 1). The stalling version speaks first, so the transfer lands on turn 2: right outcome, too late.
2. pass^3: your answer (variable outputs, all must match) was right. Add: it measures reliability, not luck.
   For voice, one failure in three runs is a real patient hearing a wrong answer one call in three.
3. Code grader vs LLM judge: your answer was right (free, repeatable, short calls can be checked exactly).

## Phase 2 quiz (about 1.25/3)
1. Booking an unoffered slot: you said it could ruin the experience. The exact reason: the model could invent or
   mis-copy a slot id, or book a time the caller never heard. Only slots this call was offered can be booked.
2. The race: it is not random. The database decides. Both inserts reach the exclusion constraint (it refuses
   overlapping time ranges, not only identical ones); the second one waits for the first to commit, then fails.
   The loser hears: "Sorry, that time was just taken. Let me find another one."
3. One transaction per tool call: **no answer given**. A Vapi request can carry several tool calls. With one
   transaction per request, one failure would undo the successful calls too (such as the slots just offered),
   and locks would be held longer, widening the race window. Per call, a failure undoes only its own writes.

## Phase 3 quiz (answers pending)
See `docs/walkthrough/phase-3-assistant-as-code.md`.

## Phase 4 and 5 quizzes (answers pending)
See `docs/walkthrough/phase-4-post-call.md` and `docs/walkthrough/phase-5-packaging.md`.

## Lessons from building (mine, for the next project)
- A passing eval proves nothing until a deliberately broken agent fails it (the mutant tests did that).
- A stub hides real bugs: the real-tool eval found the FAQ gap and the deadlock.
- When a model returns "nothing", look at `finish_reason` before blaming the prompt (hidden thinking ate the token cap).
- Read the transcript of a failing scenario before changing anything: most failures were my scenarios or harness, not the agent.
- A chained shell command hides a failing test behind a pipe; gate a commit on the real exit status.
- Real data in the dev database can break tests that assumed an empty one: give every test fresh ids.
