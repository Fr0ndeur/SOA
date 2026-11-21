"use strict";
let accessToken = null;
let configuration;
const $ = (id) => document.getElementById(id);
const show = (value) => { $("result").textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2); };
const base64url = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const random = () => base64url(crypto.getRandomValues(new Uint8Array(32)));
const redirectUri = location.origin + "/";

async function login(broker = false) {
  const verifier = random();
  const state = random();
  sessionStorage.setItem("soa-oauth", JSON.stringify({ verifier, state, created: Date.now() }));
  const challenge = base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  const scopes = [...document.querySelectorAll("#scopes input:checked")].map((input) => input.value);
  const params = new URLSearchParams({ client_id: configuration.clientId, redirect_uri: redirectUri, response_type: "code", scope: ["openid", ...scopes].join(" "), state, code_challenge: challenge, code_challenge_method: "S256" });
  if (broker) params.set("kc_idp_hint", "campus-demo");
  location.assign(configuration.issuer + "/protocol/openid-connect/auth?" + params);
}

async function callApi(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...(options.headers || {}), ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}) } });
  const body = await response.text();
  let data;
  try { data = JSON.parse(body); } catch { data = body; }
  show({ status: response.status, body: data });
  return { response, data };
}

async function start() {
  configuration = await (await fetch("/config")).json();
  const callback = new URLSearchParams(location.search);
  if (callback.has("code") || callback.has("error")) {
    history.replaceState({}, "", "/");
    const saved = JSON.parse(sessionStorage.getItem("soa-oauth") || "null");
    sessionStorage.removeItem("soa-oauth");
    if (!saved || callback.get("state") !== saved.state || Date.now() - saved.created > 600000) throw Error("Невірний або прострочений OAuth state. Повторіть вхід.");
    if (callback.has("error")) throw Error(callback.get("error_description") || callback.get("error"));
    const response = await fetch(configuration.issuer + "/protocol/openid-connect/token", {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "authorization_code", client_id: configuration.clientId, code: callback.get("code"), redirect_uri: redirectUri, code_verifier: saved.verifier })
    });
    const tokens = await response.json();
    if (!response.ok) throw Error(tokens.error_description || tokens.error || "Не вдалося обміняти code");
    accessToken = tokens.access_token;
    // The SPA does not use ID-token claims; access-token validation belongs to the gateway.
    const identity = await callApi("/auth/me");
    if (!identity.response.ok) { accessToken = null; throw Error("Gateway відхилив access token"); }
    $("status").textContent = `Вхід: ${identity.data.preferred_username || identity.data.sub}; scopes: ${identity.data.scope}`;
    $("logout").disabled = false;
    $("copy").disabled = false;
  }
  $("login").onclick = () => login().catch(showError);
  $("broker").onclick = () => login(true).catch(showError);
  $("logout").onclick = () => {
    accessToken = null;
    location.assign(configuration.issuer + "/protocol/openid-connect/logout?" + new URLSearchParams({ client_id: configuration.clientId, post_logout_redirect_uri: redirectUri }));
  };
  $("copy").onclick = () => navigator.clipboard.writeText(accessToken).then(() => { $("status").textContent = "Access token скопійовано для локальної перевірки"; }).catch(showError);
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.onclick = async () => {
      const user = Number($("user").value);
      const action = button.dataset.action;
      try {
        if (action === "identity") await callApi("/auth/me");
        if (action === "profile") await callApi(`/users/${user}`);
        if (action === "report") await callApi(`/reports/${user}`);
        if (action === "session") await callApi(`/sessions/${encodeURIComponent($("session").value.trim())}`);
        if (action === "write") {
          const result = await callApi("/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: user, duration_seconds: Number($("duration").value), fail: false }) });
          if (result.response.ok && (result.data.id || result.data.session_id)) $("session").value = result.data.id || result.data.session_id;
        }
      } catch (error) { showError(error); }
    };
  });
}
function showError(error) { show(error instanceof Error ? error.message : String(error)); }
start().catch(showError);
