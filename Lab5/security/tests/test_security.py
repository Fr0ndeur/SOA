"""Offline security regression checks; no Keycloak or running services needed.

Run with the packages from Lab5/security/requirements.txt installed:
    python -m unittest discover -s security/tests -v
"""

import importlib.util
import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lab5_proxy", ROOT / "proxy.py")
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class SecurityChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public = cls.private.public_key()

    def setUp(self):
        self.keys = patch.object(gateway.jwks, "get_signing_key_from_jwt", return_value=SimpleNamespace(key=self.public))
        self.keys.start()
        self.addCleanup(self.keys.stop)
        self.client = TestClient(gateway.app)

    def token(self, scope="profile:read", overrides=None, remove=(), key=None):
        now = int(time.time())
        payload = {"sub": "user5", "iat": now, "exp": now + 120, "iss": gateway.ISSUER, "aud": gateway.AUDIENCE, "scope": scope}
        payload.update(overrides or {})
        for name in remove:
            payload.pop(name, None)
        return jwt.encode(payload, key or self.private, algorithm="RS256", headers={"kid": "offline-test-key"})

    def auth(self, **kwargs):
        return {"Authorization": "Bearer " + self.token(**kwargs)}

    def test_valid_signed_access_token(self):
        response = self.client.get("/auth/me", headers=self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scope"], "profile:read")

    def test_invalid_identity_claims_and_signature_are_rejected(self):
        cases = [
            {"overrides": {"exp": int(time.time()) - 10}},
            {"overrides": {"iss": "https://attacker.example/realms/soa"}},
            {"overrides": {"aud": "another-api"}},
            {"remove": ["exp"]},
            {"remove": ["sub"]},
            {"key": self.other_private},
            {"overrides": {"scope": ["profile:read"]}},
        ]
        for case in cases:
            with self.subTest(case=case):
                response = self.client.get("/auth/me", headers=self.auth(**case))
                self.assertEqual(response.status_code, 401)
                self.assertIn("invalid_token", response.headers["www-authenticate"])
        self.assertEqual(self.client.get("/users/5").status_code, 401)
        self.assertEqual(self.client.get("/auth/me", headers={"Authorization": "Bearer broken"}).status_code, 401)

    def test_each_service_requires_its_exact_scope(self):
        cases = [("GET", "/users/5", "profile:read"), ("POST", "/sessions", "tracking:write"), ("GET", "/sessions/12345678-abcd-4321-abcd-1234567890ab", "tracking:read"), ("GET", "/reports/5", "reports:read")]
        for method, path, scope in cases:
            with self.subTest(path=path):
                response = self.client.request(method, path, headers=self.auth(scope="unrelated " + scope + ":extra"))
                self.assertEqual(response.status_code, 403)
                self.assertIn(scope, response.headers["www-authenticate"])

    def test_only_allowed_routes_can_be_forwarded(self):
        for path in ("/users/%2e%2e/health", "/admin", "/users/5/private", "/reports", "/http://attacker.example"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=self.auth()).status_code, 404)

    def test_framework_cors_allowlist_preflight_and_errors(self):
        headers = {"Origin": "https://cad.kpi.ua", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
        response = self.client.options("/sessions", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "https://cad.kpi.ua")
        self.assertIn("Authorization", response.headers["access-control-allow-headers"])
        response = self.client.options("/sessions", headers={**headers, "Origin": "https://attacker.example"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)
        response = self.client.get("/users/5", headers={"Origin": "https://cad.kpi.ua"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["access-control-allow-origin"], "https://cad.kpi.ua")

    def test_gateway_preserves_upstream_cors_and_body(self):
        upstream_response = httpx.Response(200, json={"id": 5}, headers={"Access-Control-Allow-Origin": "https://cad.kpi.ua", "Vary": "Origin"})
        fake = SimpleNamespace()
        async def request(*args, **kwargs):
            self.assertEqual(args[:2], ("GET", "http://profiles:8000/users/5"))
            self.assertEqual(kwargs["headers"]["origin"], "https://cad.kpi.ua")
            return upstream_response
        class Upstream:
            async def __aenter__(self):
                return fake
            async def __aexit__(self, *args):
                return False
        fake.request = request
        with patch.object(gateway.httpx, "AsyncClient", return_value=Upstream()):
            response = self.client.get("/users/5", headers={**self.auth(), "Origin": "https://cad.kpi.ua"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"id": 5})
        self.assertEqual(response.headers["access-control-allow-origin"], "https://cad.kpi.ua")

    def test_reader_and_scope_role_mapping_config(self):
        realm = json.loads((ROOT / "realms/soa-realm.json").read_text(encoding="utf-8"))
        users = {user["username"]: user for user in realm["users"]}
        self.assertIn("tracking-writer", users["user5"]["realmRoles"])
        self.assertNotIn("tracking-writer", users["reader5"]["realmRoles"])
        mappings = {mapping["clientScope"]: mapping["roles"] for mapping in realm["scopeMappings"]}
        self.assertEqual(mappings["tracking:write"], ["tracking-writer"])
        client = next(client for client in realm["clients"] if client["clientId"] == "soa-browser")
        self.assertFalse(client["directAccessGrantsEnabled"])
        self.assertEqual(client["attributes"]["pkce.code.challenge.method"], "S256")
        self.assertIn("basic", client["defaultClientScopes"])
        basic = next(scope for scope in realm["clientScopes"] if scope["name"] == "basic")
        self.assertTrue(any(mapper["protocolMapper"] == "oidc-sub-mapper" for mapper in basic["protocolMappers"]))
        provider = realm["identityProviders"][0]
        self.assertEqual(provider["providerId"], "oidc")
        self.assertEqual(provider["config"]["validateSignature"], "true")


if __name__ == "__main__":
    unittest.main(verbosity=2)
