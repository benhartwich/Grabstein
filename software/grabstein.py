#!/usr/bin/env python3
"""Flüsternder Grabstein – Halloween-Programm für Raspberry Pi 4 + ReSpeaker XVF3800.

Bewegungsmelder (PIR)  -> „jemand kommt“     -> Flüster-Clip
Radar (LD2410C, OUT)   -> „jemand bleibt“    -> Gespräch mit dem ElevenLabs-Agenten
                                                (oder Clip im Kinder-Modus / ohne Internet)
Radar wieder frei      -> „jemand geht“      -> Gespräch endet

Gesteuert wird über die Web-Oberfläche (http://grabstein.local), die der Dienst mitbringt.

Befehle:
  grabstein run        Normalbetrieb mit Web-Oberfläche (so läuft der Dienst)
  grabstein sensoren   Sensoren 60 s live anzeigen
  grabstein ton        Lautsprecher und Mikrofon testen
  grabstein reden      Sofort ein Gespräch starten (Test)
  grabstein clips      Fehlende oder geänderte Flüster-Clips mit ElevenLabs erzeugen (--neu: alle)
  grabstein agent      ElevenLabs-Agenten anlegen bzw. aktualisieren
  grabstein status     Einstellungen und heutiges Gesprächsbudget anzeigen
"""
from __future__ import annotations

import json
import os
import queue
import random
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- feste Einstellungen (env-Datei)
def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def env_hm(name: str, default: str) -> str:
    raw = env(name, default)
    return raw if HM.fullmatch(raw) else default


HM = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")
CFG = {
    "pir_pin": env("PIR_PIN", "17"),
    "radar_pin": env("RADAR_PIN", "22"),
    "agent_id": env("ELEVENLABS_AGENT_ID", ""),
    "api_key": env("ELEVENLABS_API_KEY", ""),
    "voice_id": env("ELEVENLABS_VOICE_ID", ""),
    "clip_model": env("CLIP_MODELL", "eleven_v4"),
    "agent_model": env("AGENT_MODELL", "eleven_v4_turbo"),
    "clips_dir": Path(env("CLIPS_DIR", str(HERE / "clips"))),
    "data_dir": Path(env("DATA_DIR", "/var/lib/grabstein")),
    "web_port": env_int("WEB_PORT", 80),
    "web_pin": env("WEB_PIN", ""),
}
GROUPS = ("approach", "stay", "leave", "gentle")
MODES = ("off", "gentle", "full")
LOG: deque[tuple[float, str]] = deque(maxlen=500)


def log(msg: str) -> None:
    LOG.append((time.time(), msg))
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- veränderliche Einstellungen (Web)
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
NUMBERS = {  # Schlüssel: (min, max)
    "stay_s": (1, 60),
    "leave_s": (2, 120),
    "cooldown_clip_s": (0, 900),
    "cooldown_talk_s": (0, 1800),
    "talk_max_s": (20, 600),
    "talk_budget_min": (0, 600),
    "volume": (0, 100),
}
FLAGS = ("live_agent", "talk_gentle", "approach_clips", "transcripts")


def default_settings() -> dict:
    """Erster Start: Werte aus der env-Datei übernehmen, danach gilt settings.json."""
    start, gentle_end, end = env_hm("AKTIV_AB", "16:30"), env_hm("KINDER_MODUS_BIS", "19:30"), env_hm("AKTIV_BIS", "22:30")
    return {
        "operation": {"mode": "auto", "until": None},
        "schedule": [
            {"id": "lieb", "days": ALL_DAYS, "date": None, "from": start, "until": gentle_end, "mode": "gentle"},
            {"id": "grusel", "days": ALL_DAYS, "date": None, "from": gentle_end, "until": end, "mode": "full"},
        ],
        "stay_s": env_int("STEHEN_SEKUNDEN", 4),
        "leave_s": env_int("WEG_SEKUNDEN", 8),
        "cooldown_clip_s": env_int("PAUSE_CLIP_SEKUNDEN", 45),
        "cooldown_talk_s": env_int("PAUSE_GESPRAECH_SEKUNDEN", 120),
        "talk_max_s": env_int("GESPRAECH_MAX_SEKUNDEN", 90),
        "talk_budget_min": env_int("GESPRAECH_BUDGET_MINUTEN", 20),
        "volume": env_int("LAUTSTAERKE", 100),
        "live_agent": env("LIVE_AGENT", "1") == "1",
        "talk_gentle": False,
        "approach_clips": True,
        "transcripts": env("LOG_TRANSKRIPTE", "0") == "1",
    }


def _clean_entry(e: dict) -> dict:
    if not isinstance(e, dict):
        raise ValueError("Zeitplan-Eintrag ungültig")
    mode = e.get("mode")
    if mode not in MODES:
        raise ValueError("Zeitplan: unbekannter Modus")
    start, end = str(e.get("from", "")), str(e.get("until", ""))
    if not HM.fullmatch(start) or not HM.fullmatch(end):
        raise ValueError("Zeitplan: Uhrzeit bitte als HH:MM")
    day = e.get("date") or None
    if day is not None:
        try:
            day = date.fromisoformat(str(day)).isoformat()
        except ValueError:
            raise ValueError("Zeitplan: Datum ungültig") from None
        days = []
    else:
        days = sorted({int(d) for d in e.get("days") or [] if str(d).isdigit() and 0 <= int(d) <= 6})
        if not days:
            raise ValueError("Zeitplan: mindestens einen Wochentag wählen")
    eid = str(e.get("id") or "")
    if not re.fullmatch(r"[\w-]{1,24}", eid):
        eid = secrets.token_hex(3)
    return {"id": eid, "days": days, "date": day, "from": start, "until": end, "mode": mode}


