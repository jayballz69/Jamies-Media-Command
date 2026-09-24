"""Authenticated same-origin API and locally served web interface."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import timedelta
import io
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time
import uuid
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, request, send_file, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import HTTPException

from . import integrations as providers
from .service import Service, collection_by_id, create_candidate, match_titles, validate_settings
from .store import DomainError, Store, event, public_state


def private_file(path, default):
    if not path.exists():
        path.write_text(default, encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip()


def create_app(data_dir=None, password=None, start_scheduler=False, legacy_dir=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    store = Store(data_dir or os.environ.get("CM_DATA_DIR", ".web-data"))
    key = private_file(store.directory / ".session-key", secrets.token_hex(48))
    password = password or os.environ.get("CM_PASSWORD")
    if os.environ.get("CM_PASSWORD_FILE") and not password:
        password = Path(os.environ["CM_PASSWORD_FILE"]).read_text(encoding="utf-8").strip()
    # Browser-created account lives outside exportable application state.
    with store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, password_hash TEXT NOT NULL, revision TEXT NOT NULL)")
    def account():
        with store.connect() as db:
            row = db.execute("SELECT username,password_hash,revision FROM account WHERE id=1").fetchone()
        return row

    app.config.update(SECRET_KEY=key, MAX_CONTENT_LENGTH=2 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      SESSION_COOKIE_SECURE=os.environ.get("CM_SECURE_COOKIE") == "1",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    password_hash = generate_password_hash(password) if password else None
    service = Service(store, legacy_dir or os.environ.get("CM_LEGACY_DIR"))
    app.extensions.update(collection_store=store, collection_service=service)
    attempts, login_lock = defaultdict(deque), threading.Lock()
    artwork_locks = defaultdict(threading.Lock)

    @app.before_request
    def authorize():
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            expected_origin = (os.environ.get("CM_PUBLIC_ORIGIN") or request.host_url).rstrip("/")
            if origin and origin != expected_origin:
                return jsonify(error="The request came from a different website."), 403
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(error="Cross-site requests are not accepted."), 403
        if not request.path.startswith("/api/") or request.path in {"/api/login", "/api/session", "/api/setup"}:
            return None
        saved = account()
        if session.get("authenticated") and saved and session.get("account_revision") != saved[2]:
            session.clear()
        if not session.get("authenticated") or (not saved and not password_hash):
            return jsonify(error="Sign in to continue."), 401
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            token = request.headers.get("X-CSRF-Token", "")
            if not token or not secrets.compare_digest(token, session.get("csrf", "")):
                return jsonify(error="Your session changed. Reload the page and try again."), 403

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.errorhandler(DomainError)
    def domain_error(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(Exception)
    def unexpected_error(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        logging.getLogger(__name__).error("API failure (%s)", type(error).__name__)
        return jsonify(error="The request could not be completed. Check Activity and try again."), 500

    def body():
        value = request.get_json()
        if not isinstance(value, dict):
            raise DomainError("Send a JSON object.")
        return value

    def job(kind, operation):
        return jsonify(service.submit(kind, operation)), 202

    def idle():
        if service.busy:
            raise DomainError("Wait for the current task to finish before changing this.")

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("favicon.ico")

    @app.get("/healthz")
    def health():
        with store.connect() as database:
            database.execute("SELECT id FROM state WHERE id=1").fetchone()
        return jsonify(status="ok")

    @app.get("/api/session")
    def session_state():
        saved = account()
        if session.get("authenticated") and (not saved and not password_hash or saved and session.get("account_revision") != saved[2]):
            session.clear()
        session.setdefault("csrf", secrets.token_urlsafe(32))
        return jsonify(authenticated=bool(session.get("authenticated")), csrf=session["csrf"],
                       account_setup_required=not saved and not password_hash,
                       setup_required=not bool(store.read()["settings"].get("plex_token")))

    @app.post("/api/setup")
    def setup_account():
        if account() or password_hash:
            return jsonify(error="An account already exists. Sign in instead."), 409
        token = request.headers.get("X-CSRF-Token", "")
        if not token or not secrets.compare_digest(token, session.get("csrf", "")):
            return jsonify(error="Reload the setup page and try again."), 403
        payload = body()
        username, chosen = payload.get("username", ""), payload.get("password", "")
        if not isinstance(username, str) or not 1 <= len(username.strip()) <= 64:
            raise DomainError("Choose a username between 1 and 64 characters.")
        if not isinstance(chosen, str) or not 12 <= len(chosen) <= 1024:
            raise DomainError("Choose a password with at least 12 characters.")
        if chosen != payload.get("confirm_password"):
            raise DomainError("The passwords do not match.")
        revision = secrets.token_hex(24)
        hashed = generate_password_hash(chosen)
        with store.connect() as db:
            inserted = db.execute("INSERT OR IGNORE INTO account VALUES (1,?,?,?)", (username.strip(), hashed, revision)).rowcount
        if not inserted:
            return jsonify(error="An account already exists. Sign in instead."), 409
        session.clear()
        session.update(authenticated=True, account_revision=revision, csrf=secrets.token_urlsafe(32))
        session.permanent = True
        return jsonify(authenticated=True, csrf=session["csrf"]), 201

    @app.post("/api/login")
    def login():
        if not request.is_json:
            return jsonify(error="Send a JSON sign-in request."), 415
        address = request.remote_addr or "unknown"
        with login_lock:
            now = time.monotonic()
            recent = attempts[address]
            while recent and now - recent[0] > 300:
                recent.popleft()
            if len(recent) >= 10:
                return jsonify(error="Too many sign-in attempts. Wait five minutes."), 429
            recent.append(now)
        payload = body()
        provided, username = payload.get("password", ""), payload.get("username", "")
        saved = account()
        valid_password = isinstance(provided, str) and len(provided) <= 1024
        if saved:
            valid_password = valid_password and check_password_hash(saved[1], provided)
            valid_password = valid_password and isinstance(username, str) and username.strip() == saved[0]
        else:
            valid_password = valid_password and bool(password_hash) and check_password_hash(password_hash, provided)
        if not valid_password:
            return jsonify(error="The username or password is incorrect."), 401
        session.clear()
        session.update(authenticated=True, account_revision=saved[2] if saved else "configured", csrf=secrets.token_urlsafe(32))
        session.permanent = True
        with login_lock:
            attempts.pop(address, None)
        return jsonify(authenticated=True, csrf=session["csrf"])

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/state")
    def state():
        return jsonify(public_state(store.read()))

    @app.post("/api/settings")
    def settings():
        idle()
        payload = body()
        def apply(current):
            current["settings"] = validate_settings(payload, current["settings"])
            event(current, "Settings saved.")
        store.update(apply)
        return jsonify(ok=True)

    @app.post("/api/connections/test")
    def test_connection():
        name = body().get("service")
        def run(progress):
            providers.test_connection(store.read()["settings"], name)
            return str(name).title() + " connected successfully."
        return job("Test connection", run)

    @app.get("/api/library")
    def library():
        query = request.args.get("q", "").casefold()
        return jsonify(items=[row for row in store.read()["library"] if query in row["title"].casefold()][:100])

    @app.get("/api/connections/<name>/options")
    def connection_options(name):
        if name not in {"radarr", "sonarr"}:
            raise DomainError("Choose Radarr or Sonarr.")
        settings = store.read()["settings"]
        roots = providers.arr_request(settings, name, "GET", "/rootfolder")
        profiles = providers.arr_request(settings, name, "GET", "/qualityprofile")
        return jsonify(roots=[{"path": row["path"]} for row in roots],
                       profiles=[{"id": row["id"], "name": row["name"]} for row in profiles])

    @app.post("/api/collections/review")
    def review_permanent_collections():
        return job("Review permanent collections", service.sweep_collections)

    @app.post("/api/requests/refresh")
    def refresh_requests():
        return job("Refresh request progress", service.refresh_requests)

    @app.post("/api/arrivals/review")
    def review_arrivals():
        return job("Review new arrivals", service.review_new_arrivals)

    @app.post("/api/arrivals/<identity>/<action>")
    def arrival_action(identity, action):
        return job("Review arrival suggestion", lambda progress: service.act_on_arrival(identity, action, progress))

    @app.post("/api/library/sync")
    def sync():
        return job("Sync library", service.sync)

    @app.post("/api/drift/generate")
    def generate():
        return job("Generate Drift", service.generate)

    @app.post("/api/drift/switch")
    def switch_drift():
        return job("Switch Drift shelves", service.switch_drift)

    @app.post("/api/drift/activate")
    def activate_drift():
        return job("Activate Drift", service.activate_drift)

    @app.post("/api/rotation/run")
    def rotate():
        return job("Rotate Plex Home", service.rotate)

    @app.post("/api/collections")
    def create_collection():
        idle()
        payload = body()
        if "text" in payload:
            from .discovery import parse_collection_text
            payload.update(parse_collection_text(payload["text"], payload.get("name", "")))
        candidate = create_candidate(payload, store.read()["library"])
        def apply(current):
            current["collections"].append(candidate)
            event(current, "Created draft: " + candidate["name"])
        store.update(apply)
        return jsonify(candidate), 201

    @app.post("/api/collections/discover")
    def discover_collection():
        payload = body()
        return job("Create collection idea", lambda progress: service.discover(payload, progress))

    @app.post("/api/collections/describe")
    def describe_collections():
        payload = body()
        return job("Explain collections", lambda progress: service.describe(payload, progress))

    @app.patch("/api/collections/<identity>")
    def edit_collection(identity):
        idle()
        payload = body()
        if any(key not in {"name", "description", "titles", "rotation_enabled", "home"} for key in payload):
            raise DomainError("That collection setting cannot be changed here.")
        def apply(current):
            candidate = collection_by_id(current, identity)
            if "home" in payload:
                raise DomainError("Use Rotate now to change Plex Home.")
            if "rotation_enabled" in payload:
                if not isinstance(payload["rotation_enabled"], bool):
                    raise DomainError("Rotation must be on or off.")
                if not candidate.get("managed") or candidate["status"] != "published":
                    raise DomainError("Publish this collection or choose Manage rotation here first.")
                candidate["rotation_enabled"] = payload["rotation_enabled"]
            for key in {"name", "description"} & payload.keys():
                if candidate["status"] == "published":
                    raise DomainError("Create an improvement draft to edit a published collection.")
                if candidate["origin"] == "drift":
                    raise DomainError("Drift names are reviewed with their items. Create an improvement draft to change this shelf.")
                value = payload[key]
                limit = 100 if key == "name" else 2000
                if not isinstance(value, str) or (key == "name" and len(value.strip()) < 2) or len(value) > limit:
                    raise DomainError("Invalid collection " + key + ".")
                candidate[key] = value.strip()
            if "titles" in payload:
                if candidate["status"] == "published" or candidate["origin"] == "drift":
                    raise DomainError("Create an improvement draft to edit these titles.")
                revised = create_candidate({"name": candidate["name"], "media_type": candidate["media_type"],
                    "titles": payload["titles"]}, current["library"])
                previous = {(providers.clean(row["title"]), row["year"]): row
                            for row in candidate["items"] + candidate["missing"]}
                retained = [previous.get((providers.clean(row["title"]), row["year"]), row)
                            for row in revised["items"] + revised["missing"]]
                candidate["items"], candidate["missing"] = match_titles(retained, current["library"], candidate["media_type"])
                candidate["manual_reviewed"] = True
                if candidate.get("source_collection_id"):
                    source = collection_by_id(current, candidate["source_collection_id"])
                    candidate["changes"] = {
                        "added": [i["title"] for i in candidate["items"] if i["id"] not in {x["id"] for x in source["items"]}],
                        "removed": [i["title"] for i in source["items"] if i["id"] not in {x["id"] for x in candidate["items"]}]}
            return candidate
        return jsonify(store.update(apply))

    @app.post("/api/collections/<identity>/keep")
    def keep_collection(identity):
        idle()
        def apply(current):
            candidate = collection_by_id(current, identity)
            if candidate["status"] == "archived":
                if candidate.get("origin") != "drift":
                    raise DomainError("This shelf is retired.")
                candidate.update(status="published" if candidate.get("plex_id") else "kept", home=False)
                candidate["rotation_enabled"] = bool(candidate.get("plex_id"))
            candidate["permanent"] = True
            if candidate["status"] == "draft":
                candidate["status"] = "kept"
            event(current, "Added " + candidate["name"] + " to the permanent collection pool.")
            return candidate
        return jsonify(store.update(apply))

    @app.post("/api/collections/<identity>/<action>")
    def collection_action(identity, action):
        collection_by_id(store.read(), identity)
        if action == "publish":
            return job("Publish collection", lambda progress: service.publish(identity, progress))
        if action == "archive":
            return job("Retire collection", lambda progress: service.archive(identity, progress))
        if action == "improve":
            payload = body()
            return job("Improve collection", lambda progress: service.improve(identity, progress, payload))
        if action == "metadata":
            return job("Check suggestion metadata", lambda progress: service.refresh_metadata(identity, progress))
        if action == "describe":
            return job("Explain collection", lambda progress: service.describe({"ids": [identity]}, progress))
        if action == "apply":
            return job("Apply improvements", lambda progress: service.apply_improvement(identity, progress))
        if action == "request":
            payload = body()
            return job("Request missing titles", lambda progress: service.request_missing(identity, payload, progress))
        if action == "add":
            payload = body()
            return job("Add owned suggestions", lambda progress: service.add_available(identity, payload, progress))
        if action == "adopt":
            def adopt(progress):
                current = store.read()
                candidate = collection_by_id(current, identity)
                if candidate["status"] != "published" or not candidate.get("plex_id"):
                    raise DomainError("Publish this draft first.")
                server = providers.plex(current["settings"])
                if server.machineIdentifier != current["server_id"]:
                    raise DomainError("Sync the library on the configured Plex server first.")
                collection = server.fetchItem(int(candidate["plex_id"]))
                if collection.type != "collection" or collection.title != candidate["name"]:
                    raise DomainError("This Plex collection changed. Sync and retry.")
                store.backup()
                collection.addLabel(providers.ownership_label(candidate))
                store.update(lambda state: collection_by_id(state, identity).update(managed=True, rotation_enabled=True))
                return "Added " + candidate["name"] + " to managed rotation."
            return job("Manage rotation", adopt)
        raise DomainError("Unknown collection action.")

    @app.post("/api/import")
    def import_desktop():
        return job("Import desktop collections", service.import_legacy)

    @app.post("/api/drift/opportunities/<identity>/save")
    def save_opportunity(identity):
        idle()
        def apply(current):
            existing = next((c for c in current["collections"] if c.get("opportunity_id") == identity), None)
            if existing:
                return existing
            idea = next((i for i in current["drift"]["opportunities"] if i["id"] == identity), None)
            if idea is None:
                raise DomainError("That idea is no longer available.")
            candidate = {"id": uuid.uuid4().hex, "opportunity_id": identity, "name": (idea.get("name") or idea["concept"])[:100],
                         "description": idea.get("description") or idea["thesis"], "thesis": idea["thesis"], "name_reason": idea.get("name_reason", ""),
                         "media_type": idea["media_type"], "items": idea.get("items", []), "missing": idea["missing"],
                         "status": "draft", "origin": "opportunity", "permanent": False, "managed": True,
                         "home": False, "rotation_enabled": False, "created_at": time.time()}
            current["collections"].append(candidate)
            return candidate
        return jsonify(store.update(apply)), 201

    @app.post("/api/import/trakt")
    def import_trakt():
        payload = body()
        return job("Import Trakt list", lambda progress: service.import_trakt(payload, progress))

    @app.get("/api/export")
    def export():
        # Shareable export: settings credentials, playback aggregates and identities excluded.
        state = store.read()
        def portable(candidate):
            result = {key: candidate[key] for key in ("name", "description", "thesis", "name_reason", "media_type",
                       "origin", "source_family", "permanent", "concept") if key in candidate}
            for field in ("items", "missing"):
                result[field] = [{key: item[key] for key in ("title", "year", "media_type", "reason") if key in item}
                                 for item in candidate.get(field, [])]
            return result
        data = {"schema": 2, "exported_at": time.time(), "collections": [portable(c) for c in state["collections"]],
                "opportunities": [portable(c) for c in state["drift"].get("opportunities", [])]}
        return send_file(io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode()),
                         mimetype="application/json", as_attachment=True, download_name="collection-manager-export.json")

    @app.get("/api/artwork/<identity>")
    def artwork(identity):
        if not identity.isdecimal():
            return Response(status=404)
        state = store.read()
        item = next((i for i in state["library"] if i["id"] == identity and i.get("has_art")), None)
        if item is None:
            return Response(status=404)
        cache = store.directory / "artwork"
        cache.mkdir(exist_ok=True)
        path = cache / (identity + ".jpg")
        # Coalesce duplicate requests for one poster without serializing every
        # different image on the collection grid behind a single remote fetch.
        with artwork_locks[identity]:
            if not path.exists() or time.time() - path.stat().st_mtime > 86400:
                settings = state["settings"]
                response = providers.http("GET", settings["plex_url"].rstrip("/") + "/photo/:/transcode",
                    headers={"X-Plex-Token": settings["plex_token"]},
                    params={"url": f"/library/metadata/{identity}/thumb", "width": 320, "height": 480, "minSize": 1})
                if not response.headers.get("Content-Type", "").startswith("image/") or len(response.content) > 5 * 1024 * 1024:
                    return Response(status=404)
                path.write_bytes(response.content)
        response = send_file(path, mimetype="image/jpeg", max_age=86400)
        response.headers["Cache-Control"] = "private, max-age=86400"
        return response

    if start_scheduler:
        service.start_scheduler()
    return app
