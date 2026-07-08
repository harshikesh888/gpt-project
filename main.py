import os
import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()

HF_TOKEN = os.getenv("HF_API_KEY")
MODEL = os.getenv("MODEL", "deepseek-ai/DeepSeek-R1")
API_URL = "https://router.huggingface.co/v1/chat/completions"

app = FastAPI(title="Jarvis AI")

class ChatRequest(BaseModel):
    message: str
    history: list = []

@app.get("/", response_class=HTMLResponse)
def home():
    return """<html>
<head><title>Jarvis AI</title>
<style>body{font-family:Arial;max-width:800px;margin:auto;padding:20px;}textarea{width:100%;height:120px;}button{padding:12px 24px;margin:10px 0;}</style>
</head>
<body>
<h2>🤖 Jarvis AI</h2>
<textarea id="msg" placeholder="Type your message..."></textarea><br>
<button onclick="send()">Send</button>
<div id="resp" style="margin-top:20px;white-space:pre-wrap;"></div>
<script>
async function send(){ 
  let msg = document.getElementById("msg").value;
  let res = await fetch("/chat", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({message:msg})});
  let data = await res.json();
  document.getElementById("resp").innerText = data.response || data.error;
}
</script>
</body></html>"""
    
@app.post("/chat")
def chat(req: ChatRequest):
    messages = [{"role": "system", "content": "You are a helpful AI assistant."}]
    messages.append({"role":"user","content":req.message})
    
    payload = {"model": MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 512}
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}
    
    r = requests.post(API_URL, headers=headers, json=payload, timeout=120)
    if r.status_code != 200:
        return {"error": r.text}
    return {"response": r.json()["choices"][0]["message"]["content"]}
