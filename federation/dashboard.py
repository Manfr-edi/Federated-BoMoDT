"""Small per-twin monitor, served by the owning twin process on localhost."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from .storage import latest

PAGE = b'''<!doctype html><html lang="en"><meta charset="utf-8"><title>TAZ Digital Twin</title>
<style>body{font:17px system-ui;background:#112a35;color:#e0f8ee;margin:4vw}pre{padding:24px;background:#193e48;border-radius:12px;white-space:pre-wrap}</style>
<h1>TAZ Digital Twin</h1><p>Local digital shadow, control decisions and timing (milliseconds).</p><pre id="state">Waiting for telemetry...</pre>
<script>async function refresh(){try{let r=await fetch('/state');let d=await r.json();document.querySelector('h1').textContent='TAZ '+(d.taz||'')+' Digital Twin';document.querySelector('#state').textContent=JSON.stringify(d,null,2)}catch(e){} }refresh();setInterval(refresh,1000)</script></html>'''


def serve(path, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ('/', '/state'):
                self.send_error(404); return
            payload = json.dumps(latest(path)).encode() if self.path == '/state' else PAGE
            self.send_response(200)
            self.send_header('Content-Type', 'application/json' if self.path == '/state' else 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers(); self.wfile.write(payload)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
