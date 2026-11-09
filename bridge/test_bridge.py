import sys
import os
import requests
import json
import time

def test_bridge():
    url = "http://127.0.0.1:28119"
    print(f"Testing bridge at {url}...")
    
    try:
        r = requests.get(f"{url}/health", timeout=3)
        print("Health response:", r.json())
        assert r.status_code == 200, "Health check failed"
    except Exception as e:
        print(f"Could not connect to health endpoint: {e}")
        return False

    try:
        r = requests.get(f"{url}/api/models", timeout=5)
        print("Models response:", r.json())
    except Exception as e:
        print(f"Models check failed: {e}")

    test_payload = {
        "text": "Deep residual networks have demonstrated extraordinary performance in computer vision tasks.",
        "model": "gemini-3.8-flash-high"
    }
    
    print("Testing streaming translation...")
    with requests.post(f"{url}/api/translate/stream", json=test_payload, stream=True, timeout=30) as resp:
        for line in resp.iter_lines():
            if line:
                decoded = line.decode('utf-8')
                if decoded.startswith("data: "):
                    payload = json.loads(decoded[6:])
                    if payload.get("type") == "chunk":
                        print(payload.get("text"), end="", flush=True)
                    elif payload.get("type") == "done":
                        print("\n\n[SUCCESS] Translation completed!")
                        break
                    elif payload.get("type") == "error":
                        print("\n[ERROR]", payload.get("message"))
                        return False
    return True

if __name__ == "__main__":
    test_bridge()