def clean_setting(key: str, value):
    if key in NUMBERS:
        lo, hi = NUMBERS[key]
        if isinstance(value, bool):
            raise ValueError(f"{key}: Zahl erwartet")
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key}: Zahl erwartet") from None
        if not lo <= v <= hi:
            raise ValueError(f"{key}: erlaubt sind {lo} bis {hi}")
        return v
    if key in FLAGS:
        return bool(value)
    if key == "schedule":
        if not isinstance(value, list) or len(value) > 60:
            raise ValueError("Zeitplan ungültig")
        return [_clean_entry(e) for e in value]
    if key == "operation":
        mode = (value or {}).get("mode")
        if mode not in ("auto", *MODES):
            raise ValueError("Betriebsart unbekannt")
        until = (value or {}).get("until")
        until = float(until) if until not in (None, "") and mode != "auto" else None
        return {"mode": mode, "until": until}
    raise ValueError(f"Unbekannte Einstellung: {key}")


class Settings:
    """settings.json im Datenordner; Änderungen über die Web-Oberfläche wirken sofort."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        data = default_settings()
        try:
            stored = json.loads(path.read_text())
            for key, value in stored.items():
                try:
                    data[key] = clean_setting(key, value)
                except ValueError as e:
                    log(f"Einstellung {key} verworfen: {e}")
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log(f"settings.json nicht lesbar ({e}) – nehme Standardwerte")
        self.data = data  # wird nie verändert, nur ersetzt – Lesen braucht keine Sperre

    def get(self) -> dict:
        return self.data

    def update(self, patch: dict) -> dict:
        with self._lock:
            new = dict(self.data)
            for key, value in patch.items():
                new[key] = clean_setting(key, value)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(self.path, json.dumps(new, ensure_ascii=False, indent=1))
            except OSError as e:
                log(f"Einstellungen nicht gespeichert: {e}")
            self.data = new
            return new


# ---------------------------------------------------------------- Uhrzeit und Modus
def time_trusted() -> bool:
    """Ohne Echtzeituhr stimmt die Zeit erst nach NTP-Abgleich."""
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        return out == "yes"
    except (OSError, subprocess.SubprocessError):
        return False


def _minutes(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def _entry_days(e: dict, now: datetime) -> list[date]:
    """Starttage, an denen der Eintrag jetzt gilt (über Mitternacht zählt der Starttag)."""
    m, s, end = now.hour * 60 + now.minute, _minutes(e["from"]), _minutes(e["until"])
    today = now.date()
    if s < end:
        days = [today] if s <= m < end else []
    elif s == end:
        days = [today]  # von = bis: ganzer Tag
    else:
        days = ([today] if m >= s else []) + ([today - timedelta(days=1)] if m < end else [])
    return [d for d in days if (e["date"] == d.isoformat() if e["date"] else d.weekday() in e["days"])]


def schedule_mode(now: datetime, schedule: list[dict]) -> str:
    """Datums-Einträge ersetzen an ihrem Tag die Wochentags-Einträge.
    Überschneiden sich Einträge, gewinnt „aus“, dann „gruselig“, dann „lieb“."""
    dated = {e["date"] for e in schedule if e["date"]}
    hits = {e["mode"] for e in schedule for d in _entry_days(e, now)
            if e["date"] or d.isoformat() not in dated}
    return next((m for m in ("off", "full", "gentle") if m in hits), "off")


def current_mode(now: datetime, trusted: bool, s: dict) -> tuple[str, str]:
    """(Modus, Quelle) – Quelle: manual | untrusted | schedule"""
    op = s["operation"]
    if op["mode"] != "auto" and (op["until"] is None or op["until"] > now.timestamp()):
        return op["mode"], "manual"
    if not trusted:
        return "gentle", "untrusted"      # ohne verlässliche Uhrzeit immer lieb
    return schedule_mode(now, s["schedule"]), "schedule"


def next_change(now: datetime, schedule: list[dict], horizon_h: int = 48) -> dict | None:
    t = now.replace(second=0, microsecond=0)
    first = schedule_mode(t, schedule)
    for _ in range(horizon_h * 60):
        t += timedelta(minutes=1)
        mode = schedule_mode(t, schedule)
        if mode != first:
            return {"at": t.strftime("%H:%M"), "day": t.date().isoformat(), "mode": mode}
    return None


def schedule_preview(schedule: list[dict], start: date, days: int = 7) -> list[dict]:
    """Je Tag die Abschnitte von 12:00 bis 06:00 am Folgetag – für die Wochenansicht."""
    out = []
    for i in range(days):
        day = start + timedelta(days=i)
        t = datetime.combine(day, datetime.min.time()).replace(hour=12)
        segs: list[dict] = []
        for minute in range(18 * 60):
            mode = schedule_mode(t + timedelta(minutes=minute), schedule)
            if segs and segs[-1]["mode"] == mode:
                segs[-1]["end"] = minute + 1
            else:
                segs.append({"mode": mode, "start": minute, "end": minute + 1})
        out.append({"date": day.isoformat(), "segments": [s for s in segs if s["mode"] != "off"]})
    return out


# ---------------------------------------------------------------- Abendzähler und Gesprächsbudget
class State:
    """Gesprächssekunden und Zähler pro Abend (Abend = Datum von jetzt minus 6 h)."""
    KEYS = ("talk_s", "motion", "stays", "clips", "talks")

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            self.data = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("evenings", {})

    @staticmethod
    def evening(now: datetime) -> str:
        return (now - timedelta(hours=6)).strftime("%Y-%m-%d")

    def today(self, now: datetime) -> dict:
        d = self.data["evenings"].get(self.evening(now), {})
        return {k: d.get(k, 0) for k in self.KEYS}

    def used(self, now: datetime) -> float:
        return float(self.today(now)["talk_s"])

    def left(self, now: datetime, budget_min: int) -> float:
        return max(0.0, budget_min * 60 - self.used(now))

    def add(self, now: datetime, key: str, amount: float = 1) -> None:
        with self._lock:
            evenings = self.data["evenings"]
            d = evenings.setdefault(self.evening(now), {})
            d[key] = round(d.get(key, 0) + amount, 1)
            for old in sorted(evenings)[:-21]:
                del evenings[old]
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(self.path, json.dumps(self.data))
            except OSError as e:
                log(f"Zähler nicht gespeichert: {e}")


# ---------------------------------------------------------------- Audio
def set_volume(percent: int) -> None:
    """Setzt alle Wiedergaberegler der Standard-Soundkarte (ReSpeaker).

    Nur Regler mit pvolume (der Mikrofonregler „Headset“ bleibt unberührt),
    mit Index, weil der ReSpeaker zwei „PCM“-Regler hat."""
    try:
        out = subprocess.run(["amixer", "scontents"], capture_output=True, text=True, timeout=3).stdout
        control = None
        for line in out.splitlines():
            if line.startswith("Simple mixer control"):
                name, index = line.split("'")[1], line.rsplit(",", 1)[1]
                control = f"{name},{index}"
            elif control and "Capabilities:" in line and "pvolume" in line.split():
                subprocess.run(["amixer", "-q", "sset", control, f"{percent}%", "unmute"],
                               capture_output=True, timeout=3)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"Lautstärke nicht gesetzt: {e}")


CLIP_ID = re.compile(r"[\w-]{1,40}")


class Clips:
    """Flüster-Clips: Texte und Schalter in clips.json im Datenordner, Audio als <gruppe>/<id>.mp3."""

    def __init__(self, root: Path, catalog: Path) -> None:
        self.root, self.path = root, catalog
        self._lock = threading.RLock()
        self.busy: set[str] = set()
        try:
            self.data = json.loads(catalog.read_text())
        except FileNotFoundError:
            self.data = self._seed()
        except (OSError, ValueError) as e:
            log(f"Clip-Katalog nicht lesbar ({e}) – lege ihn neu an")
            self.data = self._seed()
        for g in GROUPS:
            self.data.setdefault(g, [])
        self._scan()
        self._save()

    def _seed(self) -> dict:
        """Erster Start: Texte aus clips.json neben dem Programm, bestehende NN.mp3 zugeordnet."""
        try:
            texts = json.loads((HERE / "clips.json").read_text())
        except (OSError, ValueError):
            texts = {}
        data = {}
        for g in GROUPS:
            data[g] = []
            for i, text in enumerate(texts.get(g, []), 1):
                cid = f"{i:02d}"
                made = text if (self.root / g / f"{cid}.mp3").exists() else None
                data[g].append({"id": cid, "text": text, "made": made, "active": True})
        return data

    def _scan(self) -> None:
        """Von Hand abgelegte Audiodateien als eigene Clips aufnehmen."""
        for g in GROUPS:
            known = {c["id"] for c in self.data[g]}
            d = self.root / g
            for f in sorted(d.glob("*")) if d.is_dir() else []:
                if f.suffix.lower() in (".mp3", ".wav") and f.stem not in known and CLIP_ID.fullmatch(f.stem):
                    self.data[g].append({"id": f.stem, "text": "", "made": None, "active": True, "own": True})

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(self.path, json.dumps(self.data, ensure_ascii=False, indent=1))
        except OSError as e:
            log(f"Clip-Katalog nicht gespeichert: {e}")

    def file(self, group: str, cid: str) -> Path | None:
        if group not in GROUPS or not CLIP_ID.fullmatch(cid):
            return None
        for suffix in (".mp3", ".wav"):
            f = self.root / group / f"{cid}{suffix}"
            if f.exists():
                return f
        return None

    def _entry(self, group: str, cid: str) -> dict:
        if group not in GROUPS:
            raise ValueError("Unbekannte Gruppe")
        for c in self.data[group]:
            if c["id"] == cid:
                return c
        raise ValueError("Clip nicht gefunden")

    def files(self, group: str) -> list[Path]:
        with self._lock:
            entries = [c for c in self.data.get(group, []) if c["active"]]
        return [f for c in entries if (f := self.file(group, c["id"]))]

    def listing(self) -> dict:
        with self._lock:
            out = {}
            for g in GROUPS:
                out[g] = []
                for c in self.data[g]:
                    has = self.file(g, c["id"]) is not None
                    out[g].append({
                        "id": c["id"], "text": c["text"], "active": c["active"], "own": c.get("own", False),
                        "file": has, "stale": has and c["made"] is not None and c["made"] != c["text"],
                        "busy": f"{g}/{c['id']}" in self.busy,
                    })
            return out

    def add(self, group: str, text: str) -> str:
        text = text.strip()
        if group not in GROUPS or not text:
            raise ValueError("Text fehlt")
        with self._lock:
            used = {c["id"] for c in self.data[group]}
            n = 1
            while f"{n:02d}" in used or self.file(group, f"{n:02d}"):
                n += 1
            cid = f"{n:02d}"
            self.data[group].append({"id": cid, "text": text[:400], "made": None, "active": True})
            self._save()
            return cid

    def edit(self, group: str, cid: str, text: str | None = None, active: bool | None = None) -> None:
        with self._lock:
            c = self._entry(group, cid)
            if text is not None:
                c["text"] = text.strip()[:400]
            if active is not None:
                c["active"] = bool(active)
            self._save()

    def delete(self, group: str, cid: str) -> None:
        with self._lock:
            c = self._entry(group, cid)
            if f := self.file(group, cid):
                f.unlink()
            self.data[group].remove(c)
            self._save()

    def upload(self, group: str, name: str, data: bytes) -> str:
        if group not in GROUPS:
            raise ValueError("Unbekannte Gruppe")
        suffix = Path(name).suffix.lower()
        if suffix not in (".mp3", ".wav") or not data:
            raise ValueError("Bitte eine MP3- oder WAV-Datei wählen")
        stem = re.sub(r"[^\w-]", "_", Path(name).stem)[:30] or "eigen"
        with self._lock:
            used = {c["id"] for c in self.data[group]}
            cid, n = stem, 2
            while cid in used or self.file(group, cid):
                cid, n = f"{stem}-{n}", n + 1
            (self.root / group).mkdir(parents=True, exist_ok=True)
            (self.root / group / f"{cid}{suffix}").write_bytes(data)
            self.data[group].append({"id": cid, "text": "", "made": None, "active": True, "own": True})
            self._save()
            return cid

    def generate(self, group: str, cid: str) -> None:
        """Erzeugt die Audiodatei mit ElevenLabs (blockiert einige Sekunden)."""
        key = f"{group}/{cid}"
        with self._lock:
            c = self._entry(group, cid)
            text = c["text"]
            if not text:
                raise ValueError("Eigene Aufnahmen haben keinen Text zum Erzeugen")
            if key in self.busy:
                raise ValueError("Wird schon erzeugt")
            self.busy.add(key)
        try:
            out = self.root / group / f"{cid}.mp3"
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            with tmp.open("wb") as fh:
                for chunk in synthesize(text):
                    fh.write(chunk)
            os.replace(tmp, out)
            with self._lock:
                c["made"] = text
                self._save()
            log(f"Clip erzeugt: {group}/{cid}")
        finally:
            self.busy.discard(key)


class ClipPlayer:
    def __init__(self, clips: Clips) -> None:
        self.clips = clips
        self.proc: subprocess.Popen | None = None
        self.last: dict[str, Path] = {}
        self.label = ""

    def playing(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def play(self, group: str, cid: str | None = None) -> bool:
        if cid:
            f = self.clips.file(group, cid)
            return self.play_file(f, f"{group}: {cid}") if f else False
        files = self.clips.files(group) or self.clips.files("gentle") or self.clips.files("approach")
        if not files:
            log(f"Keine Clips für {group} – in der Web-Oberfläche erzeugen")
            return False
        choices = [f for f in files if f != self.last.get(group)] or files
        f = random.choice(choices)
        self.last[group] = f
        return self.play_file(f, f"{group}: {f.stem}")

    def play_file(self, f: Path, label: str) -> bool:
        cmd = ["mpg123", "-q", str(f)] if f.suffix.lower() == ".mp3" else ["aplay", "-q", str(f)]
        return self.play_cmd(cmd, label)

    def play_cmd(self, cmd: list[str], label: str) -> bool:
        self.stop()
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.label = label
            log(f"Clip {label}")
            return True
        except OSError as e:
            log(f"Clip nicht abspielbar: {e}")
            return False

    def stop(self) -> None:
        if self.playing():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def wait(self) -> None:
        if self.proc is not None:
            self.proc.wait()


# ---------------------------------------------------------------- ElevenLabs
def online(host: str = "api.elevenlabs.io", port: int = 443, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def synthesize(text: str):
    """Text-to-Speech mit dem Clip-Modell; Eleven v4 notfalls über die Text-to-Dialogue-API."""
    from elevenlabs.client import ElevenLabs
    if not CFG["api_key"] or not CFG["voice_id"]:
        raise ValueError("ELEVENLABS_API_KEY und ELEVENLABS_VOICE_ID fehlen in /etc/grabstein/grabstein.env")
    client = ElevenLabs(api_key=CFG["api_key"])
    model = CFG["clip_model"]
    try:
        return list(client.text_to_speech.convert(
            CFG["voice_id"], text=text, model_id=model, language_code="de",
            output_format="mp3_44100_128"))
    except Exception as e:  # noqa: BLE001
        log(f"Text-to-Speech mit {model} nicht möglich ({str(e)[:80]}) – versuche Text-to-Dialogue")
        return list(client.text_to_dialogue.convert(
            inputs=[{"text": text, "voice_id": CFG["voice_id"]}], model_id=model,
            output_format="mp3_44100_128"))


class Talk:
    """Ein Gespräch mit dem ElevenLabs-Agenten über Mikrofon und Lautsprecher des ReSpeaker."""

    def __init__(self, mode: str, on_text=None) -> None:
        from elevenlabs.client import ElevenLabs
        from elevenlabs.conversational_ai.conversation import (AudioInterface, Conversation,
                                                               ConversationInitiationData)

        class PipeAudio(AudioInterface):
            """Audio über arecord/aplay statt PortAudio: lässt sich jederzeit sofort beenden.
            (PortAudio blieb beim Stoppen des Aufnahme-Streams am ReSpeaker hängen.)

            Beim ReSpeaker liefert das Mikrofon nur, solange die Wiedergabe läuft – deshalb
            bekommt aplay Stille, wenn Gustav gerade nichts sagt."""
            CHUNK = 4000 * 2  # 250 ms bei 16 kHz, 16 bit mono
            SILENCE = bytes(640)  # 20 ms
            FMT = ["-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"]

            def __init__(inner) -> None:  # noqa: N805
                inner.first = True
                inner.out: queue.Queue[bytes] = queue.Queue()
                inner.stopped = threading.Event()
                inner.lock = threading.Lock()
                inner.procs: list[subprocess.Popen] = []

            def start(inner, input_callback) -> None:  # noqa: N805
                with inner.lock:  # stop() kann vor start() kommen (Ende während des Verbindungsaufbaus)
                    if inner.stopped.is_set():
                        return
                    inner._spawn(input_callback)

            def _spawn(inner, input_callback) -> None:  # noqa: N805
                inner.play = subprocess.Popen(["aplay", *inner.FMT, "--buffer-time=100000"],
                                              stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                inner.rec = subprocess.Popen(["arecord", *inner.FMT], stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL)
                inner.procs = [inner.play, inner.rec]

                def read() -> None:
                    while not inner.stopped.is_set():
                        data = inner.rec.stdout.read(inner.CHUNK)
                        if not data:
                            break
                        try:
                            input_callback(data)
                        except Exception:  # noqa: BLE001 – Verbindung schon zu
                            break

                def write() -> None:
                    while not inner.stopped.is_set():
                        try:
                            audio = inner.out.get(timeout=0.02)
                        except queue.Empty:
                            audio = inner.SILENCE  # aplay blockiert bei vollem Puffer – das gibt den Takt
                        try:
                            inner.play.stdin.write(audio)
                            inner.play.stdin.flush()
                        except (OSError, ValueError):
                            break

                threading.Thread(target=read, daemon=True).start()
                threading.Thread(target=write, daemon=True).start()

            def output(inner, audio: bytes) -> None:  # noqa: N805
                if inner.first:
                    inner.first = False
                    log(f"Erste Antwort nach {time.monotonic() - talk_started:.1f} s")
                inner.out.put(audio)

            def interrupt(inner) -> None:  # noqa: N805
                try:
                    while True:
                        inner.out.get_nowait()
                except queue.Empty:
                    pass

            def stop(inner) -> None:  # noqa: N805
                with inner.lock:
                    if inner.stopped.is_set():
                        return
                    inner.stopped.set()
                for p in inner.procs:
                    p.kill()
                    try:
                        p.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass

        self.ended = threading.Event()
        self.mode = mode
        self.manual = False
        self.started = talk_started = time.monotonic()
        key = CFG["api_key"] or None
        client = ElevenLabs(api_key=key)
        say = on_text or (lambda who, text: None)
        self.conv = Conversation(
            client,
            CFG["agent_id"],
            requires_auth=bool(key),
            audio_interface=PipeAudio(),
            config=ConversationInitiationData(dynamic_variables={"modus": mode}),
            callback_agent_response=lambda t: say("Gustav", t),
            callback_user_transcript=lambda t: say("Besuch", t),
            callback_end_session=self.ended.set,
        )

    def start(self) -> None:
        self.conv.start_session()
        log("Gespräch gestartet")

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def stop(self) -> None:
        if not self.ended.is_set():
            t = threading.Thread(target=self._end, daemon=True)
            t.start()
            t.join(timeout=3)
        self.ended.set()
        log(f"Gespräch beendet nach {self.elapsed():.0f} s")

    def _end(self) -> None:
        try:
            self.conv.end_session()
        except Exception as e:  # noqa: BLE001
            log(f"Gesprächsende: {e}")


def _warm_up() -> None:
    """ElevenLabs-SDK einmal beim Start laden – spart ~1-2 s beim ersten Gespräch."""
    t = time.monotonic()
    try:
        import elevenlabs.client  # noqa: F401
        import elevenlabs.conversational_ai.conversation  # noqa: F401
        log(f"ElevenLabs-SDK geladen ({time.monotonic() - t:.1f} s)")
    except Exception as e:  # noqa: BLE001
        log(f"SDK-Vorladen fehlgeschlagen: {e}")


class Gustav:
    """Systemprompt und erste Nachricht des Agenten; Vorlage ist agent_prompt.md."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.data = json.loads(path.read_text())
        except (OSError, ValueError):
            prompt, first = self._from_markdown()
            self.data = {"prompt": prompt, "first_message": first, "pushed": None}

    @staticmethod
    def _from_markdown() -> tuple[str, str]:
        text = (HERE / "agent_prompt.md").read_text()
        prompt = text.split("```", 2)[1].strip()
        first = next(line.split(">", 1)[1].strip() for line in text.splitlines() if line.strip().startswith(">"))
        return prompt, first

    def save(self, prompt: str, first: str, pushed: bool = False) -> None:
        prompt, first = prompt.strip(), first.strip()
        if not prompt or not first:
            raise ValueError("Prompt und erste Nachricht dürfen nicht leer sein")
        self.data = {"prompt": prompt[:20000], "first_message": first[:1000],
                     "pushed": time.time() if pushed else self.data.get("pushed")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, json.dumps(self.data, ensure_ascii=False, indent=1))


