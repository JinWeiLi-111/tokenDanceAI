from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from tokendanceai.models.model import UserLLMConfig
from tokendanceai.utils.crypto import decrypt


def build_chat_model(llm_config: UserLLMConfig) -> BaseChatModel:
    api_key = decrypt(llm_config.encrypted_api_key)

    if llm_config.provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=llm_config.model,
            api_key=SecretStr(api_key),
            base_url=llm_config.base_url,
        )
    elif llm_config.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model_name=llm_config.model,
            api_key=SecretStr(api_key),
            timeout=None,
            stop=None,
        )
    else:
        raise ValueError(f"unknown provider: {llm_config.provider!r}")
