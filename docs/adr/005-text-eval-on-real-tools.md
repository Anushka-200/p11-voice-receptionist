# ADR 005: Text rehearsal on the real tools

Status: accepted (2026-10-13)

## Context
Live voice calls cost money and minutes, and a free credit is small. We still need many repeated runs to measure reliability
(pass^3). Voice testing also mixes two questions: does the logic work, and does the call feel right.

## Options
- **Live calls only:** realistic but 45 runs would cost more than the whole credit.
- **A stub clinic in the eval:** free, but it hid real bugs (the FAQ missing "closing", booking race behaviour).
- **Text conversation through the same prompt and tool definitions, with the REAL tool code on a database rolled back
  after every run, a simulated caller and a code grader.**

## Decision
The third option. `make eval-live` runs 15 scenarios x 3 runs for about $0.03. The reference-agent version of the same eval
runs in CI for free and proves the grader (8 deliberately broken agents must fail).

## Consequences
- Found and fixed with it: a booking deadlock under concurrent callers, an FAQ keyword gap, a prompt that let the model say
  the transfer twice, hidden "thinking" eating the token cap, and several harness faults of our own.
- It cannot measure voice, latency, turn-taking or the live model; the 15 live calls do. The text model is a proxy.
- Scores vary by run (13/15 and 14/15 on the final two full runs), so pass^3 on 15 scenarios has about a one-scenario margin.
