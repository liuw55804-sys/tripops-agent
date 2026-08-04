from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from tripops.config import Settings


def build_chat_model(settings: Settings) -> BaseChatModel:
    """Build the configured OpenAI-compatible chat model without making a network call."""
    if not settings.model_api_key:
        raise ValueError("TRIPOPS_MODEL_API_KEY is required when agent_mode=llm")
    return ChatOpenAI(
        model=settings.model_name,
        api_key=SecretStr(settings.model_api_key),
        base_url=settings.model_base_url,
        temperature=0,
        timeout=settings.run_timeout_seconds,
        max_retries=1,
    )
