"""Fake Discord webhook - captures posts so notification logic can be verified."""
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
LOG = "/tmp/discord_posts.jsonl"
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self,*a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode()
        with open(LOG, "a") as f: f.write(body + "\n")
        self.send_response(204); self.send_header("Content-Length","0"); self.end_headers()
if __name__ == "__main__":
    open(LOG,"w").close()
    ThreadingHTTPServer(("127.0.0.1", 9922), H).serve_forever()
