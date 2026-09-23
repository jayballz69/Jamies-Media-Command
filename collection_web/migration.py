"""Read-only desktop import. Cached membership always requires a fresh Plex sync."""

from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from .store import DEFAULT_SETTINGS, DomainError


def _clean(value):
    return re.sub(r"[^\w]+", " ", str(value or "").casefold()).strip()


def _int(value, default=0):
    try:
        return int(value or default)
    except (ValueError, TypeError, OverflowError):
        return default


def _json(path):
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise DomainError(f"Could not read the legacy {path.name}. Check that it is valid JSON.") from exc
    if not isinstance(value, dict):
        raise DomainError(f"The legacy {path.name} must contain a JSON object.")
    return value


def _url(value):
    value = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in {"http", "https"} and parsed.hostname
                and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment):
            return value
    except ValueError:
        pass
    return ""


def _settings(config, state):
    target = state["settings"]
    count = 0
    repeated = bool(state.get("migration"))
    for key in ("plex_url", "plex_token", "plex_movie_lib", "plex_tv_lib",
                "tautulli_url", "tautulli_key", "radarr_url", "radarr_key",
                "sonarr_url", "sonarr_key", "radarr_root", "sonarr_root",
                "radarr_profile", "sonarr_profile", "trakt_client_id"):
        value = config.get(key)
        if value is None or value == "":
            continue
        current = target.get(key)
        if current not in (None, "") and (repeated or current != DEFAULT_SETTINGS.get(key)):
            continue
        if key.endswith("_url"):
            value = _url(value)
        elif key.endswith("_profile"):
            value = _int(value)
            if value < 1:
                continue
        elif not isinstance(value, str):
            continue
        if value != "" and value != current:
            target[key] = value
            count += 1

    # Do not substitute a different provider for Auto/Copilot or combine keys
    # from two configurations. Only an explicitly selected provider can migrate.
    if not any(target.get(k) for k in ("llm_url", "llm_key", "llm_model")):
        provider = str(config.get("ai_discover_provider") or config.get("ai_provider")
                       or config.get("llm_provider") or "").strip().casefold()
        model = str(config.get("ai_discover_model") or config.get("ai_llm_model")
                    or config.get("llm_model") or "").strip()
        url, key = "", ""
        if provider == "openai":
            url = _url(config.get("openai_base_url") or "https://api.openai.com/v1")
            key = str(config.get("openai_api_key") or "").strip()
        elif provider == "ollama":
            url = _url(config.get("ai_ollama_url") or config.get("ollama_url"))
            if url and not url.endswith("/v1"):
                url += "/v1"
        elif provider in {"openai-compatible", "openai_compatible"}:
            url = _url(config.get("llm_url"))
            key = str(config.get("llm_key") or "").strip()
        if url:
            target.update(llm_url=url, llm_key=key, llm_model=model)
            count += sum(bool(value) for value in (url, key, model))
    target.setdefault("advanced", {}).update(schedule_enabled=False, drift_schedule_enabled=False, auto_publish=False)
    return count


def _library(path):
    if not path.is_file():
        return [], 0
    try:
        # Never call the legacy open_db helper: it applies schema and WAL writes.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            columns = {row[1] for row in db.execute("PRAGMA table_info(media)")}
            required = {"media_type", "title", "release_year", "plex_rating_key"}
            if not required <= columns:
                raise DomainError("The legacy media cache has an unsupported schema. Sync Plex to rebuild the library.")
            allowed = required | {"genres_json", "plot_summary", "plex_view_count", "plex_rating"}
            selected = sorted(allowed & columns)
            raw = [dict(row) for row in db.execute("SELECT " + ",".join(selected) + " FROM media")]
    except sqlite3.Error as exc:
        raise DomainError("Could not read the legacy media cache. Close the desktop app and retry, or import without that cache.") from exc
    rows, skipped, seen = [], 0, set()
    for item in raw:
        key = str(item.get("plex_rating_key") or "").strip()
        kind = str(item.get("media_type") or "").strip().lower()
        title = str(item.get("title") or "").strip()
        if not key.isdecimal() or int(key) < 1 or kind not in {"movie", "show"} or not title or key in seen:
            skipped += 1
            continue
        seen.add(key)
        try:
            genres = json.loads(item.get("genres_json") or "[]")
        except (ValueError, TypeError):
            genres = []
        genres = [g for g in genres if isinstance(g, str)] if isinstance(genres, list) else []
        try:
            rating = float(item.get("plex_rating") or 0)
        except (ValueError, TypeError):
            rating = 0
        rows.append({"id": key, "title": title, "year": _int(item.get("release_year")),
                     "media_type": kind, "library_id": "", "genres": genres,
                     "summary": str(item.get("plot_summary") or ""), "actors": [],
                     "directors": [], "studio": "", "collections": [],
                     "play_count": max(0, _int(item.get("plex_view_count"))),
                     "rating": rating, "has_art": False})
    return rows, skipped


