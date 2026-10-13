-- Found by the live eval: the model read the whole FAQ answer aloud (46 words in one turn). Voice answers
-- must be short: one or two sentences, so the agent can say all of it.
UPDATE faq SET answer = 'We are open Monday to Thursday, eight to five, and Friday, eight to one.' WHERE topic = 'hours';
UPDATE faq SET answer = 'Yes, we accept most major plans, including Delta Dental, Cigna and Aetna.' WHERE topic = 'insurance';
UPDATE faq SET answer = 'Please arrive ten minutes early with a photo ID and your insurance card.' WHERE topic = 'new patients';
UPDATE faq SET answer = 'A cleaning is one hundred twenty dollars without insurance.' WHERE topic = 'cleaning price';
UPDATE faq SET answer = 'We see children from age three and up.' WHERE topic = 'kids';
UPDATE faq SET answer = 'We take all major credit cards and offer payment plans for larger treatments.' WHERE topic = 'payment';
UPDATE faq SET answer = 'Please give us twenty-four hours notice. Late changes may have a fee.' WHERE topic = 'cancellation';
