-- Phase 4: what the post-call pipeline needs beyond the first schema.
ALTER TABLE calls ADD COLUMN started_at timestamptz;
ALTER TABLE calls ADD COLUMN assistant_name text;
ALTER TABLE calls ADD COLUMN analysis_error text;
ALTER TABLE crm_outbox ADD COLUMN patient_id integer REFERENCES patients(id);
