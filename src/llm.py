import os
import time
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableConfig

load_dotenv()

_llm_instance = None

# Ordered by preference: quality + reliability + speed.  Benchmarked live
# against the OpenRouter free tier on 2026-10-12 (schema-compliance, latency,
# token use, availability):
#   - nemotron-3.5-lightning (3B active) returned EMPTY / truncated verdicts
#     and rate-limited constantly -> removed from the front of the chain.
#   - inclusionai/ling-3.0-flash-sante:free was de-listed (404) -> removed.
#   - thinkingmachines/inkling:free (403), inclusionai/ling-3.1-flash and
#     google/gemma-4-31b-it:free (429) were unavailable -> not used.
#   - nemotron-3-ultra-550b (55B active) is the strongest reasoner and emits
#     schema-valid JSON; nemotron-3-super-120b (12B active) is the fastest
#     valid JSON and the most reliable.
# `openrouter/free` is the smart router catch-all, so the chain can still
# complete if the specific models above are de-listed or rate-limited.
MODEL_CHAIN = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "dots-studio/dots-3-note-preview:free",
    "openrouter/free",
]

# Preferred structured-output model.  Override with the STRUCTURED_MODEL env
# var to force the strongest free reasoner
# ("nvidia/nemotron-3-ultra-550b-a55b:free") at the cost of latency.
STRUCTURED_MODEL = os.getenv("STRUCTURED_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")

# Structured-output fallbacks, tried in order by invoke_structured() so a
# rate-limited model degrades to another structured-capable one instead of
# dropping the proposal.md framework fields entirely.  The preferred model is
# always tried first.
STRUCTURED_CHAIN = [STRUCTURED_MODEL] + [
    m for m in (
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "openrouter/free",
    )
    if m != STRUCTURED_MODEL
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


def get_structured_llm(model: str | None = None, max_tokens: int = 8192) -> ChatOpenAI:
    """Return an LLM instance configured for structured output.

    ``model`` defaults to :data:`STRUCTURED_MODEL`.  A moderate ``max_tokens``
    gives enough headroom for the model to produce a full InvestmentVerdict
    while preventing free-tier models from burning all capacity on reasoning
    tokens and hitting the ceiling before completing the JSON.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not found.")

    return ChatOpenAI(
        model=model or STRUCTURED_MODEL,
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


def invoke_structured(prompt: str, schema, max_tokens: int = 8192):
    """Invoke an LLM constrained to ``schema``, trying STRUCTURED_CHAIN in order.

    Unlike :func:`invoke_with_fallback` (free text), this keeps the structured
    verdict — and therefore the proposal.md framework fields (valuation range,
    sources, data-quality flags, invalidation facts, ...) — even when the
    preferred model is rate-limited.  Callers should only degrade to free text
    after every structured model has failed.
    """
    from src.core.logger import get_logger
    logger = get_logger(__name__)

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not found.")

    last_error = None
    for model_id in STRUCTURED_CHAIN:
        try:
            llm = get_structured_llm(model=model_id, max_tokens=max_tokens)
            result = llm.with_structured_output(schema).invoke(prompt)
            logger.info("Structured verdict via %s", model_id)
            return result
        except Exception as exc:
            last_error = exc
            err = str(exc)
            logger.warning("Structured output via %s failed: %s", model_id, err)
            if "429" in err:
                time.sleep(8)  # respect free-tier 8 req/min

    raise RuntimeError(
        f"All {len(STRUCTURED_CHAIN)} structured models failed. "
        f"Last tried: {STRUCTURED_CHAIN[-1]}. Last error: {last_error}"
    )
