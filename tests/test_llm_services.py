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


def test_api_key_is_decrypted_before_use(monkeypatch):
    import tokendanceai.services.llm_service as svc

    capture = {}

    def _fake_decrypt(ciphertext: str) -> str:
        capture["ciphertext"] = ciphertext
        return "sk-decrypted"

    monkeypatch.setattr(svc, "decrypt", _fake_decrypt)

    cfg = _make_config("openai")
    model = build_chat_model(cfg)

    assert capture["ciphertext"] == cfg.encrypted_api_key
    assert model.openai_api_key.get_secret_value() == "sk-decrypted"  # pyright: ignore[reportAttributeAccessIssue]
