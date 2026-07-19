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
    messages = [{"role": "system", "content": """You are Jarvis, a fast and concise AI assistant. 

Response rules:
- Be direct and brief (2-4 sentences max)
- Use bullet points for lists
- Skip examples unless explicitly asked
- No unnecessary introductions or conclusions
- Get straight to the point
"""}]
    
    messages.extend(req.history)
    messages.append({"role": "user", "content": req.message})

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 800,
        "top_p": 0.9,
        "frequency_penalty": 0.5,
        "presence_penalty": 0.3,
        "provider": PROVIDER
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
