import asyncio
import json
import logging
import os
import socket
import threading
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import pika
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import storage

ROLE = os.getenv('ROLE', 'gateway')
RELIABLE = os.getenv('RELIABLE', '1') == '1'
SHARDS = int(os.getenv('SHARDS', '1'))
SHARD = int(os.getenv('SHARD', '0'))
REPLICA = os.getenv('REPLICA', '0') == '1'
QUEUE = os.getenv('QUEUE', 'reports')
INSTANCE = socket.gethostname()
STOP = threading.Event()
DELAY = 0.0
HTTP_DELAY = 0.0
COUNT = 0
logging.basicConfig(level=logging.INFO, format='%(message)s')
LATENCY = Histogram('http_duration_seconds', 'HTTP duration', ['role','method','route'],
                    buckets=(.005,.01,.025,.05,.1,.25,.5,1,2,5,10))
REQUESTS = Counter('http_requests', 'HTTP requests', ['role','method','route','status'])

def log(message, **data):
    logging.info(json.dumps(dict(message=message, role=ROLE, instance=INSTANCE, **data)))

def channel():
    c = pika.BlockingConnection(pika.URLParameters(os.getenv(
        'AMQP_URL', 'amqp://student:student@rabbitmq:5672/%2F')))
    ch = c.channel()
    ch.exchange_declare(exchange='sessions', exchange_type='fanout', durable=True)
    for name in ('results', 'session.dlq', QUEUE):
        ch.queue_declare(queue=name, durable=True)
    for queue in os.getenv('REPORT_QUEUES', QUEUE).split(','):
        ch.queue_declare(queue=queue, durable=True)
        ch.queue_bind(queue=queue, exchange='sessions')
    ch.confirm_delivery()
    return c, ch

def publish(ch, destination, body):
    ch.basic_publish(exchange='sessions' if destination == 'sessions' else '',
                     routing_key='' if destination == 'sessions' else destination,
                     body=json.dumps(body), mandatory=True,
                     properties=pika.BasicProperties(delivery_mode=2,
                         message_id=body['id'], content_type='application/json'))

def relay():
    while not STOP.is_set():
        try:
            c, ch = channel()
            try:
                for row in storage.rows('SELECT * FROM outbox WHERE sent=0 LIMIT 100'):
                    publish(ch, row['destination'], json.loads(row['body']))
                    with storage.transaction() as db:
                        db.execute('UPDATE outbox SET sent=1 WHERE id=?', (row['id'],))
                    log('outbox_sent', event_id=json.loads(row['body'])['id'])
            finally:
                c.close()
        except Exception as exc:
            log('relay_retry', error=str(exc))
        STOP.wait(1)

def consume():
    while not STOP.is_set():
        try:
            c, ch = channel()
            ch.basic_qos(prefetch_count=1)
            queues = [QUEUE] if ROLE == 'reporting' else ['results', 'session.dlq']
            try:
                while not STOP.is_set():
                    got = False
                    for q in queues:
                        method, _, body = ch.basic_get(queue=q, auto_ack=False)
                        if method is None:
                            continue
                        got = True
                        event = json.loads(body)
                        if ROLE == 'reporting':
                            if event['user_id'] % SHARDS == SHARD:
                                if DELAY:
                                    time.sleep(DELAY)
                                result = storage.project(event, RELIABLE and not REPLICA)
                                if not RELIABLE and not REPLICA and result != 'duplicate':
                                    publish(ch, result, dict(event, result=result))
                                log('projection', result=result, event_id=event['id'],
                                    trace_id=event.get('trace_id'))
                        else:
                            storage.finish(event, q == 'session.dlq')
                            log('saga_compensated' if q == 'session.dlq' else 'saga_completed',
                                event_id=event['id'], trace_id=event.get('trace_id'))
                        ch.basic_ack(method.delivery_tag)
                    c.process_data_events(time_limit=.1)
                    if not got:
                        STOP.wait(.2)
            finally:
                c.close()
        except Exception as exc:
            log('consumer_retry', error=str(exc))
            STOP.wait(1)