def push_agent(prompt: str, first: str, talk_max_s: int) -> tuple[str, str]:
    """Legt den ElevenLabs-Agenten per API an (oder aktualisiert ihn). -> (agent_id, Hinweis)"""
    from elevenlabs.client import ElevenLabs
    from elevenlabs.types import AgentPlatformSettingsRequestModel, ConversationalConfig
    if not CFG["api_key"] or not CFG["voice_id"]:
        raise ValueError("Erst ELEVENLABS_API_KEY und ELEVENLABS_VOICE_ID in /etc/grabstein/grabstein.env eintragen")
    cfg = ConversationalConfig.model_validate({
        "agent": {
            "first_message": first,
            "language": "de",
            "dynamic_variables": {"dynamic_variable_placeholders": {"modus": "full"}},
            "prompt": {
                "prompt": prompt,
                "built_in_tools": {"end_call": {"type": "system", "name": "end_call", "description": "",
                                                "params": {"system_tool_type": "end_call"}}},
            },
        },
        "tts": {"voice_id": CFG["voice_id"], "model_id": CFG["agent_model"]},
        "conversation": {"max_duration_seconds": talk_max_s},
    })
    privacy = AgentPlatformSettingsRequestModel.model_validate(
        {"privacy": {"record_voice": False, "retention_days": 1}})
    agents = ElevenLabs(api_key=CFG["api_key"]).conversational_ai.agents

    def call(with_privacy: bool) -> str:
        kw = {"conversation_config": cfg, "name": "Grabstein Gustav"}
        if with_privacy:
            kw["platform_settings"] = privacy
        if CFG["agent_id"]:
            agents.update(CFG["agent_id"], **kw)
            return CFG["agent_id"]
        return agents.create(**kw).agent_id

    try:
        return call(True), ""
    except Exception as e:  # noqa: BLE001
        log(f"Datenschutz-Einstellungen nicht übernommen ({e}) – versuche ohne")
        return call(False), "Bitte im ElevenLabs-Dashboard unter Datenschutz die Aufbewahrung selbst kurz stellen."


