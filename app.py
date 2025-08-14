
import redis
import os
import pandas as pd

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Fornisce SSL solo se Upstash
rdb = redis.Redis.from_url(REDIS_URL, decode_responses=True, ssl="upstash.io" in REDIS_URL)

try:
    rdb.ping()
    print("✅ Redis connesso con successo")
except Exception as e:
    print("❌ Errore di connessione Redis:", e)

anagrafica = pd.read_excel("Knowledge base.xlsx", sheet_name="Strutture")
COL_NAME = next(c for c in anagrafica.columns if "Appartamento" in c and "stanza" in c)
print("✔️ Colonna identificata:", COL_NAME)
