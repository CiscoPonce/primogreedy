#!/usr/bin/env python
"""Push the Senior Broker prompt to LangSmith Hub via REST API.

Usage:
    PYTHONPATH=. python scripts/push_prompt_to_hub.py
"""

import json
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("LANGCHAIN_API_KEY")
TENANT_ID = os.getenv("LANGSMITH_WORKSPACE_ID", "cf298ed8-839f-4fb8-8fe7-1f14c64bfa15")
BASE_URL = "https://api.smith.langchain.com"

if not API_KEY:
    print("ERROR: LANGCHAIN_API_KEY not set.")
    sys.exit(1)

from src.prompts.senior_broker import SENIOR_BROKER_TEMPLATE

HEADERS = {
    "x-api-key": API_KEY,
    "X-Tenant-Id": TENANT_ID,
    "Content-Type": "application/json",
}

# Step 1: Create the repo (prompt) if it doesn't exist
repo_name = "senior-broker"
print(f"Creating prompt repo: {repo_name}...")

create_resp = requests.post(
    f"{BASE_URL}/repos/",
    headers=HEADERS,
    json={
        "repo_handle": repo_name,
        "description": "PrimoGreedy Senior Broker analyst prompt — Graham/Lynch/Munger framework",
        "is_public": False,
        "is_archived": False,
    },
    timeout=30,
)

if create_resp.status_code == 200:
    print(f"  ✅ Repo created: {repo_name}")
elif create_resp.status_code == 409:
    print(f"  ⏩ Repo already exists: {repo_name}")
else:
    print(f"  ℹ️  Repo response ({create_resp.status_code}): {create_resp.text[:200]}")

# Step 2: Push a commit (the prompt manifest) to the repo
print("Pushing prompt content...")

# Build the prompt manifest in LangChain serialization format
manifest = {
    "lc": 1,
    "type": "constructor",
    "id": ["langchain", "prompts", "chat", "ChatPromptTemplate"],
    "kwargs": {
        "input_variables": [
            "company_name", "ticker", "price", "eps", "book_value",
            "ebitda", "thesis", "strategy", "deep_fundamentals", "sec_context"
        ],
        "messages": [
            {
                "lc": 1,
                "type": "constructor",
                "id": ["langchain", "prompts", "chat", "HumanMessagePromptTemplate"],
                "kwargs": {
                    "prompt": {
                        "lc": 1,
                        "type": "constructor",
                        "id": ["langchain", "prompts", "prompt", "PromptTemplate"],
                        "kwargs": {
                            "input_variables": [
                                "company_name", "ticker", "price", "eps", "book_value",
                                "ebitda", "thesis", "strategy", "deep_fundamentals", "sec_context"
                            ],
                            "template": SENIOR_BROKER_TEMPLATE,
                            "template_format": "f-string",
                        },
                    }
                },
            }
        ],
    },
}

commit_resp = requests.post(
    f"{BASE_URL}/commits/-/{repo_name}",
    headers=HEADERS,
    json={"manifest": manifest},
    timeout=30,
)

if commit_resp.status_code in (200, 201):
    print(f"  ✅ Prompt pushed successfully!")
    print(f"  🔗 View at: https://smith.langchain.com/hub/{repo_name}")
else:
    print(f"  ❌ Push failed ({commit_resp.status_code}): {commit_resp.text[:300]}")
    sys.exit(1)

print("\nDone! The prompt is now live in LangSmith Hub.")
