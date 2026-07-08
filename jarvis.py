import os
import requests
from dotenv import load_dotenv

load_dotenv()

HF_TOKEN = os.getenv("HF_TOKEN")
MODEL = os.getenv("MODEL", "deepseek-ai/DeepSeek-R1")

API_URL = f"https://router.huggingface.co/v1/chat/completions"

headers = {
    "Authorization": f"Bearer {HF_TOKEN}",
    "Content-Type": "application/json"
}

messages = [
    {
        "role": "system",
        "content": "You are a helpful AI assistant."
    }
]

print("Type 'exit' to quit.\n")

while True:
    user_input = input("You: ")

    if user_input.lower() == "exit":
        break

    messages.append({
        "role": "user",
        "content": user_input
    })

    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.7
    }

    response = requests.post(
        API_URL,
        headers=headers,
        json=payload,
        timeout=120
    )

    if response.status_code != 200:
        print("\nError:")
        print(response.status_code)
        print(response.text)
        continue

    data = response.json()

    assistant_message = data["choices"][0]["message"]["content"]

    print(f"\nAssistant: {assistant_message}\n")

    messages.append({
        "role": "assistant",
        "content": assistant_message
    })
