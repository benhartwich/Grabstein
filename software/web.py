"""Web-Oberfläche des Grabsteins: kleine JSON-API und statische Seite – nur Standardbibliothek.

Läuft als Thread im Dienst. Lautsprecher und Gespräch fasst nur die Hauptschleife an,
die Web-Oberfläche schickt ihr dafür Befehle (Grabstein.submit).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import grabstein as g

STATIC = g.HERE / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".webmanifest": "application/manifest+json", ".mp3": "audio/mpeg", ".wav": "audio/wav"}
SAY_FILE = Path("/tmp/grabstein-sagen.mp3")
ROUTES: list[tuple[str, re.Pattern, callable]] = []


class ApiError(Exception):
    def __init__(self, msg: str, status: int = 400) -> None:
        super().__init__(msg)
        self.status = status


def route(method: str, pattern: str):
    def deco(fn):
        ROUTES.append((method, re.compile(pattern), fn))
        return fn
    return deco


def _hm(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts).strftime("%H:%M") if ts else None


# ---------------------------------------------------------------- Status
_next_cache: dict = {}


def _next(now: datetime, schedule: list) -> dict | None:
    """Nächster Zeitplanwechsel – einmal pro Minute berechnet."""
    key = (now.strftime("%Y-%m-%d %H:%M"), json.dumps(schedule))
    if _next_cache.get("key") != key:
        _next_cache.update(key=key, value=g.next_change(now, schedule))
    return _next_cache["value"]


@route("GET", r"/api/status")
def status(h, app: g.Grabstein):
    s, now, mono = app.settings.get(), datetime.now(), time.monotonic()
    sen = app.sensors
    today = app.state.today(now)
    talk = app.talk
    talk_s = today["talk_s"] + (talk.elapsed() if talk else 0)
    return {
        "time": now.strftime("%H:%M"),
        "mode": app.mode, "source": app.mode_source, "trusted": app.trusted, "online": app.net,
        "operation": {**s["operation"], "until_hm": _hm(s["operation"]["until"])},
        "next": _next(now, s["schedule"]) if app.mode_source == "schedule" else None,
        "sensors": {
            "pir": None if not sen or sen.pir is None else bool(sen.pir.value),
            "radar": None if not sen or sen.radar is None else bool(sen.radar.value),
            "radar_s": round(mono - sen.radar_since, 1) if sen and sen.radar_since else 0,
            "motion_ago": round(mono - sen.last_motion) if sen and sen.last_motion else None,
            "distance": None if not sen or not sen.data.fresh() else {
                "state": sen.data.state, "cm": sen.data.dist_cm,
                "move_cm": sen.data.move_cm, "move_energy": sen.data.move_energy,
                "still_cm": sen.data.still_cm, "still_energy": sen.data.still_energy,
            },
        },
        "talk": None if talk is None else {
            "elapsed": round(talk.elapsed()), "max": s["talk_max_s"], "mode": talk.mode,
            "transcript": list(app.transcript)[-12:],
        },
        "playing": app.player.label if app.player.playing() else None,
        "music": {"on": s["music_on"], "source": s["music_source"], "level": round(app.music.level),
                  "running": app.music.running()},
        "cooldown": {"clip": max(0, round(app.next_clip - mono)), "talk": max(0, round(app.next_talk - mono))},
        "today": {**today, "talk_min": round(talk_s / 60, 1), "budget_min": s["talk_budget_min"]},
        "log": [{"t": t, "hm": _hm(t), "msg": m} for t, m in list(g.LOG)[-8:]],
        "agent": bool(g.CFG["agent_id"]), "tts": bool(g.CFG["api_key"] and g.CFG["voice_id"]),
    }


# ---------------------------------------------------------------- Einstellungen und Zeitplan
@route("GET", r"/api/settings")
def settings_get(h, app):
    return app.settings.get()


@route("PUT", r"/api/settings")
def settings_put(h, app):
    patch = h.json()
    if not isinstance(patch, dict):
        raise ApiError("Ungültige Daten")
    try:
        new = app.settings.update(patch)
    except ValueError as e:
        raise ApiError(str(e)) from None
    if "operation" in patch:
        op = new["operation"]
        g.log(f"Betriebsart: {op['mode']}" + (f" bis {_hm(op['until'])}" if op["until"] else ""))
    elif "schedule" in patch:
        g.log("Zeitplan geändert")
    return new


@route("POST", r"/api/vorschau")
def preview(h, app):
    body = h.json()
    try:
        schedule = g.clean_setting("schedule", body.get("schedule", app.settings.get()["schedule"]))
    except ValueError as e:
        raise ApiError(str(e)) from None
    return {"days": g.schedule_preview(schedule, date.today(), int(body.get("days", 7)))}


# ---------------------------------------------------------------- Aktionen
@route("POST", r"/api/aktion")
def action(h, app):
    body = h.json()
    kind = body.get("type")
    if kind == "clip":
        ok, msg = app.submit("clip", body.get("group", ""), body.get("id") or None)
    elif kind in ("talk", "stop", "tone"):
        ok, msg = app.submit(kind, timeout=10)
    elif kind == "say":
        text = str(body.get("text", "")).strip()[:300]
        if not text:
            raise ApiError("Was soll Gustav sagen?")
        try:
            audio = g.synthesize(text)
        except Exception as e:  # noqa: BLE001
            raise ApiError(f"Sprachausgabe fehlgeschlagen: {str(e)[:120]}") from None
        SAY_FILE.write_bytes(b"".join(audio))
        g.log(f"Gustav sagt: {text}")
        ok, msg = app.submit("file", str(SAY_FILE), "Durchsage")
    else:
        raise ApiError("Unbekannte Aktion")
    if not ok:
        raise ApiError(msg, 409)
    return {"ok": True, "msg": msg}


# ---------------------------------------------------------------- Clips
def _group(group: str) -> str:
    if group not in g.GROUPS:
        raise ApiError("Unbekannte Gruppe", 404)
    return group


def _bg_generate(app, group: str, cid: str) -> None:
    def work():
        try:
            app.clips.generate(group, cid)
        except Exception as e:  # noqa: BLE001
            g.log(f"Clip {group}/{cid} nicht erzeugt: {str(e)[:120]}")
    threading.Thread(target=work, daemon=True).start()


@route("GET", r"/api/clips")
def clips_list(h, app):
    return {"groups": app.clips.listing(), "tts": bool(g.CFG["api_key"] and g.CFG["voice_id"])}


@route("POST", r"/api/clips/(\w+)")
def clips_add(h, app, group):
    try:
        cid = app.clips.add(_group(group), str(h.json().get("text", "")))
    except ValueError as e:
        raise ApiError(str(e)) from None
    if g.CFG["api_key"]:
        _bg_generate(app, group, cid)
    return {"id": cid}


@route("PUT", r"/api/clips/(\w+)/upload")
def clips_upload(h, app, group):
    name = (h.query.get("name") or ["eigen.mp3"])[0]
    try:
        return {"id": app.clips.upload(_group(group), name, h.raw(20 * 1024 * 1024))}
    except ValueError as e:
        raise ApiError(str(e)) from None


@route("PATCH", r"/api/clips/(\w+)/([\w-]+)")
def clips_edit(h, app, group, cid):
    body = h.json()
    try:
        app.clips.edit(_group(group), cid, body.get("text"), body.get("active"))
    except ValueError as e:
        raise ApiError(str(e)) from None
    return {"ok": True}


@route("DELETE", r"/api/clips/(\w+)/([\w-]+)")
def clips_delete(h, app, group, cid):
    try:
        app.clips.delete(_group(group), cid)
    except ValueError as e:
        raise ApiError(str(e)) from None
    return {"ok": True}


@route("POST", r"/api/clips/(\w+)/([\w-]+)/erzeugen")
def clips_generate(h, app, group, cid):
    if not (g.CFG["api_key"] and g.CFG["voice_id"]):
        raise ApiError("ElevenLabs-Schlüssel oder Stimme fehlt")
    _bg_generate(app, _group(group), cid)
    return {"ok": True}


@route("GET", r"/api/clips/(\w+)/([\w-]+)/audio")
def clips_audio(h, app, group, cid):
    f = app.clips.file(_group(group), cid)
    if not f:
        raise ApiError("Keine Audiodatei", 404)
    h.send_file(f)


# ---------------------------------------------------------------- Musik
def _spotify_state() -> str:
    try:
        return subprocess.run(["systemctl", "is-active", "raspotify"], capture_output=True, text=True,
                              timeout=2).stdout.strip() or "unbekannt"
    except (OSError, subprocess.SubprocessError):
        return "unbekannt"


@route("GET", r"/api/musik")
def music_get(h, app):
    return {"tracks": app.library.listing(), "busy": app.library.busy, "spotify": _spotify_state(),
            "level": round(app.music.level), "running": app.music.running(), "tts": bool(g.CFG["api_key"])}


@route("PUT", r"/api/musik/upload")
def music_upload(h, app):
    name = (h.query.get("name") or ["musik.mp3"])[0]
    try:
        return {"name": app.library.upload(name, h.raw(60 * 1024 * 1024))}
    except ValueError as e:
        raise ApiError(str(e)) from None


@route("DELETE", r"/api/musik/([\w.-]+)")
def music_delete(h, app, name):
    try:
        app.library.delete(name)
    except ValueError as e:
        raise ApiError(str(e)) from None
    return {"ok": True}


@route("GET", r"/api/musik/([\w.-]+)/audio")
def music_audio(h, app, name):
    f = app.library.file(name)
    if not f:
        raise ApiError("Stück nicht gefunden", 404)
    h.send_file(f)


@route("POST", r"/api/musik/erzeugen")
def music_generate(h, app):
    body = h.json()
    prompt, seconds = str(body.get("prompt", "")).strip(), int(body.get("seconds") or 90)
    if not prompt:
        raise ApiError("Beschreibung fehlt")
    if app.library.busy:
        raise ApiError("Es wird schon ein Stück erzeugt", 409)

    def work():
        try:
            app.library.generate(prompt, seconds)
        except Exception as e:  # noqa: BLE001
            g.log(f"Musik nicht erzeugt: {str(e)[:160]}")
    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


# ---------------------------------------------------------------- Gustav
@route("GET", r"/api/gustav")
def gustav_get(h, app):
    return {**app.gustav.data, "agent_id": g.CFG["agent_id"], "voice_id": g.CFG["voice_id"],
            "model": g.CFG["agent_model"], "clip_model": g.CFG["clip_model"]}


@route("PUT", r"/api/gustav")
def gustav_put(h, app):
    body = h.json()
    prompt, first = str(body.get("prompt", "")), str(body.get("first_message", ""))
    hint = ""
    if body.get("push"):
        try:
            agent_id, hint = g.push_agent(prompt.strip(), first.strip(), app.settings.get()["talk_max_s"])
        except Exception as e:  # noqa: BLE001
            raise ApiError(f"ElevenLabs: {str(e)[:200]}") from None
        if not g.CFG["agent_id"]:
            raise ApiError(f"Neuer Agent {agent_id} angelegt – bitte als ELEVENLABS_AGENT_ID eintragen")
        g.log("Gustav bei ElevenLabs aktualisiert")
    try:
        app.gustav.save(prompt, first, pushed=bool(body.get("push")))
    except ValueError as e:
        raise ApiError(str(e)) from None
    return {**app.gustav.data, "hint": hint}


# ---------------------------------------------------------------- Protokoll und System
@route("GET", r"/api/log")
def log_get(h, app):
    return {"lines": [{"t": t, "hm": datetime.fromtimestamp(t).strftime("%d.%m. %H:%M:%S"), "msg": m}
                      for t, m in g.LOG]}


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


@route("GET", r"/api/system")
def system(h, app):
    temp = _read("/sys/class/thermal/thermal_zone0/temp")
    wifi = None
    for line in _read("/proc/net/wireless").splitlines()[2:]:
        parts = line.split()
        if len(parts) > 3:
            wifi = {"iface": parts[0].rstrip(":"), "dbm": int(float(parts[3]))}
    ssid = ""
    try:
        ssid = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    disk = shutil.disk_usage("/")
    try:
        ip = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2).stdout.split()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        ip = ""
    evenings = app.state.data.get("evenings", {})
    return {
        "host": socket.gethostname(), "ip": ip,
        "temp_c": round(int(temp) / 1000, 1) if temp.isdigit() else None,
        "uptime_h": round(float(_read("/proc/uptime").split()[0]) / 3600, 1) if _read("/proc/uptime") else None,
        "service_h": round((time.time() - app.started) / 3600, 1),
        "load": _read("/proc/loadavg").split()[:3],
        "wifi": {**wifi, "ssid": ssid} if wifi else None,
        "disk_free_gb": round(disk.free / 1e9, 1),
        "trusted": app.trusted, "online": app.net,
        "pins": {"pir": g.CFG["pir_pin"], "radar": g.CFG["radar_pin"]},
        "history": [{"evening": k, **v} for k, v in sorted(evenings.items(), reverse=True)[:14]],
    }


@route("POST", r"/api/system/(neustart|herunterfahren)")
def system_power(h, app, what):
    g.log("Neustart über die Web-Oberfläche" if what == "neustart" else "Herunterfahren über die Web-Oberfläche")
    if what == "neustart":  # systemd startet den Dienst wieder (Restart=always)
        threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    else:
        threading.Timer(0.5, lambda: subprocess.run(["sudo", "-n", "systemctl", "poweroff"])).start()
    return {"ok": True}


# ---------------------------------------------------------------- Anmeldung (nur mit WEB_PIN)
def _token(pin: str) -> str:
    return hashlib.sha256(f"grabstein:{pin}".encode()).hexdigest()[:32]


@route("POST", r"/api/login")
def login(h, app):
    pin = h.server.pin
    if pin and str(h.json().get("pin", "")) != pin:
        time.sleep(1)
        raise ApiError("Falsche PIN", 401)
    h.cookie = f"gs={_token(pin)}; Path=/; Max-Age=31536000; SameSite=Strict; HttpOnly"
    return {"ok": True}


# ---------------------------------------------------------------- Server
class Handler(BaseHTTPRequestHandler):
    server_version = "Grabstein"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    # --- Hilfen für die Routen
    def raw(self, limit: int = 1024 * 1024) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ApiError("Datei zu groß", 413)
        return self.rfile.read(n) if n else b""

    def json(self) -> dict:
        try:
            data = json.loads(self.raw() or b"{}")
        except ValueError:
            raise ApiError("Ungültiges JSON") from None
        return data if isinstance(data, dict) else {}

    def send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "cookie", None):
            self.send_header("Set-Cookie", self.cookie)
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, f: Path) -> None:
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(f.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _authed(self) -> bool:
        pin = self.server.pin
        if not pin:
            return True
        cookies = dict(c.strip().split("=", 1) for c in (self.headers.get("Cookie") or "").split(";") if "=" in c)
        return cookies.get("gs") == _token(pin)

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        path, self.query = url.path, parse_qs(url.query)
        if not path.startswith("/api/"):
            return self._static(path) if method == "GET" else self.send_json({"error": "Nicht gefunden"}, 404)
        if path != "/api/login" and not self._authed():
            return self.send_json({"error": "PIN nötig", "login": True}, 401)
        for m, rx, fn in ROUTES:
            if m == method and (hit := rx.fullmatch(path)):
                try:
                    result = fn(self, self.server.app, *hit.groups())
                    if result is not None:
                        self.send_json(result)
                except ApiError as e:
                    self.send_json({"error": str(e), "login": e.status == 401}, e.status)
                except Exception as e:  # noqa: BLE001
                    g.log(f"Web-Fehler bei {method} {path}: {e}")
                    self.send_json({"error": "Interner Fehler – siehe Protokoll"}, 500)
                return
        self.send_json({"error": "Nicht gefunden"}, 404)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (STATIC / rel).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            f = STATIC / "index.html"
        self.send_file(f)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(app: g.Grabstein, port: int, pin: str = "") -> None:
    server = Server(("", port), Handler)
    server.app, server.pin = app, pin
    threading.Thread(target=server.serve_forever, daemon=True, name="web").start()
    g.log(f"Web-Oberfläche auf Port {port}" + (" (mit PIN)" if pin else ""))
