"""Spotify-Steuerung für die Hintergrundmusik: Anmeldung (PKCE, ohne Client-Secret), Playlist-Suche
und Wiedergabe auf dem Spotify-Connect-Gerät „Grabstein“ (raspotify) – nur Standardbibliothek.

Spotify erlaubt für Heimgeräte als Rücksprung nur Loopback-Adressen. Nach der Anmeldung landet das
Handy deshalb auf einer Fehlerseite unter 127.0.0.1; deren Adresse enthält den Code und wird in der
Web-Oberfläche eingefügt. Danach hält das Refresh-Token die Verbindung dauerhaft.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import grabstein as g

REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = "user-read-playback-state user-modify-playback-state playlist-read-private"
DEVICE_NAME = "Grabstein"


class SpotifyError(Exception):
    pass


class Spotify:
    def __init__(self, client_id: str, path: Path) -> None:
        self.client_id, self.path = client_id, path
        self._lock = threading.Lock()
        self._verifier = self._state = ""
        try:
            self.tokens = json.loads(path.read_text())
        except (OSError, ValueError):
            self.tokens = {}
        # Wiedergabe-Abgleich (läuft im eigenen Thread, damit die Hauptschleife nie auf das Netz wartet)
        self.want: str | None = None   # Playlist-URI, die laufen soll, oder None = pausieren
        self.playing_uri: str | None = None
        self.device_ok = False
        self.track = ""
        self.last_error = ""
        if client_id:
            threading.Thread(target=self._sync_loop, daemon=True, name="spotify").start()

    # ------------------------------------------------------------ Anmeldung
    @property
    def connected(self) -> bool:
        return bool(self.tokens.get("refresh_token"))

    def login_url(self) -> str:
        if not self.client_id:
            raise SpotifyError("SPOTIFY_CLIENT_ID fehlt in /etc/grabstein/grabstein.env")
        self._verifier = secrets.token_urlsafe(64)
        self._state = secrets.token_urlsafe(12)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self._verifier.encode()).digest()).rstrip(b"=").decode()
        return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
            "code_challenge_method": "S256", "code_challenge": challenge, "state": self._state})

    def finish_login(self, pasted_url: str) -> None:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(pasted_url.strip()).query)
        if q.get("error"):
            raise SpotifyError(f"Spotify meldet: {q['error'][0]}")
        code, state = (q.get("code") or [""])[0], (q.get("state") or [""])[0]
        if not code:
            raise SpotifyError("In der Adresse fehlt ?code=… – bitte die ganze Adresse der Fehlerseite kopieren")
        if not self._verifier or state != self._state:
            raise SpotifyError("Anmeldung abgelaufen – bitte noch einmal „Mit Spotify verbinden“")
        self._token({"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                     "code_verifier": self._verifier})
        self._verifier = ""
        g.log("Spotify verbunden")

    def disconnect(self) -> None:
        self.tokens = {}
        self._save()

    def _token(self, form: dict) -> None:
        form = {**form, "client_id": self.client_id}
        req = urllib.request.Request("https://accounts.spotify.com/api/token", method="POST",
                                     data=urllib.parse.urlencode(form).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            data = json.load(urllib.request.urlopen(req, timeout=10))
        except urllib.error.HTTPError as e:
            raise SpotifyError(f"Spotify-Anmeldung fehlgeschlagen: {e.read().decode()[:200]}") from None
        self.tokens["access_token"] = data["access_token"]
        self.tokens["expires"] = time.time() + data.get("expires_in", 3600) - 60
        if data.get("refresh_token"):
            self.tokens["refresh_token"] = data["refresh_token"]
        self._save()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            g.write_atomic(self.path, json.dumps(self.tokens))
            self.path.chmod(0o600)
        except OSError as e:
            g.log(f"Spotify-Anmeldung nicht gespeichert: {e}")

    # ------------------------------------------------------------ Web-API
    def _api(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        if not self.connected:
            raise SpotifyError("Nicht mit Spotify verbunden")
        with self._lock:
            if time.time() > self.tokens.get("expires", 0):
                self._token({"grant_type": "refresh_token", "refresh_token": self.tokens["refresh_token"]})
            token = self.tokens["access_token"]
        url = "https://api.spotify.com/v1" + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else
                                     (b"" if method in ("PUT", "POST") else None),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:200]
            try:
                msg = json.loads(msg)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                pass
            raise SpotifyError(f"Spotify {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            raise SpotifyError(f"Spotify nicht erreichbar: {e.reason}") from None

    @staticmethod
    def _playlist(p: dict) -> dict:
        images = p.get("images") or []
        return {"uri": p["uri"], "name": p["name"], "owner": (p.get("owner") or {}).get("display_name", ""),
                "tracks": (p.get("tracks") or p.get("items") or {}).get("total"),
                "image": images[-1]["url"] if images else ""}

    def search(self, q: str) -> list[dict]:
        data = self._api("GET", "/search", {"q": q, "type": "playlist", "limit": 15})
        return [self._playlist(p) for p in (data.get("playlists") or {}).get("items") or [] if p]

    def my_playlists(self) -> list[dict]:
        data = self._api("GET", "/me/playlists", {"limit": 30})
        return [self._playlist(p) for p in data.get("items") or [] if p]

    def device_id(self) -> str | None:
        devices = (self._api("GET", "/me/player/devices") or {}).get("devices") or []
        return next((d["id"] for d in devices if d.get("name") == DEVICE_NAME), None)

    def play(self, uri: str) -> None:
        dev = self.device_id()
        if not dev:
            raise SpotifyError(f"Gerät „{DEVICE_NAME}“ nicht gefunden – einmal in der Spotify-App auswählen")
        self._api("PUT", "/me/player/play", {"device_id": dev}, {"context_uri": uri})
        for path, state in (("/me/player/shuffle", "true"), ("/me/player/repeat", "context")):
            try:
                self._api("PUT", path, {"state": state, "device_id": dev})
            except SpotifyError:
                pass
        self.playing_uri = uri

    def pause(self) -> None:
        dev = self.device_id()
        if dev:
            try:
                self._api("PUT", "/me/player/pause", {"device_id": dev})
            except SpotifyError as e:
                if "403" not in str(e):  # „schon pausiert“
                    raise
        self.playing_uri = None

    # ------------------------------------------------------------ Abgleich
    def _sync_loop(self) -> None:
        """Bringt die Wiedergabe alle paar Sekunden in den gewünschten Zustand."""
        last_check = 0.0
        while True:
            time.sleep(2)
            if not self.connected:
                continue
            try:
                if time.time() - last_check > 15:
                    last_check = time.time()
                    player = self._api("GET", "/me/player") or {}
                    dev = player.get("device") or {}
                    on_us = dev.get("name") == DEVICE_NAME and player.get("is_playing")
                    item = player.get("item") or {}
                    self.track = (f"{item.get('name', '')} – {', '.join(a['name'] for a in item.get('artists', []))}"
                                  if on_us and item else "")
                    ctx = (player.get("context") or {}).get("uri")
                    self.playing_uri = ctx if on_us else None
                    self.device_ok = self.device_id() is not None
                if self.want and self.want != self.playing_uri:
                    self.play(self.want)
                    last_check = 0.0
                    g.log("Spotify spielt")
                elif not self.want and self.playing_uri:
                    self.pause()
                    g.log("Spotify pausiert")
                self.last_error = ""
            except SpotifyError as e:
                if str(e) != self.last_error:
                    g.log(str(e))
                self.last_error = str(e)
                time.sleep(20)
            except Exception as e:  # noqa: BLE001 – der Abgleich darf nie sterben
                self.last_error = str(e)
                time.sleep(20)
