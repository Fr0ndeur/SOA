"""Observe actual primary/replica projection sets rather than fabricated lag."""
import asyncio
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

UP=Gauge('cluster_node_up','Node reachable',['node'])
LAG=Gauge('replication_lag_seconds','Age of oldest primary row absent from replica',['shard'])
DISTRIBUTION=Gauge('shard_sessions','Primary projection row count',['shard'])

async def collect():
    while True:
        async with httpx.AsyncClient(timeout=3) as client:
            for shard in (0,1):
                states={}
                for node in (f'reporting{shard}',f'replica{shard}'):
                    try:
                        r=await client.get(f'http://{node}:8000/debug/state')
                        r.raise_for_status()
                        states[node]=r.json()['projection']
                        UP.labels(node).set(1)
                    except (httpx.HTTPError,KeyError):
                        UP.labels(node).set(0)
                primary=states.get(f'reporting{shard}')
                replica=states.get(f'replica{shard}')
                if primary is not None:
                    DISTRIBUTION.labels(str(shard)).set(len(primary))
                if primary is not None and replica is not None:
                    ids={r['id'] for r in replica}
                    missing=[r['created_at'] for r in primary if r['id'] not in ids]
                    LAG.labels(str(shard)).set(max(0,time.time()-min(missing)) if missing else 0)
        await asyncio.sleep(2)

@asynccontextmanager
async def lifespan(app):
    task=asyncio.create_task(collect())
    yield
    task.cancel()

app=FastAPI(lifespan=lifespan)

@app.get('/metrics')
def metrics():
    return Response(generate_latest(),headers={'Content-Type':CONTENT_TYPE_LATEST})

@app.get('/health')
def health():
    return {'status':'ok'}