# ---------------------------------------------------------------- Sensoren
class Sensors:
    def __init__(self) -> None:
        self.pir = self._input(CFG["pir_pin"], "Bewegungsmelder")
        self.radar = self._input(CFG["radar_pin"], "Radar")
        self._motion = threading.Event()
        self.last_motion: float | None = None
        if self.pir is not None:
            self.pir.when_activated = self._on_motion
        self.radar_since: float | None = None
        self.radar_free_since: float | None = time.monotonic()

    @staticmethod
    def _input(pin: str, name: str):
        if not pin:
            return None
        try:
            from gpiozero import DigitalInputDevice
            return DigitalInputDevice(int(pin), pull_up=False)
        except Exception as e:  # noqa: BLE001 – ohne Sensor weiterlaufen, Web bleibt bedienbar
            log(f"{name} an GPIO{pin} nicht nutzbar: {e}")
            return None

    def _on_motion(self) -> None:
        self.last_motion = time.monotonic()
        self._motion.set()

    def motion(self) -> bool:
        """True genau einmal pro Bewegung (steigende Flanke des PIR)."""
        hit = self._motion.is_set()
        self._motion.clear()
        return hit

    def update(self) -> None:
        now = time.monotonic()
        present = bool(self.radar.value) if self.radar is not None else False
        if present:
            self.radar_free_since = None
            if self.radar_since is None:
                self.radar_since = now
        else:
            self.radar_since = None
            if self.radar_free_since is None:
                self.radar_free_since = now

    def stayed(self, seconds: float) -> bool:
        return self.radar_since is not None and time.monotonic() - self.radar_since >= seconds

    def gone(self, seconds: float) -> bool:
        if self.radar is None:
            return True
        return self.radar_free_since is not None and time.monotonic() - self.radar_free_since >= seconds


