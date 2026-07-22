#!/usr/bin/env python3
"""Dead-simple chat UI for talking to the local agent — one file, no deps.

Serves a chat page and proxies messages to the agent's native runs endpoint
(same-origin, so no CORS involved). Each page load = a new session, shown in
the header so you can find the conversation later in the observability
platform (thread_id) and the Sessions view.

    make chat            # then open http://localhost:8899
    # agent id + port come from Settings (.env); overrides:
    #   CHAT_UI_PORT   port for this UI          (default 8899)
    #   CHAT_USER_ID   user_id sent with each turn (default user-local)
"""

import json
import os
import sys
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_app.config import get_settings

SETTINGS = get_settings()
AGENT_URL = f"http://127.0.0.1:{SETTINGS.port}/agents/{SETTINGS.agent_id}/runs"
UI_PORT = int(os.getenv("CHAT_UI_PORT", "8899"))
USER_ID = os.getenv("CHAT_USER_ID", "user-local")

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>__AGENT_NAME__ chat</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; }
  #log { border: 1px solid #ccc; border-radius: 8px; padding: 1rem; min-height: 320px; }
  .me, .bot { margin: .5rem 0; padding: .5rem .8rem; border-radius: 8px; white-space: pre-wrap; }
  .me  { background: #e3f2fd; text-align: right; }
  .bot { background: #f5f5f5; }
  .err { background: #ffebee; }
  form { display: flex; gap: .5rem; margin-top: 1rem; }
  input { flex: 1; padding: .6rem; border: 1px solid #ccc; border-radius: 8px; }
  button { padding: .6rem 1.2rem; border: 0; border-radius: 8px; background: #1976d2; color: #fff; }
  small { color: #888; }
</style>
<h2>__AGENT_NAME__ <small id="sess"></small></h2>
<div id="log"></div>
<form id="f"><input id="t" placeholder="Say something…" autofocus autocomplete="off"><button>Send</button></form>
<script>
  const sess = "ui-" + Math.random().toString(36).slice(2, 10);
  document.getElementById("sess").textContent = sess;
  const log = document.getElementById("log");
  const add = (cls, text) => {
    const d = document.createElement("div");
    d.className = cls; d.textContent = text;
    log.appendChild(d); log.scrollTop = log.scrollHeight;
    return d;
  };
  document.getElementById("f").onsubmit = async (e) => {
    e.preventDefault();
    const t = document.getElementById("t");
    const msg = t.value.trim();
    if (!msg) return;
    t.value = ""; add("me", msg);
    const wait = add("bot", "…");
    try {
      const r = await fetch("/send", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg, session_id: sess }) });
      const d = await r.json();
      wait.textContent = d.content || "(no content)";
      if (d.status === "ERROR") wait.className = "bot err";
    } catch (err) { wait.textContent = "request failed: " + err; wait.className = "bot err"; }
  };
</script>
""".replace("__AGENT_NAME__", SETTINGS.agent_name)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            boundary = uuid.uuid4().hex
            parts = []
            for k, v in [("message", req["message"]), ("session_id", req["session_id"]),
                         ("user_id", USER_ID), ("stream", "false")]:
                parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                             f'name="{k}"\r\n\r\n{v}\r\n')
            payload = ("".join(parts) + f"--{boundary}--\r\n").encode()
            up = urllib.request.Request(  # noqa: S310 — fixed localhost agent URL
                AGENT_URL, data=payload, method="POST",
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            with urllib.request.urlopen(up, timeout=120) as resp:  # noqa: S310 — localhost agent
                data = json.load(resp)
            out = json.dumps({"status": data.get("status"),
                              "content": data.get("content")}).encode()
            self.send_response(200)
        except Exception as exc:  # noqa: BLE001 — surface any failure in the chat itself
            out = json.dumps({"status": "ERROR", "content": f"proxy error: {exc}"}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        print(f"[chat-ui] {fmt % args}", flush=True)


if __name__ == "__main__":
    print(f"[chat-ui] http://localhost:{UI_PORT}  →  {AGENT_URL}", flush=True)
    HTTPServer(("127.0.0.1", UI_PORT), Handler).serve_forever()
