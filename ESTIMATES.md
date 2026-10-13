# ESTIMATES: P11 Voice AI Receptionist

Rules: PERT = (O + 4M + P) / 6. Multipliers from `03_ESTIMATION_TRACKER.md` section B. Hours are YOUR hours with Claude
writing the code. At 150% of a task's planned hours, stop and diagnose. Full per-task table is built after Phase 0
(the per-task O/M/P round is still to do); this file starts with the whole-phase quick estimate.

## Quick estimates (estimate-before-you-code habit)
| Phase | Done means | Your estimate | Start | Planned end | Stop-and-diagnose at (150%) | Actual | Ratio | Why it was off |
|---|---|---|---|---|---|---|---|---|
| 0 Setup + spikes (2026-10-12) | repo scaffold + P03 basics adapted + Postgres 5441 migrated/seeded + tests/lint green + CI file; Vapi spike call end to end (needs your account) | 0.65 h (39 min) | 20:21 | 21:00 | 21:19 | 0.70 h (to 21:03; code part only, spike 0.5 still open) | 1.08 | Fast because P03 supplied most modules; the Vapi spike needs your account steps and is timed separately. |
| 1 Eval first (2026-10-12) | 15 scenario YAMLs + stub clinic + harness (scripted and simulated caller) + code grader + pass^3 report + mutant tests; `make eval` green on the reference agent, every mutant caught | 3.0 h (PROVISIONAL: Claude's number, you did not give one; replace with yours) | 22:42 | 01:42 | 03:12 (150%) | | | |
