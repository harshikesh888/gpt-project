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

app = FastAPI()

class ChatRequest(BaseModel):
    message: str
    history: list = []

@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!DOCTYPE html>
<html>
<head>
<title>Jarvis AI</title>
<style>
body{font-family:Arial;max-width:800px;margin:auto;padding:20px;}
textarea{width:100%;height:80px;}
button{padding:10px 20px;margin-top:10px;}
#response{white-space:pre-wrap;margin-top:20px;}
</style>
</head>
<body>
<h2>Jarvis AI</h2>
<textarea id="message" placeholder="Ask something..."></textarea><br>
<button onclick="send()">Send</button>
<div id="response"></div>
<script>
async function send(){
    const msg=document.getElementById("message").value;
    const res=await fetch("/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:msg,history:[]})});
    const data=await res.json();
    document.getElementById("response").innerText=data.response||data.error||"Error";
}
</script>
</body>
</html>
"""

@app.post("/chat")
def chat(req: ChatRequest):
    messages = [{"role": "system", "content": "You are a helpful AI assistant."}]
    for user, assistant in req.history:
        messages.append({"role":"user","content":user})
        messages.append({"role":"assistant","content":assistant})
    messages.append({"role":"user","content":req.message})

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 512
    }
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json"
    }
    response = requests.post(API_URL, headers=headers, json=payload, timeout=120)
    
    if response.status_code != 200:
        return {"error": response.text}
    
    data = response.json()
    return {"response": data["choices"][0]["message"]["content"]}
