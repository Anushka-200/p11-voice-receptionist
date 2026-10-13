-- Seed data for "Bright Smile Dental", a FAKE clinic. Every name, number and price here is invented.
-- Phase 2 grows the FAQ to about 25 rows; these are enough for Phase 0 and Phase 1.

INSERT INTO providers (name, role, on_call) VALUES
    ('Dr. Patel', 'dentist', true),
    ('Dr. Nguyen', 'dentist', false),
    ('Hannah', 'hygienist', false);

INSERT INTO appointment_types (code, name, duration_min, provider_role, spoken_name) VALUES
    ('cleaning',  'Teeth cleaning',        60, 'hygienist', 'a teeth cleaning'),
    ('checkup',   'Exam and checkup',      30, 'dentist',   'a checkup'),
    ('emergency', 'Emergency exam',        30, 'dentist',   'an emergency exam'),
    ('filling',   'Filling',               60, 'dentist',   'a filling'),
    ('whitening', 'Whitening consultation', 30, 'dentist',   'a whitening consultation');

INSERT INTO faq (topic, keywords, answer) VALUES
    ('hours', ARRAY['hours','open','close','opening'],
     'We are open Monday to Thursday from eight to five, and Friday from eight to one. We are closed on weekends.'),
    ('address', ARRAY['address','where','located','location','directions'],
     'We are at 120 Maple Street, Suite 4, in Austin. There is free parking behind the building.'),
    ('parking', ARRAY['parking','park'],
     'There is free parking in the lot behind the building.'),
    ('insurance', ARRAY['insurance','delta','cigna','aetna','covered','accept'],
     'We accept most major dental insurance plans, including Delta Dental, Cigna and Aetna. Please bring your card to your visit.'),
    ('new patients', ARRAY['new','first','patient','register'],
     'New patients are welcome. Please arrive ten minutes early, and bring a photo ID and your insurance card.'),
    ('cleaning price', ARRAY['cleaning','price','cost','much','self-pay'],
     'A routine cleaning is one hundred twenty dollars without insurance. Your insurance may cover most or all of it.'),
    ('checkup price', ARRAY['checkup','exam','price','cost','much'],
     'An exam and checkup is eighty-five dollars without insurance.'),
    ('whitening', ARRAY['whitening','whiten','bleach'],
     'We offer professional whitening. The first step is a short consultation with the dentist, and I can book one for you.'),
    ('kids', ARRAY['kids','children','child','pediatric'],
     'Yes, we see children from age three and up.'),
    ('payment', ARRAY['payment','pay','credit','card','financing'],
     'We accept all major credit cards, and we offer payment plans for larger treatments.'),
    ('cancellation', ARRAY['cancel','cancellation','late','fee','no-show'],
     'Please give us twenty-four hours notice if you need to cancel or change a visit. Late changes may have a fee.'),
    ('covid', ARRAY['mask','covid','safety'],
     'We follow standard infection control and sterilize all instruments between patients.'),
    ('emergency', ARRAY['emergency','after','hours','urgent'],
     'If you have swelling, a knocked-out tooth, or bleeding that will not stop, I can connect you to our on-call dentist right now.'),
    ('wisdom teeth', ARRAY['wisdom','extraction','extract'],
     'The dentist can look at your wisdom teeth during an exam, and then discuss options with you. I can book that exam.');
