import io
import json
import subprocess
import unittest
from email.message import Message
from unittest.mock import Mock, patch

from idefix_web import Controller, build_parser, make_handler


class WebTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(build_parser().parse_args(["--host", "192.168.10.2"]))

    @patch("idefix_web.subprocess.run")
    def test_four_led_commands_reuse_cli(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "ACK MAV_RESULT_ACCEPTED", "")
        for mode in ("off", "bounce", "spin", "blink"):
            status, result = self.controller.send(mode)
            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            self.assertEqual(run.call_args.args[0][-2:], ["led", mode])
            self.assertNotIn("shell", run.call_args.kwargs)

    @patch("idefix_web.subprocess.run")
    def test_rejects_servo_and_non_string_without_invoking_cli(self, run):
        for mode in ("servo", "wiggle", "60011", "blink; reboot", None, [], {}):
            self.assertEqual(self.controller.send(mode)[0], 400)
        run.assert_not_called()

    @patch("idefix_web.subprocess.run")
    def test_busy_does_not_start_a_second_sender(self, run):
        with self.controller.lock:
            self.assertEqual(self.controller.send("off")[0], 409)
        run.assert_not_called()

    @patch("idefix_web.subprocess.run")
    def test_missing_ack_is_not_success(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "--- nessun ACK entro 2.0 s", "")
        status, result = self.controller.send("blink")
        self.assertEqual(status, 502)
        self.assertFalse(result["ok"])
        self.assertIn("Nessuna conferma", result["message"])
        self.assertFalse(self.controller.lock.locked())

    @patch("idefix_web.subprocess.run")
    def test_rejected_ack_is_not_success(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "ACK MAV_RESULT_UNSUPPORTED", "")
        self.assertFalse(self.controller.send("blink")[1]["ok"])

    @patch("idefix_web.subprocess.run")
    def test_timeout_releases_lock(self, run):
        run.side_effect = subprocess.TimeoutExpired("tool", 9)
        self.assertEqual(self.controller.send("blink")[0], 504)
        self.assertFalse(self.controller.lock.locked())

    @patch("idefix_web.subprocess.run")
    def test_bind_error_is_reported(self, run):
        run.return_value = subprocess.CompletedProcess([], 2, "", "Errore: impossibile usare UDP 0.0.0.0:14551")
        status, result = self.controller.send("off")
        self.assertEqual(status, 502)
        self.assertIn("14551", result["detail"])

    def request(self, method, path, payload=None, token="test-token"):
        with patch("idefix_web.secrets.token_urlsafe", return_value="test-token"):
            handler_type = make_handler(self.controller)
        handler = object.__new__(handler_type)
        handler.path = path
        handler.headers = Message()
        handler.headers["X-Idefix-Token"] = token
        handler.headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler.reply = Mock()
        getattr(handler, "do_" + method)()
        return handler.reply.call_args.args

    def test_post_requires_page_token(self):
        with patch.object(self.controller, "send") as send:
            self.assertEqual(self.request("POST", "/api/led", {"mode": "off"}, "wrong")[0], 403)
            send.assert_not_called()

    def test_post_accepts_only_json_object(self):
        with patch.object(self.controller, "send") as send:
            self.assertEqual(self.request("POST", "/api/led", ["off"])[0], 400)
            send.assert_not_called()

    def test_post_dispatches_led_mode(self):
        with patch.object(self.controller, "send", return_value=(200, {"ok": True})) as send:
            self.assertEqual(self.request("POST", "/api/led", {"mode": "off"})[0], 200)
            send.assert_called_once_with("off")

    def test_get_never_sends_a_command(self):
        with patch.object(self.controller, "send") as send:
            self.assertEqual(self.request("GET", "/api/led?mode=blink")[0], 404)
            self.assertEqual(self.request("GET", "/")[0], 200)
            self.assertEqual(self.request("GET", "/api/info")[0], 200)
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
