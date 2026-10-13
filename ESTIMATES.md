# ESTIMATES: P11 Voice AI Receptionist

Rules: PERT = (O + 4M + P) / 6. Multipliers from `03_ESTIMATION_TRACKER.md` section B. Hours are YOUR hours with Claude
writing the code. At 150% of a task's planned hours, stop and diagnose. Full per-task table is built after Phase 0
(the per-task O/M/P round is still to do); this file starts with the whole-phase quick estimate.

## Quick estimates (estimate-before-you-code habit)
| Phase | Done means | Your estimate | Start | Planned end | Stop-and-diagnose at (150%) | Actual | Ratio | Why it was off |
|---|---|---|---|---|---|---|---|---|
| 0 Setup + spikes (2026-10-12) | repo scaffold + P03 basics adapted + Postgres 5441 migrated/seeded + tests/lint green + CI file; Vapi spike call end to end (needs your account) | 0.65 h (39 min) | 20:21 | 21:00 | 21:19 | 0.70 h (to 21:03; code part only, spike 0.5 still open) | 1.08 | Fast because P03 supplied most modules; the Vapi spike needs your account steps and is timed separately. |
| 1 Eval first (2026-10-12) | 15 scenario YAMLs + stub clinic + harness (scripted and simulated caller) + code grader + pass^3 report + mutant tests; `make eval` green on the reference agent, every mutant caught | 3.0 h (PROVISIONAL: Claude's number, you did not give one; replace with yours) | 22:42 | 01:42 | 03:12 (150%) | ended 15:12 on Oct 13 (your read-through, all 15 scenarios marked reviewed: true); active hours still to be given | | |
| 0.5 Vapi spike (2026-10-13) | a browser call reaches our tool through the tunnel and we receive the end-of-call report | not given | (not noted) | n/a | | ended 14:47 | | Needed dashboard steps and a tunnel dropout; the report held cost split and per-turn latency. |
| 2 Tool server (2026-10-13) | 6 tools + date resolver + phone parsing + auth + idempotency + call_sessions + fake adapters, unit tests green, race = one booking; Google Calendar live run split out (needs your service account) | 0.68 h (41 min) | 14:49 | 15:30 | 15:50 (150% = 61 min) | 0.72 h wall-clock (14:49 to 15:32); active hours still to be given | 1.05 | Build ran while you read Phase 1; Google Calendar adapter split out as Phase 2b. |
| 3 Assistant as code (2026-10-13) | voice prompt + `assistant/receptionist.yaml` (model, voice, endpointing, silence ladder, tools, transfer, limits) + `make sync-assistant`; text eval with the live prompt >= 13/15 pass^3, emergencies 100%; 3 live web calls (book, FAQ, emergency) | 0.78 h (47 min, your estimate) | 15:13 | 16:00 | 16:23 (150% = 70 min) | 16:39 marked as end of your review; acceptance NOT yet met (sync apply, eval-live, 3 live calls pending); wall-clock 1.43 h, active hours still to be given | 1.83 (wall-clock, provisional) | Over the 150% mark at 16:23. Claude's view: 2.5 to 3.5 h, because it needs your Anthropic key, a first-time Vapi apply that may need fixes, a real-tool eval backend and three live calls. Tell me at 16:00 if we are behind. |


## Project summary (draft: actual hours still to be given by the owner)
Wall-clock hours below are start to end on the clock and include reading and waiting, so the ratios are rough.

| Phase | Planned | Wall-clock | Ratio | Note |
|---|---|---|---|---|
| 0 Setup | 0.65 h | 0.70 h | 1.08 | P03 supplied most modules |
| 1 Eval first | 3.0 h (Claude's) | owner review ended 15:12 | n/a | active hours not given |
| 2 Tool server | 0.68 h | 0.72 h | 1.05 | built while the owner read Phase 1 |
| 3 Assistant as code | 0.78 h | 1.43 h to the owner's marked end (16:39); acceptance (live eval, Vapi publishing, live calls) ran on well past it | well over 1.8 | the acceptance run exposed harness faults, prompt gaps and a real concurrency bug; exact active hours not given |
| 4 Post-call | not given | pending | n/a | built on the three real calls |
| 5 Packaging | not given | pending | n/a | measurements wait for live calls |

Scope note: the live evaluation (15 scripted calls, five experiments, demo video) was deferred by the owner on 2026-10-13 and is not in these hours.

Three biggest estimation lessons so far:
1. **"Acceptance" is where the time goes.** Building the eval harness took a fraction of the time spent making the numbers mean something.
2. **A plan that depends on a platform's undocumented behaviour needs a timeboxed spike first** (the Vapi spike answered four open questions in one call).
3. **The owner's manual steps (accounts, dashboard publishing) are not free**: the draft-versus-published confusion took a long back and forth.

Suggested multipliers for the next voice project: agent/eval acceptance 3.0, integration 1.4 (unchanged), docs 1.0.
