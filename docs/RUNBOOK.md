# Runbook: when something breaks

First, always: `make check-assistant` (is what Vapi holds the same as the repo?) and `curl localhost:8011/health`.

| Symptom | Likely cause | What to do |
|---|---|---|
| The bot answers but every tool fails ("I'm having trouble…, let me connect you with the front desk") | Tunnel down or tool server down | `make api`, `make tunnel`; a restarted quick tunnel has a NEW url: set `PUBLIC_BASE_URL`, run `make sync-assistant-apply`, then Restore/publish in the dashboard |
| 401 in the server log | Webhook secret differs between `.env` and the Vapi credential | Re-create the credential with the `VAPI_WEBHOOK_SECRET` value; never paste it in chat |
| Dashboard prompt looks old | The dashboard shows a per-user draft or older version | `make check-assistant` is the truth; discard the draft in the Publish dialog (never Publish a stale draft) |
| Calls end with `call.in-progress.error-transfer-failed` | Transfer to a number that cannot ring (browser call, or a placeholder number) | Expected in v1. Put the clinic's real numbers in `assistant/receptionist.yaml` and test on a phone line |
| Calls stay `pending` on the dashboard | No `ANTHROPIC_API_KEY`, or the provider is down | Calls are already saved. `make worker` retries; nothing is lost |
| `needs_review` calls | The model failed to produce valid analysis twice | Read the transcript on the dashboard; `analysis_error` has the reason |
| `outcome_mismatch` flag | The LLM disagreed with the phone log | The log wins and is stored; read the transcript to see which was wrong |
| `disclosure_missing` flag | The stored transcript lacks "AI" (seen once in three calls, heard correctly by the owner: probably a transcription artifact) | Listen to the recording to confirm; if it was really missing, try variant E7 ("artificial intelligence"). It is a compliance item, so verify, do not ignore |
| CRM notes stuck | CRM down (fake CRM only in v1) | They stay `pending` with attempts counted; after 5 failures they are `failed` with the error |
| Booking says "just taken" often | Two callers racing for one slot | Working as designed (per-provider lock plus a database constraint); no double bookings are possible |
| Cost spike | Long calls or long prompts | Calls are capped at 300 s and 200 output tokens per reply; E4 shortens the prompt |
| Vapi credit exhausted | $5 free credit | Stop; the text eval (`make eval-live`, about $0.03) covers logic, live calls need credit |
| Duplicate webhook | Vapi retried | Harmless: the call id is unique, a replay stores nothing and triggers no second analysis |

## Provider outage plan
- **Speech-to-text:** Vapi's automatic transcriber fallback is on (`fallbackPlan.autoFallback`).
- **Live LLM:** not configured with a fallback in v1; an outage ends calls with Vapi's own error. Next step: a fallback model in the assistant config.
- **Our tool server:** tools return a spoken "let me connect you with the front desk" and the calendar breaker stops hammering a down calendar.
- **Post-call LLM:** LiteLLM retries with backoff, then a fallback model; if both fail the call stays pending for the worker.
