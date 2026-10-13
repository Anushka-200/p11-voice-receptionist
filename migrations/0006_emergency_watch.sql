-- Layer two of the emergency rule: remember, per call, that we saw an emergency, whether the agent transferred, and
-- whether the server had to force the transfer (so it happens once, and the post-call record can say so).
ALTER TABLE call_sessions ADD COLUMN emergency_detected_at timestamptz;
ALTER TABLE call_sessions ADD COLUMN transfer_seen boolean NOT NULL DEFAULT false;
ALTER TABLE call_sessions ADD COLUMN emergency_forced boolean NOT NULL DEFAULT false;
