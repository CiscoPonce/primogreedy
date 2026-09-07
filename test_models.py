import os
import time
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()
api_key = os.getenv("OPENROUTER_API_KEY")

models_to_test = [
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "dots-studio/dots-3-note-preview:free",
    "inclusionai/ling-3.0-flash-sante:free",
    "openrouter/free"
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
