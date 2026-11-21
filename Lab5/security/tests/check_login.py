"""Exercise real Authorization Code + PKCE login and public API (not password grant).

Start Lab5 first, then: python security/tests/check_login.py
Requires httpx. Credentials are intentionally public, local laboratory fixtures.
"""

import base64
import hashlib
import secrets
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

ISSUER = "http://localhost:8180/realms/soa"
GATEWAY = "http://localhost:8080"
SCOPES = "openid profile:read tracking:read tracking:write reports:read"


class LoginForm(HTMLParser):
    action = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "kc-form-login":
            self.action = attrs["action"]


def login(username, password, provider=None):
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    with httpx.Client(timeout=30, follow_redirects=False) as browser:
        params = {
            "client_id": "soa-browser", "redirect_uri": GATEWAY + "/", "response_type": "code",
            "scope": SCOPES, "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        }
        if provider:
            params["kc_idp_hint"] = provider
        response = browser.get(ISSUER + "/protocol/openid-connect/auth", params=params)
        def follow_auth_redirects(response):
            for _ in range(12):
                # Browsers treat localhost as trustworthy for Secure cookies; http.cookiejar does not.
                # This adjustment is limited to this fixed HTTP localhost laboratory client.
                for cookie in browser.cookies.jar:
                    if cookie.domain == "localhost.local":
                        cookie.secure = False
                if not response.is_redirect or response.headers["location"].startswith(GATEWAY + "/?"):
                    return response
                location = urljoin(str(response.url), response.headers["location"])
                assert location.startswith("http://localhost:8180/"), "Unexpected authorization server origin"
                response = browser.get(location)
            raise AssertionError("Too many identity-provider redirects")
        response = follow_auth_redirects(response)
        response.raise_for_status()
        form = LoginForm()
        form.feed(response.text)
        assert form.action, f"Login form not found for {username} (HTTP {response.status_code})"
        response = browser.post(form.action, data={"username": username, "password": password, "credentialId": ""})
        response = follow_auth_redirects(response)
        assert response.is_redirect, f"Login did not redirect for {username} (HTTP {response.status_code})"
        redirect = response.headers["location"]
        assert redirect.startswith(GATEWAY + "/?"), f"Unexpected callback: {urlparse(redirect).path}"
        query = parse_qs(urlparse(redirect).query)
        assert query.get("state") == [state], "OAuth state mismatch"
        assert "code" in query, "Authorization code missing"
        tokens = browser.post(ISSUER + "/protocol/openid-connect/token", data={
            "grant_type": "authorization_code", "client_id": "soa-browser", "redirect_uri": GATEWAY + "/",
            "code": query["code"][0], "code_verifier": verifier,
        })
        tokens.raise_for_status()
        # Authorization codes must be single-use.
        replay = browser.post(ISSUER + "/protocol/openid-connect/token", data={
            "grant_type": "authorization_code", "client_id": "soa-browser", "redirect_uri": GATEWAY + "/",
            "code": query["code"][0], "code_verifier": verifier,
        })
        assert replay.status_code == 400, f"Reused code unexpectedly accepted: {replay.status_code}"
        return tokens.json()["access_token"]


def main():
    with httpx.Client(base_url=GATEWAY, timeout=30) as api:
        assert api.get("/users/5").status_code == 401
        preflight = {"Origin": "https://cad.kpi.ua", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
        allowed = api.options("/sessions", headers=preflight)
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "https://cad.kpi.ua"
        denied = api.options("/sessions", headers={**preflight, "Origin": "https://attacker.example"})
        assert denied.status_code == 400 and "access-control-allow-origin" not in denied.headers
        print("PASS: unauthenticated 401; CORS permitted/denied origins")
        for username, password, provider in (("user5", "User5-demo!", None), ("reader5", "Reader5-demo!", None), ("upstream5", "Upstream5-demo!", "campus-demo")):
            token = login(username, password, provider)
            headers = {"Authorization": "Bearer " + token, "Origin": "https://cad.kpi.ua"}
            identity = api.get("/auth/me", headers=headers)
            identity.raise_for_status()
            scopes = identity.json()["scope"].split()
            assert ("tracking:write" in scopes) == (username == "user5")
            profile = api.get("/users/5", headers=headers)
            assert profile.status_code == 200, profile.text
            assert profile.headers["access-control-allow-origin"] == "https://cad.kpi.ua"
            write = api.post("/sessions", headers=headers, json={"user_id": 5, "duration_seconds": 120, "fail": False})
            expected = (202,) if username == "user5" else (403,)
            assert write.status_code in expected, f"{username}: {write.status_code} {write.text}"
            if username == "user5":
                session_id = write.json().get("id") or write.json().get("session_id")
                if session_id is not None:
                    session = api.get(f"/sessions/{session_id}", headers=headers)
                    assert session.status_code == 200, session.text
            report = api.get("/reports/5", headers=headers)
            assert report.status_code == 200, report.text
            print(f"PASS: {username}; real code+PKCE exchange and replay rejection; scopes={','.join(sorted(scopes))}; profile=200, write={write.status_code}, report=200")
    print("Live checks passed, including local OIDC identity brokering. Browser JavaScript UI remains a separate manual check.")


if __name__ == "__main__":
    main()
