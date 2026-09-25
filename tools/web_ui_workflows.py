"""Run isolated browser regressions against local UI assets and mocked APIs.

No application server, credentials, Plex connection, or media service is used.
Every browser request is intercepted; unexpected requests fail the run.

Run from ``current`` with::

    .web-venv/Scripts/python.exe tools/web_ui_workflows.py

Use ``--browser-channel chromium`` for Playwright's installed Chromium instead
of the default local Chrome, and ``--report PATH`` to save the JSON results.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, expect, sync_playwright


ORIGIN = "http://collection-manager-ui.invalid"
STATIC_DIRECTORY = Path(__file__).resolve().parents[1] / "collection_web" / "static"
EDITED_TITLES = "An Edited Film (2003)"
SAMPLE_ITEM = {
    "id": "1",
    "title": "Test Film",
    "year": 2001,
    "media_type": "movie",
    "has_art": False,
}


def collection(identity: str, **overrides: object) -> dict:
    """Create explicit test data that never leaves the intercepted browser."""
    result = {
        "id": identity,
        "name": "Test Collection " + identity,
        "media_type": "movie",
        "description": "Test description",
        "status": "draft",
        "origin": "manual",
        "items": [copy.deepcopy(SAMPLE_ITEM)],
        "missing": [],
        "created_at": 1,
        "home": False,
        "managed": True,
        "rotation_enabled": False,
        "permanent": True,
    }
    result.update(overrides)
    return result


class MockBackend:
    """Serve local frontend files and the smallest API contract these checks use."""

    def __init__(self) -> None:
        self.writes: list[dict] = []
        self.unexpected_requests: list[str] = []
        self.paste_missing = False
        self.fail_next_missing_request = False
        self.state = {
            "collections": [
                collection("source", status="published", home=True),
                collection("improvement", origin="improve", source_collection_id="source"),
                collection("opportunity", origin="opportunity"),
                collection("imported", status="published", managed=False),
            ],
            "library": {"count": 1, "movies": 1, "shows": 0, "synced_at": 1},
            "drift": {"opportunities": [], "diagnostics": {}, "active_batch_ids": []},
            "activity": [],
            "jobs": [],
            "settings": {"advanced": {"auto_publish": True, "drift_schedule_enabled": True, "schedule_enabled": True},
                         "permanent_movie_slots": 2, "permanent_show_slots": 2, "drift_slots": 4, "drift_tv_slots": 2,
                         "rotation_hours": 24, "drift_interval_hours": 48,
                         "has_radarr_key": True, "has_sonarr_key": True,
                         "radarr_profile": 7, "sonarr_profile": 7, "radarr_root": "/movies", "sonarr_root": "/tv"},
            "setup_required": False,
        }

    def collection(self, identity: str) -> dict:
        return next(row for row in self.state["collections"] if row["id"] == identity)

    def intercept(self, route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        if f"{parsed.scheme}://{parsed.netloc}" != ORIGIN:
            self.reject(route)
            return

        assets = {
            "/": ("index.html", "text/html"),
            "/static/app.js": ("app.js", "text/javascript"),
            "/static/app.css": ("app.css", "text/css"),
        }
        if path in {"/static/icon.png", "/favicon.ico"}:
            route.fulfill(status=200, body=(STATIC_DIRECTORY / ("icon.png" if path.endswith("png") else "favicon.ico")).read_bytes(), content_type="image/png" if path.endswith("png") else "image/vnd.microsoft.icon")
            return

        if path in assets and request.method == "GET":
            filename, content_type = assets[path]
            route.fulfill(
                status=200,
                body=(STATIC_DIRECTORY / filename).read_text(encoding="utf-8"),
                content_type=content_type,
            )
            return

        if path == "/api/session" and request.method == "GET":
            payload = {"authenticated": True, "csrf": "isolated-test-token"}
        elif path == "/api/state" and request.method == "GET":
            payload = self.state
        elif path in {"/api/connections/radarr/options", "/api/connections/sonarr/options"} and request.method == "GET":
            payload = {"roots": [{"path": "/movies" if "radarr" in path else "/tv"}],
                       "profiles": [{"id": 7, "name": "HD-1080p"}, {"id": 8, "name": "UHD"}]}
        elif path == "/api/settings" and request.method == "POST":
            if request.headers.get("x-csrf-token") != "isolated-test-token":
                self.reject(route)
                return
            body = request.post_data_json
            self.writes.append({"method": request.method, "path": path, "body": body})
            self.state["settings"].update(body)
            payload = {"ok": True}
        elif path in {"/api/collections", "/api/collections/discover"} and request.method == "POST":
            if request.headers.get("x-csrf-token") != "isolated-test-token":
                self.reject(route)
                return
            body = request.post_data_json
            self.writes.append({"method": request.method, "path": path, "body": body})
            if path.endswith("/discover"):
                payload = self.completed_job("Create collection idea")
            else:
                # Parsing itself is covered by backend tests; the browser must
                # preserve the original pasted text and optional name override.
                identity = "pasted-" + str(len(self.writes))
                row = collection(
                    identity,
                    name=body.get("name") or body["text"].splitlines()[0],
                    media_type=body["media_type"],
                    description=body.get("description", ""),
                    missing=[dict(SAMPLE_ITEM, id="missing", title="Missing Test Film")] if self.paste_missing else [],
                )
                self.state["collections"].append(row)
                payload = row
        elif path.startswith("/api/collections/") and request.method in {"POST", "PATCH"}:
            parts = path.strip("/").split("/")
            identity = parts[2]
            row = next((c for c in self.state["collections"] if c["id"] == identity), None)
            if row is None:
                self.reject(route)
                return
            if request.headers.get("x-csrf-token") != "isolated-test-token":
                self.reject(route)
                return
            body = request.post_data_json
            self.writes.append({"method": request.method, "path": path, "body": body})
            if request.method == "PATCH" and len(parts) == 3:
                row.update({key: value for key, value in body.items() if key != "titles"})
                if "titles" in body:
                    row["manual_reviewed"] = True
                    row["items"] = [dict(SAMPLE_ITEM, title=body["titles"].split(" (")[0])]
                payload = row
            elif request.method == "POST" and parts[3:] == ["adopt"]:
                job = {
                    "id": "adopt",
                    "kind": "Manage rotation",
                    "status": "running",
                    "message": "Adopting",
                }
                self.state["jobs"] = [job]
                payload = job
            elif request.method == "POST" and parts[3:] == ["keep"]:
                row.update(permanent=True, rotation_enabled=True)
                if row["status"] == "draft":
                    row["status"] = "kept"
                elif row["status"] == "archived":
                    row["status"] = "published" if row.get("plex_id") else "kept"
                payload = row
            elif request.method == "POST" and parts[3:] == ["describe"]:
                row["thesis"] = "A refreshed, item-grounded connection."
                payload = self.completed_job("Explain collection")
            elif request.method == "POST" and parts[3:] == ["improve"]:
                draft_id = "inline-improvement-" + str(len(self.writes))
                self.state["collections"].append(collection(
                    draft_id,
                    name="A reviewed improvement",
                    origin="improve",
                    source_collection_id=identity,
                    created_at=1700000000 + len(self.writes),
                    thesis="Stories about uneasy journeys and unlikely companions.",
                    items=copy.deepcopy(row["items"]) + [dict(SAMPLE_ITEM, id="suggested-owned", title="Overlooked Owned Film", reason="Its uneasy journey gives the existing theme a new perspective.")],
                    available=[dict(SAMPLE_ITEM, id="available-in-draft", title="Another Owned Suggestion", reason="Another library title with the same strained alliance.")],
                    missing=[dict(SAMPLE_ITEM, id="suggested-missing", title="Missing Suggested Film", reason="A stranger aboard the expedition tests the same trust.")],
                ))
                payload = self.completed_job("Improve collection")
            elif request.method == "POST" and parts[3:] == ["add"]:
                chosen = next(item for item in row.get("available", []) if item["id"] == body.get("item_id"))
                row["items"].append(chosen)
                row["available"] = [item for item in row["available"] if item["id"] != chosen["id"]]
                payload = self.completed_job("Add library suggestion")
            elif request.method == "POST" and parts[3:] == ["request"] and identity.startswith("pasted-"):
                if self.fail_next_missing_request:
                    self.fail_next_missing_request = False
                    route.fulfill(status=503, content_type="application/json", body=json.dumps({"error": "The request service is temporarily unavailable."}))
                    return
                for item in row["missing"]:
                    item["requested_at"] = 1700000000
                payload = self.completed_job("Request missing titles")
            else:
                # In particular, Apply/Publish must never fire while edits are dirty.
                self.reject(route)
                return
        else:
            self.reject(route)
            return
        route.fulfill(status=200, body=json.dumps(payload), content_type="application/json")

    def reject(self, route: Route) -> None:
        self.unexpected_requests.append(f"{route.request.method} {route.request.url}")
        route.abort("blockedbyclient")

    def completed_job(self, kind: str) -> dict:
        job = {"id": "mock-job-" + str(len(self.writes)), "kind": kind, "status": "succeeded", "message": "Test draft operation completed."}
        self.state["jobs"].append(job)
        return job

    def latest_payload(self, path: str) -> dict:
        return next(write["body"] for write in reversed(self.writes) if write["path"] == path)


def advance_poll(page: Page, milliseconds: int) -> None:
    """Advance browser time and await the resulting intercepted state response."""
    with page.expect_response(lambda response: response.url == ORIGIN + "/api/state"):
        page.clock.fast_forward(milliseconds)


def exercise_workflows(page: Page, backend: MockBackend, passed: list[str]) -> None:
    page.clock.install()
    page.goto(ORIGIN + "/#collections")
    page.locator('[data-action="open"][data-id="improvement"]').click()
    page.locator('[data-tab="edit"]').click()
    titles = page.locator('#collection-edit-form [name="titles"]')
    titles.fill(EDITED_TITLES)

    expect(page.locator('[data-action="apply-improvement"]')).to_be_disabled()
    expect(page.locator('[data-action="publish"]')).to_be_disabled()
    if backend.writes:
        raise AssertionError("Typing edits unexpectedly sent a mutation.")
    passed.append("Unsaved improvement edits cannot be applied or published")

    page.locator('[data-tab="story"]').click()
    page.locator('[data-tab="edit"]').click()
    expect(titles).to_have_value(EDITED_TITLES)
    passed.append("Tab changes preserve unsaved collection text")

    backend.state["library"]["count"] = 2
    backend.collection("source")["name"] = "Source refreshed between polls"
    backend.state["jobs"] = [{
        "id": "fast",
        "kind": "Sync library",
        "status": "succeeded",
        "message": "Finished between polls",
    }]
    advance_poll(page, 16000)
    expect(titles).to_have_value(EDITED_TITLES)
    expect(page.locator('[data-action="open"][data-id="source"] h3')).to_have_text(
        "Source refreshed between polls"
    )
    expect(page.locator(".toast").filter(has_text="Finished between polls")).to_be_visible()
    passed.append("Polling preserves edits and renders jobs completed between polls")

    page.locator('#collection-edit-form button[type="submit"]').click()
    expect(page.locator('[data-action="apply-improvement"]')).to_be_enabled()
    if not any(
        write["method"] == "PATCH"
        and write["path"] == "/api/collections/improvement"
        and write["body"].get("titles") == EDITED_TITLES
        for write in backend.writes
    ):
        raise AssertionError("The saved title edits did not reach the mocked API.")
    passed.append("Saving sends edited titles and enables Apply")

    page.keyboard.press("Escape")
    page.locator('[data-action="open"][data-id="opportunity"]').click()
    expect(page.locator('[data-action="publish"]')).to_have_count(0)
    page.locator('[data-action="review-opportunity"]').click()
    page.locator('#collection-edit-form button[type="submit"]').click()
    expect(page.locator('[data-action="publish"]')).to_be_enabled()
    if not backend.collection("opportunity").get("manual_reviewed"):
        raise AssertionError("Opportunity was not reviewed through the save workflow.")
    passed.append("Opportunity drafts require a saved review before publishing")

    page.keyboard.press("Escape")
    page.locator('[data-action="open"][data-id="imported"]').click()
    page.locator('[data-tab="edit"]').click()
    with page.expect_response(lambda response: response.url == ORIGIN + "/api/state"):
        page.locator('[data-action="adopt"]').click()
    expect(page.locator("#jobs-bar")).to_contain_text("Adopting")
    backend.collection("imported").update(managed=True, rotation_enabled=True)
    backend.state["jobs"][0].update(status="succeeded", message="Managed")
    advance_poll(page, 3000)
    expect(page.locator('[data-rotation="imported"]')).to_be_checked()
    expect(page.locator('[data-action="adopt"]')).to_have_count(0)
    passed.append("Options refresh after an imported collection is adopted")

    page.keyboard.press("Escape")
    for row in backend.state["collections"]:
        row["rotation_enabled"] = False
    backend.state["jobs"].append({
        "id": "fast2",
        "kind": "Sync library",
        "status": "succeeded",
        "message": "Second fast update",
    })
    advance_poll(page, 16000)
    page.locator('a[href="#rotation"]').click()
    expect(page.locator('[data-action="rotate"]')).to_be_enabled()
    if any(row["rotation_enabled"] for row in backend.state["collections"]):
        raise AssertionError("The empty rotation pool fixture was not empty.")
    passed.append("An empty rotation pool can clear existing managed Home shelves")

    page.set_viewport_size({"width": 390, "height": 844})
    mobile_routes = [
        ("collections", "Collections", "Collections"),
        ("overview", "Overview", "Your library at a glance."),
        ("settings", "Settings", "Make it yours."),
        ("rotation", "Rotation", "Keep Home moving."),
    ]
    for route, label, heading in mobile_routes:
        page.goto(ORIGIN + "/#" + route)
        expect(page.locator("#page-content h1")).to_have_text(heading)
        expect(page.locator("#page-crumb")).to_have_text(label)
        current_link = page.locator('.nav-link[aria-current="page"]')
        expect(current_link).to_have_count(1)
        expect(current_link).to_have_attribute("data-page", route)
        expect(current_link).to_be_visible()
        # CSS transitions briefly retain the previous page's colour. Wait for
        # the selected colour before treating a screenshot as settled.
        expect(current_link).to_have_css("color", "rgb(238, 182, 152)")
    passed.append("Mobile navigation highlights exactly the displayed page")
    page.goto(ORIGIN + "/#settings")
    for service, root in [("radarr", "/movies"), ("sonarr", "/tv")]:
        expect(page.locator(f'[name="{service}_profile"] option:checked')).to_have_text("HD-1080p")
        expect(page.locator(f'[name="{service}_root"]')).to_have_value(root)
    page.locator('[name="radarr_profile"]').select_option("8")
    page.locator('[name="plex_url"]').fill("http://unsaved-library:32400")
    advance_poll(page, 16000)
    expect(page.locator('[name="radarr_profile"]')).to_have_value("8")
    expect(page.locator('[name="plex_url"]')).to_have_value("http://unsaved-library:32400")
    passed.append("Arr dropdowns use named choices and retain unsaved settings during polling")

    page.locator('a[href="#rotation"]').click()
    for key, value in {
        "permanent_movie_slots": "2", "permanent_show_slots": "2", "drift_slots": "4", "drift_tv_slots": "2",
        "rotation_hours": "24", "drift_interval_hours": "48",
    }.items():
        expect(page.locator(f'#rotation-form [name="{key}"]')).to_have_value(value)
    page.locator('#rotation-schedule > summary').click()
    page.locator('#rotation-form [name="drift_interval_hours"]').fill("24")
    page.locator('#rotation-form [name="drift_tv_slots"]').fill("1")
    page.locator('#rotation-form [name="drift_pool_size"]').fill("16")
    expect(page.locator('#rotation-form [name="drift_tv_slots"]')).to_have_attribute("max", "4")
    expect(page.locator('#rotation-form [name="drift_tv_slots"]').locator("..").locator("small")).to_have_text("3 remaining Drift shelves are movies.")
    backend.state["library"]["count"] = 9
    advance_poll(page, 16000)
    expect(page.locator('#rotation-form [name="drift_interval_hours"]')).to_have_value("24")
    page.locator('#rotation-form button[type="submit"]').click()
    expect(page.locator("#rotation-save-note")).to_have_text(
        "Changes apply on the next rotation or Drift refresh."
    )
    saved_setup = next(write["body"] for write in reversed(backend.writes) if write["path"] == "/api/settings")
    if saved_setup != {
        "permanent_movie_slots": 2, "permanent_show_slots": 2, "drift_slots": 4, "drift_tv_slots": 1,
        "rotation_hours": 24, "drift_interval_hours": 24, "drift_pool_size": 16, "drift_generation_hours": 168,
    }:
        raise AssertionError(f"Rotation setup sent unexpected fields or values: {saved_setup!r}")
    passed.append("Rotation counts and timing save independently and retain unsaved edits during polling")

    backend.state["collections"].extend([
        collection("movie-two", status="published", home=True),
        collection("show-one", status="published", home=True, media_type="show"),
        collection("show-two", status="published", home=True, media_type="show"),
    ])
    live_ids = [f"drift-{index}" for index in range(4)]
    for index, identity in enumerate(live_ids):
        backend.state["collections"].append(collection(
            identity, status="published", origin="drift", permanent=False, home=True,
            media_type="movie" if index < 2 else "show",
        ))
    backend.state["drift"]["active_batch_ids"] = live_ids
    backend.state["drift"]["last_activated_at"] = 1700000000
    advance_poll(page, 16000)
    expect(page.locator("[data-permanent-home] .collection-card")).to_have_count(4)
    expect(page.locator("[data-drift-live] .collection-card")).to_have_count(4)
    expect(page.locator('[data-permanent-pool] [data-id^="drift-"]')).to_have_count(0)
    page.locator('a[href="#drift"]').click()
    expect(page.locator("[data-drift-live] .collection-card")).to_have_count(4)
    expect(page.locator(".drift-intro")).to_contain_text("Auto-publish after review")
    expect(page.locator('.heading-actions [data-action="generate"]')).to_have_text("Generate ideas")
    passed.append("Four live temporary Drift shelves stay separate from four permanent Home shelves")

    page.locator('[data-drift-live] [data-action="keep"][data-id="drift-0"]').click()
    expect(page.locator("[data-drift-live] .collection-card")).to_have_count(3)
    page.locator('a[href="#rotation"]').click()
    expect(page.locator('[data-permanent-pool] [data-action="open"][data-id="drift-0"]')).to_have_count(1)
    expect(page.locator('[data-drift-live] [data-id="drift-0"]')).to_have_count(0)
    if not backend.collection("drift-0")["permanent"]:
        raise AssertionError("Keeping Drift did not promote it into the permanent pool.")
    passed.append("Keeping a live Drift collection promotes it into permanent rotation")


def exercise_discovery_workflows(page: Page, backend: MockBackend, passed: list[str]) -> None:
    """Check browser payloads and outcomes without invoking the actual curator."""
    page.set_viewport_size({"width": 1440, "height": 1000})

    def open_create() -> None:
        for selector in ("#create-dialog[open]", "#collection-dialog[open]"):
            if page.locator(selector).count():
                page.keyboard.press("Escape")
        page.goto(ORIGIN + "/#collections")
        page.locator('#collection-status').select_option('all')
        page.locator('.heading-actions [data-action="create"]').click()

    pasted = "Shared Journeys\n1. Test Film (2001)\n- Another Film (2002)"
    open_create()
    expect(page.locator('#create-form [name="name"]')).to_be_empty()
    page.locator('#create-form [name="text"]').fill(pasted)
    page.locator('#create-form button[type="submit"]').click()
    expect(page.locator("#collection-dialog[open] #dialog-title")).to_have_text("Shared Journeys")
    payload = backend.latest_payload("/api/collections")
    if payload != {"name": "", "media_type": "movie", "text": pasted, "description": ""}:
        raise AssertionError(f"Name-first paste changed the submitted list: {payload!r}")
    passed.append("Name-first pasted lists retain bullets and optional-name semantics")

    backend.paste_missing = True
    open_create()
    page.locator('#create-form [name="name"]').fill("A Named Import")
    page.locator('#create-form [name="media_type"]').select_option("show")
    page.locator('#create-form [name="text"]').fill("Test Show (2001)\nMissing Show (2002)")
    page.locator('#create-form [name="auto_request"]').check()
    page.locator('#create-form button[type="submit"]').click()
    expect(page.locator("#collection-dialog[open] #dialog-title")).to_have_text("A Named Import")
    created = backend.state["collections"][-1]
    if backend.latest_payload(f"/api/collections/{created['id']}/request") != {"all": True}:
        raise AssertionError("Opted-in pasted imports did not request all missing titles.")
    if backend.latest_payload("/api/collections")["media_type"] != "show":
        raise AssertionError("Paste import did not preserve the chosen TV library.")
    passed.append("Pasted imports request missing titles only after explicit opt-in")

    open_create()
    backend.fail_next_missing_request = True
    page.locator('#create-form [name="text"]').fill("Saved Despite Request Failure\nTest Film (2001)")
    page.locator('#create-form [name="auto_request"]').check()
    page.locator('#create-form button[type="submit"]').click()
    expect(page.locator("#collection-dialog[open] #dialog-title")).to_have_text("Saved Despite Request Failure")
    expect(page.locator("#create-dialog[open]")).to_have_count(0)
    expect(page.locator(".toast.error").filter(has_text="request")).to_be_visible()
    if sum(row["name"] == "Saved Despite Request Failure" for row in backend.state["collections"]) != 1:
        raise AssertionError("A missing-title request failure duplicated the imported draft.")
    passed.append("Request failures preserve the imported draft without inviting duplicate import")

    open_create()
    page.locator('#create-dialog [data-action="discover-dialog"]').click()
    expect(page.locator('#discovery-form [name="auto_request"]')).to_be_disabled()
    expect(page.locator('#discovery-form [name="auto_request"]')).not_to_be_checked()
    expect(page.locator('#discovery-form [name="prompt"]')).to_be_empty()
    page.locator('#discovery-form button[type="submit"]').click()
    expect(page.locator("#create-dialog[open]")).to_have_count(0)
    payload = backend.latest_payload("/api/collections/discover")
    if payload != {"media_type": "movie", "mode": "library", "prompt": "", "limit": 12, "auto_request": False}:
        raise AssertionError(f"Random library discovery sent incorrect options: {payload!r}")
    expect(page.locator('#collection-status')).to_have_value('draft')
    passed.append("Blank-prompt discovery requests a random library-only draft")

    open_create()
    page.locator('#create-dialog [data-action="discover-dialog"]').click()
    page.locator('#discovery-form [name="media_type"]').select_option("show")
    page.locator('#discovery-form [name="mode"]').select_option("expand")
    page.locator('#discovery-form [name="prompt"]').fill("Small-town mysteries with a supernatural edge")
    page.locator('#discovery-form [name="limit"]').fill("18")
    page.locator('#discovery-form [name="auto_request"]').check()
    page.locator('#discovery-form button[type="submit"]').click()
    expect(page.locator("#create-dialog[open]")).to_have_count(0)
    payload = backend.latest_payload("/api/collections/discover")
    if payload != {"media_type": "show", "mode": "expand", "prompt": "Small-town mysteries with a supernatural edge", "limit": 18, "auto_request": True}:
        raise AssertionError(f"Prompted external discovery sent incorrect options: {payload!r}")
    passed.append("Prompted discovery preserves library, external scope, limit, and request opt-in")

    original = copy.deepcopy(backend.collection("source"))
    cases = [
        ("library", "expand", False),
        ("library", "gaps", False),
        ("expand", "quality", True),
        ("expand", "normalize", False),
    ]
    for mode, goal, auto_request in cases:
        page.goto(ORIGIN + "/#collections")
        page.locator('#collection-status').select_option('all')
        page.locator('[data-action="open"][data-id="source"]').click()
        page.locator('#collection-dialog .dialog-heading [data-action="improve"]').click()
        expect(page.locator('#discovery-form [name="media_type"]')).to_have_count(0)
        page.locator('#discovery-form [name="mode"]').select_option(mode)
        page.locator('#discovery-form [name="goal"]').select_option(goal)
        page.locator('#discovery-form [name="prompt"]').fill("Keep its original mood")
        page.locator('#discovery-form [name="limit"]').fill("9")
        if auto_request:
            page.locator('#discovery-form [name="auto_request"]').check()
        page.locator('#discovery-form button[type="submit"]').click()
        expect(page.locator("#create-dialog[open]")).to_have_count(0)
        expect(page.locator("#collection-dialog[open] #dialog-title")).to_have_text(original["name"])
        payload = backend.latest_payload("/api/collections/source/improve")
        if payload != {"mode": mode, "goal": goal, "prompt": "Keep its original mood", "limit": 9, "auto_request": auto_request}:
            raise AssertionError(f"Improvement {mode}/{goal} sent incorrect options: {payload!r}")
        if backend.collection("source") != original:
            raise AssertionError("Requesting an improvement changed the original collection.")
        preview = page.locator("[data-improvement-preview]")
        expect(preview).to_have_count(1)
        expect(preview).to_contain_text("Overlooked Owned Film")
        expect(preview).to_contain_text("Its uneasy journey gives the existing theme a new perspective.")
        expect(preview).to_contain_text("Missing Suggested Film")
        expect(preview).to_contain_text("A stranger aboard the expedition tests the same trust.")
        expect(preview).to_contain_text("Another Owned Suggestion")
        expect(preview).to_contain_text("Another library title with the same strained alliance.")
        expect(preview.locator('[data-action="open"]')).to_have_text("Review improvements")
        if goal == "normalize":
            preview.locator('[data-action="open"]').click()
            expect(page.locator("#dialog-title")).to_have_text("A reviewed improvement")
        page.keyboard.press("Escape")
    passed.append("Improve supports library and expansion modes with all four goals and boolean request consent")
    passed.append("Improve stays on the original with inline owned and missing suggestions, fit reasons, and draft review")

    open_create()
    page.locator('#create-dialog [data-action="discover-dialog"]').click()
    page.locator('#discovery-form [name="mode"]').select_option("expand")
    page.locator('#discovery-form [name="auto_request"]').check()
    page.locator('#discovery-form [name="mode"]').select_option("library")
    expect(page.locator('#discovery-form [name="auto_request"]')).to_be_disabled()
    expect(page.locator('#discovery-form [name="auto_request"]')).not_to_be_checked()
    page.locator('#discovery-form button[type="submit"]').click()
    expect(page.locator("#create-dialog[open]")).to_have_count(0)
    if backend.latest_payload("/api/collections/discover")["auto_request"] is not False:
        raise AssertionError("Returning to library-only retained an external request opt-in.")
    passed.append("Returning to library-only clears automatic external requests")

    backend.collection("source")["available"] = [dict(SAMPLE_ITEM, id="available-owned", title="Ready Owned Film", reason="Its isolated expedition matches this collection's premise.")]
    backend.collection("source")["missing"] = [dict(SAMPLE_ITEM, id="not-owned", title="Genuinely Missing Film", reason="A shared survival problem makes this a strong fit.")]
    advance_poll(page, 16000)
    page.locator('#collection-status').select_option('all')
    page.locator('[data-action="open"][data-id="source"]').click()
    available = page.locator('[data-collection-suggestions] [data-action="add-available"][data-id="source"]')
    expect(page.locator("[data-collection-suggestions]")).to_have_count(1)
    expect(page.get_by_role("heading", name="Could complete the picture", exact=True)).to_have_count(1)
    expect(available).to_have_count(1)
    expect(available).to_have_attribute("data-item-id", "available-owned")
    expect(page.locator("[data-collection-suggestions]")).to_contain_text("Its isolated expedition matches this collection's premise.")
    expect(page.locator('[data-collection-suggestions] [data-action="request"][data-id="source"]')).to_have_count(1)
    available.click()
    expect(page.locator('[data-collection-suggestions] [data-action="add-available"][data-id="source"]')).to_have_count(0)
    if backend.latest_payload("/api/collections/source/add") != {"item_id": "available-owned"}:
        raise AssertionError("An already owned suggestion used the wrong add payload.")
    expect(page.locator("[data-collection-suggestions]")).to_contain_text("Drift additions also pass a fresh fit review.")
    page.keyboard.press("Escape")
    passed.append("Owned suggestions add directly while missing titles retain requests and arrival-review notes")

    backend.state["collections"].append(collection("old-drift", origin="drift", status="archived", permanent=False, name="Yesterday's Discovery"))
    backend.state["collections"].append(collection("old-manual", origin="manual", status="archived", name="Archived Manual"))
    advance_poll(page, 16000)
    page.locator('a[href="#drift"]').click()
    page.locator('[data-action="drift-history"]').click()
    expect(page.locator('#collection-status')).to_have_value('drift-history')
    expect(page.locator('[data-action="open"][data-id="old-drift"]')).to_have_count(1)
    expect(page.locator('[data-action="open"][data-id="old-manual"]')).to_have_count(0)
    passed.append("Drift history opens archived Drift collections without mixing in manual archives")
    page.locator('[data-action="open"][data-id="old-drift"]').click()
    page.locator('#collection-dialog [data-action="keep"]').click()
    if backend.latest_payload("/api/collections/old-drift/keep") != {}:
        raise AssertionError("Restoring a past Drift shelf used the wrong payload.")
    page.keyboard.press("Escape")
    page.locator('#collection-status').select_option('all')
    page.locator('[data-action="open"][data-id="source"]').click()
    page.locator('[data-tab="story"]').click()
    page.locator('[data-action="describe"]').click()
    expect(page.locator('#detail-panel')).to_contain_text("A refreshed, item-grounded connection.")
    passed.append("History Keep restores a shelf and The Connection can refresh in place")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-channel", default="chrome")
    parser.add_argument("--report", type=Path, help="Optional JSON report destination.")
    args = parser.parse_args()
    backend = MockBackend()
    passed: list[str] = []
    page_errors: list[str] = []
    failure = None

    try:
        with sync_playwright() as playwright:
            launch_options = {"headless": True}
            if args.browser_channel != "chromium":
                launch_options["channel"] = args.browser_channel
            browser = playwright.chromium.launch(**launch_options)
            try:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 1000}, service_workers="block"
                )
                context.route("**/*", backend.intercept)
                page = context.new_page()
                page.set_default_timeout(10000)
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                exercise_workflows(page, backend, passed)
                exercise_discovery_workflows(page, backend, passed)
            finally:
                browser.close()
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"

    success = (
        len(passed) == 23
        and not failure
        and not page_errors
        and not backend.unexpected_requests
    )
    report = {
        "success": success,
        "passed": passed,
        "page_errors": page_errors,
        "unexpected_requests": backend.unexpected_requests,
        "failure": failure,
    }
    serialized = json.dumps(report, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
