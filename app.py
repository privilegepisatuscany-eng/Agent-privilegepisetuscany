# app.py
from fastapi import FastAPI, Request, Form
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
import openai
import pandas as pd
import requests
import os
import json
import redis
from datetime import datetime
import traceback

kb = pd.read_excel("Knowledge base.xlsx", sheet_name="Knowledge base")
anagrafica = pd.read_excel("Knowledge base.xlsx", sheet_name="Strutture")

app = FastAPI()

CIAOBOOKING_API_BASE = "https://api.ciaobooking.com/api/public"
CIAOBOOKING_EMAIL = os.getenv("CIAOBOOKING_EMAIL")
CIAOBOOKING_PASSWORD = os.getenv("CIAOBOOKING_PASSWORD")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

openai.api_key = OPENAI_API_KEY
rdb = redis.Redis.from_url(REDIS_URL, decode_responses=True)

AGENT_PROMPT = """
Sei un assistente virtuale altamente qualificato che lavora per una struttura alberghiera di lusso. [...] Se non comprendi esattamente la richiesta, fai domande di chiarimento in modo gentile.
"""

class IncomingMessage(BaseModel):
    phone: str
    message: str

def get_bearer_token():
    res = requests.post(
        f"{CIAOBOOKING_API_BASE}/login",
        data={"email": CIAOBOOKING_EMAIL, "password": CIAOBOOKING_PASSWORD, "locale": "it"}
    )
    res.raise_for_status()
    return res.json()["data"]["token"]

def get_client_by_phone(phone: str, token: str):
    res = requests.get(
        f"{CIAOBOOKING_API_BASE}/clients/paginated",
        headers={"Authorization": f"Bearer {token}"},
        params={"search": phone}
    )
    clients = res.json()["data"]["collection"]
    return clients[0] if clients else None

def get_reservations_by_client(client_id: int, token: str):
    res = requests.get(
        f"{CIAOBOOKING_API_BASE}/reservations",
        headers={"Authorization": f"Bearer {token}"},
        params={"from": "2023-01-01", "to": "2026-01-01"}
    )
    all_res = res.json()["data"]["collection"]
    return [r for r in all_res if r["client_id"] == client_id and r["status"] == 2]

def extract_property_context(reservations):
    if not reservations:
        return None
    latest = sorted(reservations, key=lambda x: x["start_date"], reverse=True)[0]
    return latest["property"]["name"]

def get_struttura_info(property_name: str):
    match = anagrafica[anagrafica["Appartamento /stanza"] == property_name]
    return match.iloc[0].to_dict() if not match.empty else {}

def query_kb(property_name: str, message: str):
    filtered_kb = kb[kb["Appartamento /stanza"].str.contains(property_name, na=False)]
    for _, row in filtered_kb.iterrows():
        if str(row["descrizione"]).lower() in message.lower():
            return row["risposta"]
    return None

def ask_gpt(prompt: str):
    completion = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": AGENT_PROMPT}, {"role": "user", "content": prompt}]
    )
    return completion.choices[0].message.content

def get_session(phone: str):
    raw = rdb.get(phone)
    return json.loads(raw) if raw else {"messages": []}

def save_session(phone: str, session: dict):
    session["last_seen"] = datetime.now().isoformat()
    rdb.set(phone, json.dumps(session))

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
    response = await handle_message(IncomingMessage(phone=phone, message=message))
    reply = response.body.decode() if hasattr(response, 'body') else response
    history = get_session(phone)["messages"]

    bubbles = "".join([
        f"<div class='bubble user'><div class='msg user'>{m[0]}</div></div>" +
        f"<div class='bubble bot'><div class='msg bot'>{m[1]}</div></div>"
        for m in history
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
        token = get_bearer_token()
        client = get_client_by_phone(msg.phone, token)

        if not client:
            return JSONResponse({"reply": "Gentile ospite, non trovo una prenotazione a suo nome. Un membro del nostro staff, Niccolò, la contatterà a breve."})

        reservations = get_reservations_by_client(client["id"], token)
        property_name = extract_property_context(reservations)

        if not property_name:
            return JSONResponse({"reply": "Gentile ospite, non riesco a trovare una prenotazione attiva. Niccolò la ricontatterà al più presto."})

        struttura = get_struttura_info(property_name)
        session = get_session(msg.phone)

        response = query_kb(property_name, msg.message)
        if response:
            session["messages"].append((msg.message, response))
            save_session(msg.phone, session)
            return JSONResponse({"reply": response})

        extra_info = ""
        if struttura:
            extra_info = f"\nNome struttura: {struttura.get('Struttura')}\nTipo: {struttura.get('Tipo struttura')}\nIndirizzo: {struttura.get('Indirizzo')}\nComune: {struttura.get('Comune')}"

        enriched_prompt = f"Domanda del cliente: '{msg.message}'\nStruttura: {property_name}.{extra_info}"
        fallback = ask_gpt(enriched_prompt)
        session["messages"].append((msg.message, fallback))
        save_session(msg.phone, session)
        return JSONResponse({"reply": fallback})

    except Exception as e:
        print("ERRORE:", e)
        traceback.print_exc()
        return JSONResponse({"reply": "Si è verificato un errore nel sistema. Stiamo verificando. Nel frattempo, Niccolò la contatterà."})

@app.get("/sessioni")
def list_sessions():
    keys = rdb.keys()
    output = {k: json.loads(rdb.get(k)) for k in keys}
    return output

@app.get("/flush")
def flush_sessions():
    rdb.flushdb()
    return {"status": "tutte le sessioni sono state cancellate."}

@app.get("/pingciao")
def ping_ciao():
    try:
        token = get_bearer_token()
        return {"token": token[:5] + "..."}
    except Exception as e:
        return {"error": str(e)}