@asynccontextmanager
async def lifespan(app):
    STOP.clear()
    storage.init()
    if ROLE == 'profiles':
        with storage.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, email TEXT UNIQUE, daily_limit INTEGER)')
            db.executemany('INSERT OR IGNORE INTO users VALUES(?,?,?)', [(5,'student5@example.com',28800),(6,'student6@example.com',28800)])
    threads = []
    if ROLE in ('tracking', 'reporting'):
        if RELIABLE:
            threads.append(threading.Thread(target=relay, daemon=True))
        threads.append(threading.Thread(target=consume, daemon=True))
    for t in threads:
        t.start()
    yield
    STOP.set()
    for t in threads:
        t.join(timeout=2)

app = FastAPI(title=f'Time tracker {ROLE}', lifespan=lifespan)
app.add_middleware(CORSMiddleware,
    allow_origins=['https://cad.kpi.ua', 'http://localhost:8080'],
    allow_methods=['GET', 'POST', 'OPTIONS'], allow_headers=['Authorization','Content-Type'])

@app.middleware('http')
async def observe(request: Request, call_next):
    parent = request.headers.get('traceparent','').split('-')
    trace = parent[1] if len(parent) == 4 else request.headers.get('x-trace-id', uuid.uuid4().hex)
    if len(trace) != 32 or any(c not in '0123456789abcdef' for c in trace):
        trace = uuid.uuid4().hex
    request.state.trace_id = trace
    request.state.span_id = uuid.uuid4().hex[:16]
    start = time.monotonic()
    response = await call_next(request)
    route = getattr(request.scope.get('route'), 'path', 'unmatched')
    if request.url.path != '/metrics':
        LATENCY.labels(ROLE,request.method,route).observe(time.monotonic()-start)
        REQUESTS.labels(ROLE,request.method,route,str(response.status_code)).inc()
        log('http', path=request.url.path, status=response.status_code,
            trace_id=trace, traceparent=f'00-{trace}-{request.state.span_id}-01',
            parent_span_id=parent[2] if len(parent)==4 else None)
    response.headers['x-trace-id'] = trace
    return response

@app.get('/health')
def health():
    return dict(status='ok', role=ROLE, instance=INSTANCE)

@app.get('/metrics')
def metrics():
    return Response(generate_latest(), headers={'Content-Type': CONTENT_TYPE_LATEST})

USERS = {5: dict(id=5,email='student5@example.com',daily_limit=28800),
         6: dict(id=6,email='student6@example.com',daily_limit=28800)}

async def forward(request, service, path):
    global COUNT
    target = service
    # Docker DNS returns the current replica IPs; rotate across fresh answers.
    if service == 'tracking' and ROLE == 'gateway':
        addresses = sorted({x[4][0] for x in socket.getaddrinfo(service,8000,type=socket.SOCK_STREAM)})
        target = addresses[COUNT % len(addresses)]
        COUNT += 1
    headers = {'x-trace-id': request.state.trace_id,
               'traceparent': f'00-{request.state.trace_id}-{request.state.span_id}-01'}
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            r = await client.request(request.method, f'http://{target}:8000{path}',
                content=await request.body(), params=request.query_params,
                headers=dict(headers, **{'content-type': request.headers.get('content-type','application/json')}))
        except httpx.RequestError as exc:
            raise HTTPException(503, f'Upstream unavailable: {service}') from exc
    return Response(r.content, status_code=r.status_code,
                    media_type=r.headers.get('content-type','application/json'))

class SessionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    user_id: int = Field(gt=0)
    duration_seconds: int = Field(gt=0, le=28800)
    fail: bool = False

if ROLE == 'profiles':
    @app.get('/users')
    def find_user(email: str):
        return storage.rows('SELECT * FROM users WHERE email=?', (email,))

    @app.get('/users/{user_id}')
    def profile(user_id: int):
        rows = storage.rows('SELECT * FROM users WHERE id=?', (user_id,))
        if not rows:
            raise HTTPException(404, 'User not found')
        return rows[0]