def _identity_terms(config):
    terms = set()
    names = config.get("user_nicknames")
    if isinstance(names, dict):
        for key, value in names.items():
            for text in (key, value):
                if isinstance(text, str) and len(_clean(text)) >= 2:
                    terms.add(_clean(text))
    drift = config.get("weekly_drift")
    if isinstance(drift, dict) and isinstance(drift.get("users"), list):
        terms.update(_clean(u) for u in drift["users"] if isinstance(u, str) and len(_clean(u)) >= 2)
    return terms


def _personal(payload):
    if not isinstance(payload, dict):
        return False
    for key, value in payload.items():
        if key in {"source_user", "user", "user_key", "username", "for_user", "owner_name"} and value:
            return True
        if key in {"lane", "source", "source_family", "mode"} and _clean(str(value).replace("_", " ")) in {"for you", "personal", "personalized", "user"}:
            return True
        if isinstance(value, dict) and _personal(value):
            return True
    return False


def _ledger_exclusions(ledger, identities):
    """Inspect historical provenance locally; retain only comparison sets."""
    personal_names, drift_names = set(), set()

    def visit(value):
        if isinstance(value, list):
            for entry in value:
                visit(entry)
        elif isinstance(value, dict):
            name = _clean(value.get("name") or value.get("collection_name"))
            if name:
                drift_names.add(name)
                if _personal(value):
                    personal_names.add(name)
            for key, entry in value.items():
                if key in {"source_user", "user", "user_key", "username"} and isinstance(entry, str):
                    if len(_clean(entry)) >= 2:
                        identities.add(_clean(entry))
                if isinstance(entry, (dict, list)):
                    visit(entry)

    visit(ledger)
    return personal_names, drift_names


def _temporary(payload):
    marker = payload.get("_weekly_drift")
    ai = payload.get("_ai") if isinstance(payload.get("_ai"), dict) else {}
    return (bool(marker) or bool(ai.get("temporary"))
            or str(ai.get("source") or "").casefold() == "weekly_drift")


def _name_hash(name, media_type):
    return hashlib.sha256((_clean(name) + "\0" + media_type).encode()).hexdigest()


def is_excluded_legacy_shelf(state, name, media_type):
    """Prevent Plex sync from reintroducing an excluded legacy personal shelf."""
    return _name_hash(name, media_type) in state.get("migration", {}).get("excluded_name_hashes", [])


