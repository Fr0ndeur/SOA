"""Public gateway: only explicit application routes can reach private services."""

import os
import re
from pathlib import Path

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError

ISSUER = os.getenv("JWT_ISSUER", "http://localhost:8180/realms/soa")
AUDIENCE = os.getenv("JWT_AUDIENCE", "soa-api")
JWKS_URL = os.getenv("JWKS_URL", f"{ISSUER}/protocol/openid-connect/certs")
jwks = PyJWKClient(JWKS_URL, cache_jwk_set=True, lifespan=300, timeout=5)
STATIC = Path(__file__).parent / "static"

# A scope authorizes access to a service resource, not arbitrary proxy URLs.
ROUTES = [
    ("GET", re.compile(r"users(?:/[0-9]+)?"), "profile:read", os.getenv("PROFILES_URL", "http://profiles:8000")),
    ("POST", re.compile(r"sessions"), "tracking:write", os.getenv("TRACKING_URL", "http://tracking:8000")),
    ("GET", re.compile(r"sessions/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "tracking:read", os.getenv("TRACKING_URL", "http://tracking:8000")),
    ("GET", re.compile(r"reports/[0-9]+"), "reports:read", os.getenv("REPORTING_URL", "http://reporting:8000")),
]


def authenticate(authorization: str | None, required_scope: str | None = None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:]
    try:
        key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iss", "aud", "sub", "iat"]},
        )
        if not isinstance(claims.get("scope", ""), str):
            raise jwt.InvalidTokenError("Invalid scope claim")
    except PyJWKClientConnectionError as exc:
        raise HTTPException(503, "Authorization server keys unavailable") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Invalid or expired access token", headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}) from exc
    if required_scope and required_scope not in claims.get("scope", "").split():
        raise HTTPException(
            403,
            f"Missing scope: {required_scope}",
            headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{required_scope}"'},
        )
    return claims


app = FastAPI(title="Lab 5 secured API gateway", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://cad.kpi.ua", "http://localhost:8080"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["WWW-Authenticate"],
)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/app.js")
def javascript():
    return FileResponse(STATIC / "app.js", media_type="text/javascript")


@app.get("/config")
def config():
    return {"issuer": ISSUER, "clientId": "soa-browser"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "secured-gateway"}


@app.get("/auth/me")
async def me(request: Request):
    claims = await run_in_threadpool(authenticate, request.headers.get("authorization"))
    return {name: claims.get(name) for name in ("sub", "preferred_username", "iss", "aud", "scope", "exp")}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request):
    route = next((row for row in ROUTES if row[0] == request.method and row[1].fullmatch(path)), None)
    if not route:
        raise HTTPException(404, "No public route")
    _, _, scope, upstream = route
    await run_in_threadpool(authenticate, request.headers.get("authorization"), scope)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.request(
                request.method,
                f"{upstream}/{path}",
                params=request.query_params,
                content=await request.body(),
                headers={name: request.headers[name] for name in ("content-type", "authorization", "origin") if name in request.headers},
            )
    except httpx.RequestError as exc:
        raise HTTPException(502, "Upstream service unavailable") from exc
    # httpx decompresses the body; retain framework CORS headers, remove transport headers.
    blocked = {"content-length", "content-encoding", "transfer-encoding", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade"}
    headers = {key: value for key, value in response.headers.items() if key.lower() not in blocked}
    return Response(response.content, status_code=response.status_code, headers=headers)
