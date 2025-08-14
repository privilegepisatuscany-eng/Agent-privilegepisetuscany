from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
import pandas as pd
import json
import redis

app = FastAPI()

# Redis setup
REDIS_URL = "redis://localhost:6379/0"
rdb = redis.from_url(REDIS_URL)

# Load JSON data
with open("knowledge_base.json") as f:
    kb = pd.DataFrame(json.load(f))

with open("strutture.json") as f:
    anagrafica = pd.DataFrame(json.load(f))

def get_session(phone: str):
    raw = rdb.get(phone)
    if raw:
        return json.loads(raw)
    return {"messages": []}

def save_session(phone: str, session_data):
    rdb.set(phone, json.dumps(session_data))

def query_kb(property_name: str, message: str) -> str:
    df = kb[kb["nome_struttura"].str.lower() == property_name.lower()]
    if df.empty:
        return "Non ho trovato informazioni su questa struttura."

    for _, row in df.iterrows():
        if "Testo FAQ" in row:
            testo = str(row["Testo FAQ"]).lower()
            if testo in message.lower():
                return str(row.get("risposta", ""))

    return "Mi dispiace, non ho trovato una risposta precisa alla tua domanda."

@app.get("/", response_class=HTMLResponse)
async def whatsapp_style():
    return """
    <html><head><title>Chat WhatsApp</title>
    <style>
    body { font-family: sans-serif; background: #e5ddd5; padding: 20px; }
    .chat { max-width: 500px; margin: auto; background: #fff; border-radius: 10px; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
    .msg { margin: 5px 0; padding: 8px 12px; border-radius: 10px; max-width: 70%; }
    .user { background: #dcf8c6; align-self: flex-end; text-align: right; }
    .bot { background: #f1f0f0; align-self: flex-start; text-align: left; }
    .bubble { display: flex; flex-direction: column; }
    </style></head><body>
    <div class='chat'>
      <h3>Simula una chat WhatsApp</h3>
      <form method='post' action='/test'>
        <div><label>Telefono:</label><input name='phone' /></div>
        <div><label>Messaggio:</label><input name='message' /></div>
        <button>Invia</button>
      </form>
    </div></body></html>
    """

@app.post("/test", response_class=HTMLResponse)
async def test_form(phone: str = Form(...), message: str = Form(...)):
    session = get_session(phone)
    response = query_kb("nome_struttura_example", message)
    session["messages"].append((message, response))
    save_session(phone, session)

    bubbles = "".join([
        f"<div class='bubble user'><div class='msg user'>{m[0]}</div></div>" +
        f"<div class='bubble bot'><div class='msg bot'>{m[1]}</div></div>"
        for m in session["messages"]
    ])

    return HTMLResponse(content=f"""
        <html><body><div class='chat'>
        {bubbles}
        <form method='post' action='/test'>
        <input type='hidden' name='phone' value='{phone}' />
        <div><label>Messaggio:</label><input name='message' /></div>
        <button>Invia</button>
        </form>
        <a href='/'>↩️ Nuova sessione</a></div></body></html>
    """)
