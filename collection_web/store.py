"""Transactional, private runtime state, separate from the desktop installation."""

from __future__ import annotations

import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


DEFAULT_SETTINGS = {
    "plex_url": "", "plex_token": "", "plex_movie_lib": "", "plex_tv_lib": "",
    "tautulli_url": "", "tautulli_key": "", "radarr_url": "", "radarr_key": "",
    "sonarr_url": "", "sonarr_key": "", "radarr_root": "", "sonarr_root": "",
    "radarr_profile": 1, "sonarr_profile": 1, "trakt_client_id": "",
    "llm_url": "", "llm_key": "", "llm_model": "",
    "drift_interval_hours": 48, "drift_generation_hours": 168, "drift_pool_size": 12, "rotation_hours": 24,
    "permanent_movie_slots": 2, "permanent_show_slots": 2, "drift_slots": 4, "drift_tv_slots": 2, "library_sync_minutes": 10,
    "advanced": {"watch_inspiration": True, "schedule_enabled": False, "drift_schedule_enabled": False,
                 "auto_publish": False, "home_enabled": True, "shared_home": False,
                 "missing_suggestions": True, "diversity": True, "sync_enabled": True},
}
SECRETS = {"plex_token", "tautulli_key", "radarr_key", "sonarr_key", "llm_key", "trakt_client_id"}


class DomainError(Exception):
    """Safe, user-facing error without connection strings or credentials."""


def event(state, message, level="info"):
    state["activity"].insert(0, {"id": uuid.uuid4().hex, "time": time.time(),
                                  "level": level, "message": message})
    state["activity"] = state["activity"][:250]


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.directory.chmod(0o700)
        self.path = self.directory / "collection-manager.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            state = {"schema": 1, "settings": copy.deepcopy(DEFAULT_SETTINGS), "collections": [],
                     "library": [], "synced_at": 0, "server_id": "", "jobs": [], "activity": [],
                     "drift": {"diagnostics": {}, "last_generated_at": 0, "opportunities": []},
                     "history": [], "last_rotation_at": 0, "last_schedule_attempt": 0}
            db.execute("INSERT OR IGNORE INTO state VALUES (1, ?)", (json.dumps(state),))
            saved = json.loads(db.execute("SELECT payload FROM state WHERE id=1").fetchone()[0])
            for key, value in DEFAULT_SETTINGS.items():
                saved["settings"].setdefault(key, copy.deepcopy(value))
            for key, value in DEFAULT_SETTINGS["advanced"].items():
                saved["settings"]["advanced"].setdefault(key, value)
            saved["settings"].pop("home_slots", None)
            saved["drift"].setdefault("active_batch_ids", [])
            saved["drift"].setdefault("last_activated_at", 0)
            db.execute("UPDATE state SET payload=? WHERE id=1", (json.dumps(saved),))
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def read(self):
        with self.connect() as db:
            return json.loads(db.execute("SELECT payload FROM state WHERE id=1").fetchone()[0])

    def update(self, change):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT payload FROM state WHERE id=1").fetchone()[0])
            result = change(state)
            db.execute("UPDATE state SET payload=? WHERE id=1", (json.dumps(state, ensure_ascii=False),))
            return result

    def backup(self):
        destination = self.directory / "backups"
        destination.mkdir(exist_ok=True)
        path = destination / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6] + ".sqlite3")
        target = sqlite3.connect(path)
        try:
            with self.connect() as source:
                source.backup(target)
        finally:
            target.close()
        if os.name != "nt":
            path.chmod(0o600)
        return path


def public_settings(settings):
    result = {key: value for key, value in settings.items() if key not in SECRETS}
    result.update({"has_" + key: bool(settings.get(key)) for key in SECRETS})
    return result


def public_state(state):
    result = {key: value for key, value in state.items()
              if key not in {"settings", "library", "server_id", "history"}}
    result["settings"] = public_settings(state["settings"])
    if "migration" in result:
        result["migration"] = {key: value for key, value in result["migration"].items() if key != "excluded_name_hashes"}
    result["library"] = {"count": len(state["library"]),
                         "movies": sum(x["media_type"] == "movie" for x in state["library"]),
                         "shows": sum(x["media_type"] == "show" for x in state["library"]),
                         "synced_at": state["synced_at"]}
    result["setup_required"] = not bool(state["settings"].get("plex_token"))
    result["demo"] = False
    return result
