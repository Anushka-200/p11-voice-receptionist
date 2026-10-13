-- A call may only reschedule or cancel bookings it has looked up by the patient's phone on this same call
-- (ownership by construction: the model cannot name an arbitrary booking id).
ALTER TABLE call_sessions ADD COLUMN found_booking_ids jsonb NOT NULL DEFAULT '[]';
