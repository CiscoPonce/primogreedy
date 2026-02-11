from src.llm import get_llm

print("\n--- 🧠 PRIMOGREEDY BRAIN TEST ---\n")

try:
    llm = get_llm()
    print("🤖 Asking Llama 3: 'What is the most dangerous risk for a bank?'...")
    
    response = llm.invoke("What is the single most dangerous risk for a bank? Answer in 1 sentence.")
    
    print(f"\n🗣️ ANSWER:\n{response.content}")
    print("\n✅ Brain is functioning.")

except Exception as e:
    print(f"\n❌ BRAIN DEAD: {e}")