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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__AGENT_NAME__</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, "Segoe UI", Helvetica, sans-serif; background: #d9dbd5; }
  #app { display: flex; flex-direction: column; height: 100dvh; max-width: 680px;
         margin: 0 auto; box-shadow: 0 0 14px rgba(0,0,0,.25); }
  header { background: #008069; color: #fff; padding: .55rem .9rem;
           display: flex; align-items: center; gap: .7rem; }
  #avatar { width: 40px; height: 40px; border-radius: 50%; background: #25d366;
            display: grid; place-items: center; font-weight: 600; font-size: 1.15rem; flex: none; }
  .name { font-weight: 600; line-height: 1.3; }
  .status { font-size: .78rem; opacity: .85; }
  #sess { margin-left: auto; font-size: .68rem; opacity: .65; }
  #log { flex: 1; overflow-y: auto; padding: 1rem .9rem; display: flex; flex-direction: column;
         gap: 4px; background: #efeae2;
         background-image: radial-gradient(rgba(0,0,0,.035) 1px, transparent 1.2px);
         background-size: 22px 22px; }
  .me, .bot { max-width: 75%; padding: .4rem .6rem .35rem .6rem;
              border-radius: 8px; white-space: pre-wrap; font-size: .95rem;
              box-shadow: 0 1px .5px rgba(0,0,0,.13); }
  .me  { background: #d9fdd3; align-self: flex-end; border-top-right-radius: 0; }
  .bot { background: #fff; align-self: flex-start; border-top-left-radius: 0; }
  .err { background: #ffebee; }
  /* WhatsApp-style: the meta floats at the end of the LAST text line and
     drops below when the line is full — never overlaps the text. */
  .meta { float: right; margin: .55rem 0 0 .75rem; font-size: .66rem; color: #667781;
          display: flex; gap: 3px; align-items: center; }
  .ticks { color: #53bdeb; font-size: .8rem; line-height: 1; }
  .dots { display: inline-flex; gap: 3px; padding: .25rem .1rem; }
  .dots i { width: 7px; height: 7px; border-radius: 50%; background: #9aa0a6;
            animation: bounce 1.2s infinite; }
  .dots i:nth-child(2) { animation-delay: .15s; }
  .dots i:nth-child(3) { animation-delay: .3s; }
  @keyframes bounce { 0%,60%,100% { transform: translateY(0); opacity: .5; }
                      30% { transform: translateY(-4px); opacity: 1; } }
  footer { background: #f0f2f5; padding: .5rem .6rem; }
  form { display: flex; gap: .5rem; align-items: center; }
  input { flex: 1; padding: .68rem 1rem; border: 0; border-radius: 24px;
          font-size: .95rem; outline: none; background: #fff; }
  button { width: 46px; height: 46px; border: 0; border-radius: 50%; background: #008069;
           color: #fff; display: grid; place-items: center; cursor: pointer; flex: none; }
  button:active { background: #017561; }
</style>
<div id="app">
  <header>
    <div id="avatar"></div>
    <div><div class="name">__AGENT_NAME__</div><div class="status">online</div></div>
    <div id="sess"></div>
  </header>
  <div id="log"></div>
  <footer>
    <form id="f">
      <input id="t" placeholder="Mensagem" autofocus autocomplete="off">
      <button aria-label="Send"><svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
        <path d="M3.4 20.4l17.45-7.48a1 1 0 000-1.84L3.4 3.6a.993.993 0 00-1.39.91L2 9.12c0
                 .5.37.93.87.99L17 12 2.87 13.88c-.5.07-.87.5-.87 1l.01 4.61c0 .71.73 1.2 1.39.91z"/>
      </svg></button>
    </form>
  </footer>
</div>
<script>
  const sess = "ui-" + Math.random().toString(36).slice(2, 10);
  document.getElementById("sess").textContent = sess;
  document.getElementById("avatar").textContent =
    "__AGENT_NAME__".charAt(0).toUpperCase() || "A";
  const log = document.getElementById("log");
  const now = () => new Date().toLocaleTimeString([],
    { hour: "2-digit", minute: "2-digit", hour12: false });
  // Each bubble = content span + meta (time, and WhatsApp-style ✓✓ on outgoing).
  const add = (cls, text, ticks) => {
    const d = document.createElement("div");
    d.className = cls;
    const txt = document.createElement("span");
    txt.className = "txt"; txt.textContent = text;
    const meta = document.createElement("span");
    meta.className = "meta"; meta.textContent = now();
    if (ticks) {
      const t = document.createElement("span");
      t.className = "ticks"; t.textContent = "\\u2713\\u2713";
      meta.appendChild(t);
    }
    d.append(txt, meta);
    log.appendChild(d); log.scrollTop = log.scrollHeight;
    return d;
  };
  document.getElementById("f").onsubmit = async (e) => {
    e.preventDefault();
    const t = document.getElementById("t");
    const msg = t.value.trim();
    if (!msg) return;
    t.value = ""; add("me", msg, true);
    const wait = add("bot", "");
    wait.querySelector(".txt").innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
    const done = (text, err) => {
      wait.querySelector(".txt").textContent = text;
      if (err) wait.className = "bot err";
      log.scrollTop = log.scrollHeight;
    };
    try {
      const r = await fetch("/send", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg, session_id: sess }) });
      const d = await r.json();
      done(d.content || "(no content)", d.status === "ERROR");
    } catch (err) { done("request failed: " + err, true); }
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