def import_legacy(source_dir: Path, state: dict) -> dict:
    """Import fixed desktop files into the caller's transaction; return safe counts.

    This function neither connects to a provider nor publishes or activates shelves.
    The caller owns committing ``state`` and choosing the explicitly mounted source.
    """
    source_dir = Path(source_dir)
    fixed = ("collection_manager_config.json", "collections_data.json", "media_cache.db")
    if not source_dir.is_dir() or not any((source_dir / f).is_file() for f in fixed):
        raise DomainError("Legacy source was not found. Mount the desktop data folder and include its configuration, collections, or media cache.")
    config = _json(source_dir / fixed[0])
    collections = _json(source_dir / fixed[1])
    ledger = _json(source_dir / "weekly_drift.json")
    cached, skipped_library = _library(source_dir / fixed[2])
    # Prepare changes off-state so a malformed source does not leave partial data
    # even when this helper is used without Store.update.
    target = deepcopy(state)
    setting_count = _settings(config, target)
    identities = _identity_terms(config)
    personal_names, drift_names = _ledger_exclusions(ledger, identities)
    excluded_hashes = set(target.get("migration", {}).get("excluded_name_hashes", []))
    # Ledger rows can be absent from collections_data.json; block those names
    # on the first live Plex sync too, without retaining names or user identities.
    for name in personal_names | drift_names:
        excluded_hashes.update(_name_hash(name, kind) for kind in ("movie", "show"))
    by_match = {}
    for item in cached:
        by_match.setdefault((_clean(item["title"]), item["year"], item["media_type"]), []).append(item)
    existing_keys = {c.get("import_key") for c in target["collections"] if c.get("import_key")}
    existing_names = {(_clean(c.get("name")), c.get("media_type")) for c in target["collections"]}
    counts = {"collections_imported": 0, "collections_existing": 0, "personal_skipped": 0,
              "drift_skipped": 0, "invalid_skipped": 0, "items_matched": 0, "items_missing": 0}
    for name, payload in collections.items():
        if not isinstance(payload, dict) or not str(name).strip() or str(name).startswith("_"):
            counts["invalid_skipped"] += 1
            continue
        normalized = _clean(name)
        named_personal = any(normalized == u or normalized.startswith(u + " picks")
                             or re.search(r"(?:^| )" + re.escape(u) + r" s(?: |$)", normalized)
                             or re.search(r"(?:^| )for " + re.escape(u) + r"(?: |$)", normalized)
                             for u in identities)
        if (_personal(payload) or named_personal or normalized in personal_names
                or normalized == "for you" or normalized.startswith("for you ")):
            excluded_hashes.update(_name_hash(name, kind) for kind in ("movie", "show"))
            counts["personal_skipped"] += 1
            continue
        ai = payload.get("_ai") if isinstance(payload.get("_ai"), dict) else {}
        promoted = str(ai.get("source") or "").casefold() == "weekly_drift_promoted"
        if _temporary(payload) or (normalized in drift_names and not promoted):
            excluded_hashes.update(_name_hash(name, kind) for kind in ("movie", "show"))
            counts["drift_skipped"] += 1
            continue
        kind = str(payload.get("type") or payload.get("media_type") or "").casefold()
        kind = {"movies": "movie", "tv": "show", "shows": "show", "series": "show"}.get(kind, kind)
        if kind not in {"movie", "show"} or not isinstance(payload.get("items"), list):
            counts["invalid_skipped"] += 1
            continue
        import_key = "desktop:" + hashlib.sha256((kind + "\0" + normalized).encode()).hexdigest()
        if import_key in existing_keys or (normalized, kind) in existing_names:
            counts["collections_existing"] += 1
            continue
        owned, missing, seen = [], [], set()
        for entry in payload["items"]:
            if not isinstance(entry, dict):
                continue
            title, year = str(entry.get("title") or "").strip(), _int(entry.get("year"))
            identity = (_clean(title), year, kind)
            if not title or year < 1 or identity in seen:
                continue
            seen.add(identity)
            matches = by_match.get(identity, [])
            if len(matches) == 1:
                owned.append(deepcopy(matches[0]))
            else:
                missing.append({"title": title, "year": year, "media_type": kind,
                                "reason": "Not verified in the legacy Plex cache. Sync your library to check availability."})
        if not owned and not missing:
            counts["invalid_skipped"] += 1
            continue
        rotation = payload.get("_rotation") if isinstance(payload.get("_rotation"), dict) else {}
        description = str(payload.get("description") or payload.get("ai_subtext") or "").strip()
        target["collections"].append({"id": import_key.split(":", 1)[1][:32], "import_key": import_key,
                                      "name": str(name).strip(), "description": description,
                                      "thesis": description, "media_type": kind, "library_id": "",
                                      "items": owned, "missing": missing, "status": "draft",
                                      "origin": "import", "managed": False, "permanent": True,
                                      "rotation_enabled": bool(rotation.get("in_pool", False)),
                                      "plex_id": "", "active": False, "home": False, "needs_sync": True,
                                      "created_at": 0, "updated_at": 0})
        existing_keys.add(import_key)
        existing_names.add((normalized, kind))
        counts["collections_imported"] += 1
        counts["items_matched"] += len(owned)
        counts["items_missing"] += len(missing)
    # Preserve an already synced library, but cached material itself never receives
    # a fresh timestamp or server identity and never replaces richer live rows.
    known_ids = {str(row.get("id")) for row in target["library"]}
    additions = [row for row in cached if row["id"] not in known_ids]
    target["library"].extend(additions)
    if additions or counts["collections_imported"]:
        target["synced_at"] = 0
    report = {**counts, "library_imported": len(additions), "library_skipped": skipped_library,
              "settings_imported": setting_count, "needs_sync": True,
              "automation_enabled": False, "llm_configured": bool(target["settings"].get("llm_url")
                                                                    and target["settings"].get("llm_model"))}
    target["migration"] = {**report, "excluded_name_hashes": sorted(excluded_hashes)}
    state.clear()
    state.update(target)
    return report
