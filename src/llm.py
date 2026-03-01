import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

_llm_instance = None


def get_llm() -> ChatOpenAI:
    """Lazy-initialised LLM singleton.

    The connection is created on first call, not at import time, so
    modules can be imported without triggering API-key validation.
    """
    global _llm_instance
    if _llm_instance is not None:
        return _llm_instance

    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key:
        from src.core.logger import get_logger
        logger = get_logger(__name__)
        logger.error("OPENROUTER_API_KEY not found in environment")
        available = [k for k in os.environ if "API" in k or "KEY" in k]
        logger.error("Available key-like env vars: %s", available)
        raise ValueError("OPENROUTER_API_KEY not found. Check your secrets.")

    _llm_instance = ChatOpenAI(
        model="google/gemma-3-27b-it:free",
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
    )
    return _llm_instance