if ROLE == 'tracking':
    @app.post('/sessions', status_code=202)
    async def create(data: SessionInput, request: Request):
        async with httpx.AsyncClient(timeout=3) as client:
            try:
                r = await client.get(f'http://profiles:8000/users/{data.user_id}',
                                     headers={'x-trace-id':request.state.trace_id, 'traceparent':f'00-{request.state.trace_id}-{request.state.span_id}-01'})
            except httpx.RequestError as exc:
                raise HTTPException(503, 'Profiles unavailable') from exc
        if r.status_code != 200:
            raise HTTPException(r.status_code, 'User lookup failed')
        if data.duration_seconds > r.json()['daily_limit']:
            raise HTTPException(422, 'Session exceeds daily limit')
        event = storage.create_session(data.user_id, data.duration_seconds, data.fail,
                                       RELIABLE, request.state.trace_id)
        if not RELIABLE:
            try:
                c, ch = channel()
                try:
                    publish(ch, 'sessions', event)
                finally:
                    c.close()
            except Exception as exc:
                # Lab2 deliberately exposes the dual-write gap; Lab3 fixes it.
                raise HTTPException(503, dict(message='Publish failed after DB commit',
                                             session_id=event['id'])) from exc
        return dict(id=event['id'],user_id=data.user_id,
                    duration_seconds=data.duration_seconds,status='pending',instance=INSTANCE)

    @app.get('/sessions/{session_id}')
    def session(session_id: str):
        result = storage.rows('SELECT * FROM sessions WHERE id=?', (session_id,))
        if not result:
            raise HTTPException(404,'Session not found')
        return dict(result[0],instance=INSTANCE)

    @app.get('/debug/state')
    def state():
        return dict(sessions=storage.rows('SELECT * FROM sessions'),
                    outbox=storage.rows('SELECT * FROM outbox'))

if ROLE == 'reporting':
    @app.get('/reports/{user_id}')
    def report(user_id: int):
        if HTTP_DELAY:
            time.sleep(HTTP_DELAY)
        result = storage.rows('SELECT coalesce(sum(duration_seconds),0) AS total_seconds FROM projection WHERE user_id=?',(user_id,))
        return dict(user_id=user_id,total_seconds=result[0]['total_seconds'],instance=INSTANCE,shard=SHARD)

    @app.get('/debug/state')
    def state():
        return dict(projection=storage.rows('SELECT * FROM projection'),
                    inbox=storage.rows('SELECT * FROM inbox'),
                    outbox=storage.rows('SELECT * FROM outbox'))

    @app.post('/admin/delay/{seconds}')
    def delay(seconds: float):
        global DELAY
        if not 0 <= seconds <= 60:
            raise HTTPException(422, 'Delay must be 0..60 seconds')
        DELAY = seconds
        return dict(delay=DELAY)

    @app.post('/admin/http-delay/{seconds}')
    def http_delay(seconds: float):
        global HTTP_DELAY
        if not 0 <= seconds <= 10:
            raise HTTPException(422, 'HTTP delay must be 0..10 seconds')
        HTTP_DELAY = seconds
        return dict(delay=HTTP_DELAY)

if ROLE == 'gateway':
    @app.api_route('/{path:path}', methods=['GET','POST'])
    async def gateway(path: str, request: Request):
        first = path.split('/')[0]
        service = {'users':'profiles','sessions':'tracking','debug':'tracking','reports':'reporting'}.get(first)
        if not service:
            raise HTTPException(404,'Route not found')
        if first == 'reports' and SHARDS > 1:
            try:
                service = f'reporting{int(path.split("/")[1]) % SHARDS}'
            except (ValueError,IndexError) as exc:
                raise HTTPException(422,'Numeric user ID required') from exc
        return await forward(request,service,'/'+path)
