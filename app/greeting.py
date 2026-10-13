"""The first thing every call hears: AI disclosure + recording notice, then an open question.

One definition, used by the assistant config (Vapi's first message) and by the evals, so the disclosure the
grader checks is the disclosure the callers hear (EU AI Act Art. 50; recording consent in all-party states).
"""

GREETING = (
    "Thank you for calling Bright Smile Dental. I'm an AI assistant, and this call may be recorded. "
    "How can I help you today?"
)
