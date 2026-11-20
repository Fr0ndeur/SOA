"""Small A2A 1.0 JSON-RPC binding pinned to the 1.0.1 specification.

Deterministic producer/consumer agents: protocol interoperability needs no LLM key.
The backend's actual RabbitMQ consumer remains responsible for the session saga.
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from time_tools import EMAIL, GuardrailError, TimeTools, integer, session_identifier

VERSION = "1.0"


class RPCError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def agent_card(role, origin):
    skill = "produce-session" if role == "producer" else "observe-session"
    return {"name": f"Time tracker {role}", "description": f"Lab4 deterministic {role} agent",
            "version": "1.0.0", "supportedInterfaces": [{"url": origin + "/rpc",
            "protocolBinding": "JSONRPC", "protocolVersion": VERSION}],
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["application/json"], "defaultOutputModes": ["application/json"],
            "skills": [{"id": skill, "name": skill,
            "description": ("Create a REST session through the RabbitMQ producer and delegate observation"
                            if role == "producer" else "Observe the result of the RabbitMQ consumer via REST"),
            "tags": ["time-tracker", "rabbitmq"],
            "examples": [json.dumps({"email": EMAIL, "duration_seconds": 600} if role == "producer"
                                     else {"session_id": "00000000-0000-4000-8000-000000000001"})]}]}


def rpc(url, method, params):
    response = httpx.post(url, headers={"A2A-Version": VERSION},
        json={"jsonrpc": "2.0", "id": str(uuid4()), "method": method, "params": params},
        timeout=45, follow_redirects=False)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise RPCError(body["error"]["code"], body["error"]["message"])
    return body["result"]


def discover(origin):
    response = httpx.get(origin + "/.well-known/agent-card.json", timeout=10,
                         headers={"A2A-Version": VERSION}, follow_redirects=False)
    response.raise_for_status()
    card = response.json()
    interface = next((i for i in card["supportedInterfaces"]
                      if i["protocolVersion"] == VERSION and i["protocolBinding"] == "JSONRPC"), None)
    if interface is None or urlsplit(interface["url"]).netloc != urlsplit(origin).netloc:
        raise GuardrailError("Untrusted/unsupported Agent Card interface")
    return card, interface["url"]


def message(data):
    return {"message": {"messageId": str(uuid4()), "role": "ROLE_USER",
            "parts": [{"data": data, "mediaType": "application/json"}]}}


class Agent:
    def __init__(self, role, consumer_url=None, tools_factory=TimeTools, poll_seconds=20):
        self.role = role
        self.consumer_url = consumer_url or os.getenv("CONSUMER_URL", "http://localhost:8102")
        self.tools_factory, self.poll_seconds = tools_factory, poll_seconds
        self.tasks, self.messages = {}, {}

    def execute(self, data):
        api = self.tools_factory()
        try:
            if self.role == "consumer":
                if set(data) != {"session_id"}:
                    raise GuardrailError("Consumer requires only session_id")
                session_identifier(data["session_id"])
                deadline = time.monotonic() + self.poll_seconds
                while True:
                    session = api.get_session(data["session_id"])
                    if session["status"] in {"completed", "compensated", "failed"}:
                        return session, "TASK_STATE_COMPLETED" if session["status"] == "completed" else "TASK_STATE_FAILED"
                    if time.monotonic() >= deadline:
                        return {"session": session, "error": "Observation timed out; session may finish later"}, "TASK_STATE_FAILED"
                    time.sleep(0.25)
            if set(data) != {"email", "duration_seconds"}:
                raise GuardrailError("Producer requires email and duration_seconds")
            user = api.find_user(data["email"])
            report = api.get_report(user["id"])
            # Resolve peer before mutation so discovery errors cannot orphan new sessions.
            card, endpoint = discover(self.consumer_url)
            if "observe-session" not in {s["id"] for s in card["skills"]}:
                raise GuardrailError("Consumer does not declare the required skill")
            session = api.create_session(user["id"], data["duration_seconds"])
            try:
                child = rpc(endpoint, "SendMessage", message({"session_id": session["id"]}))["task"]
                return {"session": session, "report_before": report,
                        "consumer_agent": card["name"], "consumer_task": child}, child["status"]["state"]
            except (httpx.HTTPError, RPCError) as error:
                return {"session": session, "error": f"Observation failed: {type(error).__name__}; do not repeat creation"}, "TASK_STATE_FAILED"
        finally:
            api.close()

    def dispatch(self, method, params):
        if not isinstance(params, dict):
            raise RPCError(-32602, "Parameters must be an object")
        if method == "SendMessage":
            msg = params.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "ROLE_USER":
                raise RPCError(-32602, "A ROLE_USER message is required")
            mid = msg.get("messageId")
            if not isinstance(mid, str) or not 1 <= len(mid) <= 128:
                raise RPCError(-32602, "A bounded messageId is required")
            if msg.get("taskId"):
                if msg["taskId"] not in self.tasks:
                    raise RPCError(-32001, "Task not found")
                raise RPCError(-32004, "Terminal tasks cannot accept new messages")
            parts = msg.get("parts")
            if (not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict)
                    or not isinstance(parts[0].get("data"), dict)
                    or any(k in parts[0] for k in ("text", "url", "raw"))):
                raise RPCError(-32602, "Exactly one application/json data part is required")
            signature = json.dumps(msg, sort_keys=True)
            if mid in self.messages:
                old_signature, tid = self.messages[mid]
                if old_signature != signature:
                    raise RPCError(-32602, "messageId reused with different content")
                return {"task": self.tasks[tid]}
            if len(self.tasks) >= 100:
                raise RPCError(-32004, "Lab task budget exhausted; restart the agent")
            context_id = msg.get("contextId") or str(uuid4())
            if not isinstance(context_id, str) or len(context_id) > 128:
                raise RPCError(-32602, "Invalid contextId")
            data, state = self.execute(parts[0]["data"])
            tid = str(uuid4())
            task = {"id": tid, "contextId": context_id, "status": {"state": state},
                    "artifacts": [{"artifactId": str(uuid4()), "name": "session-result",
                    "parts": [{"data": data, "mediaType": "application/json"}]}]}
            self.tasks[tid] = task
            self.messages[mid] = (signature, tid)
            return {"task": task}
        if method in {"GetTask", "CancelTask"}:
            tid = params.get("id")
            if not isinstance(tid, str):
                raise RPCError(-32602, "Task id is required")
            if tid not in self.tasks:
                raise RPCError(-32001, "Task not found")
            if method == "CancelTask":
                raise RPCError(-32004, "Task is terminal and cannot be canceled")
            return self.tasks[tid]
        if method == "ListTasks":
            size = integer(params.get("pageSize", 50), "pageSize", maximum=100)
            try:
                offset = int(params.get("pageToken") or "0")
                if offset < 0:
                    raise ValueError()
            except (ValueError, TypeError):
                raise RPCError(-32602, "Invalid pageToken")
            rows = [task for task in reversed(list(self.tasks.values()))
                    if (not params.get("contextId") or task["contextId"] == params["contextId"])
                    and (not params.get("status") or task["status"]["state"] == params["status"])]
            return {"tasks": rows[offset:offset + size], "pageSize": size, "totalSize": len(rows),
                    "nextPageToken": str(offset + size) if offset + size < len(rows) else ""}
        if method in {"SendStreamingMessage", "SubscribeToTask"}:
            raise RPCError(-32004, "Streaming is not supported")
        if "PushNotification" in method:
            raise RPCError(-32003, "Push notifications are not supported")
        if method == "GetExtendedAgentCard":
            raise RPCError(-32007, "Extended Agent Card is not configured")
        raise RPCError(-32601, "Method not found")


def handler_for(agent):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, data):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("A2A-Version", VERSION)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                return self.respond(200, {"status": "ok", "role": agent.role})
            if self.path != "/.well-known/agent-card.json":
                return self.respond(404, {"error": "Not found"})
            host = self.headers.get("Host", "")
            if not re.fullmatch(r"[a-zA-Z0-9.-]+:\d+", host):
                return self.respond(400, {"error": "Invalid Host"})
            self.respond(200, agent_card(agent.role, "http://" + host))

        def do_POST(self):
            if self.path != "/rpc":
                return self.respond(404, {"error": "Not found"})
            request_id = None
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise RPCError(-32600, "Body must contain at most 8192 bytes")
                try:
                    body = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeError):
                    raise RPCError(-32700, "Invalid JSON payload")
                if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or "id" not in body:
                    raise RPCError(-32600, "A JSON-RPC 2.0 request with id is required")
                request_id = body["id"]
                if self.headers.get("A2A-Version") != VERSION:
                    raise RPCError(-32009, "Version not supported; use A2A-Version: 1.0")
                if not isinstance(body.get("method"), str):
                    raise RPCError(-32600, "method must be a string")
                result = agent.dispatch(body["method"], body.get("params", {}))
                self.respond(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
            except RPCError as error:
                self.respond(200, {"jsonrpc": "2.0", "id": request_id,
                                  "error": {"code": error.code, "message": error.message}})
            except (GuardrailError, ValueError, KeyError, TypeError) as error:
                self.respond(200, {"jsonrpc": "2.0", "id": request_id,
                                  "error": {"code": -32602, "message": str(error)}})
            except httpx.HTTPError:
                self.respond(200, {"jsonrpc": "2.0", "id": request_id,
                                  "error": {"code": -32603, "message": "REST/peer unavailable; inspect service logs"}})

    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=["producer", "consumer"], required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    # Single-threaded execution keeps mutation limits deterministic for this local lab.
    HTTPServer(("0.0.0.0", args.port), handler_for(Agent(args.role))).serve_forever()
