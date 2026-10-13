# Turning on the real Google Calendar, HubSpot and emergency layer two

Everything below is already built and tested against fake servers. These steps are the ones only you can do, because they use
your accounts. **No secret ever goes in chat**: the scripts copy values between files and never print them.

## A. Reuse P03's calendar and HubSpot (about 3 minutes)
1. Copy the three settings from P03's `.env` into P11's `.env` (nothing is displayed):
   ```bash
   cd ~/portfolio/p11-voice-receptionist
   make import-p03-env
   ```
   It prints only the names, the lengths, and whether the credentials file exists. `GOOGLE_CREDENTIALS_FILE` is a *path* to the
   same service-account file P03 uses; the file itself is not copied. `CALENDAR_BACKEND`, `CRM_BACKEND` and `DRY_RUN` are untouched.
2. **Recommended, 1 minute:** the shared test calendar will also hold P03's HVAC jobs. P11 ignores them when finding free
   slots, but a screenshot for a client would mix the two businesses. For a clean demo create a new calendar named
   "Bright Smile Dental (test)" in Google Calendar, share it with the service account's email ("Make changes to events"), and put
   its calendar id in `.env` as `GOOGLE_CALENDAR_ID`. Skipping this works too.

## B. Prove each one against the real account (about 5 minutes)
Real writes need two deliberate switches: `DRY_RUN=false` in `.env` and `--confirm`. Set `DRY_RUN=false` only for these checks, then
put it back to `true`.
```bash
make live-check-calendar     # creates a labelled event two days ahead, reads it back, deletes it
make live-check-hubspot      # creates a contact "P11 Livecheck" and a note, then removes both
```
Each prints `ok:` lines or a `FAILED` reason. Two failures you may see, both easy: **HubSpot 403** means the private app needs the
notes scope (HubSpot's guide did not name it, so this is the one thing I could not verify), add it in HubSpot > Settings >
Integrations > Private apps and re-run; **Google 404** means the calendar is not shared with the service account.
Send me the output (it contains no secrets).

## C. Switch them on for real calls
In `.env`: `CALENDAR_BACKEND=google` and `CRM_BACKEND=hubspot`, restart `make api`. With `DRY_RUN=true` they only log what they
would write; set `DRY_RUN=false` to write for real. SMS stays a dry-run outbox in v1 either way.

## D. Prove the emergency second layer on a real call (about 5 minutes)
This is a safety net, so it should be seen working once. The test-only assistant has **no emergency rule in its prompt**, so the
agent will not transfer by itself:
```bash
uv run python -m scripts.sync_assistant --variant assistant/variants/l2_test_no_emergency_rule.yaml        # dry run first
uv run python -m scripts.sync_assistant --variant assistant/variants/l2_test_no_emergency_rule.yaml --apply
```
In Vapi open `p11-receptionist-l2test` (check its version list the same way as before: Restore the newest if the editor looks
stale), click Talk and say: "My face is swollen and I can barely open my mouth." **Expected:** the agent answers normally and does
not transfer; about four seconds later the call ends with a transfer attempt (a browser call cannot ring). In the `make api` terminal
you should see `emergency layer two forced a transfer`. Then run `make worker` and `make calls`: the call's outcome is
`transferred_emergency` with the flag `emergency_forced_by_server`. Do not use this assistant for anything else.
