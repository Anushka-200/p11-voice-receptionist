"""The LLM wrapper's guards, tested with a fake provider (no network, no cost)."""

from types import SimpleNamespace

import pytest

from app.llm import LLMError, complete
from app.settings import Settings


def fake_response(text="hello", finish="stop", out_tokens=5, tool_calls=None):
    msg = SimpleNamespace(content=text, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=out_tokens, prompt_tokens_details=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)], usage=usage)


def run(model, response, settings=None, **kw):
    seen = {}

    def fake_completion(**kwargs):
        seen.update(kwargs)
        return response

    result = complete(
        [{"role": "user", "content": "hi"}],
        model=model,
        settings=settings or Settings(),
        completion_fn=fake_completion,
        cost_fn=lambda **_: 0.001,
        **kw,
    )
    return result, seen


def test_claude_models_get_thinking_disabled_by_default_and_other_models_do_not():
    _, seen = run("claude-haiku-5-5", fake_response())
    assert seen["thinking"] == {"type": "disabled"}
    _, seen = run("gpt-4.1", fake_response())
    assert "thinking" not in seen  # the parameter would be rejected by another provider


def test_thinking_can_be_turned_on_by_setting():
    _, seen = run("claude-haiku-5-5", fake_response(), settings=Settings(llm_thinking=True))
    assert "thinking" not in seen


def test_a_cut_off_answer_with_no_text_fails_loudly_instead_of_returning_an_empty_string():
    with pytest.raises(LLMError, match="produced no text"):
        run("claude-haiku-5-5", fake_response(text="", finish="length", out_tokens=200), max_tokens=200)


def test_a_cut_off_answer_that_still_has_text_is_flagged_but_returned():
    result, _ = run("claude-haiku-5-5", fake_response(text="partial answer", finish="length"))
    assert result.truncated is True and result.text == "partial answer"


def test_a_normal_answer_is_not_truncated():
    result, _ = run("claude-haiku-5-5", fake_response())
    assert result.truncated is False and result.cost_usd == 0.001
