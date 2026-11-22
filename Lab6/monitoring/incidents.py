"""Local persistent incident tracker used as n8n's ticket destination."""
import json
import os
import sqlite3
import uuid
from fastapi import FastAPI
from pydantic import BaseModel, Field

os.makedirs('/data',exist_ok=True)
DB='/data/incidents.db'
with sqlite3.connect(DB) as db:
    db.execute('CREATE TABLE IF NOT EXISTS tickets (fingerprint TEXT PRIMARY KEY, id TEXT, status TEXT, payload TEXT)')
app=FastAPI()

class AlertBatch(BaseModel):
    alerts: list[dict] = Field(default_factory=list, max_length=100)

@app.post('/incidents')
def create(batch: AlertBatch):
    result=[]
    with sqlite3.connect(DB) as db:
        for alert in batch.alerts:
            labels=alert.get('labels',{})
            fingerprint=alert.get('fingerprint') or json.dumps(labels,sort_keys=True)
            db.execute('INSERT INTO tickets VALUES(?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET status=excluded.status,payload=excluded.payload',
                       (fingerprint,str(uuid.uuid4()),alert.get('status','firing'),json.dumps(alert)))
            result.append(fingerprint)
    return {'saved':len(result),'fingerprints':result}

@app.get('/incidents')
def tickets():
    with sqlite3.connect(DB) as db:
        return [dict(fingerprint=r[0],id=r[1],status=r[2],alert=json.loads(r[3])) for r in db.execute('SELECT * FROM tickets')]
