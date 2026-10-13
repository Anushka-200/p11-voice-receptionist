# ADR 001: Voice platform choice

Status: accepted (2026-10-12)

## Context
We need an inbound voice receptionist that handles turn-taking, STT, LLM, TTS and tool calls with sub-second
response. Budget is $0 new spend and about 3 working days. The brief's job type ("Vapi/Retell voice agent") is what
Upwork clients post most often. A self-built pipeline is a separate v1.5 goal.

## Options
- **Vapi:** hosted pipeline; assistant config via API (can live in the repo as YAML); server-URL webhooks for tool
  calls and end-of-call reports; built-in transfer and end-call tools; browser web calls; free starting credit.
- **Retell AI:** similar hosted pipeline, strong turn-taking; equally valid. Fewer of our target postings name it.
- **Pipecat / LiveKit (framework):** full control of VAD, endpointing, barge-in and providers (e.g. Sarvam), but we
  host and scale it, and a phone line needs Twilio Media Streams. Days of work before the first good call.

## Decision
- **Vapi for v1.** The assistant is defined as code (`assistant/receptionist.yaml`) and pushed with
  `make sync-assistant`, so the config is reviewable, diffable and portable.
- **Browser web calls only** because of the $0 budget: no Twilio number, no international call charges from India.
- **Pipecat + Sarvam is v1.5.** It reuses the same tool server, so the two builds can be compared like-for-like.

## Consequences
- Fastest path to a working, demo-able agent; matches client vocabulary.
- Turn-taking internals are a black box: we can only tune what Vapi exposes. v1.5 fixes that.
- Web calls skip the phone network: no 8 kHz audio, no PSTN latency (~100–200 ms, stated as an estimate), and
  transfers can't reach a real phone. We verify transfers by the tool call and its destination, not by a ring.
- Vendor lock-in is limited: tools live on our server, and the prompt and config live in our repo.
- Verify in Phase 0: free credit amount, platform fee per minute, web-call support for tools and end-of-call reports.
