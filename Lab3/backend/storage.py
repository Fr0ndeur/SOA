import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

DB = os.getenv('DB_PATH', '/data/service.db')

@contextmanager
def transaction():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA busy_timeout=15000')
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init():
    os.makedirs(os.path.dirname(DB) or '.', exist_ok=True)
    with transaction() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            duration_seconds INTEGER NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox (
            id TEXT PRIMARY KEY, destination TEXT NOT NULL,
            body TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS inbox (id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS projection (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            duration_seconds INTEGER NOT NULL, created_at REAL NOT NULL);
        ''')

def enqueue(db, destination, body):
    db.execute('INSERT INTO outbox(id,destination,body) VALUES(?,?,?)',
               (str(uuid.uuid4()), destination, json.dumps(body)))

def create_session(user_id, duration_seconds, fail, reliable, trace_id):
    event = dict(id=str(uuid.uuid4()), user_id=user_id,
                 duration_seconds=duration_seconds, fail=fail,
                 type='session.created', created_at=time.time(), trace_id=trace_id)
    with transaction() as db:
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',
                   (event['id'], user_id, duration_seconds, 'pending'))
        if reliable:
            enqueue(db, 'sessions', event)
    return event

def project(event, reliable=True):
    """Inbox + projection + response outbox share one local transaction."""
    with transaction() as db:
        if db.execute('SELECT 1 FROM inbox WHERE id=?', (event['id'],)).fetchone():
            return 'duplicate'
        # Fail before inserting the projection, just as a database write failure.
        try:
            db.execute('SAVEPOINT projection_write')
            if event.get('fail'):
                raise sqlite3.IntegrityError('injected reporting write failure')
            db.execute('INSERT INTO projection VALUES(?,?,?,?)',
                       (event['id'], event['user_id'], event['duration_seconds'],
                        event['created_at']))
            db.execute('RELEASE projection_write')
            destination = 'results'
        except sqlite3.IntegrityError:
            db.execute('ROLLBACK TO projection_write')
            db.execute('RELEASE projection_write')
            destination = 'session.dlq'
        db.execute('INSERT INTO inbox VALUES(?)', (event['id'],))
        if reliable:
            enqueue(db, destination, dict(event, result=destination))
    return destination

def finish(event, failed):
    with transaction() as db:
        # This conditional update is the idempotent compensation.
        db.execute('UPDATE sessions SET status=? WHERE id=? AND status=?',
                   ('compensated' if failed else 'completed', event['id'], 'pending'))

def rows(sql, params=()):
    with transaction() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]
