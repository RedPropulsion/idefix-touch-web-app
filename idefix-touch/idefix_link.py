"""Persistent MAVLink endpoint and coherent LoRa observations (common dialect).

Only the receiver thread reads the UDP socket. HTTP command callers wait on an
ACK event; they never consume radio telemetry or each other's acknowledgements.
"""
from __future__ import annotations

import math
import secrets
import threading
import time
from collections import deque

from idefix_mavlink import open_sender, parse_mode, require_pymavlink, result_name

FIELDS = frozenset(("LR_BOOT", "LR_SEQ", "LR_TX", "LR_RX", "LR_OK", "LR_TO",
                    "LR_ERR", "LR_RESULT", "LR_RSSI", "LR_SNR", "LR_RTT",
                    "LR_AGE", "LR_END"))
RESULTS = {1: "pong", 2: "timeout", 3: "unexpected", 4: "error", 5: "unavailable"}
MAX_POINTS = 6000  # more than eight hours at the existing five-second interval
STALE_SECONDS = 12.0


class SignalStore:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.instance = secrets.token_hex(8)
        self.lock = threading.RLock()
        self.pending = {}
        self.retired_boots = deque(maxlen=32)
        self.latest = None
        self.latest_at = None
        self.last_message_at = None
        self.transport_error = None
        self.led_mode = None
        self.led_ack_at = None
        self.recording = False
        self.started_at = None
        self.stopped_at = None
        self.points = deque(maxlen=MAX_POINTS)
        self.generation = 0
        self.point_id = 0
        self.demo = False
        self.scenario = "near"

    def ingest(self, stamp, name, value):
        """Ignore incomplete, duplicate, old or invalid snapshots; UDP may reorder."""
        if name not in FIELDS:
            return False
        now = self.clock()
        with self.lock:
            self.last_message_at = now
            self.pending = {k: v for k, v in self.pending.items() if now - v[0] <= 3}
            if stamp not in self.pending:
                if len(self.pending) >= 8:
                    self.pending.pop(next(iter(self.pending)))
                self.pending[stamp] = (now, {})
            values = self.pending[stamp][1]
            values[name] = int(value)
            if values.keys() != FIELDS:
                return False
            del self.pending[stamp]
            return self._accept(values, now)

    def _accept(self, v, now):
        if (v["LR_END"] != 1 or v["LR_RESULT"] not in RESULTS or
                any(v[k] < 0 for k in ("LR_BOOT", "LR_SEQ", "LR_TX", "LR_RX",
                                       "LR_OK", "LR_TO", "LR_ERR")) or
                v["LR_OK"] > v["LR_TX"] or v["LR_OK"] > v["LR_RX"] or
                v["LR_AGE"] < -1 or v["LR_RTT"] < -1 or
                not -200 <= v["LR_RSSI"] <= 30 or not -128 <= v["LR_SNR"] <= 127):
            return False
        success = v["LR_RESULT"] == 1
        if success and (v["LR_AGE"] < 0 or v["LR_RTT"] < 0):
            return False
        previous = self.latest
        boot, seq = v["LR_BOOT"], v["LR_SEQ"]
        reboot = previous is not None and boot != previous["boot"]
        if boot in self.retired_boots:
            return False
        if previous and not reboot and seq <= previous["sequence"]:
            return False
        if reboot:
            self.retired_boots.append(previous["boot"])
            self.led_mode = None
        has_signal = v["LR_AGE"] >= 0
        sample = dict(boot=boot, sequence=seq, tx=v["LR_TX"], rx=v["LR_RX"],
                      pong=v["LR_OK"], timeouts=v["LR_TO"], errors=v["LR_ERR"],
                      result=RESULTS[v["LR_RESULT"]],
                      rssi=v["LR_RSSI"] if has_signal else None,
                      snr=v["LR_SNR"] if has_signal else None,
                      rtt_ms=v["LR_RTT"] if success else None,
                      age_ms=v["LR_AGE"], reboot=reboot)
        gap = previous is None or reboot or seq != previous["sequence"] + 1
        self.latest, self.latest_at = sample, now
        self.transport_error = None
        if self.recording:
            self.point_id += 1
            self.points.append(dict(id=self.point_id, t=round(now-self.started_at, 3),
                                    rssi=sample["rssi"] if success else None,
                                    snr=sample["snr"] if success else None,
                                    result=sample["result"], gap=gap))
        return True

    def experiment(self, action):
        with self.lock:
            if action == "start":
                if not self.recording:
                    self.points.clear()
                    self.generation += 1
                    self.started_at = self.clock()
                    self.stopped_at = None
                    self.recording = True
            elif action == "stop":
                if self.recording:
                    self.stopped_at = self.clock()
                    self.recording = False
            else:
                raise ValueError("Azione non valida")

    def snapshot(self, after=0):
        now = self.clock()
        with self.lock:
            age = None if self.latest_at is None else now - self.latest_at
            latest = dict(self.latest) if self.latest else None
            if latest:
                latest["signal_age_s"] = (None if latest["age_ms"] < 0 else
                                          latest["age_ms"] / 1000 + age)
            elapsed = (0 if self.started_at is None else
                       (now if self.recording else self.stopped_at) - self.started_at)
            return dict(instance=self.instance, demo=self.demo, scenario=self.scenario,
                        telemetry_available=latest is not None,
                        telemetry_age_s=age, stale=age is None or age > STALE_SECONDS,
                        transport_error=self.transport_error, latest=latest,
                        led_mode=self.led_mode, led_ack_at=self.led_ack_at,
                        recording=self.recording, elapsed_s=round(elapsed, 3),
                        generation=self.generation, sample_count=len(self.points),
                        points=[dict(p) for p in self.points if p["id"] > after])