# ---------------------------------------------------------------- Grabstein (Hauptschleife)
class Grabstein:
    """Hält alles zusammen. Nur die Hauptschleife fasst Lautsprecher und Gespräch an;
    die Web-Oberfläche schickt ihr Befehle über submit()."""

    def __init__(self) -> None:
        data = CFG["data_dir"]
        self.settings = Settings(data / "settings.json")
        self.state = State(data / "state.json")
        self.clips = Clips(CFG["clips_dir"], data / "clips.json")
        self.gustav = Gustav(data / "gustav.json")
        self.player = ClipPlayer(self.clips)
        self.sensors: Sensors | None = None
        self.talk: Talk | None = None
        self.transcript: deque[dict] = deque(maxlen=60)
        self.commands: queue.Queue = queue.Queue()
        self.mode, self.mode_source = "off", "schedule"
        self.trusted = False
        self.net = False
        self.next_clip = self.next_talk = 0.0
        self.started = time.time()
        self._stop = threading.Event()

    # --- für die Web-Oberfläche
    def submit(self, name: str, *args, timeout: float = 6.0) -> tuple[bool, str]:
        done, box = threading.Event(), {}
        self.commands.put((name, args, done, box))
        if not done.wait(timeout):
            return False, "Der Grabstein antwortet gerade nicht"
        return box["ok"], box["msg"]

    def shutdown(self) -> None:
        self._stop.set()

    def on_text(self, who: str, text: str) -> None:
        if self.settings.get()["transcripts"]:
            self.transcript.append({"t": time.time(), "who": who, "text": text})  # nur im Speicher, nicht ins Journal

    # --- Befehle (laufen in der Hauptschleife)
    def _cmd_clip(self, group: str, cid: str | None = None) -> tuple[bool, str]:
        if self.talk is not None:
            return False, "Gerade läuft ein Gespräch"
        if group not in GROUPS:
            return False, "Unbekannte Gruppe"
        if not self.player.play(group, cid):
            return False, "Kein Clip vorhanden"
        self.state.add(datetime.now(), "clips")
        return True, "Clip läuft"

    def _cmd_file(self, path: str, label: str) -> tuple[bool, str]:
        if self.talk is not None:
            return False, "Gerade läuft ein Gespräch"
        return (True, "Wird abgespielt") if self.player.play_file(Path(path), label) else (False, "Abspielen fehlgeschlagen")

    def _cmd_tone(self) -> tuple[bool, str]:
        if self.talk is not None:
            return False, "Gerade läuft ein Gespräch"
        self.player.play_cmd(["speaker-test", "-t", "sine", "-f", "440", "-l", "1", "-s", "1"], "Testton")
        return True, "Testton läuft"

    def _cmd_talk(self) -> tuple[bool, str]:
        if self.talk is not None:
            return False, "Es läuft schon ein Gespräch"
        if not CFG["agent_id"]:
            return False, "ELEVENLABS_AGENT_ID fehlt"
        if not self.net:
            return False, "Keine Verbindung zu ElevenLabs"
        self.player.stop()
        return (True, "Gespräch startet") if self._start_talk("gentle" if self.mode == "gentle" else "full", True) \
            else (False, "Gespräch nicht möglich – siehe Protokoll")

    def _cmd_stop(self) -> tuple[bool, str]:
        self.player.stop()
        if self.talk is not None:
            self._end_talk("von Hand beendet")
        return True, "Gestoppt"

    def _start_talk(self, mode: str, manual: bool = False) -> bool:
        try:
            self.transcript.clear()
            self.talk = Talk(mode, self.on_text)
            self.talk.manual = manual
            self.talk.start()
            self.state.add(datetime.now(), "talks")
            return True
        except Exception as e:  # noqa: BLE001
            log(f"Gespräch nicht möglich: {e}")
            self.talk = None
            return False

    def _end_talk(self, reason: str) -> None:
        s, mono = self.settings.get(), time.monotonic()
        log(f"Gesprächsende: {reason}")
        self.talk.stop()
        self.state.add(datetime.now(), "talk_s", self.talk.elapsed())
        self.talk = None
        if reason in ("Zeitlimit", "Budget aufgebraucht"):
            time.sleep(0.5)
            self.player.play("leave")
        self.next_talk = mono + s["cooldown_talk_s"]
        self.next_clip = mono + s["cooldown_clip_s"]

    # --- Hintergrund: Uhrzeit und Internet prüfen, ohne die Schleife aufzuhalten
    def _watch(self) -> None:
        while not self._stop.is_set():
            if not self.trusted:
                self.trusted = time_trusted()
            self.net = online()
            self._stop.wait(30)

    def run(self) -> None:
        log("Grabstein startet")
        volume = self.settings.get()["volume"]
        set_volume(volume)
        threading.Thread(target=_warm_up, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
        self.sensors = sensors = Sensors()
        last_mode = ""
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())

        while not self._stop.is_set():
            time.sleep(0.1)
            s, mono, now = self.settings.get(), time.monotonic(), datetime.now()

            while True:
                try:
                    name, args, done, box = self.commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    box["ok"], box["msg"] = getattr(self, f"_cmd_{name}")(*args)
                except Exception as e:  # noqa: BLE001
                    box["ok"], box["msg"] = False, str(e)
                done.set()

            if s["volume"] != volume:
                volume = s["volume"]
                set_volume(volume)
            op = s["operation"]
            if op["mode"] != "auto" and op["until"] is not None and op["until"] <= now.timestamp():
                log("Manuelle Betriebsart abgelaufen – zurück zum Zeitplan")
                s = self.settings.update({"operation": {"mode": "auto", "until": None}})
            mode, self.mode_source = current_mode(now, self.trusted, s)
            self.mode = mode
            if mode != last_mode:
                log(f"Modus: {mode}" + ("" if self.trusted else " (Uhrzeit noch nicht synchronisiert)"))
                last_mode = mode
            sensors.update()
            motion = sensors.motion()
            if motion:
                self.state.add(now, "motion")

            # --- laufendes Gespräch
            if self.talk is not None:
                talk, reason = self.talk, ""
                if talk.ended.is_set():
                    reason = "Agent hat aufgelegt"
                elif talk.elapsed() > s["talk_max_s"]:
                    reason = "Zeitlimit"
                elif not talk.manual and sensors.gone(s["leave_s"]):
                    reason = "niemand mehr da"
                elif self.state.left(now, s["talk_budget_min"]) - talk.elapsed() <= 0:
                    reason = "Budget aufgebraucht"
                elif mode == "off" and not talk.manual:
                    reason = "Ende der Aktivzeit"
                if reason:
                    self._end_talk(reason)
                continue

            if mode == "off" or self.player.playing():
                continue

            # --- jemand bleibt stehen
            if sensors.stayed(s["stay_s"]) and mono >= self.next_talk:
                self.state.add(now, "stays")
                can_talk = (s["live_agent"] and CFG["agent_id"] and (mode == "full" or s["talk_gentle"])
                            and self.state.left(now, s["talk_budget_min"]) > 15)
                if can_talk and self.net and self._start_talk(mode):
                    continue
                if self.player.play("gentle" if mode == "gentle" else "stay"):
                    self.state.add(now, "clips")
                self.next_talk = mono + s["cooldown_talk_s"]
                self.next_clip = mono + s["cooldown_clip_s"]
                continue

            # --- jemand kommt vorbei
            if motion and s["approach_clips"] and mono >= self.next_clip:
                if self.player.play("gentle" if mode == "gentle" else "approach"):
                    self.state.add(now, "clips")
                self.next_clip = mono + s["cooldown_clip_s"]

        if self.talk is not None:
            self.talk.stop()
        self.player.stop()
        log("Grabstein beendet")


