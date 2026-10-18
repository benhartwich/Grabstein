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
    "spotify_client_id": env("SPOTIFY_CLIENT_ID", ""),
}
GROUPS = ("approach", "lure", "stay", "wake", "leave", "sleep", "silent", "gentle")
LURE_PAUSE_S = 20
REWAKE_S = 10  # nach Auflegen wegen Stille: wer stehen bleibt, weckt Gustav einmal so schnell wieder  # Abstand zwischen zwei Lockrufen, wenn jemand zu weit weg stehen bleibt
MODES = ("off", "gentle", "full", "music")  # music = nur Hintergrundmusik, Sensoren ruhen
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
    "near_cm": (50, 600),
    "leave_s": (2, 120),
    "cooldown_clip_s": (0, 900),
    "cooldown_talk_s": (0, 1800),
    "talk_max_s": (20, 600),
    "talk_budget_min": (0, 600),
    "volume": (0, 100),
    "music_volume": (0, 100),
    "music_duck": (0, 100),
}
FLAGS = ("live_agent", "talk_gentle", "approach_clips", "transcripts", "leds", "music_on")
MUSIC_SOURCES = ("tracks", "stream", "spotify")


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
        "near_cm": 150,
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
        "leds": True,
        "codeword": "Kürbiskern",
        "music_on": False,
        "music_source": "tracks",
        "music_stream_url": "",
        "music_volume": 60,
        "music_duck": 25,
        "spotify_playlist": None,
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
    if key == "music_source":
        if value not in MUSIC_SOURCES:
            raise ValueError("Musikquelle unbekannt")
        return value
    if key == "spotify_playlist":
        if not value:
            return None
        uri = str(value.get("uri", ""))
        if not re.fullmatch(r"spotify:playlist:\w{10,40}", uri):
            raise ValueError("Ungültige Playlist")
        return {"uri": uri, "name": str(value.get("name", ""))[:120], "image": str(value.get("image", ""))[:300]}
    if key == "music_stream_url":
        url = str(value or "").strip()
        if url and not re.fullmatch(r"https?://\S{4,500}", url):
            raise ValueError("Stream-Adresse bitte mit http:// oder https://")
        return url
    if key == "codeword":
        word = str(value or "").strip()
        if not 2 <= len(word) <= 30:
            raise ValueError("Codewort: 2 bis 30 Zeichen")
        return word
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
    Überschneiden sich Einträge, gewinnt „aus“, dann „gruselig“, dann „lieb“, dann „nur Musik“."""
    dated = {e["date"] for e in schedule if e["date"]}
    hits = {e["mode"] for e in schedule for d in _entry_days(e, now)
            if e["date"] or d.isoformat() not in dated}
    return next((m for m in ("off", "full", "gentle", "music") if m in hits), "off")


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
        if budget_min <= 0:  # 0 = ohne Limit
            return float("inf")
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

    Nur Regler mit pvolume (der Mikrofonregler „Headset“ bleibt unberührt), mit Index, weil der
    ReSpeaker zwei „PCM“-Regler hat. „Musik“ (softvol aus asound.conf) regelt die Klasse Music."""
    try:
        out = subprocess.run(["amixer", "scontents"], capture_output=True, text=True, timeout=3).stdout
        control = None
        for line in out.splitlines():
            if line.startswith("Simple mixer control"):
                name, index = line.split("'")[1], line.rsplit(",", 1)[1]
                control = f"{name},{index}"
            elif control and not control.startswith("Musik,") and "Capabilities:" in line and "pvolume" in line.split():
                subprocess.run(["amixer", "-q", "sset", control, f"{percent}%", "unmute"],
                               capture_output=True, timeout=3)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"Lautstärke nicht gesetzt: {e}")


