import os
import json
import re
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

load_dotenv()

HF_TOKEN = os.getenv("HF_API_KEY")
MODEL = os.getenv("MODEL", "deepseek-ai/DeepSeek-R1")
PROVIDER = os.getenv("PROVIDER", "novita")

app = FastAPI(title="Jarvis AI")


def fix_spacing(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    text = text.replace("(space)", " ")
    text = re.sub(r'\(space\)', ' ', text)
    text = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', text)
    text = re.sub(r' +', ' ', text)
    return text.strip()


SYSTEM_PROMPT = """You are Jarvis, a fast and concise AI assistant.

Strict rules:
- NEVER describe what you are doing (no "I will", "Let me", "We are given")
- NEVER say "the user" - just answer directly
- Get straight to the answer with no meta-commentary
- Use "I" when referring to yourself

Formatting rules - use Markdown:
- **Bold** key terms and important words
- Use bullet lists (- item) for multiple points
- Use numbered lists (1. item) for sequential steps
- Use `code` for inline code and ```language for code blocks
- Leave blank lines between sections
- Keep answers clean and scannable
- Use ### headings for major sections in long answers"""


class ChatRequest(BaseModel):
    message: str
    history: list = []


@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.post("/chat")
def chat(req: ChatRequest):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
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
        "provider": PROVIDER,
    }

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }

    def stream():
        try:
            with requests.post(
                "https://router.huggingface.co/v1/chat/completions",
                headers=headers,
                json={**payload, "stream": True},
                timeout=180,
                stream=True,
            ) as r:
                if r.status_code != 200:
                    error_msg = r.text
                    yield f"data: {json.dumps({'token': '', 'done': True, 'error': error_msg})}\n\n"
                    return

                for line in r.iter_lines():
                    if not line:
                        continue
                    line = line.decode("utf-8", errors="ignore")
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
                        return
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            content = fix_spacing(content)
                            yield f"data: {json.dumps({'token': content, 'done': False})}\n\n"
                    except json.JSONDecodeError:
                        continue

                yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'token': '', 'done': True, 'error': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
