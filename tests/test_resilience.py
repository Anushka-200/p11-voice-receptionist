# Adapted from P03 (p03-ai-operations-agent/tests/test_adapters_real.py: breaker and retry tests).
import pytest

from app.adapters.ports import AdapterUnavailable
from app.adapters.resilience import CircuitBreaker, retry


def _down():
    raise AdapterUnavailable("down")


def test_breaker_opens_after_3_failures_fails_fast_then_recovers_after_cooldown():
    now = {"t": 0.0}
    br = CircuitBreaker("x", clock=lambda: now["t"])
    for _ in range(3):
        with pytest.raises(AdapterUnavailable):
            br.call(_down)
    assert br.state == "open"
    calls = []
    with pytest.raises(AdapterUnavailable, match="temporarily disabled"):
        br.call(lambda: calls.append(1))
    assert calls == []  # failed fast: the protected call was never made
    now["t"] = 121
    assert br.state == "half_open"
    assert br.call(lambda: "ok") == "ok"  # the trial call succeeded
    assert br.state == "closed"


def test_a_failed_trial_call_reopens_the_breaker():
    now = {"t": 0.0}
    br = CircuitBreaker("x", threshold=1, clock=lambda: now["t"])
    with pytest.raises(AdapterUnavailable):
        br.call(_down)
    now["t"] = 200
    with pytest.raises(AdapterUnavailable):
        br.call(_down)
    assert br.state == "open"


def test_retry_waits_with_growing_jittered_delays_and_gives_up():
    waits: list[float] = []
    with pytest.raises(AdapterUnavailable):
        retry(_down, attempts=3, base_s=1, sleep=waits.append)
    assert len(waits) == 2 and 0.5 <= waits[0] <= 1.5 and 1.0 <= waits[1] <= 3.0
