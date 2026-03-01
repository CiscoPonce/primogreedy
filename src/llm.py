#model="z-ai/glm-4.5-air:free",
#"google/gemini-2.0-flash-lite-preview-02-05:free"
#model="upstage/solar-pro-3:free", 

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# Load .env locally (This line does nothing on the cloud if .env is missing, which is fine)
load_dotenv()

def get_llm():
    """
    The Brain.
    Connects to OpenRouter.
    """
    # 1. Try to get the key
    api_key = os.getenv("OPENROUTER_API_KEY")
    
    # 2. Debugging Block (This prints to Hugging Face Logs)
    if not api_key:
        print("⚠️ CRITICAL ERROR: API Key is missing!")
        print("---------------------------------------------------")
        print("I looked for: 'OPENROUTER_API_KEY'")
        print("But I only found these keys in the environment:")
        # Print only the NAMES of the keys (safe), not the values
        for key in os.environ.keys():
            if "API" in key or "KEY" in key:
                print(f" - {key}")
        print("---------------------------------------------------")
        raise ValueError("❌ OPENROUTER_API_KEY not found. Please check your Hugging Face Secrets.")
    else:
        # If it works, print a masked version to confirm
        print(f"✅ API Key loaded successfully! (Starts with: {api_key[:8]}...)")

    # 3. Connect to the LLM
    llm = ChatOpenAI(
        model="meta-llama/llama-3.1-8b-instruct:free", 
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0
    )
    
    return llm

