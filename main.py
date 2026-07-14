import os
import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()

HF_TOKEN = os.getenv("HF_API_KEY")
# Use a supported provider for DeepSeek-R1
MODEL = os.getenv("MODEL", "deepseek-ai/DeepSeek-R1")
PROVIDER = os.getenv("PROVIDER", "novita")  # novita, fireworks-ai, deepinfra, etc.

app = FastAPI(title="Jarvis AI")

class ChatRequest(BaseModel):
    message: str
    history: list = []

@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/chat")
def chat(req: ChatRequest):
    messages = [{"role": "system", "content": """You are a helpful AI assistant. Always analyze what the user truly wants to know before responding.

Your responses MUST follow this section-based structure:

1. **Overview** — A brief summary answering the user's core question in 1-2 sentences.
2. **Key Points** — The main details organized as numbered or bulleted items. Keep each point clear and concise.
3. **Example** (when applicable) — A practical example or analogy to make the concept easier to understand.
4. **Summary** — A one-line takeaway to reinforce the answer.

Rules:
- Start every response by identifying the user's intent.
- Use markdown formatting with bold section headers.
- Keep language simple and direct.
"""}]
    
    messages.extend(req.history)
    messages.append({"role": "user", "content": req.message})

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1024,
        "provider": PROVIDER   # ← This fixes the "model not supported" error
    }
    
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json"
    }
    
    try:
        r = requests.post(
            "https://router.huggingface.co/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=180  # Increased for large reasoning model
        )
        
        if r.status_code != 200:
            return {"error": r.text}
        
        return {"response": r.json()["choices"][0]["message"]["content"]}
        
    except Exception as e:
        return {"error": str(e)}
