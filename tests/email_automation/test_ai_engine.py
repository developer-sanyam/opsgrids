"""Tests for AIReplyEngine."""

from tools.email_automation.ai_engine import AIReplyEngine


def test_ai_reply_engine_fallback_when_no_keys() -> None:
    engine = AIReplyEngine(openai_key="", gemini_key="")
    assert not engine.has_ai_configured()

    email_reply = engine.generate_reply(
        incoming_message="Can you give me a walkthrough of OpsGrids?",
        sender_name="Dr. Mehta",
        subject="Product Demo",
        channel="email",
    )
    assert "Dr. Mehta" in email_reply
    assert "OpsGrids" in email_reply

    wa_reply = engine.generate_reply(
        incoming_message="Hi, is CRM included?",
        sender_name="Sunil",
        channel="whatsapp",
    )
    assert "Sunil" in wa_reply
    assert "OpsGrids" in wa_reply


def test_ai_reply_engine_provider_selection() -> None:
    # Test OpenAI preference
    openai_engine = AIReplyEngine(openai_key="sk-test-123", gemini_key="", provider="openai")
    assert openai_engine._select_provider() == "openai"

    # Test Gemini preference
    gemini_engine = AIReplyEngine(openai_key="", gemini_key="gem-test-456", provider="gemini")
    assert gemini_engine._select_provider() == "gemini"

    # Test auto selection
    auto_engine = AIReplyEngine(
        openai_key="sk-test-123", gemini_key="gem-test-456", provider="auto"
    )
    assert auto_engine._select_provider() in ("gemini", "openai")
