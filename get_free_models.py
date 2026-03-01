import requests

def get_free_models():
    try:
        res = requests.get("https://openrouter.ai/api/v1/models")
        if res.status_code == 200:
            data = res.json()
            free_models = []
            for m in data.get("data", []):
                if "pricing" in m:
                    prompt_price = str(m["pricing"].get("prompt", "")).strip()
                    comp_price = str(m["pricing"].get("completion", "")).strip()
                    if prompt_price == "0" and comp_price == "0":
                        free_models.append(m["id"])
            
            print(f"Found {len(free_models)} free models. Here are the first 30:")
            for m in free_models[:30]:
                print(f" - {m}")
        else:
            print("Failed to fetch models:", res.status_code)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    get_free_models()
