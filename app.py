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

with open("knowledge_base.json") as f:
    kb = pd.DataFrame(json.load(f))

with open("strutture.json") as f:
    anagrafica = pd.DataFrame(json.load(f))

print("[ANAGRAFICA COLONNE]:", list(anagrafica.columns))

app = FastAPI()

CIAOBOOKING_API_BASE = "https://api.ciaobooking.com/api/public"
CIAOBOOKING_EMAIL = os.getenv("CIAOBOOKING_EMAIL")
CIAOBOOKING_PASSWORD = os.getenv("CIAOBOOKING_PASSWORD")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

openai.api_key = OPENAI_API_KEY
import urllib.parse

import redis
from redis.connection import SSLConnection

if "upstash.io" in REDIS_URL:
    rdb = redis.Redis.from_url(REDIS_URL, decode_responses=True, connection_class=SSLConnection)
else:
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
    col_name = next((c for c in anagrafica.columns if "appartamento" in c.lower() and "stanza" in c.lower()), None)
    if not col_name:
        return {}
    match = anagrafica[anagrafica[col_name] == property_name]
    return match.iloc[0].to_dict() if not match.empty else {}

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
    <html>
    <head>
        <title>Chat WhatsApp</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background-color: #e5ddd5;
                margin: 0;
                padding: 0;
            }
            .container {
                display: flex;
                flex-direction: column;
                max-width: 600px;
                margin: 20px auto;
                height: 90vh;
                border: 1px solid #ccc;
                border-radius: 10px;
                overflow: hidden;
                background: #fff;
            }
            .header {
                background-color: #075e54;
                color: white;
                padding: 16px;
                font-weight: bold;
            }
            .chat {
                flex: 1;
                padding: 16px;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
                gap: 10px;
                background-color: #ece5dd;
            }
            .bubble {
                padding: 10px 15px;
                border-radius: 10px;
                max-width: 75%;
                line-height: 1.4;
                word-wrap: break-word;
            }
            .user {
                align-self: flex-end;
                background-color: #dcf8c6;
                text-align: right;
            }
            .bot {
                align-self: flex-start;
                background-color: #ffffff;
                text-align: left;
            }
            .form {
                display: flex;
                gap: 10px;
                padding: 10px;
                background: #f0f0f0;
                border-top: 1px solid #ccc;
            }
            input[type=text] {
                flex: 1;
                padding: 10px;
                font-size: 16px;
                border-radius: 5px;
                border: 1px solid #ccc;
            }
            button {
                padding: 10px 16px;
                background: #075e54;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 16px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">💬 Concierge AI (Simulazione WhatsApp)</div>
            <div class="chat">
                <div class="bubble bot">Ciao! Inserisci il numero di telefono e inizia la conversazione.</div>
            </div>
            <form class="form" method="post" action="/test">
                <input type="text" name="phone" placeholder="Telefono" required />
                <input type="text" name="message" placeholder="Messaggio..." required />
                <button type="submit">➤</button>
            </form>
        </div>
    </body>
    </html>
    """

@app.post("/test")
async def test_form(phone: str = Form(...), message: str = Form(...)):
    response = await handle_message(IncomingMessage(phone=phone, message=message))
    reply_raw = response.body.decode() if hasattr(response, 'body') else response

    try:
        reply_data = json.loads(reply_raw) if isinstance(reply_raw, str) else reply_raw
        bot_reply = reply_data["reply"] if isinstance(reply_data, dict) else str(reply_data)
    except Exception as e:
        print("⚠️ Errore parsing risposta:", e)
        bot_reply = str(reply_raw)

    session = get_session(phone)
    session["messages"].append((message, bot_reply))
    save_session(phone, session)

    bubbles = "".join([
        f"<div class='bubble user'><div class='msg user'>{m[0]}</div></div>" +
        f"<div class='bubble bot'><div class='msg bot'>{m[1]}</div></div>"
        for m in session["messages"]
    ])
    return HTMLResponse(content=f"""
        <html>
        <head>
            <style>
                body {{ font-family: sans-serif; padding: 2em; background-color: #f9f9f9; }}
                .chat {{ max-width: 600px; margin: auto; }}
                .bubble {{ margin-bottom: 1em; }}
                .msg {{ padding: 1em; border-radius: 10px; display: inline-block; max-width: 90%; }}
                .user .msg {{ background-color: #dcf8c6; }}
                .bot .msg {{ background-color: #eee; }}
            </style>
        </head>
        <body>
            <div class='chat'>
                {bubbles}
                <form method='post' action='/test'>
                    <input type='hidden' name='phone' value='{phone}' />
                    <div><label>Messaggio:</label><input name='message' required /></div>
                    <button>Invia</button>
                </form>
                <a href='/'>↩️ Nuova sessione</a>
            </div>
        </body>
        </html>
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
