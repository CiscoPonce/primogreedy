import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

def get_llm():
    """
    The Brain.
    Connects to OpenRouter to use Llama 3 or Claude.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not found in .env")

    # We use ChatOpenAI because OpenRouter is compatible with it
    llm = ChatOpenAI(
        model="z-ai/glm-4.5-air:free", # Powerful & Cheap
        openai_api_key=api_key,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0
    )
    
    return llm