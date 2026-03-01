import asyncio
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

load_dotenv()

async def main():
    api_key = os.getenv("OPENROUTER_API_KEY")
    llm = ChatOpenAI(
        model="openrouter/free", 
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0
    )
    
    # We ask the model what it is, and we also inspect the response headers/metadata if possible
    # Langchain's response often includes the actual model used by openrouter in the provider metadata
    response = await llm.ainvoke([HumanMessage(content="What exact AI model architecture are you running on right now?")])
    
    print("\n--- MODEL RESPONSE ---")
    print(response.content)
    print("\n--- METADATA ---")
    print(response.response_metadata)

if __name__ == "__main__":
    asyncio.run(main())
