import pytest

from app.llm import client as llm_client
from app.llm.models import ChatResponse


async def test_valid_parse_returns_model_and_uses_skill_params(fake_anthropic):
    parsed = ChatResponse(message="ok")
    fake = fake_anthropic(parsed=parsed)
    out = await llm_client.call_llm("sys", [{"role": "user", "content": "hi"}])
    assert out is parsed
    kwargs = fake.calls[0]
    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["output_format"] is ChatResponse
    assert kwargs["system"] == "sys"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    for banned in ("temperature", "top_p", "top_k", "extra_body", "reasoning_effort"):
        assert banned not in kwargs


@pytest.mark.parametrize("reason", ["refusal", "max_tokens"])
async def test_bad_stop_reason_raises(fake_anthropic, reason):
    fake_anthropic(parsed=ChatResponse(message="partial"), stop_reason=reason)
    with pytest.raises(llm_client.LLMError):
        await llm_client.call_llm("sys", [{"role": "user", "content": "hi"}])


async def test_exception_raises_llm_error(fake_anthropic):
    fake_anthropic(exc=RuntimeError("boom"))
    with pytest.raises(llm_client.LLMError):
        await llm_client.call_llm("sys", [{"role": "user", "content": "hi"}])


async def test_none_parsed_output_raises(fake_anthropic):
    fake_anthropic(parsed=None)
    with pytest.raises(llm_client.LLMError):
        await llm_client.call_llm("sys", [{"role": "user", "content": "hi"}])


def test_mock_mode_flag(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    assert llm_client.is_mock_mode()
    monkeypatch.setenv("LLM_MOCK", "TRUE")
    assert llm_client.is_mock_mode()
    monkeypatch.setenv("LLM_MOCK", "false")
    assert not llm_client.is_mock_mode()
    monkeypatch.delenv("LLM_MOCK")
    assert not llm_client.is_mock_mode()


def test_get_client_is_async_anthropic(monkeypatch):
    from anthropic import AsyncAnthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    llm_client.set_client(None)
    try:
        assert isinstance(llm_client.get_client(), AsyncAnthropic)
    finally:
        llm_client.set_client(None)
