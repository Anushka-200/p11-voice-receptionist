# Demo video script (3 minutes)

Record real calls, not slides. Have `make api`, `make tunnel` and `make dashboard` running and `make check-assistant` showing IDENTICAL.
Use a fake name and number. Do the recording after the 15 scripted calls so the numbers on screen are final.

| Time | What you show | What you say |
|---|---|---|
| 0:00 to 0:15 | The clinic problem | "A dental clinic loses bookings when nobody answers. This voice agent answers every call." |
| 0:15 to 0:45 | `docs/architecture.mmd` rendered | "Vapi does the voice. The rules, tools, safety and analytics are my code: the agent can only book slots it offered, only change your own appointment, and dates like 'next Friday' are resolved by code." |
| 0:45 to 1:35 | **Live call 1: booking.** Say "next Friday morning" | The agent asks which Friday, offers two times, reads the phone back, books it. Point at the read-back and the clarifying question |
| 1:35 to 2:00 | **Live call 2: emergency.** "My face is swollen and I can barely open my mouth" | Immediate transfer announcement and the nine-one-one line. Say plainly: a browser call can't ring a phone, so the transfer is verified by the tool call |
| 2:00 to 2:15 | The dashboard row for that call | The summary, the outcome, the flags (`transfer_failed` is expected here) |
| 2:15 to 2:45 | `make bench` and the experiments table | "Median turn latency, cost per minute by stage, task success. Here is what each experiment changed and what I kept." Show one failure honestly (the dropped "AI") |
| 2:45 to 3:00 | Close | "Code and runbook on GitHub. Inbound only, AI disclosure on every call." |

Failure case to show on screen: stop the tunnel mid-call, or ask for medical advice, and show the safe answer.
