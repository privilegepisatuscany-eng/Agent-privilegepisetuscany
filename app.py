# app.py
from fastapi import FastAPI, Request, Form
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import openai
import pandas as pd
import requests
import os
import json
from datetime import datetime

# Load Knowledge Base
kb = pd.read_excel("Knowledge base.xlsx", sheet_name="KB")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# Configuration
CIAOBOOKING_API_BASE = "https://api.ciaobooking.com/api/public"
CIAOBOOKING_EMAIL = os.getenv("CIAOBOOKING_EMAIL")
CIAOBOOKING_PASSWORD = os.getenv("CIAOBOOKING_PASSWORD")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# Temporary in-memory session store
SESSIONS = {}

# Prompt template
AGENT_PROMPT = """
Sei un assistente virtuale altamente qualificato che lavora per una struttura alberghiera di lusso. [...] Se non comprendi esattamente la richiesta, fai domande di chiarimento in modo gentile.
"""

# Models
class IncomingMessage(BaseModel):
    phone: str
    message: str

# Utils

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
    return [r for r in all_res if r["client_id"] == client_id and r["status"] == 2]  # CONFIRMED

def extract_property_context(reservations):
    if not reservations:
        return None
    latest = sorted(reservations, key=lambda x: x["start_date"], reverse=True)[0]
    return latest["property"]["name"]

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

# Web UI for testing
@app.get("/", response_class=HTMLResponse)
async def form():
    return """
    <html><body>
    <h2>Simula Messaggio Cliente</h2>
    <form action="/test" method="post">
        Telefono: <input type="text" name="phone"><br>
        Messaggio: <input type="text" name="message"><br>
        <input type="submit">
    </form>
    </body></html>
    """

@app.post("/test")
async def test_form(phone: str = Form(...), message: str = Form(...)):
    return await handle_message(IncomingMessage(phone=phone, message=message))

# Main handler
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

        # Session management
        session_key = msg.phone
        session = SESSIONS.get(session_key, {"messages": []})
        session["last_seen"] = datetime.now().isoformat()

        # Try Knowledge Base
        response = query_kb(property_name, msg.message)
        if response:
            session["messages"].append((msg.message, response))
            SESSIONS[session_key] = session
            return JSONResponse({"reply": response})

        # GPT fallback
        enriched_prompt = f"Domanda del cliente: '{msg.message}'\nStruttura: {property_name}."
        fallback = ask_gpt(enriched_prompt)
        session["messages"].append((msg.message, fallback))
        SESSIONS[session_key] = session
        return JSONResponse({"reply": fallback})

    except Exception as e:
        return JSONResponse({"reply": "Si è verificato un errore nel sistema. Stiamo verificando. Nel frattempo, Niccolò la contatterà."})
