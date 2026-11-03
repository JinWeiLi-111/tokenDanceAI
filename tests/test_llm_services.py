import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from tokendanceai.models.model import UserLLMConfig
from tokendanceai.services.llm_service import build_chat_model
from tokendanceai.utils.crypto import encrypt


def _make_config(provider: str) -> UserLLMConfig:
    """construct a llm config without database"""
    return UserLLMConfig(
        user_id=1,
        provider=provider,
        base_url="https://api.example/com",
        model="test-model",
        encrypted_api_key=encrypt("sk_test_key"),
    )


def test_build_openai_model():
    chat_model = build_chat_model(_make_config("openai"))

    assert isinstance(chat_model, ChatOpenAI)
    assert chat_model.model == "test-model"


def test_build_anthropic_model():
    chat_model = build_chat_model(_make_config("anthropic"))

    assert isinstance(chat_model, ChatAnthropic)
    assert chat_model.model == "test-model"


def test_unknown_provider_model():
    with pytest.raises(ValueError, match="unknown provider"):
        build_chat_model(_make_config("deepseek"))
