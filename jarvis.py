import os
import requests
import gradio as gr

HF_TOKEN = os.getenv("HF_TOKEN")
MODEL = os.getenv("MODEL", "deepseek-ai/DeepSeek-R1")
API_URL = "https://router.huggingface.co/v1/chat/completions"

headers = {
    "Authorization": f"Bearer {HF_TOKEN}",
    "Content-Type": "application/json",
}

def chat(message, history):
    messages = [{"role": "system", "content": "You are a helpful AI assistant."}]

    for user, assistant in history:
        messages.append({"role": "user", "content": user})
        messages.append({"role": "assistant", "content": assistant})

    messages.append({"role": "user", "content": message})

    payload = {
        "model": MODEL,
        "messages": messages,
    }

    response = requests.post(API_URL, headers=headers, json=payload)

    if response.status_code != 200:
        return f"Error: {response.text}"

    return response.json()["choices"][0]["message"]["content"]

gr.ChatInterface(chat).launch()
