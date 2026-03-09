import os
import time
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableConfig

load_dotenv()

_llm_instance = None

# Ordered by preference: quality + reliability + speed
# NOTE: Nemotron is a *reasoning* model — it burns tokens on internal
# chain-of-thought before producing output. Keep it as fallback only;
# non-reasoning models should come first for structured-output tasks.
MODEL_CHAIN = [
    "stepfun/step-3.5-flash:free",
    "z-ai/glm-4.5-air:free",
    "arcee-ai/trinity-large-preview:free",
    "arcee-ai/trinity-mini:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
]

# Best model for structured (JSON) output — must NOT be a reasoning model
# which wastes completion tokens on internal chain-of-thought.
STRUCTURED_MODEL = "meta-llama/llama-3.3-70b-instruct:free"


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


def get_structured_llm(max_tokens: int = 8192) -> ChatOpenAI:
    """Return an LLM instance configured for structured output.

    A moderate ``max_tokens`` gives enough headroom for the model to
    produce a full InvestmentVerdict while preventing free-tier models
    from burning all capacity on reasoning tokens and hitting the
    ceiling before completing the JSON.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not found.")

    return ChatOpenAI(
        model=STRUCTURED_MODEL,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
        max_tokens=max_tokens,
        request_timeout=120,
    )


def invoke_with_fallback(prompt: str, max_retries: int = 2, run_name: str = "llm_call") -> str:
    """Invoke the LLM with automatic model fallback on 429 rate limits.

    Tries each model in MODEL_CHAIN until one succeeds.  Returns the
    response content string.

    Each invocation is tagged with the model name so LangSmith can filter
    by ``model:<name>`` and ``error:429`` for the error dashboard.
    """
    from src.core.logger import get_logger
    logger = get_logger(__name__)

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not found.")

    last_error = None

    for model_id in MODEL_CHAIN:
        for attempt in range(max_retries):
            try:
                llm = ChatOpenAI(
                    model=model_id,
                    api_key=api_key,
                    base_url="https://openrouter.ai/api/v1",
                    temperature=0,
                )

                # LangSmith: tag every call with model name + attempt number
                config = RunnableConfig(
                    run_name=run_name,
                    tags=[f"model:{model_id}", f"attempt:{attempt + 1}"],
                    metadata={
                        "model_id": model_id,
                        "attempt": attempt + 1,
                        "fallback_position": MODEL_CHAIN.index(model_id),
                    },
                )

                response = llm.invoke(prompt, config=config)
                logger.info("LLM response from %s (attempt %d)", model_id, attempt + 1)
                return response.content
            except Exception as exc:
                last_error = exc
                err_str = str(exc)
                if "429" in err_str:
                    logger.warning("Rate-limited on %s (attempt %d), trying next...", model_id, attempt + 1)
                    time.sleep(8)  # respect free-tier 8 req/min
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

    raise RuntimeError(f"All {len(MODEL_CHAIN)} models failed. Last tried: {MODEL_CHAIN[-1]}. Last error: {last_error}")
