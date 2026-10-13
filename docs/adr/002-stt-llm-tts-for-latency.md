# ADR 002: STT, LLM and TTS choice for latency

Status: accepted (2026-10-12); provider and model names are confirmed in Phase 0 with dated prices

## Context
The target is p50 turn latency under 800 ms (end of caller speech → first agent audio). The latency is a sum:
endpointing + STT final + LLM time-to-first-token + TTS first byte + network. Cost per minute is the sum of the
same stages plus the Vapi platform fee. Our standard says LLM calls go through LiteLLM and are traced in Langfuse.

## Options
- **LLM during the call:**
  - Vapi-native provider/model: no extra hop; traced by Vapi, not Langfuse.
  - Vapi "custom LLM" pointing at our LiteLLM server: full tracing and routing, but every turn would travel
    Vapi (US) → tunnel → laptop (India) → provider, adding roughly 250–350 ms per turn.
- **STT:** Deepgram streaming (fast, endpointing controls, low cost) vs others offered by Vapi.
- **TTS:** low-latency voices (e.g. ElevenLabs flash-class, Vapi/Cartesia-class) vs higher-quality, slower voices.

## Decision
- **During calls, use Vapi's native LLM** on a fast, cheap tier. The model is chosen in Phase 0 from what Vapi
  offers, then challenged in experiment E1.
- **Deepgram streaming STT** as the baseline. Endpointing is tuned in experiment E3.
- **A low-latency TTS voice** as the baseline. Experiment E2 compares it with one alternative.
- **LiteLLM + Langfuse for everything we run ourselves:** post-call analysis and the text eval's agent and
  simulated caller.
- **Vapi's per-call metrics are imported** into our `calls` and `call_turns` tables, so latency and cost are
  measured from real call data, not estimates.

## Consequences
- The lowest-latency path, but live-call LLM traces live in Vapi, not Langfuse. We document this split.
- The text eval uses the same prompt through LiteLLM. It tests the *logic*, not the voice latency. Its model
  should match the live one as closely as possible.
- Tool calls still go through the tunnel to the laptop; their latency is measured separately. The v2 fix is
  hosting in a US region.
