-- P11 schema. Phase 0 creates the whole shape from PLAN.md so later phases only add behaviour.
-- All patients are fake. Phone numbers are stored in E.164 (+15125550123).

-- ---------- clinic data ----------
CREATE TABLE providers (
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    role        text NOT NULL CHECK (role IN ('dentist', 'hygienist')),
    on_call     boolean NOT NULL DEFAULT false
);

CREATE TABLE appointment_types (
    code          text PRIMARY KEY,
    name          text NOT NULL,
    duration_min  integer NOT NULL CHECK (duration_min > 0),
    provider_role text NOT NULL CHECK (provider_role IN ('dentist', 'hygienist')),
    spoken_name   text NOT NULL            -- how the agent says it aloud
);

CREATE TABLE patients (
    id          serial PRIMARY KEY,
    phone       text NOT NULL UNIQUE,
    name        text NOT NULL,
    crm_contact_id text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE bookings (
    id                serial PRIMARY KEY,
    patient_id        integer NOT NULL REFERENCES patients(id),
    provider_id       integer NOT NULL REFERENCES providers(id),
    type_code         text NOT NULL REFERENCES appointment_types(code),
    start_at          timestamptz NOT NULL,
    end_at            timestamptz NOT NULL,
    reason            text,
    status            text NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'cancelled')),
    calendar_event_id text,
    source_call_id    text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (end_at > start_at)
);
-- The database itself refuses a double booking: two active bookings for one provider cannot overlap.
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE bookings ADD CONSTRAINT no_double_booking
    EXCLUDE USING gist (provider_id WITH =, tstzrange(start_at, end_at) WITH &&) WHERE (status = 'booked');

CREATE TABLE calendar_events (          -- the fake calendar (also holds "busy" blocks that are not bookings)
    id          serial PRIMARY KEY,
    provider_id integer NOT NULL REFERENCES providers(id),
    start_at    timestamptz NOT NULL,
    end_at      timestamptz NOT NULL,
    title       text NOT NULL,
    booking_id  integer REFERENCES bookings(id)
);

CREATE TABLE faq (
    id        serial PRIMARY KEY,
    topic     text NOT NULL,
    keywords  text[] NOT NULL,
    answer    text NOT NULL            -- written to be spoken: short, no symbols
);

-- ---------- calls ----------
CREATE TABLE calls (
    id             serial PRIMARY KEY,
    vapi_call_id   text NOT NULL UNIQUE,   -- replayed webhooks upsert on this key
    raw_report     jsonb,                  -- saved FIRST, before any processing
    transcript     text,
    recording_url  text,
    ended_reason   text,
    duration_s     numeric,
    cost_breakdown jsonb,                  -- telephony / stt / llm / tts / platform
    summary        text,
    outcome        text CHECK (outcome IN ('booked', 'rescheduled', 'cancelled', 'faq',
                                           'transferred_human', 'transferred_emergency', 'abandoned')),
    sentiment      text CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    eval_scores    jsonb,                  -- online-eval checks from the post-call analysis
    analysis_status text NOT NULL DEFAULT 'pending'
                   CHECK (analysis_status IN ('pending', 'done', 'needs_review')),
    flags          text[] NOT NULL DEFAULT '{}',   -- e.g. outcome_mismatch
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE call_turns (
    id           serial PRIMARY KEY,
    call_id      integer NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    turn_no      integer NOT NULL,
    latency_ms   integer,
    stage_ms     jsonb                     -- endpointing / stt / llm / tts, when Vapi reports them
);

CREATE TABLE tool_invocations (
    id          serial PRIMARY KEY,
    vapi_call_id text NOT NULL,
    tool        text NOT NULL,
    args_redacted jsonb,
    latency_ms  integer,
    ok          boolean NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON tool_invocations (vapi_call_id);

-- ---------- control ----------
CREATE TABLE call_sessions (            -- what we offered on this call, so a booking can only use an offered slot
    vapi_call_id  text PRIMARY KEY,
    caller_phone  text,
    offered_slots jsonb NOT NULL DEFAULT '[]',
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE idempotency_keys (
    key         text PRIMARY KEY,
    tool        text NOT NULL,
    result_json jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbound_messages (        -- the SMS outbox; with DRY_RUN nothing is ever delivered
    id          serial PRIMARY KEY,
    patient_id  integer REFERENCES patients(id),
    body        text NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    dry_run     boolean NOT NULL DEFAULT true,
    status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'failed')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE crm_outbox (               -- a CRM outage never loses a call record; the worker retries these
    id          serial PRIMARY KEY,
    call_id     integer NOT NULL REFERENCES calls(id),
    note        text NOT NULL,
    status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
    attempts    integer NOT NULL DEFAULT 0,
    last_error  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
