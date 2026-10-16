#!/usr/bin/env python3
"""Flüsternder Grabstein – Halloween-Programm für Raspberry Pi 4 + ReSpeaker XVF3800.

Bewegungsmelder (PIR)  -> „jemand kommt“     -> Flüster-Clip
Radar (LD2410C, OUT)   -> „jemand bleibt“    -> Gespräch mit dem ElevenLabs-Agenten
                                                (oder Clip im Kinder-Modus / ohne Internet)
Radar wieder frei      -> „jemand geht“      -> Gespräch endet

Befehle:
  grabstein run        Normalbetrieb (so läuft der Dienst)
  grabstein sensoren   Sensoren 60 s live anzeigen
  grabstein ton        Lautsprecher und Mikrofon testen
  grabstein reden      Sofort ein Gespräch starten (Test)
  grabstein clips      Flüster-Clips mit ElevenLabs erzeugen
  grabstein agent      ElevenLabs-Agenten anlegen bzw. aktualisieren (aus agent_prompt.md)
  grabstein status     Einstellungen und heutiges Gesprächsbudget anzeigen
"""
from __future__ import annotations

import json
import os
import random
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- Einstellungen
def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def env_time(name: str, default: str) -> tuple[int, int]:
    raw = env(name, default)
    try:
        h, m = raw.split(":")
        return int(h), int(m)
    except ValueError:
        h, m = default.split(":")
        return int(h), int(m)


CFG = {
    "pir_pin": env("PIR_PIN", "17"),
    "radar_pin": env("RADAR_PIN", "22"),
    "active_from": env_time("AKTIV_AB", "16:30"),
    "active_until": env_time("AKTIV_BIS", "22:30"),
    "gentle_until": env_time("KINDER_MODUS_BIS", "19:30"),
    "stay_s": env_int("STEHEN_SEKUNDEN", 4),
    "leave_s": env_int("WEG_SEKUNDEN", 8),
    "cooldown_clip_s": env_int("PAUSE_CLIP_SEKUNDEN", 45),
    "cooldown_talk_s": env_int("PAUSE_GESPRAECH_SEKUNDEN", 120),
    "talk_max_s": env_int("GESPRAECH_MAX_SEKUNDEN", 90),
    "talk_budget_min": env_int("GESPRAECH_BUDGET_MINUTEN", 20),
    "volume": env_int("LAUTSTAERKE", 75),
    "live_agent": env("LIVE_AGENT", "1") == "1",
    "agent_id": env("ELEVENLABS_AGENT_ID", ""),
    "api_key": env("ELEVENLABS_API_KEY", ""),
    "voice_id": env("ELEVENLABS_VOICE_ID", ""),
    "clip_model": env("CLIP_MODELL", "eleven_v4"),
    "agent_model": env("AGENT_MODELL", "eleven_v4_turbo"),
    "clips_dir": Path(env("CLIPS_DIR", str(HERE / "clips"))),
    "state_file": Path(env("STATE_FILE", str(Path.home() / ".grabstein-state.json"))),
    "log_transcripts": env("LOG_TRANSKRIPTE", "0") == "1",
}
GROUPS = ("approach", "stay", "leave", "gentle")


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


# ---------------------------------------------------------------- Uhrzeit und Modus
def time_trusted() -> bool:
    """Ohne Echtzeituhr stimmt die Zeit erst nach NTP-Abgleich."""
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        return out == "yes"
    except (OSError, subprocess.SubprocessError):
        return False


def _minutes(hm: tuple[int, int]) -> int:
    return hm[0] * 60 + hm[1]


def in_window(now: datetime, start: tuple[int, int], end: tuple[int, int]) -> bool:
    m, s, e = now.hour * 60 + now.minute, _minutes(start), _minutes(end)
    return s <= m < e if s <= e else (m >= s or m < e)


def current_mode(now: datetime, trusted: bool) -> str:
    """'off' | 'gentle' | 'full'"""
    if not trusted:
        return "gentle"          # ohne verlässliche Uhrzeit immer lieb
    if not in_window(now, CFG["active_from"], CFG["active_until"]):
        return "off"
    if in_window(now, CFG["active_from"], CFG["gentle_until"]):
        return "gentle"
    return "full"


# ---------------------------------------------------------------- Gesprächsbudget
class Budget:
    """Gesprächssekunden pro Abend (Abend = Datum von jetzt minus 6 h)."""

    def __init__(self, path: Path, minutes: int) -> None:
        self.path, self.limit = path, minutes * 60

    @staticmethod
    def _evening(now: datetime) -> str:
        return (now - timedelta(hours=6)).strftime("%Y-%m-%d")

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def used(self, now: datetime) -> float:
        d = self._load()
        return float(d.get("talk_s", 0)) if d.get("evening") == self._evening(now) else 0.0

    def left(self, now: datetime) -> float:
        return max(0.0, self.limit - self.used(now))

    def add(self, now: datetime, seconds: float) -> None:
        data = {"evening": self._evening(now), "talk_s": round(self.used(now) + seconds, 1)}
        try:
            self.path.write_text(json.dumps(data))
        except OSError as e:
            log(f"Budget nicht gespeichert: {e}")


