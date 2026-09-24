#!/usr/bin/env python3
"""Touch panel with LED controls and real LoRa signal history; offline preview optional."""
from __future__ import annotations

import argparse
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from idefix_link import DemoLink, MavlinkLink, SignalStore
from idefix_mavlink import add_sender_options

ROOT = Path(__file__).resolve().parent
MODES = {"off": "Spento", "bounce": "Bounce", "spin": "Spin", "blink": "Blink"}


class Controller:
    def __init__(self, args, link=None, store=None):
        self.args = args
        self.store = store or SignalStore()
        self.link = link or (DemoLink if args.demo else MavlinkLink)(args, self.store)
        self.lock = self.link.command_lock

    def send(self, mode):
        if not isinstance(mode, str) or mode not in MODES:
            return 400, {"ok": False, "message": "Comando LED non valido."}
        return self.link.send_led(mode)


def make_handler(controller):
    token = secrets.token_urlsafe(32)
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)
    script = (ROOT / "static" / "panel.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "static" / "panel.css").read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def reply(self, status, data, content_type="application/json; charset=utf-8"):
            payload = (json.dumps(data, ensure_ascii=False, allow_nan=False) if isinstance(data, dict)
                       else data).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/":
                self.reply(200, page, "text/html; charset=utf-8")
            elif url.path == "/static/panel.js":
                self.reply(200, script, "text/javascript; charset=utf-8")
            elif url.path == "/static/panel.css":
                self.reply(200, stylesheet, "text/css; charset=utf-8")
            elif url.path == "/api/info":
                self.reply(200, {"target": controller.args.host,
                                 "busy": controller.lock.locked(), "demo": controller.args.demo})
            elif url.path == "/api/lora/state":
                try:
                    after = max(0, int(parse_qs(url.query).get("after", ["0"])[0]))
                except (ValueError, IndexError):
                    self.reply(400, {"ok": False, "message": "Cursore non valido."})
                    return
                state = controller.store.snapshot(after)
                state["busy"] = controller.lock.locked()
                self.reply(200, state)
            else:
                self.reply(404, {"ok": False, "message": "Pagina non trovata."})

        def do_POST(self):
            allowed = {"/api/led", "/api/lora/experiment"}
            if controller.args.demo:
                allowed.add("/api/demo/scenario")
            if self.path not in allowed:
                self.reply(404, {"ok": False, "message": "Endpoint non trovato."})
                return
            if self.headers.get("X-Idefix-Token") != token:
                self.reply(403, {"ok": False, "message": "Ricarica la pagina e riprova."})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 256 or self.headers.get_content_type() != "application/json":
                    raise ValueError()
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                self.reply(400, {"ok": False, "message": "Richiesta non valida."})
                return
            if self.path == "/api/led":
                status, result = controller.send(body.get("mode"))
                self.reply(status, result)
            elif self.path == "/api/lora/experiment":
                action = body.get("action")
                if action not in ("start", "stop"):
                    self.reply(400, {"ok": False, "message": "Azione non valida."})
                    return
                controller.store.experiment(action)
                self.reply(200, {"ok": True})
            else:
                scenario = body.get("scenario")
                if scenario not in ("near", "far", "obstacle", "off"):
                    self.reply(400, {"ok": False, "message": "Scenario non valido."})
                    return
                with controller.store.lock:
                    controller.store.scenario = scenario
                self.reply(200, {"ok": True})

    return Handler


def build_parser():
    parser = argparse.ArgumentParser(description="Idefix · LED ObelICS e segnale LoRa")
    add_sender_options(parser)
    parser.set_defaults(retries=1, host="192.168.10.2")
    parser.add_argument("--web-bind", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("--demo", action="store_true", help="Anteprima simulata: nessun accesso hardware o UDP")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not 0.1 <= args.timeout <= 10 or not 0 <= args.retries <= 2:
        parser.error("usa --timeout tra 0.1 e 10 secondi e --retries tra 0 e 2")
    if any(not 1 <= getattr(args, n) <= 65535 for n in ("port", "local_port", "web_port")):
        parser.error("le porte devono essere comprese tra 1 e 65535")
    controller = Controller(args)
    server = None
    try:
        controller.link.start()
        server = ThreadingHTTPServer((args.web_bind, args.web_port), make_handler(controller))
        print(f"Idefix · http://{args.web_bind}:{args.web_port} · "
              f"{'DATI SIMULATI' if args.demo else 'ObelICS '+args.host}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, ConnectionError) as exc:
        parser.exit(1, f"Avvio non riuscito: {exc}\n")
    finally:
        if server:
            server.server_close()
        controller.link.close()


if __name__ == "__main__":
    main()
