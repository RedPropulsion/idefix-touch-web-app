#!/usr/bin/env python3
"""Local touchscreen controller; delegates LED commands to the tested CLI."""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
MODES = {"off": "Spento", "bounce": "Bounce", "spin": "Spin", "blink": "Blink"}


class Controller:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()

    def send(self, mode):
        if not isinstance(mode, str) or mode not in MODES:
            return 400, {"ok": False, "message": "Comando LED non valido."}
        if not self.lock.acquire(blocking=False):
            return 409, {"ok": False, "message": "Un comando è già in corso. Attendi l’esito."}
        try:
            # No shell; only the four explicitly allowed LED modes reach the CLI.
            command = [
                sys.executable, str(ROOT / "idefix_mavlink.py"),
                "send", "--host", self.args.host,
                "--port", str(self.args.port),
                "--bind", self.args.bind,
                "--local-port", str(self.args.local_port),
                "--system-id", str(self.args.system_id),
                "--component-id", str(self.args.component_id),
                "--target-system", str(self.args.target_system),
                "--target-component", str(self.args.target_component),
                "--timeout", str(self.args.timeout),
                "--retries", str(self.args.retries), "led", mode,
            ]
            result = subprocess.run(
                command, capture_output=True, text=True,
                timeout=(self.args.retries + 1) * self.args.timeout + 5,
            )
            detail = (result.stdout + "\n" + result.stderr).strip()
            if result.returncode == 0:
                return 200, {"ok": True, "mode": mode,
                             "message": f"{MODES[mode]} · comando accettato da ObelICS.",
                             "detail": detail}
            if "nessun ACK" in detail and "ACK MAV_RESULT_" not in detail:
                message = "Nessuna conferma da ObelICS. Verifica alimentazione e collegamento."
            elif "ACK MAV_RESULT_" in detail:
                message = "ObelICS ha risposto, ma non ha accettato il comando."
            else:
                message = "Invio non riuscito. Apri i dettagli per la causa."
            return 502, {"ok": False, "mode": mode, "message": message, "detail": detail}
        except subprocess.TimeoutExpired:
            return 504, {"ok": False, "message": "Tempo di attesa terminato. Esito del comando non confermato."}
        except OSError as exc:
            return 502, {"ok": False, "message": "Impossibile avviare il tool MAVLink.", "detail": str(exc)}
        finally:
            self.lock.release()


def make_handler(controller):
    # Same-origin page receives a token; no commands accepted from arbitrary forms.
    token = secrets.token_urlsafe(32)
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, data, content_type="application/json; charset=utf-8"):
            payload = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, dict) else data.encode()
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
                pass  # A closed browser must not affect the completed radio command.

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                self.reply(200, page, "text/html; charset=utf-8")
            elif path == "/api/info":
                self.reply(200, {"target": controller.args.host, "busy": controller.lock.locked()})
            else:
                self.reply(404, {"ok": False, "message": "Pagina non trovata."})

        def do_POST(self):
            if self.path != "/api/led":
                self.reply(404, {"ok": False, "message": "Endpoint non trovato."})
                return
            if self.headers.get("X-Idefix-Token") != token:
                self.reply(403, {"ok": False, "message": "Ricarica la pagina e riprova."})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 256:
                    raise ValueError("dimensione non valida")
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("JSON richiesto")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("oggetto richiesto")
            except (ValueError, UnicodeDecodeError):
                self.reply(400, {"ok": False, "message": "Richiesta non valida."})
                return
            status, result = controller.send(body.get("mode"))
            self.reply(status, result)

    return Handler


def build_parser():
    from idefix_mavlink import add_sender_options
    parser = argparse.ArgumentParser(description="Pannello touch locale per i LED di ObelICS")
    add_sender_options(parser)
    parser.set_defaults(retries=1)
    parser.add_argument("--web-bind", default="127.0.0.1", help="127.0.0.1 per il touchscreen; 0.0.0.0 anche per il PC")
    parser.add_argument("--web-port", type=int, default=8080)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not 0.1 <= args.timeout <= 10 or not 0 <= args.retries <= 2:
        parser.error("usa --timeout tra 0.1 e 10 secondi e --retries tra 0 e 2")
    if any(not 1 <= getattr(args, name) <= 65535 for name in ("port", "local_port", "web_port")):
        parser.error("le porte devono essere comprese tra 1 e 65535")
    from idefix_mavlink import require_pymavlink
    require_pymavlink()
    try:
        server = ThreadingHTTPServer((args.web_bind, args.web_port), make_handler(Controller(args)))
    except OSError as exc:
        parser.exit(1, f"Impossibile avviare la pagina: {exc}\n")
    print(f"Pannello: http://{args.web_bind}:{args.web_port} · ObelICS: {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
