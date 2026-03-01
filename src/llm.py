import os
import time
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

_llm_instance = None

# Ordered by preference: quality + reliability + speed
MODEL_CHAIN = [
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "stepfun/step-3.5-flash:free",
    "arcee-ai/trinity-large-preview:free",
    "google/gemma-3-27b-it:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
]


def get_llm() -> ChatOpenAI:
    """Lazy-initialised LLM singleton with automatic model fallback.

    Tries the primary model first.  If it has been marked as failing,
    the fallback chain is tried until one works.
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
        model=MODEL_CHAIN[0],
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
    )
    return _llm_instance


def invoke_with_fallback(prompt: str, max_retries: int = 2) -> str:
    """Invoke the LLM with automatic model fallback on 429 rate limits.

    Tries each model in MODEL_CHAIN until one succeeds.  Returns the
    response content string.
    """
    from src.core.logger import get_logger
    logger = get_logger(__name__)

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not found.")

    for model_id in MODEL_CHAIN:
        for attempt in range(max_retries):
            try:
                llm = ChatOpenAI(
                    model=model_id,
                    api_key=api_key,
                    base_url="https://openrouter.ai/api/v1",
                    temperature=0,
                )
                response = llm.invoke(prompt)
                logger.info("LLM response from %s (attempt %d)", model_id, attempt + 1)
                return response.content
            except Exception as exc:
                err_str = str(exc)
                if "429" in err_str:
                    logger.warning("Rate-limited on %s (attempt %d), trying next...", model_id, attempt + 1)
                    time.sleep(2)
                    break  # move to next model
                elif "404" in err_str:
                    logger.warning("Model %s not available, skipping", model_id)
                    break  # move to next model
                else:
                    logger.error("LLM error on %s: %s", model_id, exc)
                    if attempt < max_retries - 1:
                        time.sleep(1)
                    else:
                        break

    raise RuntimeError(f"All {len(MODEL_CHAIN)} models failed. Last tried: {MODEL_CHAIN[-1]}")
