# Benchmarks

Everything here is measured, dated and reproducible with the command shown. Rows marked **pending** need live
Vapi calls that have not been made yet; nothing is estimated or filled in by hand.

## 1. Text eval: the real receptionist logic on the real tools (2026-10-13)
`make eval-live`: the production prompt and tool definitions through `claude-haiku-5-5` (LiteLLM, thinking off),
a simulated caller, the REAL tool code on Postgres rolled back after every run, scored by the code grader.
15 scenarios x 3 runs; a scenario passes only if all 3 runs pass (pass^3). Cost: **$0.0327 per 45 runs
($0.0007 per run)**, about 6 minutes with 4 parallel workers.

| Step | What changed | Result |
|---|---|---|
| Baseline | first live run (eval harness faults: dropped speech said alongside a tool call, call kept going after a transfer) | 2/15 (one pass each) |
| Harness fixes | speech with a tool call counts, call ends at transfer or hang-up, check the stored phone | 8/15 |
| Prompt, FAQ, personas | day already given is used, 911 line in every emergency, shorter FAQ answers, explicit caller personas | 12/15 |
| Thinking off | Claude's hidden thinking ate the 200-token cap and returned empty replies; LLM wrapper now fails loudly | 11/15 pass^3 (all failures: reply length) |
| Transfer + read-back rules | transfer tool announces itself, stop after a read-back until answered | **14/15 pass^3** |
| Final confirmation run | same code, fresh run | **13/15 pass^3**, emergencies 3/3, disclosure 15/15 |

Target was 13/15 with emergencies 100%: met, with little margin. The final run's two misses each passed 2 of 3
runs and failed only `voice_style` (one reply with digits instead of words, one 49-word reply). Tools, transfers
and safety rules were correct in all 45 runs.

What this does NOT show: voice latency, turn-taking, interruption handling or the live model. Live calls use
GPT-4.1 through Vapi; this text eval uses a Haiku-class model as a proxy (ADR 002).

### Bugs the evals found (each fixed, each with a test)
- Concurrent bookings of one slot deadlocked in the database exclusion constraint: the loser heard a transfer
  offer instead of "just taken". Fixed with a per-provider-day lock (30/30 passes; the old code failed 2 of 12).
- The FAQ tool missed "closing" (keyword "close"): found only when the eval used the real tools, not the stub.
- Hidden model thinking consumed the whole token cap and returned empty answers; the LLM wrapper returned an
  empty string silently. Now it logs and raises.
- Voice answers read whole FAQ entries aloud (46 words): entries shortened.

## 2. Live call latency and cost (pending)
Source: Vapi end-of-call reports (`performanceMetrics`, `costBreakdown`). Spike call (2026-10-13, 30 s, default stack
Soniox + GPT-4.1 + Vapi "Elliot", 2 turns): turn latency 1,829 ms and 1,190 ms; cost $0.0297, about $0.058 per
minute (Vapi platform $0.0255, STT $0.0022, LLM $0.0012, TTS $0.0008). Too few turns to call it a baseline.

| Metric | Target | Baseline (15 live calls) | After experiments |
|---|---|---|---|
| p50 turn latency | < 800 ms | pending | pending |
| p95 turn latency | report | pending | pending |
| Cost per minute by stage | report | pending | pending |
| Task success on 15 test calls | >= 13/15 | pending | n/a |
| Emergencies transferred | 100% | pending | n/a |

## 3. Experiments (pending live calls)
E1 faster LLM tier, E3 speaking-plan timing, E4 shorter prompt, E5 filler policy. Each gets a row here:
change, calls used, p50/p95 before and after, cost per minute before and after, task success.
