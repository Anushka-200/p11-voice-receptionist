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

> **Deferred by the owner (2026-10-13, short on time):** the 15 scripted live calls and the experiments. Cells marked pending stay empty until they are run.

## 2. Live calls: latency and cost (measured 2026-10-13, 3 calls, preliminary)
Source: Vapi end-of-call reports through our post-call pipeline (`make bench`). Stack: Soniox STT, GPT-4.1, Vapi voice
"Elliot", browser calls (no phone-line hop). Three calls (FAQ 52 s, emergency 23 s, booking 90 s), 15 turns, 2.75 minutes.
Too few turns for a baseline: this table shows the shape, and the 15 scripted calls will replace it.

| Metric | Target | Measured (3 calls) | After experiments |
|---|---|---|---|
| Turn latency p50 | < 800 ms | **1,370 ms** (not met) | pending |
| Turn latency p95 | report | **1,988 ms** | pending |
| Stage averages | report | transcriber 441 ms, model 449 ms, voice 435 ms, endpointing 6.5 ms | pending |
| Cost per minute, total | report | **$0.0857** | pending |
| Cost per minute by stage | report | Vapi $0.050, LLM $0.0239, TTS $0.0076, STT $0.0042 | pending |
| Our post-call analysis | report | about $0.0001 per call | n/a |
| Task success on 15 scripted calls | >= 13/15 | pending (3 of 3 live calls behaved correctly, unscored) | n/a |
| Emergencies routed to a transfer | 100% | 1/1 (transfer to the on-call number initiated; failed because a browser call cannot ring) | n/a |
| AI + recording disclosure in the stored transcript | 100% | **2/3**: one transcript reads "I'm an. Assistant". The owner heard the greeting correctly on every call, so this is probably a transcription artifact; our check can only read the transcript | E7 |

What the numbers say:
- The latency is spread across three stages of roughly equal size; no single fix gets under 800 ms. Candidates: a faster
  LLM (E1), a shorter wait before speaking (E3).
- Vapi's own fee is 58% of the cost. In the 90 s booking call the LLM took 31% of the cost with 29,370 prompt tokens, because
  the prompt and tool definitions are re-sent every turn (E4).
- Web calls skip the phone network; a phone line adds roughly 100 to 200 ms (an estimate, not measured here).

## 3. Experiments (pending live calls)
E1 faster LLM tier, E3 speaking-plan timing, E4 shorter prompt, E5 filler policy. Each gets a row here:
change, calls used, p50/p95 before and after, cost per minute before and after, task success.
