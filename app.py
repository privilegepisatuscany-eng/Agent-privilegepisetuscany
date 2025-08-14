import os
import json
import traceback
import redis
import pandas as pd
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# Variabili ambiente
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
PORT = int(os.environ.get("PORT", 10000))

# Redis o fallback in memoria
try:
    rdb = redis.Redis.from_url(REDIS_URL, decode_responses=True, ssl="upstash.io" in REDIS_URL)
    rdb.ping()
    print("✅ Redis connesso con successo")
except Exception as e:
    print("⚠️ Redis non disponibile, uso fallback in memoria")
    rdb = None
    memory_db = {}

def get_session(phone):
    if rdb:
        raw = rdb.get(phone)
        return json.loads(raw) if raw else {"messages": []}
    return memory_db.get(phone, {"messages": []})

def save_session(phone, session):
    if rdb:
        rdb.set(phone, json.dumps(session))
    else:
        memory_db[phone] = session

# Modelli
class IncomingMessage(BaseModel):
    phone: str
    message: str

# Dati KB
with open("knowledge_base.json") as f:
    kb = pd.DataFrame(json.load(f))

with open("strutture.json") as f:
    anagrafica = pd.DataFrame(json.load(f))

print("[ANAGRAFICA COLONNE]:", list(anagrafica.columns))

# FastAPI
app = FastAPI()

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

@app.post("/test")
async def test_form(phone: str = Form(...), message: str = Form(...)):
    msg = IncomingMessage(phone=phone, message=message)
    reply = await handle_message(msg)
    session = get_session(phone)
    session["messages"].append((message, reply["reply"]))
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

@app.post("/webhook")
async def handle_message(msg: IncomingMessage):
    try:
        # Dummy property context
        property_name = "Privilege Pisa Tuscany"
        session = get_session(msg.phone)

        risposta = query_kb(property_name, msg.message)
        if risposta:
            session["messages"].append((msg.message, risposta))
            save_session(msg.phone, session)
            return JSONResponse({"reply": risposta})

        # Fallback
        fallback = f"Non ho trovato una risposta precisa. Ti risponderà Niccolò."
        session["messages"].append((msg.message, fallback))
        save_session(msg.phone, session)
        return JSONResponse({"reply": fallback})

    except Exception as e:
        print("ERRORE:", e)
        traceback.print_exc()
        return JSONResponse({"reply": "Errore interno. Niccolò la contatterà."})

def query_kb(property_name, message):
    df = kb[kb["nome_struttura"].str.lower() == property_name.lower()]
    for _, row in df.iterrows():
        testo = str(row.get("Testo FAQ", ""))
        if testo and testo.lower() in message.lower():
            return row.get("risposta", "")
    return None

@app.get("/ping")
def ping():
    return {"status": "ok"}