# ---------------------------------------------------------------- Audio
def set_volume(percent: int) -> None:
    """Setzt alle Wiedergaberegler der Standard-Soundkarte (ReSpeaker)."""
    try:
        out = subprocess.run(["amixer", "scontrols"], capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            if "'" in line:
                name = line.split("'")[1]
                subprocess.run(["amixer", "-q", "sset", name, f"{percent}%", "unmute"],
                               capture_output=True, timeout=3)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"Lautstärke nicht gesetzt: {e}")


class ClipPlayer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.last: dict[str, Path] = {}

    def clips(self, group: str) -> list[Path]:
        d = self.root / group
        return sorted(p for p in d.glob("*") if p.suffix.lower() in (".mp3", ".wav")) if d.is_dir() else []

    def playing(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def play(self, group: str) -> bool:
        files = self.clips(group) or self.clips("gentle") or self.clips("approach")
        if not files:
            log(f"Keine Clips in {self.root}/{group} – erst 'grabstein clips' ausführen")
            return False
        choices = [f for f in files if f != self.last.get(group)] or files
        f = random.choice(choices)
        self.last[group] = f
        self.stop()
        cmd = ["mpg123", "-q", str(f)] if f.suffix.lower() == ".mp3" else ["aplay", "-q", str(f)]
        try:
            self.proc = subprocess.Popen(cmd)
            log(f"Clip {group}: {f.name}")
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


# ---------------------------------------------------------------- ElevenLabs-Gespräch
def online(host: str = "api.elevenlabs.io", port: int = 443, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class Talk:
    """Ein Gespräch mit dem ElevenLabs-Agenten über Mikrofon und Lautsprecher des ReSpeaker."""

    def __init__(self, mode: str) -> None:
        from elevenlabs.client import ElevenLabs
        from elevenlabs.conversational_ai.conversation import Conversation, ConversationInitiationData
        from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface

        class SafeAudio(DefaultAudioInterface):
            """stop() darf mehrfach kommen (eigenes Ende + Auflegen durch den Agenten)."""
            _stopped = False
            _first = True

            def output(inner, audio: bytes) -> None:  # noqa: N805
                if inner._first:
                    inner._first = False
                    log(f"Erste Antwort nach {time.monotonic() - talk_started:.1f} s")
                super().output(audio)

            def stop(self) -> None:
                if self._stopped:
                    return
                self._stopped = True
                try:
                    super().stop()
                except Exception as e:  # noqa: BLE001
                    log(f"Audio-Stopp: {e}")

        self.ended = threading.Event()
        self.started = talk_started = time.monotonic()
        key = CFG["api_key"] or None
        client = ElevenLabs(api_key=key)
        show = CFG["log_transcripts"]
        self.conv = Conversation(
            client,
            CFG["agent_id"],
            requires_auth=bool(key),
            audio_interface=SafeAudio(),
            config=ConversationInitiationData(dynamic_variables={"modus": mode}),
            callback_agent_response=(lambda t: log(f"Geist: {t}")) if show else None,
            callback_user_transcript=(lambda t: log(f"Besuch: {t}")) if show else None,
            callback_end_session=self.ended.set,
        )

    def start(self) -> None:
        self.conv.start_session()
        log("Gespräch gestartet")

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def stop(self) -> None:
        if not self.ended.is_set():
            try:
                self.conv.end_session()
            except Exception as e:  # noqa: BLE001
                log(f"Gesprächsende: {e}")
        self.ended.set()
        log(f"Gespräch beendet nach {self.elapsed():.0f} s")


def _warm_up() -> None:
    """ElevenLabs-SDK einmal beim Start laden – spart ~1-2 s beim ersten Gespräch."""
    t = time.monotonic()
    try:
        import elevenlabs.client  # noqa: F401
        import elevenlabs.conversational_ai.conversation  # noqa: F401
        import elevenlabs.conversational_ai.default_audio_interface  # noqa: F401
        log(f"ElevenLabs-SDK geladen ({time.monotonic() - t:.1f} s)")
    except Exception as e:  # noqa: BLE001
        log(f"SDK-Vorladen fehlgeschlagen: {e}")


# ---------------------------------------------------------------- Sensoren
class Sensors:
    def __init__(self) -> None:
        from gpiozero import DigitalInputDevice
        self.pir = DigitalInputDevice(int(CFG["pir_pin"]), pull_up=False) if CFG["pir_pin"] else None
        self.radar = DigitalInputDevice(int(CFG["radar_pin"]), pull_up=False) if CFG["radar_pin"] else None
        self._motion = threading.Event()
        if self.pir is not None:
            self.pir.when_activated = self._motion.set
        self.radar_since: float | None = None
        self.radar_free_since: float | None = time.monotonic()

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


# ---------------------------------------------------------------- Hauptschleife
def run() -> None:
    log("Grabstein startet")
    set_volume(CFG["volume"])
    threading.Thread(target=_warm_up, daemon=True).start()
    player = ClipPlayer(CFG["clips_dir"])
    sensors = Sensors()
    budget = Budget(CFG["state_file"], CFG["talk_budget_min"])
    talk: Talk | None = None
    next_clip = next_talk = 0.0
    last_mode = ""
    trusted, trusted_checked = False, 0.0
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    while not stop.is_set():
        time.sleep(0.1)
        mono, now = time.monotonic(), datetime.now()
        if not trusted and mono - trusted_checked > 30:
            trusted, trusted_checked = time_trusted(), mono
        mode = current_mode(now, trusted)
        if mode != last_mode:
            log(f"Modus: {mode}" + ("" if trusted else " (Uhrzeit noch nicht synchronisiert)"))
            last_mode = mode
        sensors.update()
        motion = sensors.motion()

        # --- laufendes Gespräch
        if talk is not None:
            reason = ""
            if talk.ended.is_set():
                reason = "Agent hat aufgelegt"
            elif talk.elapsed() > CFG["talk_max_s"]:
                reason = "Zeitlimit"
            elif sensors.gone(CFG["leave_s"]):
                reason = "niemand mehr da"
            elif budget.left(now) - talk.elapsed() <= 0:
                reason = "Budget aufgebraucht"
            elif mode == "off":
                reason = "Ende der Aktivzeit"
            if reason:
                log(f"Gesprächsende: {reason}")
                talk.stop()
                budget.add(now, talk.elapsed())
                talk = None
                if reason in ("Zeitlimit", "Budget aufgebraucht"):
                    time.sleep(0.5)
                    player.play("leave")
                next_talk = mono + CFG["cooldown_talk_s"]
                next_clip = mono + CFG["cooldown_clip_s"]
            continue

        if mode == "off" or player.playing():
            continue

        # --- jemand bleibt stehen
        if sensors.stayed(CFG["stay_s"]) and mono >= next_talk:
            can_talk = (mode == "full" and CFG["live_agent"] and CFG["agent_id"]
                        and budget.left(now) > 15)
            if can_talk and online():
                try:
                    talk = Talk(mode)
                    talk.start()
                    continue
                except Exception as e:  # noqa: BLE001
                    log(f"Gespräch nicht möglich: {e}")
                    talk = None
            player.play("gentle" if mode == "gentle" else "stay")
            next_talk = mono + CFG["cooldown_talk_s"]
            next_clip = mono + CFG["cooldown_clip_s"]
            continue

        # --- jemand kommt vorbei
        if motion and mono >= next_clip:
            player.play("gentle" if mode == "gentle" else "approach")
            next_clip = mono + CFG["cooldown_clip_s"]

    if talk is not None:
        talk.stop()
    player.stop()
    log("Grabstein beendet")


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
    set_volume(CFG["volume"])
    print("1) Testton über den Lautsprecher …")
    subprocess.run(["speaker-test", "-t", "sine", "-f", "440", "-l", "1", "-s", "1"], capture_output=True)
    print("2) Sprich jetzt 4 Sekunden lang ins Mikrofon …")
    wav = Path("/tmp/grabstein-test.wav")
    subprocess.run(["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", "4", str(wav)])
    print("3) Wiedergabe der Aufnahme …")
    subprocess.run(["aplay", "-q", str(wav)])
    print("Fertig. Gehört? Dann passen Lautsprecher und Mikrofon.")


def cmd_reden() -> None:
    if not CFG["agent_id"]:
        sys.exit("ELEVENLABS_AGENT_ID fehlt in /etc/grabstein/grabstein.env")
    if not online():
        sys.exit("Keine Verbindung zu ElevenLabs (Internet/WLAN prüfen)")
    set_volume(CFG["volume"])
    _warm_up()
    t = Talk("full")
    t.start()
    print(f"Gespräch läuft höchstens {CFG['talk_max_s']} s (Strg+C beendet) …")
    try:
        while not t.ended.is_set() and t.elapsed() < CFG["talk_max_s"]:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    t.stop()


def _synthesize(client, text: str):
    """Text-to-Speech mit dem gewählten Modell; Eleven v4 notfalls über die Text-to-Dialogue-API."""
    model = CFG["clip_model"]
    try:
        return list(client.text_to_speech.convert(
            CFG["voice_id"], text=text, model_id=model, language_code="de",
            output_format="mp3_44100_128"))
    except Exception as e:  # noqa: BLE001
        print(f"  Text-to-Speech mit {model} nicht möglich ({str(e)[:80]}) – versuche Text-to-Dialogue")
        return list(client.text_to_dialogue.convert(
            inputs=[{"text": text, "voice_id": CFG["voice_id"]}], model_id=model,
            output_format="mp3_44100_128"))


def cmd_clips() -> None:
    from elevenlabs.client import ElevenLabs
    if not CFG["api_key"] or not CFG["voice_id"]:
        sys.exit("ELEVENLABS_API_KEY und ELEVENLABS_VOICE_ID in /etc/grabstein/grabstein.env eintragen")
    texts = json.loads((HERE / "clips.json").read_text())
    client = ElevenLabs(api_key=CFG["api_key"])
    force = "--neu" in sys.argv
    for group in GROUPS:
        out_dir = CFG["clips_dir"] / group
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, text in enumerate(texts.get(group, []), 1):
            f = out_dir / f"{i:02d}.mp3"
            if f.exists() and not force:
                continue
            print(f"{group}/{f.name}: {text}")
            audio = _synthesize(client, text)
            with f.open("wb") as fh:
                for chunk in audio:
                    fh.write(chunk)
    print(f"Clips ({CFG['clip_model']}) liegen in {CFG['clips_dir']}")


ENV_FILE = Path("/etc/grabstein/grabstein.env")


def _prompt_parts() -> tuple[str, str]:
    """Systemprompt (Codeblock) und erste Nachricht (Zitat) aus agent_prompt.md."""
    text = (HERE / "agent_prompt.md").read_text()
    prompt = text.split("```", 2)[1].strip()
    first = next(line.split(">", 1)[1].strip() for line in text.splitlines() if line.strip().startswith(">"))
    return prompt, first


def cmd_agent() -> None:
    """Legt den ElevenLabs-Agenten per API an (oder aktualisiert ihn) und trägt die ID ein."""
    from elevenlabs.client import ElevenLabs
    from elevenlabs.types import AgentPlatformSettingsRequestModel, ConversationalConfig
    if not CFG["api_key"] or not CFG["voice_id"]:
        sys.exit("Erst ELEVENLABS_API_KEY und ELEVENLABS_VOICE_ID in /etc/grabstein/grabstein.env eintragen")
    prompt, first = _prompt_parts()
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
        "conversation": {"max_duration_seconds": CFG["talk_max_s"]},
    })
    privacy = AgentPlatformSettingsRequestModel.model_validate(
        {"privacy": {"record_voice": False, "retention_days": 1}})
    client = ElevenLabs(api_key=CFG["api_key"])
    agents = client.conversational_ai.agents

    def call(with_privacy: bool):
        kw = {"conversation_config": cfg, "name": "Grabstein Gustav"}
        if with_privacy:
            kw["platform_settings"] = privacy
        if CFG["agent_id"]:
            agents.update(CFG["agent_id"], **kw)
            return CFG["agent_id"]
        return agents.create(**kw).agent_id

    try:
        agent_id = call(True)
    except Exception as e:  # noqa: BLE001
        print(f"Datenschutz-Einstellungen nicht übernommen ({e}) – versuche ohne")
        agent_id = call(False)
        print("Bitte im Dashboard unter Datenschutz die Aufbewahrung selbst kurz stellen.")
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
    b = Budget(CFG["state_file"], CFG["talk_budget_min"])
    print(f"Uhrzeit synchronisiert: {'ja' if trusted else 'nein'}")
    print(f"Modus jetzt: {current_mode(now, trusted)}")
    print(f"Aktiv {CFG['active_from'][0]:02d}:{CFG['active_from'][1]:02d}–"
          f"{CFG['active_until'][0]:02d}:{CFG['active_until'][1]:02d}, "
          f"Kinder-Modus bis {CFG['gentle_until'][0]:02d}:{CFG['gentle_until'][1]:02d}")
    print(f"Gesprächsbudget heute: {b.used(now)/60:.1f} von {CFG['talk_budget_min']} min verbraucht")
    print(f"Live-Agent: {'an' if CFG['live_agent'] and CFG['agent_id'] else 'aus'}  "
          f"Internet: {'ja' if online() else 'nein'}")
    for g in GROUPS:
        print(f"Clips {g}: {len(ClipPlayer(CFG['clips_dir']).clips(g))}")


if __name__ == "__main__":
    cmds = {"run": run, "sensoren": cmd_sensoren, "ton": cmd_ton, "reden": cmd_reden,
            "clips": cmd_clips, "status": cmd_status, "agent": cmd_agent}
    name = sys.argv[1] if len(sys.argv) > 1 else "run"
    if name not in cmds:
        sys.exit(__doc__)
    cmds[name]()
