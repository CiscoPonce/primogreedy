import os
import time
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()
api_key = os.getenv("OPENROUTER_API_KEY")

models_to_test = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "qwen/qwen3-coder:free",
    "google/gemma-3-27b-it:free"
]

prompt = "Extract a stock ticker from this text: Apple is launching new iPhones. I am buying AAPL calls tomorrow. Reply only with the ticker."

print("🚀 Starting OpenRouter Free Model Benchmark...\n")

for m in models_to_test:
    print(f"--- Testing {m} ---")
    llm = ChatOpenAI(
        model=m, 
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0
    )
    t0 = time.time()
    try:
        res = llm.invoke(prompt)
        elapsed = time.time() - t0
        print(f"✅ Success! Response time: {elapsed:.2f}s")
        print(f"🤖 Output: {res.content.strip()}\n")
    except Exception as e:
        print(f"❌ Failed after {time.time() - t0:.2f}s. Error: {e}\n")
