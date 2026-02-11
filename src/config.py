import os
from dotenv import load_dotenv

# Load .env file if it exists (for local testing)
load_dotenv()

# Get keys from System Environment (for Cloud) OR .env (for Local)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")

# Check if keys are missing
if not OPENROUTER_API_KEY:
    raise ValueError("⚠️ Error: OPENROUTER_API_KEY not found. Please add it to your Hugging Face Secrets.")

if not BRAVE_API_KEY:
    print("⚠️ Warning: BRAVE_API_KEY not found. Search might not work.")