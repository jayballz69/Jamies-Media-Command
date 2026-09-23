"""HTTP security and user-workflow regression checks, without live providers."""

from contextlib import contextmanager
import json

import pytest

from collection_web.app import create_app


@pytest.fixture
def app(tmp_path):
    instance = create_app(tmp_path, password="test-password")
    instance.config["TESTING"] = True
    yield instance
    instance.extensions["collection_service"].close()


def login(client):
    response = client.post("/api/login", json={"password": "test-password"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json["csrf"]}


def test_api_requires_login_and_health_does_not_expose_data(app):
    client = app.test_client()
    assert client.get("/healthz").json == {"status": "ok"}
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/export").status_code == 401
    assert client.get("/api/artwork/123").status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401


def test_csrf_and_cross_origin_writes_are_blocked(app):
    client = app.test_client()
    headers = login(client)
    assert client.post("/api/settings", json={"permanent_movie_slots": 2}).status_code == 403
    assert client.post("/api/settings", json={"permanent_movie_slots": 2}, headers={**headers, "Origin": "https://evil.test"}).status_code == 403
    assert client.post("/api/settings", json={"permanent_movie_slots": 2}, headers=headers).status_code == 200
    assert client.get("/api/state").json["settings"]["permanent_movie_slots"] == 2


def test_secret_settings_are_write_only_and_empty_fields_keep_them(app):
    client = app.test_client()
    headers = login(client)
    assert client.post("/api/settings", json={"plex_token": "super-private-token"}, headers=headers).status_code == 200
    state = client.get("/api/state")
    assert b"super-private-token" not in state.data
    assert state.json["settings"]["has_plex_token"] is True
    client.post("/api/settings", json={"plex_token": ""}, headers=headers)
    assert app.extensions["collection_store"].read()["settings"]["plex_token"] == "super-private-token"
    assert b"super-private-token" not in client.get("/api/export").data


def test_invalid_settings_do_not_partially_commit(app):
    client = app.test_client()
    headers = login(client)
    response = client.post("/api/settings", json={"permanent_movie_slots": 2, "plex_url": "http://user:secret@plex:32400"}, headers=headers)
    assert response.status_code == 400
    assert client.get("/api/state").json["settings"]["permanent_movie_slots"] == 2
    assert client.post("/api/settings", json={"advanced": {"auto_publish": "false"}}, headers=headers).status_code == 400
    assert client.post("/api/settings", json={"permanent_movie_slots": 999}, headers=headers).status_code == 400


def test_editing_draft_preserves_existing_request_tracking(app):
    client = app.test_client()
    headers = login(client)
    draft = client.post("/api/collections", json={"name": "Space horror", "titles": "Alien (1979)"}, headers=headers).json
    def requested(state):
        state["collections"][0]["missing"][0].update(requested_at=123, request_status="Requested", reason="Trapped with a creature")
    app.extensions["collection_store"].update(requested)
    result = client.patch("/api/collections/" + draft["id"], json={"titles": "Alien (1979)\nAliens (1986)"}, headers=headers)
    assert result.status_code == 200
    assert result.json["missing"][0]["requested_at"] == 123
    assert result.json["missing"][0]["reason"] == "Trapped with a creature"


def test_portable_export_contains_titles_without_server_ids_or_viewing_data(app):
    store = app.extensions["collection_store"]
    store.update(lambda s: s["collections"].append({"id": "private-shelf", "name": "Space tension", "plex_id": "private-plex",
        "items": [{"id": "private-rating-key", "title": "Alien", "year": 1979, "media_type": "movie",
                   "play_count": 37, "collections": ["PRIVATE-VIEWER Picks"], "library_id": "private-library"}],
        "review": {"watch_influence": 0.1}, "missing": []}))
    client = app.test_client()
    login(client)
    response = client.get("/api/export")
    assert response.json["collections"][0]["items"] == [{"title": "Alien", "year": 1979, "media_type": "movie"}]
    for forbidden in [b"private-", b"PRIVATE-VIEWER", b"play_count", b"watch_influence"]:
        assert forbidden not in response.data


def test_arr_options_expose_named_choices_without_provider_credentials(app, monkeypatch):
    from collection_web import integrations
    calls = []
    def request(settings, service, method, path):
        calls.append((service, method, path))
        return ([{"id": 4, "path": "/movies", "freeSpace": 500}] if path == "/rootfolder"
                else [{"id": 7, "name": "HD-1080p", "items": ["internal"]}])
    monkeypatch.setattr(integrations, "arr_request", request)
    client = app.test_client()
    assert client.get("/api/connections/radarr/options").status_code == 401
    login(client)
    response = client.get("/api/connections/radarr/options")
    assert response.status_code == 200
    assert response.json == {"roots": [{"path": "/movies"}], "profiles": [{"id": 7, "name": "HD-1080p"}]}
    assert calls == [("radarr", "GET", "/rootfolder"), ("radarr", "GET", "/qualityprofile")]
    assert client.get("/api/connections/other/options").status_code == 400


def test_create_matches_exact_titles_and_leaves_missing_as_suggestions(app):
    store = app.extensions["collection_store"]
    store.update(lambda s: s["library"].append({"id": "1", "title": "Alien", "year": 1979, "media_type": "movie", "library_id": "1"}))
    client = app.test_client()
    headers = login(client)
    response = client.post("/api/collections", json={"name": "Space tension", "media_type": "movie", "titles": "Alien (1979)\nAliens (1986)"}, headers=headers)
    assert response.status_code == 201
    candidate = response.json
    assert [i["id"] for i in candidate["items"]] == ["1"]
    assert candidate["missing"][0]["title"] == "Aliens"
    assert candidate["status"] == "draft"
    assert client.post(f"/api/collections/{candidate['id']}/keep", json={}, headers=headers).json["permanent"] is True
    invalid = client.post("/api/collections", json={"name": "Bad", "titles": "Alien"}, headers=headers)
    assert invalid.status_code == 400


def test_name_first_paste_creates_reviewable_collection(app):
    client = app.test_client()
    headers = login(client)
    response = client.post("/api/collections", headers=headers,
        json={"media_type": "movie", "text": "# My collection\n1. Alien (1979)\n- Aliens (1986)"})
    assert response.status_code == 201
    assert response.json["name"] == "My collection"
    assert [i["title"] for i in response.json["missing"]] == ["Alien", "Aliens"]


def test_history_keep_restores_drift_to_permanent_pool_without_immediate_home_write(app):
    app.extensions["collection_store"].update(lambda state: state["collections"].append({
        "id": "past-drift", "name": "Past discovery", "status": "archived", "origin": "drift", "plex_id": "retained",
        "permanent": False, "home": False, "rotation_enabled": False}))
    client = app.test_client()
    response = client.post("/api/collections/past-drift/keep", json={}, headers=login(client))
    assert response.status_code == 200
    assert response.json["status"] == "published"
    assert response.json["permanent"] and response.json["rotation_enabled"]
    assert not response.json["home"]


def test_rotation_rejects_more_drift_tv_shelves_than_total(app):
    client = app.test_client()
    headers = login(client)
    assert client.post("/api/settings", json={"drift_slots": 4, "drift_tv_slots": 5}, headers=headers).status_code == 400
    assert client.post("/api/settings", json={"drift_slots": 1}, headers=headers).status_code == 400
    assert client.post("/api/settings", json={"drift_slots": 4, "drift_tv_slots": 2}, headers=headers).status_code == 200


def test_logout_expires_session_and_static_assets_are_local(app):
    client = app.test_client()
    headers = login(client)
    assert client.post("/api/logout", json={}, headers=headers).status_code == 200
    assert client.get("/api/state").status_code == 401
    page = client.get("/")
    assert page.status_code == 200
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/../../collection_manager_config.json").status_code == 404


def test_restarted_jobs_are_marked_failed_without_replaying_actions(tmp_path):
    app = create_app(tmp_path, password="test-password")
    app.extensions["collection_store"].update(lambda s: s["jobs"].append({"id": "interrupted", "status": "running"}))
    app.extensions["collection_service"].close()
    second = create_app(tmp_path, password="test-password")
    assert second.extensions["collection_store"].read()["jobs"][0]["status"] == "failed"
    second.extensions["collection_service"].close()


def test_explicit_https_proxy_origin_is_supported_without_trusting_forwarded_headers(app, monkeypatch):
    monkeypatch.setenv("CM_PUBLIC_ORIGIN", "https://collections.home")
    client = app.test_client()
    response = client.post("/api/login", json={"password": "test-password"}, headers={"Origin": "https://collections.home"})
    assert response.status_code == 200
    assert client.post("/api/login", json={"password": "test-password"}, headers={"Origin": "https://attacker.test"}).status_code == 403


def test_switch_drift_endpoint_uses_saved_pool_instead_of_generation(app):
    from unittest.mock import patch
    client=app.test_client();headers=login(client)
    service=app.extensions["collection_service"]
    with patch.object(service,"submit",return_value={"id":"switch-test"}) as submit:
        response=client.post("/api/drift/switch",json={},headers=headers)
    assert response.status_code==202
    assert submit.call_args.args[1].__name__=="switch_drift"