class Leds:
    """LED-Ring des ReSpeaker XVF3800 – dieselben USB-Steuerbefehle wie Seeeds xvf_host.py.
    Aus im Ruhezustand, grünes Atmen bei Clips, im Gespräch pulsiert das Grün mit Gustavs Stimme."""
    RESID = 20
    EFFECT, SPEED, COLOR = 12, 15, 16          # Befehls-IDs: Effekt (uint8), Tempo (uint8), Farbe (uint32)
    OFF, BREATH, SINGLE = 0, 1, 3
    GREEN = (0x40, 0xFF, 0x70)

    def __init__(self) -> None:
        self.dev = None
        self.last: tuple | None = None
        try:
            import usb.core
            self.dev = usb.core.find(idVendor=0x2886, idProduct=0x001A)
        except Exception as e:  # noqa: BLE001
            log(f"LED-Ring nicht nutzbar: {e}")
        if self.dev is None:
            log("LED-Ring: ReSpeaker nicht gefunden")

    def _write(self, cmd: int, value: int, size: int) -> None:
        import usb.util
        self.dev.ctrl_transfer(usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                               0, cmd, self.RESID, value.to_bytes(size, "little"), 1000)

    def show(self, effect: int, level: float = 1.0) -> None:
        """effect: OFF, BREATH oder SINGLE; level 0..1 dimmt die Farbe (nur SINGLE)."""
        state = (effect, round(level * 12))  # in Stufen, damit nicht jeder Takt USB-Verkehr macht
        if self.dev is None or state == self.last:
            return
        try:
            if effect != (self.last or (None,))[0]:
                if effect == self.BREATH:
                    self._write(self.SPEED, 2, 1)
                    self._write(self.COLOR, int.from_bytes(bytes(self.GREEN), "big"), 4)
                self._write(self.EFFECT, effect, 1)
            if effect == self.SINGLE:
                r, g, b = (int(c * state[1] / 12) for c in self.GREEN)
                self._write(self.COLOR, (r << 16) | (g << 8) | b, 4)
            self.last = state
        except Exception as e:  # noqa: BLE001 – z. B. fehlende USB-Rechte: einmal melden, dann Ruhe
            log(f"LED-Ring nicht steuerbar ({e}) – udev-Regel fehlt? Siehe install.sh")
            self.dev = None