def run() -> None:
    app = Grabstein()
    try:
        import web
        web.start(app, CFG["web_port"], CFG["web_pin"])
    except Exception as e:  # noqa: BLE001 – ohne Web läuft der Grabstein trotzdem
        log(f"Web-Oberfläche nicht gestartet: {e}")
    app.run()


# ---------------------------------------------------------------- Testbefehle
def cmd_sensoren() -> None:
    s = Sensors()
    print("Sensoren 60 s lang (Strg+C beendet). 1 = erkannt")
    end = time.monotonic() + 60
    last = None
    while time.monotonic() < end:
        state = (int(s.pir.value) if s.pir else "-", int(s.radar.value) if s.radar else "-")
        if state != last:
            print(f"{datetime.now():%H:%M:%S}  Bewegungsmelder: {state[0]}   Radar: {state[1]}", flush=True)
            last = state
        time.sleep(0.05)


def cmd_ton() -> None:
    set_volume(Settings(CFG["data_dir"] / "settings.json").get()["volume"])
    print("1) Testton über den Lautsprecher …", flush=True)
    subprocess.run(["speaker-test", "-t", "sine", "-f", "440", "-l", "1", "-s", "1"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    print("2) Sprich jetzt 4 Sekunden lang ins Mikrofon …", flush=True)
    wav = Path("/tmp/grabstein-test.wav")
    # Das ReSpeaker-Mikrofon liefert nur, solange die Wiedergabe läuft: Stille dazu abspielen.
    silence = subprocess.Popen(["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1", "/dev/zero"],
                               stderr=subprocess.DEVNULL)
    try:
        subprocess.run(["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", "4", str(wav)], timeout=15)
    finally:
        silence.kill()
        silence.wait()
    print("3) Wiedergabe der Aufnahme …", flush=True)
    subprocess.run(["aplay", "-q", str(wav)], timeout=15)
    print("Fertig. Gehört? Dann passen Lautsprecher und Mikrofon.")


def cmd_reden() -> None:
    if not CFG["agent_id"]:
        sys.exit("ELEVENLABS_AGENT_ID fehlt in /etc/grabstein/grabstein.env")
    if not online():
        sys.exit("Keine Verbindung zu ElevenLabs (Internet/WLAN prüfen)")
    s = Settings(CFG["data_dir"] / "settings.json").get()
    set_volume(s["volume"])
    _warm_up()
    t = Talk("full", lambda who, text: log(f"{who}: {text}") if s["transcripts"] else None)
    t.start()
    print(f"Gespräch läuft höchstens {s['talk_max_s']} s (Strg+C beendet) …")
    try:
        while not t.ended.is_set() and t.elapsed() < s["talk_max_s"]:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    t.stop()


def cmd_clips() -> None:
    clips = Clips(CFG["clips_dir"], CFG["data_dir"] / "clips.json")
    force = "--neu" in sys.argv
    for group, entries in clips.listing().items():
        for c in entries:
            if c["own"] or (c["file"] and not c["stale"] and not force):
                continue
            print(f"{group}/{c['id']}: {c['text']}", flush=True)
            clips.generate(group, c["id"])
    print(f"Clips ({CFG['clip_model']}) liegen in {CFG['clips_dir']}")


ENV_FILE = Path("/etc/grabstein/grabstein.env")


def cmd_agent() -> None:
    g = Gustav(CFG["data_dir"] / "gustav.json")
    talk_max = Settings(CFG["data_dir"] / "settings.json").get()["talk_max_s"]
    try:
        agent_id, hint = push_agent(g.data["prompt"], g.data["first_message"], talk_max)
    except ValueError as e:
        sys.exit(str(e))
    if hint:
        print(hint)
    if CFG["agent_id"]:
        print(f"Agent {agent_id} aktualisiert.")
        return
    print(f"Agent angelegt: {agent_id}")
    try:
        lines = ENV_FILE.read_text().splitlines()
        lines = [f"ELEVENLABS_AGENT_ID={agent_id}" if ln.startswith("ELEVENLABS_AGENT_ID=") else ln for ln in lines]
        ENV_FILE.write_text("\n".join(lines) + "\n")
        print(f"ID in {ENV_FILE} eingetragen.")
    except OSError as e:
        print(f"Bitte selbst eintragen: ELEVENLABS_AGENT_ID={agent_id} ({e})")


def cmd_status() -> None:
    now = datetime.now()
    trusted = time_trusted()
    s = Settings(CFG["data_dir"] / "settings.json").get()
    st = State(CFG["data_dir"] / "state.json")
    mode, source = current_mode(now, trusted, s)
    print(f"Uhrzeit synchronisiert: {'ja' if trusted else 'nein'}")
    print(f"Modus jetzt: {mode} ({source})")
    for e in s["schedule"]:
        when = e["date"] or ",".join("Mo Di Mi Do Fr Sa So".split()[d] for d in e["days"])
        print(f"  Zeitplan {when}: {e['from']}–{e['until']} {e['mode']}")
    print(f"Gesprächsbudget heute: {st.used(now)/60:.1f} von {s['talk_budget_min']} min verbraucht")
    print(f"Live-Agent: {'an' if s['live_agent'] and CFG['agent_id'] else 'aus'}  "
          f"Internet: {'ja' if online() else 'nein'}")
    for g, entries in Clips(CFG["clips_dir"], CFG["data_dir"] / "clips.json").listing().items():
        print(f"Clips {g}: {sum(c['file'] and c['active'] for c in entries)} aktiv von {len(entries)}")
    print(f"Web-Oberfläche: http://{socket.gethostname()}.local" + ("" if CFG["web_port"] == 80 else f":{CFG['web_port']}"))


if __name__ == "__main__":
    cmds = {"run": run, "sensoren": cmd_sensoren, "ton": cmd_ton, "reden": cmd_reden,
            "clips": cmd_clips, "status": cmd_status, "agent": cmd_agent}
    name = sys.argv[1] if len(sys.argv) > 1 else "run"
    if name not in cmds:
        sys.exit(__doc__)
    cmds[name]()
