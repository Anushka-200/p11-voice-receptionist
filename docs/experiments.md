# Experiments (each needs live calls; status PENDING until run)

Budget: Vapi free credit was $5.00; the three first calls cost about $0.24 and the two earlier test calls about $0.03.
At about $0.086 per minute and about 1.3 minutes per scripted call: 15 baseline calls about $1.7, three variants of
five calls about $1.7, the three extra calls about $0.3. That fits in the remaining credit, with little spare.

**Method for every experiment:** push the variant with
`uv run python -m scripts.sync_assistant --variant assistant/variants/<file>.yaml --apply`, make the same five
scripted calls (calls 1, 3, 4, 8 and 10 in `docs/TEST_CALLS.md`) on the baseline and on the variant, then compare
`make bench` against `uv run python -m app.bench --assistant <variant name>`. Decide by the rule below. A result from
five calls is **directional**, and says so in the table.

| # | Variant file | Change | Hypothesis | Decision rule |
|---|---|---|---|---|
| E1 | `e1_faster_llm.yaml` | gpt-4.1 to gpt-4.1-mini | LLM stage 449 ms drops, cost per minute drops | adopt if p50 drops 150 ms or more and task success does not fall |
| E3 | `e3_faster_start.yaml` | start-speaking wait 0.4 s to 0.1 s | turn latency drops about 300 ms | adopt only if the agent still never cuts the caller off mid-number (watch call 3, the fast talker) |
| E4 | `e4_short_prompt.yaml` | prompt 552 words to under 360 | cost per minute and model latency drop (29,000 prompt tokens in a 90 s call) | adopt if cost drops and the safety calls (emergency, medical advice) still pass |
| E5 | `e5_no_filler.yaml` | remove "one moment" fillers | turn latency number unchanged, perceived dead air increases | keep fillers unless silence under 1.5 s is not noticed |
| E7 | `e7_spelled_ai.yaml` | "AI" to "artificial intelligence" in the greeting | disclosure heard in every call (1 of 3 real calls dropped "AI") | adopt if `disclosure_said` is 5/5 |

Not yet planned: E2 (a different voice: needs a voice id picked in the dashboard), a fallback live LLM, and a turn-taking
lab (Pipecat v1.5).

## Results
| # | Calls | Metric before → after | Verdict |
|---|---|---|---|
| E1 | pending | | |
| E3 | pending | | |
| E4 | pending | | |
| E5 | pending | | |
| E7 | pending | | |