def tone_file() -> Path:
    """Leiser Testton (1 s, 440 Hz, etwa -16 dB) – speaker-test spielt mit vollem Pegel und zu lange."""
    import math
    import wave
    f = Path("/tmp/grabstein-ton.wav")
    if not f.exists():
        rate = 16000
        frames = bytearray()
        for i in range(rate):
            fade = min(1.0, i / 800, (rate - i) / 800)  # weich ein- und ausblenden
            frames += int(5000 * fade * math.sin(2 * math.pi * 440 * i / rate)).to_bytes(2, "little", signed=True)
        with wave.open(str(f), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
    return f


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
        missing = [g for g in GROUPS if g not in self.data]
        if missing:  # neue Gruppe in einer neueren Version: Vorlagetexte übernehmen
            seed = self._seed()
            for g in missing:
                self.data[g] = seed[g]
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


# ---------------------------------------------------------------- Musik
class MusicLibrary:
    """Eigene und erzeugte Musikstücke als MP3 in einem Ordner; Beschreibungen erzeugter Stücke in tracks.json."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.meta_path = root / "tracks.json"
        self.busy = ""  # Beschreibung des Stücks, das gerade erzeugt wird
        self.version = 0  # zählt bei jeder Änderung hoch, damit der Spieler neu mischt
        try:
            self.meta = json.loads(self.meta_path.read_text())
        except (OSError, ValueError):
            self.meta = {}

    def files(self) -> list[Path]:
        return sorted(f for f in self.root.glob("*") if f.suffix.lower() in (".mp3", ".wav")) if self.root.is_dir() else []

    def listing(self) -> list[dict]:
        return [{"name": f.name, "mb": round(f.stat().st_size / 1e6, 1), "prompt": self.meta.get(f.name, "")}
                for f in self.files()]

    def file(self, name: str) -> Path | None:
        f = self.root / name
        return f if re.fullmatch(r"[\w.-]{1,80}", name) and f in self.files() else None

    def _save_meta(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        write_atomic(self.meta_path, json.dumps(self.meta, ensure_ascii=False, indent=1))

    def _free_name(self, stem: str, suffix: str) -> Path:
        stem = re.sub(r"[^\w-]", "_", stem)[:40].strip("_") or "musik"
        f, n = self.root / f"{stem}{suffix}", 2
        while f.exists():
            f, n = self.root / f"{stem}-{n}{suffix}", n + 1
        return f

    def upload(self, name: str, data: bytes) -> str:
        suffix = Path(name).suffix.lower()
        if suffix not in (".mp3", ".wav") or not data:
            raise ValueError("Bitte eine MP3- oder WAV-Datei wählen")
        self.root.mkdir(parents=True, exist_ok=True)
        f = self._free_name(Path(name).stem, suffix)
        f.write_bytes(data)
        self.version += 1
        return f.name

    def delete(self, name: str) -> None:
        f = self.file(name)
        if not f:
            raise ValueError("Stück nicht gefunden")
        f.unlink()
        self.meta.pop(name, None)
        self._save_meta()
        self.version += 1

    def generate(self, prompt: str, seconds: int) -> str:
        """Erzeugt ein Instrumentalstück mit ElevenLabs Music (blockiert, oft eine Minute und mehr)."""
        from elevenlabs.client import ElevenLabs
        prompt = prompt.strip()[:500]
        if not prompt:
            raise ValueError("Beschreibung fehlt")
        if not CFG["api_key"]:
            raise ValueError("ELEVENLABS_API_KEY fehlt")
        if self.busy:
            raise ValueError("Es wird schon ein Stück erzeugt")
        self.busy = prompt
        try:
            audio = ElevenLabs(api_key=CFG["api_key"]).music.compose(
                prompt=prompt, music_length_ms=max(10, min(300, seconds)) * 1000, force_instrumental=True)
            self.root.mkdir(parents=True, exist_ok=True)
            f = self._free_name("erzeugt-" + "-".join(prompt.split()[:4]), ".mp3")
            tmp = f.with_suffix(".tmp")
            with tmp.open("wb") as fh:
                for chunk in audio:
                    fh.write(chunk)
            os.replace(tmp, f)
            self.meta[f.name] = prompt
            self._save_meta()
            self.version += 1
            log(f"Musik erzeugt: {f.name}")
            return f.name
        finally:
            self.busy = ""


class Music:
    """Hintergrundmusik über das ALSA-Gerät „musik“ (softvol-Regler „Musik“ vor dem dmix-Mischer).
    Eigene Tracks und Web-Streams spielt mpg123; Spotify kommt über raspotify auf dasselbe Gerät.
    Ein- und Ausblenden geschieht nur über den Regler, die Quelle läuft einfach weiter."""
    FADE_S = 2.0

    def __init__(self, library: MusicLibrary, spotify=None) -> None:
        self.library = library
        self.spotify = spotify
        self.proc: subprocess.Popen | None = None
        self.key: tuple | None = None       # was gerade läuft (Quelle, Adresse, Bibliotheksstand)
        self.retry_at = 0.0
        self.level = 0.0                     # aktuelle Lautstärke in %
        self.applied = -1
        self.applied_at = 0.0

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _stop(self) -> None:
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc, self.key = None, None

    def _start(self, key: tuple) -> None:
        source, url, _ = key
        cmd = ["mpg123", "-q", "-o", "alsa", "-a", "musik"]
        if source == "stream":
            cmd += (["-@", url] if re.search(r"\.(m3u8?|pls)(\?|$)", url) else [url])
        else:
            files = self.library.files()
            if not files:
                return
            cmd += ["-Z", *map(str, files)]  # zufällig, endlos
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.key = key
            log("Musik: " + ("Stream " + url if source == "stream" else f"{len(files)} eigene Stücke"))
        except OSError as e:
            log(f"Musik nicht abspielbar: {e}")

    def update(self, s: dict, active: bool, target: float) -> None:
        """Einmal pro Takt: Quelle starten/stoppen, Lautstärke in Richtung target (in %) blenden."""
        mono = time.monotonic()
        want = None
        if s["music_on"] and active and s["music_source"] in ("tracks", "stream"):
            if s["music_source"] == "tracks" or s["music_stream_url"]:
                want = (s["music_source"], s["music_stream_url"], self.library.version)
        if not (s["music_on"] and active):
            target = 0.0
        # Ausschalten erst, wenn ausgeblendet ist – sonst bricht die Musik hart ab
        fading_out = want is None and self.running() and self.level > 0
        if not fading_out and (want != self.key or (want and not self.running())):
            if self.running() or self.key:
                self._stop()
            if want and mono >= self.retry_at:
                self._start(want)
                self.retry_at = mono + 10  # bricht ein Stream ab, frühestens in 10 s neu verbinden
        if fading_out:
            target = 0.0
        if self.spotify is not None:
            # Spotify spielt auf dem Gerät „Grabstein“; pausiert wird erst, wenn ausgeblendet ist
            playlist = (s["spotify_playlist"] or {}).get("uri")
            if s["music_on"] and active and s["music_source"] == "spotify" and playlist:
                self.spotify.want = playlist
            elif self.level <= 0:
                self.spotify.want = None
        step = max(1.0, s["music_volume"] / (self.FADE_S * 10))
        self.level += max(-step, min(step, target - self.level))
        # Regler auch regelmäßig neu setzen: er entsteht erst, wenn das Gerät „musik“ zum ersten Mal
        # geöffnet wird (z. B. durch Spotify), und stünde dann auf 100 %
        if round(self.level) != self.applied or mono - self.applied_at > 3:
            self.applied, self.applied_at = round(self.level), mono
            subprocess.run(["amixer", "-q", "-c", "Array", "sset", "Musik", f"{self.applied}%"],
                           capture_output=True, timeout=3)

    def stop(self) -> None:
        self._stop()
        subprocess.run(["amixer", "-q", "-c", "Array", "sset", "Musik", "0%"], capture_output=True, timeout=3)


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


def _rms(pcm: bytes) -> float:
    samples = memoryview(pcm).cast("h") if len(pcm) % 2 == 0 else memoryview(pcm[:-1]).cast("h")
    return (sum(x * x for x in samples) / max(1, len(samples))) ** 0.5


WRAP_UP = ("ZEIT UM: Antworte noch kurz auf das, was der Besuch gerade gesagt hat. Ist ein Rätsel offen, "
           "verrate jetzt die Lösung. Verabschiede dich dann in einem Satz – sag dabei, dass du jetzt wieder schlafen gehst – und beende das Gespräch "
           "sofort mit dem Werkzeug end_call.")
WRAP_GRACE_S = 30  # so lange darf die Verabschiedung dauern, dann wird hart beendet
# Ohne "interruption": Gustav spricht jeden Satz zu Ende. Sonst hält er sein eigenes Echo
# aus dem Lautsprecher für einen Zwischenruf und bricht ab.
SILENCE_END_S = 25  # antwortet so lange niemand, legt Gustav auf (z. B. Katze statt Mensch, Kind schon weg)
NUDGE_S = 8  # nach so viel Stille fragt Gustav von sich aus nach – Kinder überlegen oft länger
CLIENT_EVENTS = ["audio", "agent_response", "user_transcript", "agent_response_correction", "agent_tool_response"]


class Talk:
    """Ein Gespräch mit dem ElevenLabs-Agenten über Mikrofon und Lautsprecher des ReSpeaker."""

    def __init__(self, mode: str, on_text=None, codeword: str = "Kürbiskern", link: "AgentLink | None" = None,
                 hold=None) -> None:
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
                inner.drain = True  # beim Auflegen durch Gustav erst fertig sprechen lassen
                inner.level = 0.0   # aktuelle Lautstärke von Gustavs Stimme 0..1 (für den LED-Ring)
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
                            if hold and hold():  # Aufwach-Clip läuft noch: Gustav wartet, bis er fertig ist
                                raise queue.Empty
                            audio = inner.out.get(timeout=0.02)
                        except queue.Empty:
                            audio = inner.SILENCE  # aplay blockiert bei vollem Puffer – das gibt den Takt
                        real = audio is not inner.SILENCE
                        try:
                            # in 20-ms-Stücken schreiben: aplay gibt den Takt vor, der Pegel läuft mit
                            for i in range(0, len(audio), len(inner.SILENCE)):
                                piece = audio[i:i + len(inner.SILENCE)]
                                inner.play.stdin.write(piece)
                                inner.play.stdin.flush()
                                inner.level = max(_rms(piece) / 4000, inner.level * 0.75)
                                if real:
                                    talk.last_voice = time.monotonic()
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
                if inner.drain and inner.procs:
                    end = time.monotonic() + 8  # Verabschiedung noch ausspielen
                    while not inner.out.empty() and time.monotonic() < end:
                        time.sleep(0.05)
                    time.sleep(0.4)  # Rest im aplay-Puffer
                with inner.lock:
                    if inner.stopped.is_set():
                        return
                    inner.stopped.set()
                for p in inner.procs:
                    if p.stdin:
                        try:
                            p.stdin.close()
                        except OSError:
                            pass  # Leitung schon zu
                    p.kill()
                    try:
                        p.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass

        self.ended = threading.Event()
        self.mode = mode
        self.manual = False
        self.wrap_since: float | None = None
        self.wrap_reason = ""
        self.audio = PipeAudio()
        self.started = talk_started = time.monotonic()
        talk = self  # für PipeAudio: wann Gustav zuletzt hörbar gesprochen hat
        key = CFG["api_key"] or None
        client = link.client() if link else ElevenLabs(api_key=key)
        say = on_text or (lambda who, text: None)
        self._say = say
        self.last_heard = self.last_voice = self.started
        self.conv = Conversation(
            client,
            CFG["agent_id"],
            requires_auth=bool(key),
            audio_interface=self.audio,
            config=ConversationInitiationData(dynamic_variables={"modus": mode, "codewort": codeword}),
            callback_agent_response=lambda t: say("Gustav", t),
            callback_user_transcript=self._heard,
            callback_end_session=self.ended.set,
        )
        url = link.take() if link and key else None
        if url:  # vorab geholte Adresse: spart den Abruf beim Start
            self.conv._get_signed_url = lambda: url

    def _heard(self, text: str) -> None:
        self.last_heard = time.monotonic()
        self._say("Besuch", text)

    def silent_for(self) -> float:
        """Sekunden, seit zuletzt jemand gesprochen hat – Gustav oder Besuch."""
        return time.monotonic() - max(self.last_heard, self.last_voice)

    def start(self) -> None:
        self.conv.start_session()
        log("Gespräch gestartet")

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def wrap_up(self, reason: str) -> None:
        """Gustav bittet, sich zu verabschieden und selbst aufzulegen – statt mitten im Satz abzubrechen."""
        if self.wrap_since is not None:
            return
        self.wrap_since, self.wrap_reason = time.monotonic(), reason
        log(f"{reason} – Gustav verabschiedet sich")
        try:
            self.conv.send_contextual_update(WRAP_UP)
        except Exception as e:  # noqa: BLE001
            log(f"Abschiedshinweis nicht gesendet: {e}")

    def stop(self) -> None:
        """Sofort beenden (von Hand, Zeitüberschreitung, niemand mehr da)."""
        self.audio.drain = False
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


class AgentLink:
    """Hält einen ElevenLabs-Client und eine frische signierte Gesprächsadresse bereit.
    Beim Gesprächsstart fällt so der Abruf weg (0,2 s, mit kalter Verbindung bis 0,8 s)."""
    MAX_AGE_S = 600  # ElevenLabs lässt signierte Adressen 15 min gelten

    def __init__(self) -> None:
        self._client = None
        self._url: str | None = None
        self._at = 0.0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        if CFG["api_key"] and CFG["agent_id"]:
            threading.Thread(target=self._loop, daemon=True, name="agent-link").start()

    def client(self):
        from elevenlabs.client import ElevenLabs
        if self._client is None:
            self._client = ElevenLabs(api_key=CFG["api_key"] or None)
        return self._client

    def take(self) -> str | None:
        """Adresse abgeben (gilt nur für ein Gespräch); danach sofort eine neue holen."""
        with self._lock:
            url = self._url if time.monotonic() - self._at < self.MAX_AGE_S else None
            self._url = None
        self._wake.set()
        return url

    def _fetch(self) -> str:
        from types import SimpleNamespace
        from elevenlabs.conversational_ai.conversation import Conversation
        # dieselbe Adresse, die das SDK beim Start selbst holen würde (inkl. source/version)
        stub = SimpleNamespace(client=self.client(), agent_id=CFG["agent_id"], environment=None)
        return Conversation._get_signed_url(stub)

    def _loop(self) -> None:
        while True:
            try:
                url = self._fetch()
                with self._lock:
                    self._url, self._at = url, time.monotonic()
                wait = self.MAX_AGE_S - 60
            except Exception as e:  # noqa: BLE001 – ohne Vorrat holt das SDK die Adresse selbst
                log(f"Gesprächsadresse nicht vorab geholt: {str(e)[:80]}")
                wait = 60
            self._wake.wait(wait)
            self._wake.clear()


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
            "dynamic_variables": {"dynamic_variable_placeholders": {"modus": "full", "codewort": "Kürbiskern"}},
            "prompt": {
                "prompt": prompt,
                "built_in_tools": {"end_call": {"type": "system", "name": "end_call", "description": "",
                                                "params": {"system_tool_type": "end_call"}}},
            },
        },
        "tts": {"voice_id": CFG["voice_id"], "model_id": CFG["agent_model"]},
        "conversation": {"max_duration_seconds": talk_max_s + WRAP_GRACE_S + 15, "client_events": CLIENT_EVENTS},
        # Dazwischenreden wird bewusst verworfen: sonst schreibt das Mikrofon Gustavs eigenes Echo als Besuch mit (getestet)
        "turn": {"silence_end_call_timeout": SILENCE_END_S, "turn_timeout": NUDGE_S,
                 "transcribe_on_disabled_interruptions": False},
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
class RadarData:
    """Liest die Datenpakete des LD2410C über die serielle Schnittstelle (Pin 8/10, 256000 Baud):
    Abstand und Signalstärke des bewegten bzw. ruhenden Ziels, etwa zehnmal pro Sekunde."""
    PORT = "/dev/serial0"
    HEAD, TAIL = b"\xf4\xf3\xf2\xf1", b"\xf8\xf7\xf6\xf5"

    def __init__(self) -> None:
        self.state = 0                     # 0 niemand, 1 bewegt, 2 steht, 3 beides
        self.move_cm = self.move_energy = self.still_cm = self.still_energy = self.dist_cm = 0
        self.updated: float | None = None  # monotonic der letzten Messung
        try:
            import serial
            self.port = serial.Serial(self.PORT, 256000, timeout=0.5)
        except Exception as e:  # noqa: BLE001 – ohne Abstand läuft alles über den OUT-Pin weiter
            log(f"Radar-Daten nicht lesbar ({e}) – nur Ja/Nein über den OUT-Pin")
            self.port = None
            return
        threading.Thread(target=self._read, daemon=True, name="radar").start()

    def fresh(self) -> bool:
        return self.updated is not None and time.monotonic() - self.updated < 2

    def _read(self) -> None:
        import struct
        buf = b""
        while True:
            try:
                buf += self.port.read(256)
            except Exception as e:  # noqa: BLE001
                log(f"Radar-Daten abgebrochen: {e}")
                return
            while True:
                i = buf.find(self.HEAD)
                if i < 0:
                    buf = buf[-3:]
                    break
                if len(buf) < i + 6:
                    break
                n = struct.unpack_from("<H", buf, i + 4)[0]
                if n > 64:  # kein gültiges Paket – Kopf weiter hinten suchen
                    buf = buf[i + 4:]
                    continue
                if len(buf) < i + 10 + n:
                    break
                d, buf = buf[i + 6:i + 6 + n], buf[i + 10 + n:]
                if len(d) >= 13 and d[1] == 0xAA:  # Grunddaten
                    self.state = d[2]
                    (self.move_cm, self.move_energy, self.still_cm,
                     self.still_energy, self.dist_cm) = struct.unpack_from("<HBHBH", d, 3)
                    self.updated = time.monotonic()


class Sensors:
    def __init__(self) -> None:
        self.data = RadarData()
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

    def distance_cm(self) -> int | None:
        """Abstand der erkannten Person laut Radar-Daten, None ohne frische Daten oder ohne Ziel."""
        if not self.data.fresh() or not self.data.state:
            return None
        return self.data.dist_cm

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
        self.link = AgentLink()
        self.library = MusicLibrary(CFG["clips_dir"].parent / "musik")
        self.spotify = None
        if CFG["spotify_client_id"]:
            from spotify import Spotify
            self.spotify = Spotify(CFG["spotify_client_id"], data / "spotify.json")
        self.music = Music(self.library, self.spotify)
        self.sensors: Sensors | None = None
        self.talk: Talk | None = None
        self.transcript: deque[dict] = deque(maxlen=60)
        self.commands: queue.Queue = queue.Queue()
        self.mode, self.mode_source = "off", "schedule"
        self.trusted = False
        self.net = False
        self.next_clip = self.next_talk = self.next_lure = 0.0
        self.rewake = True  # einmal pro Besuch: nach Stille-Auflegen schnell wieder weckbar
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
        self.player.play_file(tone_file(), "Testton")
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
            # Aufwach-Clip überbrückt den Verbindungsaufbau; Gustavs erste Worte warten, bis er fertig ist
            woke = not self.player.playing() and self.player.play("wake")
            self.talk = Talk(mode, self.on_text, self.settings.get()["codeword"], self.link,
                             hold=self.player.playing if woke else None)
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
        manual = self.talk.manual
        self.talk.stop()
        self.state.add(datetime.now(), "talk_s", self.talk.elapsed())
        self.talk = None
        # Hörbares Ende: sonst merkt niemand, dass Gustav wieder schläft
        if reason.startswith(("Zeitlimit", "Budget")):  # Gustav hat sich nicht selbst verabschiedet
            group = "leave"
        elif reason in ("keine Antwort mehr", "niemand mehr da"):
            group = "silent"
        elif reason == "von Hand beendet":
            group = None
        else:
            group = "sleep"
        if group:
            time.sleep(0.5)
            self.player.play(group)
        self.next_talk = mono + s["cooldown_talk_s"]
        self.next_clip = mono + s["cooldown_clip_s"]
        if reason == "keine Antwort mehr" and self.rewake and not manual:
            self.rewake = False  # bleibt jemand stehen, wacht Gustav gleich wieder auf (nicht endlos, z. B. bei Katzen)
            self.next_talk = mono + REWAKE_S
            log(f"Wieder weckbar in {REWAKE_S} s")

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
        leds = Leds()
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
            if self.talk is not None:
                music_target = 0.0
            elif self.player.playing():
                music_target = s["music_volume"] * s["music_duck"] / 100
            else:
                music_target = s["music_volume"]
            self.music.update(s, mode != "off", music_target)
            if not s["leds"]:
                leds.show(Leds.OFF)
            elif self.talk is not None:
                leds.show(Leds.SINGLE, 0.12 + 0.88 * min(1.0, self.talk.audio.level))
            elif self.player.playing():
                leds.show(Leds.BREATH)
            else:
                leds.show(Leds.OFF)
            sensors.update()
            if sensors.gone(s["leave_s"]):
                self.rewake = True
                # Gesprächspause gilt nur für dieselben Besucher: sind alle weg, darf der Nächste gleich reden
                self.next_talk = min(self.next_talk, mono)
            motion = sensors.motion()
            if motion:
                self.state.add(now, "motion")

            # --- laufendes Gespräch
            if self.talk is not None:
                talk, reason = self.talk, ""
                if talk.ended.is_set():
                    reason = ("Gustav hat sich verabschiedet" if talk.wrap_since else
                              "keine Antwort mehr" if talk.silent_for() >= SILENCE_END_S - 2 else "Agent hat aufgelegt")
                elif talk.wrap_since is not None and mono - talk.wrap_since > WRAP_GRACE_S:
                    reason = f"{talk.wrap_reason} (Verabschiedung zu lang)"
                elif not talk.manual and sensors.gone(s["leave_s"]):
                    reason = "niemand mehr da"
                elif talk.elapsed() > s["talk_max_s"]:
                    talk.wrap_up("Zeitlimit")
                elif self.state.left(now, s["talk_budget_min"]) - talk.elapsed() <= 0:
                    talk.wrap_up("Budget aufgebraucht")
                elif mode in ("off", "music") and not talk.manual:
                    reason = "Ende der Aktivzeit"
                if reason:
                    self._end_talk(reason)
                continue

            if mode in ("off", "music") or self.player.playing():
                continue

            # --- jemand bleibt stehen, aber zu weit weg: anlocken
            dist = sensors.distance_cm()
            if sensors.stayed(s["stay_s"]) and dist is not None and dist > s["near_cm"]:
                if mono >= self.next_lure:
                    if self.player.play("lure"):
                        self.state.add(now, "clips")
                    self.next_lure = mono + LURE_PAUSE_S
                continue

            # --- jemand bleibt nah genug stehen
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
        self.music.stop()
        leds.show(Leds.OFF)
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
    subprocess.run(["aplay", "-q", str(tone_file())], timeout=15)
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
    t = Talk("full", lambda who, text: log(f"{who}: {text}") if s["transcripts"] else None, s["codeword"])
    t.start()
    print(f"Gespräch läuft höchstens {s['talk_max_s']} s (Strg+C beendet) …")
    try:
        while not t.ended.is_set() and t.elapsed() < s["talk_max_s"] + WRAP_GRACE_S:
            if t.elapsed() > s["talk_max_s"]:
                t.wrap_up("Zeitlimit")
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