class MavlinkLink:
    def __init__(self, args, store):
        self.args, self.store = args, store
        self.command_lock = threading.Lock()
        self.tx_lock = threading.Lock()
        self.ack_lock = threading.Lock()
        self.pending = None
        self.stop_event = threading.Event()
        self.connection = None
        self.thread = None

    def start(self):
        a = self.args
        require_pymavlink()
        self.connection = open_sender(a.host, a.port, a.bind, a.local_port,
                                      a.system_id, a.component_id)
        self.thread = threading.Thread(target=self._receive, name="mavlink-rx", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        with self.ack_lock:
            if self.pending:
                self.pending["event"].set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.connection:
            self.connection.close()

    def _receive(self):
        while not self.stop_event.is_set():
            try:
                message = self.connection.recv_match(blocking=True, timeout=0.2)
                if message is not None:
                    self.handle(message)
            except (OSError, ValueError) as exc:
                with self.store.lock:
                    self.store.transport_error = str(exc)
                self.stop_event.wait(0.5)

    def handle(self, message):
        a = self.args
        if (message.get_srcSystem() != a.target_system or
                message.get_srcComponent() != a.target_component):
            return
        kind = message.get_type()
        if kind == "NAMED_VALUE_INT":
            name = message.name
            if isinstance(name, bytes):
                name = name.decode("ascii", errors="replace")
            self.store.ingest(message.time_boot_ms, name.rstrip("\0"), message.value)
        elif kind == "COMMAND_ACK":
            if (getattr(message, "target_system", 0) not in (0, a.system_id) or
                    getattr(message, "target_component", 0) not in (0, a.component_id)):
                return
            with self.ack_lock:
                pending = self.pending
                if pending and message.command == pending["command"]:
                    pending["result"] = message.result
                    pending["event"].set()

    def send_led(self, mode):
        if not self.command_lock.acquire(blocking=False):
            return 409, dict(ok=False, message="Un comando LED è già in corso.")
        command = int(parse_mode("led", mode).value)
        pending = dict(command=command, event=threading.Event(), result=None)
        try:
            with self.ack_lock:
                self.pending = pending
            a = self.args
            for attempt in range(a.retries + 1):
                with self.tx_lock:
                    self.connection.mav.command_long_send(a.target_system, a.target_component,
                        command, attempt, 0, 0, 0, 0, 0, 0, 0)
                if pending["event"].wait(a.timeout):
                    break
            result = pending["result"]
            if result == require_pymavlink().mavlink.MAV_RESULT_ACCEPTED:
                with self.store.lock:
                    self.store.led_mode = mode
                    self.store.led_ack_at = time.time()
                return 200, dict(ok=True, mode=mode, message="ACK ricevuto",
                                 detail=f"COMMAND_ACK {result_name(result)} · ObelICS 1/1")
            with self.store.lock:
                self.store.led_mode = None
            if result is None:
                return 502, dict(ok=False, message="Nessuna conferma da ObelICS.",
                                 detail="Esito LED non confermato. Verifica Ethernet e alimentazione.")
            return 502, dict(ok=False, message="ObelICS non ha accettato il comando.",
                             detail=result_name(result))
        except (OSError, ValueError) as exc:
            with self.store.lock:
                self.store.led_mode = None
            return 502, dict(ok=False, message="Invio MAVLink non riuscito.", detail=str(exc))
        finally:
            with self.ack_lock:
                self.pending = None
            self.command_lock.release()


class DemoLink:
    """Explicit offline preview: no radio or UDP socket is opened."""
    def __init__(self, args, store):
        self.store = store
        store.demo = True
        self.command_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.seq = self.good = self.timeouts = 0
        self.rssi, self.snr = -62, 10
        self.last_good = None

    def start(self):
        self.tick()
        self.thread = threading.Thread(target=self._run, name="preview", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.wait(5):
            self.tick()

    def tick(self):
        with self.store.lock:
            self.seq += 1
            scenario = self.store.scenario
            ok = scenario != "off" and not (scenario == "obstacle" and self.seq % 4 == 0)
            now = self.store.clock()
            if ok:
                target_rssi, target_snr = {"near": (-62, 10), "far": (-88, 6),
                                          "obstacle": (-112, -3)}[scenario]
                self.rssi = target_rssi + round(2 * math.sin(self.seq * 1.7))
                self.snr = target_snr + round(math.cos(self.seq * 1.3))
                self.good += 1
                self.last_good = now
            else:
                self.timeouts += 1
            values = dict(LR_BOOT=1, LR_SEQ=self.seq, LR_TX=self.seq, LR_RX=self.good,
                          LR_OK=self.good, LR_TO=self.timeouts, LR_ERR=0,
                          LR_RESULT=1 if ok else 2, LR_RSSI=self.rssi, LR_SNR=self.snr,
                          LR_RTT=280 if ok else -1,
                          LR_AGE=-1 if self.last_good is None else int((now-self.last_good)*1000),
                          LR_END=1)
            for key, value in values.items():
                self.store.ingest(self.seq * 5000, key, value)

    def send_led(self, mode):
        with self.store.lock:
            self.store.led_mode, self.store.led_ack_at = mode, time.time()
        return 200, dict(ok=True, mode=mode, message="ACK simulato · nessun comando hardware")

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
