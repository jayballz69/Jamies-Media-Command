"""Collection workflows and a single persistent, observable background worker."""

from __future__ import annotations

import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
import logging
import re
import threading
import time
from urllib.parse import urlparse
import uuid

from . import integrations as providers
from .seasonal import active_events, context_key, seasonal_rank
from .store import DEFAULT_SETTINGS, DomainError, SECRETS, event


LOG = logging.getLogger(__name__)


def collection_by_id(state, identity):
    for candidate in state["collections"]:
        if candidate["id"] == identity:
            return candidate
    raise DomainError("That collection could not be found.")


def membership_revision(candidate):
    return hashlib.sha256(json_dumps({"name": candidate["name"],
        "items": sorted(str(item["id"]) for item in candidate["items"])}).encode()).hexdigest()


def validate_settings(payload, current):
    if not isinstance(payload, dict):
        raise DomainError("Settings must be an object.")
    result = copy.deepcopy(current)
    for key, value in payload.items():
        if key.startswith("has_"):
            continue
        if key not in DEFAULT_SETTINGS:
            raise DomainError("Unknown setting: " + key)
        if key == "advanced":
            if not isinstance(value, dict):
                raise DomainError("Advanced settings must be an object.")
            for toggle, enabled in value.items():
                if toggle not in DEFAULT_SETTINGS["advanced"] or not isinstance(enabled, bool):
                    raise DomainError("Invalid advanced setting.")
                result["advanced"][toggle] = enabled
        elif key in {"permanent_movie_slots", "permanent_show_slots", "drift_slots", "drift_tv_slots", "drift_pool_size", "drift_generation_hours", "rotation_hours", "drift_interval_hours", "radarr_profile", "sonarr_profile", "library_sync_minutes"}:
            try:
                number = int(value)
            except (ValueError, TypeError):
                raise DomainError("Use a whole number for " + key.replace("_", " ")) from None
            bounds = ((0, 20) if key.startswith("permanent_") or key == "drift_tv_slots" else (2, 20)) if key.endswith("slots") else ((1, 10000) if key.endswith("profile") else ((5, 1440) if key == "library_sync_minutes" else (1, 8760)))
            if key == "drift_pool_size":
                bounds = (4, 20)
            if not bounds[0] <= number <= bounds[1]:
                raise DomainError(f"{key.replace('_', ' ')} must be between {bounds[0]} and {bounds[1]}.")
            result[key] = number
        else:
            if not isinstance(value, str) or len(value) > 4000:
                raise DomainError("Invalid connection setting.")
            value = value.strip()
            if key.endswith("_url") and value:
                parsed = urlparse(value)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise DomainError("Use an http or https service address without credentials or query parameters.")
            if key not in SECRETS or value:
                result[key] = value
    if "drift_slots" in payload and "drift_tv_slots" not in payload:
        result["drift_tv_slots"] = min(result["drift_tv_slots"], result["drift_slots"])
    if result["drift_tv_slots"] > result["drift_slots"]:
        raise DomainError("The number of TV shelves cannot exceed the total Drift shelves.")
    return result


def match_titles(titles, library, media_type):
    owned, missing = [], []
    seen = set()
    for row in titles:
        title, year = str(row.get("title") or "").strip(), int(row.get("year") or 0)
        if not title or not 1870 <= year <= 2200:
            raise DomainError("Each title needs a release year, for example Alien (1979).")
        identity = (providers.clean(title), year)
        if identity in seen:
            continue
        seen.add(identity)
        matches = [item for item in library if item["media_type"] == media_type
                   and providers.clean(item["title"]) == identity[0] and item["year"] == year]
        if len(matches) == 1:
            owned.append(copy.deepcopy(matches[0]))
            if row.get("reason") and row["reason"] not in {"Ambiguous library match", "Not in the synced library"}:
                owned[-1]["reason"] = row["reason"]
        else:
            missing.append({"title": title, "year": year, "media_type": media_type,
                            "reason": "Ambiguous library match" if matches else row.get("reason", "Not in the synced library")})
            for key in ("requested_at", "request_status", "fit_reason", "metadata", "metadata_status", "metadata_checked_at", "metadata_error", "fit_status", "request_progress", "request_error", "request_checked_at"):
                if key in row:
                    missing[-1][key] = row[key]
    if owned and len({r["library_id"] for r in owned}) > 1:
        raise DomainError("The titles span more than one Plex library. Choose one movie/TV library in Settings and sync it first.")
    return owned, missing


def possible_matches(row, library):
    """Identify uncertain catalog aliases without treating a guess as ownership."""
    title, year = providers.clean(row["title"]), row["year"]
    matches = []
    for item in library:
        actual = providers.clean(item["title"])
        close_year = not item.get("year") or abs(item["year"] - year) <= 1
        alias = min(len(title), len(actual)) >= 12 and (actual.startswith(title + " ") or title.startswith(actual + " "))
        if (title == actual and close_year) or (alias and item.get("year") == year):
            matches.append({key: item[key] for key in ("id", "title", "year", "media_type")})
    return matches[:8]


def create_candidate(payload, library, titles=None):
    name = str(payload.get("name") or "").strip()
    if not 2 <= len(name) <= 100:
        raise DomainError("Give the collection a name between 2 and 100 characters.")
    media_type = payload.get("media_type", "movie")
    if media_type not in {"movie", "show"}:
        raise DomainError("Choose movies or TV shows.")
    if titles is None:
        lines = str(payload.get("titles") or "").splitlines()
        titles = []
        for line in lines:
            if not line.strip():
                continue
            match = re.fullmatch(r"\s*(.+?)\s*\((\d{4})\)\s*", line)
            if not match:
                raise DomainError("Use one Title (Year) per line.")
            titles.append({"title": match[1], "year": int(match[2])})
    if not titles or len(titles) > 2000:
        raise DomainError("Add between 1 and 2,000 titles.")
    owned, missing = match_titles(titles, library, media_type)
    return {"id": uuid.uuid4().hex, "name": name, "media_type": media_type,
            "description": str(payload.get("description") or "")[:2000],
            "thesis": "", "name_reason": "", "items": owned, "missing": missing,
            "status": "draft", "origin": "manual", "permanent": True, "managed": True,
            "rotation_enabled": False, "home": False, "created_at": time.time()}


def reconcile_suggestions(candidate, library):
    """Keep external requests, owned additions and existing members distinct."""
    members = {(providers.clean(row["title"]), row["year"]) for row in candidate.get("items", [])}
    seen, available, missing = set(members), [], []
    for row in candidate.get("missing", []) + candidate.get("available", []):
        key = (providers.clean(row["title"]), row["year"])
        if key in seen:
            continue
        seen.add(key)
        owned, absent = match_titles([row], library, candidate["media_type"])
        if owned:
            item = owned[0]
            for field in ("requested_at", "request_status", "arrival_review_error", "request_progress", "request_error", "request_checked_at"):
                if field in row:
                    item[field] = row[field]
            available.append(item)
        else:
            potential = possible_matches(row, library)
            if potential:
                absent[0].update(reason="Ambiguous library match", possible_matches=potential,
                                 fit_reason=row.get("fit_reason", row.get("reason", "")))
            elif row.get("reason") == "Ambiguous library match" and row.get("fit_reason"):
                absent[0]["reason"] = row["fit_reason"]
            missing.extend(absent)
    candidate.update(available=available, missing=missing)


class Service:
    def __init__(self, store, legacy_dir=None):
        self.store, self.legacy_dir = store, legacy_dir
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="collections")
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.busy = False
        self.store.update(self._recover)

    @staticmethod
    def _recover(state):
        for job in state["jobs"]:
            if job["status"] in {"running", "queued"}:
                job.update(status="failed", finished_at=time.time(),
                           message="The app restarted before this task finished. Review Activity and retry.")
                event(state, job["message"], "warning")

    def submit(self, kind, operation):
        with self.lock:
            if self.busy:
                raise DomainError("Another task is still running. Its progress is shown in Activity.")
            self.busy = True
            job = {"id": uuid.uuid4().hex, "kind": kind, "status": "running", "message": "Starting…",
                   "started_at": time.time(), "finished_at": 0}
            self.store.update(lambda state: state["jobs"].insert(0, job))
            self.executor.submit(self._run, job["id"], operation)
            return job

    def _job(self, identity, **changes):
        def update(state):
            for job in state["jobs"]:
                if job["id"] == identity:
                    job.update(changes)
            state["jobs"] = state["jobs"][:40]
        self.store.update(update)

    def _run(self, identity, operation):
        try:
            message = operation(lambda text: self._job(identity, message=text)) or "Task complete."
            self._job(identity, status="succeeded", message=message, finished_at=time.time())
            self.store.update(lambda state: event(state, message))
        except Exception as exc:
            # Provider exceptions may contain token-bearing URLs. Never serialize them.
            message = str(exc) if isinstance(exc, DomainError) else "The service could not complete the task. Check the connection and retry."
            LOG.error("Task %s failed (%s)", identity, type(exc).__name__)
            self._job(identity, status="failed", message=message, finished_at=time.time())
            self.store.update(lambda state: event(state, message, "error"))
        finally:
            with self.lock:
                self.busy = False

    def sync(self, progress):
        from .migration import is_excluded_legacy_shelf
        state = self.store.read()
        try:
            library, shelves, server_id = providers.scan_library(state["settings"], progress)
        except Exception:
            def failed(current):
                scan = current.setdefault("library_diagnostics", {})
                count = scan.get("consecutive_failures", 0) + 1
                scan.update(consecutive_failures=count, needs_attention=count >= 3,
                            message="Plex sync failed; the last verified library was retained.", checked_at=time.time())
            self.store.update(failed)
            raise
        for row in library:
            row["collections"] = [name for name in row.get("collections", [])
                                  if not is_excluded_legacy_shelf(state, name, row["media_type"])]
        lookup = {row["id"]: row for row in library}
        backup_due = time.time() - state.get("last_sync_backup_at", 0) >= 86400
        if backup_due:
            self.store.backup()
        def apply(current):
            if current["server_id"] and current["server_id"] != server_id:
                raise DomainError("This is a different Plex server. Use a separate data directory to avoid changing collection identities.")
            from .new_arrivals import observe
            observe(current, library)
            current["library"], current["server_id"], current["synced_at"] = library, server_id, time.time()
            if backup_due:
                current["last_sync_backup_at"] = time.time()
            current["library_diagnostics"] = {"consecutive_failures": 0, "needs_attention": False,
                                               "message": "Library sync completed.", "checked_at": time.time()}
            existing = {row.get("plex_id"): row for row in current["collections"] if row.get("plex_id")}
            for shelf in shelves:
                if is_excluded_legacy_shelf(current, shelf["name"], shelf["media_type"]):
                    continue
                candidate = existing.get(shelf["plex_id"])
                if candidate is None:
                    candidate = next((c for c in current["collections"] if c.get("origin") == "import"
                                      and c.get("media_type") == shelf["media_type"]
                                      and providers.clean(c["name"]) == providers.clean(shelf["name"])), None)
                if candidate is None:
                    # Recover ownership after a restart between Plex creation and local commit.
                    candidate = next((c for c in current["collections"]
                                      if providers.is_owned(shelf["labels"], c)), None)
                if candidate is None:
                    candidate = {"id": uuid.uuid4().hex, "origin": "plex", "status": "published", "permanent": True,
                                 "managed": False, "home": False, "rotation_enabled": False,
                                 "created_at": time.time(), "missing": [], "thesis": "", "name_reason": ""}
                    current["collections"].append(candidate)
                candidate.update({key: shelf[key] for key in ("plex_id", "name", "description", "media_type", "library_id")})
                if candidate["status"] != "archived":
                    candidate["status"] = "published"
                refreshed = [lookup[key] for key in shelf["item_ids"] if key in lookup]
                if candidate.get("origin") != "drift" or {i["id"] for i in candidate.get("items", [])} != {i["id"] for i in refreshed}:
                    notes = {row["id"]: row.get("reason") for row in candidate.get("items", [])}
                    candidate["items"] = [dict(row, **({"reason": notes[row["id"]]} if notes.get(row["id"]) else {})) for row in refreshed]
                candidate["managed"] = providers.is_owned(shelf["labels"], candidate)
                if "home" in shelf:
                    candidate["home"] = shelf["home"]
            for candidate in current["collections"]:
                if not candidate.get("plex_id"):
                    if candidate.get("origin") == "drift":
                        # Approved items remain locked. Acquisitions require a fresh review.
                        continue
                    titles = candidate.get("items", []) + candidate.get("missing", [])
                    if titles:
                        candidate["items"], candidate["missing"] = match_titles(titles, library, candidate["media_type"])
                reconcile_suggestions(candidate, library)
        self.store.update(apply)
        self.reconcile_arrivals(progress)
        return f"Synced {len(library):,} titles and {len(shelves)} Plex collections."

    def reconcile_arrivals(self, progress):
        state = self.store.read()
        for candidate in state["collections"]:
            if (candidate["status"] != "published" or not candidate.get("managed") or not candidate.get("auto_add_arrivals")):
                continue
            arrived = []
            for missing in candidate.get("missing", []) + candidate.get("available", []):
                owned, _ = match_titles([missing], state["library"], candidate["media_type"])
                if missing.get("requested_at") and owned and not missing.get("arrival_review_error"):
                    arrived.extend(owned)
            if not arrived:
                continue
            progress("Adding newly available titles to " + candidate["name"] + "…")
            try:
                self._add_items(candidate, arrived, state, progress)
            except DomainError as error:
                self.store.update(lambda current, message=str(error): event(current, "Arrival update deferred: " + message, "warning"))
                if candidate.get("origin") == "drift":
                    def mark(current):
                        target = collection_by_id(current, candidate["id"])
                        for row in target.get("available", []) + target.get("missing", []):
                            if (providers.clean(row["title"]), row["year"]) in {(providers.clean(item["title"]), item["year"]) for item in arrived}:
                                row["arrival_review_error"] = str(error)
                    self.store.update(mark)
                continue
            self.store.update(lambda current, name=candidate["name"], count=len(arrived): event(current, f"Added {count} newly available requested titles to {name}."))

    def _add_items(self, candidate, additions, state, progress):
        if candidate.get('family_rolling'):
            raise DomainError('This shelf automatically keeps the newest 25 family movies. Use Refresh family shelf on Rotation.')
        replacement = copy.deepcopy(candidate)
        present = {row["id"] for row in replacement["items"]}
        replacement["items"].extend(copy.deepcopy(row) for row in additions if row["id"] not in present)
        if candidate.get("origin") == "drift":
            from .arrivals import review_drift_additions
            progress("Checking the new titles against this Drift collection’s reviewed theme…")
            replacement = review_drift_additions(candidate, replacement, state,
                                                lambda prompt: providers.call_llm(state["settings"], prompt))
        reconcile_suggestions(replacement, state["library"])
        if candidate["status"] == "published":
            if not candidate.get("managed"):
                raise DomainError("Choose Manage rotation here before adding titles to this Plex collection.")
            self.store.backup()
            providers.publish(state["settings"], replacement, state["server_id"], expected_source=candidate)
        self.store.update(lambda current: collection_by_id(current, candidate["id"]).update(replacement))

    def add_available(self, identity, payload, progress):
        state = self.store.read()
        candidate = collection_by_id(state, identity)
        if candidate["status"] == "archived":
            raise DomainError("This collection is retired. Keep a current collection before adding titles.")
        reconcile_suggestions(candidate, state["library"])
        rows = [row for row in candidate["available"] if payload.get("all") is True or str(row["id"]) == str(payload.get("item_id"))]
        if not rows:
            raise DomainError("That suggestion is no longer available to add. Refresh this collection.")
        self._add_items(candidate, rows, state, progress)
        return f"Added {len(rows)} owned titles to {candidate['name']}."

    def generate(self, progress):
        from .curation import generate_batch
        state = self.store.read()
        settings = state["settings"]
        seasons = active_events(settings)
        if any(event["kind"] == "qld_school" for event in seasons) and state["library"] and not any("content_rating" in row for row in state["library"]):
            progress("Refreshing library age classifications for school-holiday curation...")
            self.sync(progress)
            state = self.store.read()
        rows = copy.deepcopy(state["library"])
        if not state["synced_at"]:
            raise DomainError("Sync your Plex library before generating Drift.")
        signal_status = "disabled"
        if settings["advanced"]["watch_inspiration"]:
            try:
                recent = providers.watch_counts(settings)
                for row in rows:
                    row["play_count"] = recent.get(row["id"], 0)
                signal_status = "available" if recent else "unavailable"
            except (DomainError, ValueError, TypeError):
                signal_status = "unavailable"
                for row in rows:
                    row["play_count"] = 0
        previous = state["drift"]["diagnostics"].get("consecutive_degraded_count", 0)
        pool_size = max(settings["drift_slots"], settings.get("drift_pool_size", 12))
        pool_tv = round(pool_size * settings["drift_tv_slots"] / settings["drift_slots"])
        efficient = settings.get("llm_model") == "gpt-6-luna"
        usage = []
        def curator(prompt):
            if efficient:
                return providers.call_llm(settings, prompt, usage=usage)
            return providers.call_llm(settings, prompt)
        batch = generate_batch(rows, [c for c in state["collections"] if c["status"] != "archived"],
                               state["history"], {"watch_inspiration": settings["advanced"]["watch_inspiration"],
                               "batch_size": pool_size, "minimum_batch_size": max(2, settings["drift_slots"]),
                               "show_slots": pool_tv,
                               "movie_slots": pool_size - pool_tv,
                               "seasonal_events": seasons, "allow_partial": True, "creative_editor": efficient or bool(seasons), "adjudicate": True, "previous_degraded_count": previous, "diversity": settings["advanced"]["diversity"]},
                               curator, progress)
        if efficient:
            input_tokens = sum(u.get("prompt_tokens", 0) for u in usage)
            output_tokens = sum(u.get("completion_tokens", 0) for u in usage)
            batch["diagnostics"]["usage"] = {"model": settings["llm_model"], "calls":len(usage),
                "input_tokens":input_tokens, "output_tokens":output_tokens,
                "estimated_usd":round((input_tokens * 0.125 + output_tokens * 0.5) / 1_000_000, 5),
                "estimate_basis":"Standard short-context rates; input conservatively priced as cache writes. Excludes tax."}
        batch["diagnostics"]["watch_signal_status"] = signal_status
        collections = batch.get("collections", [])
        reserves = batch.get("reserve_collections", [])
        if not collections:
            batch["diagnostics"]["consecutive_degraded_count"] = previous + 1
            batch["diagnostics"]["needs_attention"] = previous + 1 >= 3
        def apply(current):
            current["drift"]["diagnostics"] = batch["diagnostics"]
            if settings["advanced"]["missing_suggestions"]:
                ideas = current["drift"].setdefault("opportunities", [])
                known = {(i.get("media_type"), i.get("thesis", "").strip().casefold()) for i in ideas}
                for idea in batch.get("opportunities", []):
                    key = (idea.get("media_type"), idea.get("thesis", "").strip().casefold())
                    if key not in known:
                        ideas.append(idea)
                        known.add(key)
            if not collections:
                event(current, "Drift kept your existing shelves: no complete, reviewed batch was available.", "warning")
                return
            for candidate in collections + reserves:
                candidate.update(managed=True, permanent=False, home=False, rotation_enabled=False)
                if not settings["advanced"]["missing_suggestions"]:
                    candidate["missing"] = []
            # Keep reviewed discoveries between runs. Fill remaining Home slots
            # from the previous active shelves; an incomplete run is useful.
            prior_pool = set(current["drift"].get("pool_ids", []))
            retained_seasonal = [c["id"] for c in current["collections"] if c["id"] in prior_pool
                and self.temporary(c) and c["status"] != "archived" and seasonal_rank(c, seasons) < 0]
            for old in current["collections"]:
                if old["id"] in prior_pool and old["id"] not in retained_seasonal and self.temporary(old) and not old.get("home"):
                    old.update(status="archived", rotation_enabled=False, retired_at=time.time())
            current["collections"].extend(collections + reserves)
            current["drift"]["pool_ids"] = [c["id"] for c in collections + reserves] + retained_seasonal
            current["drift"]["seasonal_context"] = context_key(seasons)
            current["drift"]["current_batch_ids"] = self._choose_pool(current)
            current["drift"]["incremental"] = True
            current["drift"]["last_generated_at"] = time.time()
            current["history"] = (current["history"] + [{"name": c["name"], "thesis": c.get("thesis", ""),
                "source_family": c.get("source_family", ""), "time": time.time()} for c in collections + reserves])[-250:]
        self.store.update(apply)
        if not collections:
            raise DomainError("Drift did not find enough fully reviewed shelves. Your existing batch is unchanged. See Drift for diagnostics.")
        if settings["advanced"]["auto_publish"]:
            placement = self.activate_drift(progress)
            return f"Weekly pool: {len(collections) + len(reserves)} reviewed collections. {placement}"
        return f"Weekly pool: {len(collections) + len(reserves)} reviewed collections ready to browse."

    def publish(self, identity, progress):
        from .curation import validate_candidate
        state = self.store.read()
        candidate = collection_by_id(state, identity)
        if candidate["status"] == "archived":
            raise DomainError("This collection is retired.")
        if candidate["origin"] == "opportunity" and not candidate.get("manual_reviewed"):
            raise DomainError("Review and save this idea's name and titles before publishing it as a manual collection.")
        if not state["synced_at"]:
            raise DomainError("Sync Plex before publishing imported collections.")
        if candidate["origin"] == "drift":
            errors = validate_candidate(candidate, state["library"])
            if errors:
                raise DomainError("This draft needs a new review: " + "; ".join(errors))
        progress("Verifying owned titles in Plex…")
        self.store.backup()
        plex_id = providers.publish(state["settings"], candidate, state["server_id"])
        def apply(current):
            row = collection_by_id(current, identity)
            row.update(plex_id=plex_id, status="published", managed=True, published_at=time.time(), rotation_enabled=True)
        self.store.update(apply)
        return f"Published {candidate['name']} to Plex and added it to the rotation pool."

    def archive(self, identity, progress):
        candidate = collection_by_id(self.store.read(), identity)
        if candidate.get("home") and candidate.get("managed"):
            progress("Removing this shelf from Plex Home…")
            current = self.store.read()
            providers.set_visibility(current["settings"], candidate, False, current["server_id"])
        self.store.update(lambda state: collection_by_id(state, identity).update(status="archived", home=False, rotation_enabled=False))
        return f"Retired {candidate['name']} from rotation."

    @staticmethod
    def temporary(candidate):
        return candidate.get("origin") == "drift" and not candidate.get("permanent")

    @staticmethod
    def eligible(candidate):
        return candidate["status"] == "published" and candidate.get("managed") and candidate.get("rotation_enabled")

    def _permanent_choices(self, state, preserve=False):
        settings = state["settings"]
        chosen = []
        for kind, key in (("movie", "permanent_movie_slots"), ("show", "permanent_show_slots")):
            pool = [c for c in state["collections"] if self.eligible(c) and not self.temporary(c) and not c.get('pinned_home')
                    and c.get("media_type", "movie") == kind]
            if preserve:
                current = [c for c in pool if c.get("home")][:settings[key]]
                chosen.extend(current)
                pool = [c for c in pool if c not in current]
            else:
                current = []
            while pool and len(current) < settings[key]:
                used = {item["id"] for c in chosen for item in c["items"]}
                def rank(c):
                    overlap = len(used & {item["id"] for item in c["items"]}) / max(1, len(c["items"]))
                    return (bool(c.get("home")), c.get("last_rotated_at", 0),
                            overlap if settings["advanced"]["diversity"] else 0, c["id"])
                pool.sort(key=rank)
                pick = pool.pop(0)
                chosen.append(pick)
                current.append(pick)
        return chosen

    def _choose_pool(self, state):
        from .curation import validate_candidate
        settings = state["settings"]
        pool_ids = set(state["drift"].get("pool_ids", []))
        pool = [c for c in state["collections"] if c["id"] in pool_ids and self.temporary(c)
                and c["status"] in {"draft", "published"} and not validate_candidate(c, state["library"])]
        chosen = []
        for kind, count in (("movie", settings["drift_slots"] - settings["drift_tv_slots"]),
                            ("show", settings["drift_tv_slots"])):
            eligible = sorted((c for c in pool if c["media_type"] == kind),
                              key=lambda c: (seasonal_rank(c, active_events(settings)), bool(c.get("home")), c.get("last_drift_shown_at", 0)))
            chosen.extend(eligible[:count])
        active = set(state["drift"].get("active_batch_ids", []))
        previous = [c for c in state["collections"] if c["id"] in active and self.temporary(c)
                    and self.eligible(c) and c not in chosen]
        for kind, count in (("movie", settings["drift_slots"] - settings["drift_tv_slots"]),
                            ("show", settings["drift_tv_slots"])):
            needed = max(0, count - sum(c["media_type"] == kind for c in chosen))
            chosen.extend([c for c in previous if c["media_type"] == kind][:needed])
        for c in previous:
            if c in chosen:
                continue
            if len(chosen) >= settings["drift_slots"]:
                break
            chosen.append(c)
        return [c["id"] for c in chosen]

    def _drift_choices(self, state):
        slots = state["settings"]["drift_slots"]
        current_ids = state["drift"].get("current_batch_ids", [])
        current = [c for c in state["collections"] if c["id"] in current_ids and self.temporary(c)]
        order = {identity: index for index, identity in enumerate(current_ids)}
        current = sorted(current, key=lambda c: order[c["id"]])[:slots]
        # A partly published replacement cannot displace the last complete batch.
        ready = bool(current) and (len(current) >= slots or state["drift"].get("incremental")) and all(self.eligible(c) for c in current)
        if ready:
            return current, True
        active = set(state["drift"].get("active_batch_ids", []))
        previous = [c for c in state["collections"] if c["id"] in active and self.temporary(c) and self.eligible(c)]
        if not previous and not active:
            previous = [c for c in state["collections"] if self.temporary(c) and self.eligible(c) and c.get("home")]
        return previous[:slots], False

    def activate_drift(self, progress):
        """Publish reviewed discoveries before replacing temporary Home slots."""
        from .curation import validate_candidate
        state = self.store.read()
        if not state["settings"]["advanced"]["home_enabled"]:
            raise DomainError("Enable Plex Home in Advanced settings before activating Drift.")
        batch_ids = state["drift"].get("current_batch_ids", [])
        lookup = {c["id"]: c for c in state["collections"]}
        batch = [lookup[identity] for identity in batch_ids if identity in lookup and self.temporary(lookup[identity])]
        slots = state["settings"]["drift_slots"]
        if not batch or (len(batch) < slots and not state["drift"].get("incremental")) or any(c["status"] == "archived" for c in batch):
            raise DomainError("Generate a complete Drift batch before replacing the current shelves.")
        for candidate in batch:
            if validate_candidate(candidate, state["library"]):
                raise DomainError("A Drift shelf changed since review. Generate a fresh batch; current Home shelves are retained.")
        for candidate in batch[:slots]:
            if candidate["status"] != "published":
                self.publish(candidate["id"], progress)
        state = self.store.read()
        drift, ready = self._drift_choices(state)
        if not ready:
            raise DomainError("The replacement is not fully published. Current Drift shelves are retained.")
        chosen = self._permanent_choices(state, preserve=True) + drift
        self._apply_home(state, chosen, progress, rotate_permanent=False, replacement=True)
        new_count = sum(c["id"] not in state["drift"].get("active_batch_ids", []) for c in drift)
        retained_count = len(drift) - new_count
        return f"Added {new_count} reviewed Drift shelves; retained {retained_count} previous shelves alongside your permanent rotation."

    def _apply_home(self, state, chosen, progress, *, rotate_permanent, replacement):
        from .curation import validate_candidate
        settings = state["settings"]
        chosen = list(chosen) + [c for c in state['collections'] if c.get('pinned_home')
            and c.get('managed') and c['status']=='published' and c['id'] not in {r['id'] for r in chosen}]
        for candidate in chosen:
            if candidate.get("origin") == "drift" and validate_candidate(candidate, state["library"]):
                raise DomainError("A Drift shelf changed since review. Generate a fresh batch before changing Home.")
        selected = {c["id"] for c in chosen}
        temporary_ids = [c["id"] for c in chosen if self.temporary(c)]
        # Reveal all replacements first. A partial provider failure retains the
        # previous useful shelves and never marks the batch successfully active.
        for candidate in chosen:
            if not candidate.get("home"):
                progress("Putting " + candidate["name"] + " on Plex Home?")
                providers.set_visibility(settings, candidate, True, state["server_id"])
                self.store.update(lambda current, cid=candidate["id"]: collection_by_id(current, cid).update(home=True))
        for candidate in state["collections"]:
            if candidate.get("home") and candidate.get("managed") and candidate["id"] not in selected:
                providers.set_visibility(settings, candidate, False, state["server_id"])
                self.store.update(lambda current, cid=candidate["id"]: collection_by_id(current, cid).update(home=False))
        def finish(current):
            old_ids = set(current["drift"].get("active_batch_ids", []))
            drift_switched = replacement and (not rotate_permanent or set(temporary_ids) != old_ids)
            retired = 0
            for candidate in current["collections"]:
                if rotate_permanent and candidate["id"] in selected and not self.temporary(candidate):
                    candidate["last_rotated_at"] = time.time()
                if replacement and self.temporary(candidate) and candidate["status"] in {"draft", "published"} and candidate["id"] not in temporary_ids and candidate["id"] not in current["drift"].get("pool_ids", []):
                    candidate.update(status="archived", rotation_enabled=False, home=False, retired_at=time.time())
                    retired += 1
            if drift_switched:
                for candidate in current["collections"]:
                    if candidate["id"] in temporary_ids:
                        candidate["last_drift_shown_at"] = time.time()
            old_ids = set(current["drift"].get("active_batch_ids", []))
            current["drift"]["active_batch_ids"] = temporary_ids
            if drift_switched:
                current["drift"]["last_activated_at"] = time.time()
            if retired:
                event(current, f"Retired {retired} previous Drift shelves after their replacement reached Home.")
            if rotate_permanent:
                current["last_rotation_at"] = time.time()
        self.store.update(finish)

    def rotate(self, progress):
        state = self.store.read()
        if not state["settings"]["advanced"]["home_enabled"]:
            raise DomainError("Enable Plex Home in Advanced settings to rotate shelves.")
        permanent = self._permanent_choices(state)
        drift, replacement = self._drift_choices(state)
        chosen = permanent + drift
        if not chosen and not any(c.get("home") and c.get("managed") for c in state["collections"]):
            raise DomainError("Publish a collection and add it to rotation first.")
        self._apply_home(state, chosen, progress, rotate_permanent=True, replacement=replacement)
        return f"Rotated {len(permanent)} permanent shelves alongside {len(drift)} Drift shelves."

    def request_missing(self, identity, payload, progress):
        state = self.store.read()
        candidate = collection_by_id(state, identity)
        if payload.get("all") is True:
            indices = list(range(len(candidate["missing"])))
        else:
            try:
                index = int(payload.get("index", -1))
            except (ValueError, TypeError):
                raise DomainError("Choose a missing title.") from None
            if not 0 <= index < len(candidate["missing"]):
                raise DomainError("Choose a missing title.")
            indices = [index]
        self.store.update(lambda current: collection_by_id(current, identity).update(auto_add_arrivals=True))
        done = 0
        failures = []
        for index in indices:
            item = candidate["missing"][index]
            if item.get("requested_at") or item.get("reason") == "Ambiguous library match" or item.get("metadata_status") == "needs_check" or item.get("fit_status") in {"weak", "uncertain"}:
                continue
            owned, _ = match_titles([item], state["library"], candidate["media_type"])
            if owned or possible_matches(item, state["library"]):
                continue
            progress("Requesting " + item["title"] + "…")
            self.store.update(lambda current: collection_by_id(current, identity)["missing"][index].update(request_progress="Requesting", request_error=""))
            try:
                result = providers.request_title(state["settings"], item)
            except DomainError as error:
                message = str(error)
                self.store.update(lambda current: collection_by_id(current, identity)["missing"][index].update(request_progress="Request failed", request_error=message))
                failures.append(item["title"])
                continue
            def mark(current):
                target = collection_by_id(current, identity)["missing"][index]
                target.update(requested_at=time.time(), request_status=result, request_progress="Requested", request_error="")
                event(current, item["title"] + ": " + result)
            self.store.update(mark)
            done += 1
        self.store.update(lambda current: reconcile_suggestions(collection_by_id(current, identity), current["library"]))
        if failures:
            raise DomainError(f"Processed {done} requests; {len(failures)} could not finish: " + ", ".join(failures) + ". Successful requests are saved. Check the title status and retry the remaining picks.")
        return f"Processed {done} missing-title requests."

    def sweep_collections(self, progress):
        from .sweep import review
        state = self.store.read()
        progress("Reviewing permanent collections together: distinctive themes and better-fit additions...")
        report = review(state, lambda prompt: providers.call_llm(state["settings"], prompt))
        report["created_at"] = time.time()
        self.store.update(lambda current: current.update(collection_review=report))
        return "Permanent collection review is ready on Collections. No titles were moved or requested."

    def refresh_requests(self, progress):
        from .requests_status import snapshot, progress_for
        state = self.store.read()
        tracked = [(c, item) for c in state["collections"] if c["status"] != "archived"
                   for field in ("missing", "available") for item in c.get(field, []) if item.get("requested_at")]
        snapshots = {}
        for kind in {c["media_type"] for c, item in tracked}:
            progress("Checking " + ("Sonarr" if kind == "show" else "Radarr") + " request progress...")
            try:
                snapshots[kind] = snapshot(state["settings"], kind)
            except DomainError:
                snapshots[kind] = None
        now = time.time()
        def save(current):
            for c in current["collections"]:
                if c["status"] == "archived":
                    continue
                members = {(providers.clean(i["title"]),i["year"]) for i in c.get("items", [])}
                for field in ("missing", "available"):
                    for item in c.get(field, []):
                        if not item.get("requested_at"):
                            continue
                        owned, _ = match_titles([item], current["library"], c["media_type"])
                        if (providers.clean(item["title"]), item["year"]) in members:
                            label = "In collection" if c["status"] == "published" else "In draft"
                        elif owned:
                            label = "In Plex; awaiting collection review" if item.get("arrival_review_error") else "In Plex"
                        elif snapshots.get(c["media_type"]) is not None:
                            label = progress_for(item, c["media_type"], *snapshots[c["media_type"]])
                        else:
                            item["request_error"] = "Status unavailable; last known progress retained."
                            continue
                        item.update(request_progress=label, request_error="", request_checked_at=now)
            current["requests_checked_at"] = now
        self.store.update(save)
        return f"Updated progress for {len(tracked)} requested titles."

    def refresh_metadata(self, identity, progress):
        state = self.store.read()
        source = collection_by_id(state, identity)
        rows = [source] + [row for row in state["collections"]
            if row.get("source_collection_id") == identity and row.get("status") in {"draft", "kept"}]
        cache = {}
        for row in rows:
            reconcile_suggestions(row, state["library"])
            for item in row.get("missing", []):
                key = (providers.clean(item["title"]), item["year"], row["media_type"])
                if key not in cache:
                    progress("Checking catalog metadata for " + item["title"] + "...")
                    try:
                        cache[key] = providers.title_metadata(state["settings"], {**item, "media_type": row["media_type"]}, strict=True)
                    except DomainError:
                        cache[key] = "unavailable"
        ids = {row["id"] for row in rows}
        def save(current):
            for row in current["collections"]:
                if row["id"] not in ids:
                    continue
                reconcile_suggestions(row, current["library"])
                for item in row.get("missing", []):
                    key = (providers.clean(item["title"]), item["year"], row["media_type"])
                    if key in cache:
                        metadata = cache[key]
                        if metadata == "unavailable":
                            item["metadata_error"] = "Catalog unavailable; retry later."
                            if not item.get("metadata"):
                                item["metadata_status"] = "unavailable"
                        else:
                            item.update(metadata=metadata, metadata_status="verified" if metadata else "needs_check",
                                        metadata_checked_at=time.time(), metadata_error="")
        self.store.update(save)
        if state["settings"].get("llm_url") and state["settings"].get("llm_model"):
            try:
                self.describe({"ids": list(ids)}, progress)
            except DomainError:
                return "Catalog metadata saved. Fit notes could not refresh; existing picks are preserved. Retry Check metadata to refresh their notes."
        return f"Checked {len(cache)} titles; {sum(isinstance(value, dict) for value in cache.values())} exact catalog matches. Unresolved picks are kept for review."

    def improve(self, identity, progress, payload=None):
        from .discovery import propose_improvement
        payload = payload or {}
        if type(payload.get("auto_request", False)) is not bool:
            raise DomainError("Choose whether to request missing suggestions.")
        state = self.store.read()
        source = collection_by_id(state, identity)
        if source.get('family_rolling'):
            raise DomainError('This shelf is maintained automatically. Improve the broader family collection instead.')
        original = source
        source = copy.deepcopy(source)
        # Accumulate pending choices without changing the live shelf or its revision.
        for previous in sorted(state["collections"], key=lambda row: row.get("created_at", 0)):
            if previous.get("source_collection_id") == identity and previous.get("status") in {"draft", "kept"}:
                for field in ("items", "missing", "available"):
                    existing = {(providers.clean(row["title"]), row["year"]) for row in source.get(field, [])}
                    for item in previous.get(field, []):
                        key = (providers.clean(item["title"]), item["year"])
                        if key not in existing:
                            source.setdefault(field, []).append(copy.deepcopy(item))
                            existing.add(key)
        progress("Finding additions across your library and reviewing their fit…")
        candidate = propose_improvement(state, source, payload, lambda prompt: providers.call_llm(state["settings"], prompt),
                                        metadata_lookup=lambda item: providers.title_metadata(state["settings"], item))
        candidate["source_revision"] = membership_revision(original)
        candidate["changes"] = {
            "added": [row["title"] for row in candidate["items"] if row["id"] not in {i["id"] for i in original["items"]}],
            "removed": [row["title"] for row in original["items"] if row["id"] not in {i["id"] for i in candidate["items"]}]}
        reconcile_suggestions(candidate, state["library"])
        self.store.update(lambda current: current["collections"].append(candidate))
        self.refresh_metadata(candidate["id"], progress)
        if payload.get("auto_request") and payload.get("mode") == "expand":
            self.request_missing(candidate["id"], {"all": True}, progress)
        return "An improvement draft is ready: " + candidate["name"]

    def discover(self, payload, progress):
        from .discovery import propose_collection
        if type(payload.get("auto_request", False)) is not bool:
            raise DomainError("Choose whether to request missing suggestions.")
        state = self.store.read()
        progress("Finding a collection idea and independently reviewing its titles…")
        candidate = propose_collection(state, payload, lambda prompt: providers.call_llm(state["settings"], prompt),
                                       metadata_lookup=lambda item: providers.title_metadata(state["settings"], item))
        reconcile_suggestions(candidate, state["library"])
        self.store.update(lambda current: current["collections"].append(candidate))
        if payload.get("auto_request") and payload.get("mode") == "expand":
            self.request_missing(candidate["id"], {"all": True}, progress)
        return "A new collection is ready to review: " + candidate["name"]

    def describe(self, payload, progress):
        from .context import describe_collections
        state = self.store.read()
        ids = payload.get("ids")
        if ids is not None and (not isinstance(ids, list) or any(not isinstance(identity, str) for identity in ids)):
            raise DomainError("Choose collections to explain.")
        rows = [row for row in state["collections"] if row["status"] != "archived" and row.get("origin") != "drift"
                and (row["id"] in ids if ids is not None else not row.get("context_updated_at"))]
        count = 0
        context_library = list(state["library"])
        checked = set()
        for offset in range(0, len(rows), 4):
            progress("Checking catalog descriptions for missing suggestions…")
            for row in rows[offset:offset + 4]:
                reconcile_suggestions(row, state["library"])
                for item in row.get("missing", []):
                    key = (providers.clean(item["title"]), item["year"], row["media_type"])
                    if key in checked or item.get("reason") == "Ambiguous library match":
                        continue
                    checked.add(key)
                    metadata = item.get("metadata") if item.get("metadata_status") == "verified" else providers.title_metadata(state["settings"], {**item, "media_type": row["media_type"]})
                    if metadata:
                        context_library.append(metadata)
            progress(f"Writing the connection and fit notes for collections {offset + 1}–{min(offset + 4, len(rows))} of {len(rows)}…")
            descriptions = describe_collections(rows[offset:offset + 4], context_library,
                lambda prompt: providers.call_llm(state["settings"], prompt), include_member_notes=False)
            def save(current):
                for result in descriptions:
                    target = collection_by_id(current, result["id"])
                    reconcile_suggestions(target, current["library"])
                    target.update(thesis=result["thesis"], context_updated_at=time.time())
                    notes = {(providers.clean(row["title"]), row["year"]): row for row in result["notes"]}
                    for field in ("items", "missing", "available"):
                        for item in target.get(field, []):
                            key = (providers.clean(item["title"]), item["year"])
                            if key in notes:
                                item["fit_reason" if item.get("reason") == "Ambiguous library match" else "reason"] = notes[key]["reason"]
                                if field == "missing":
                                    item["fit_status"] = notes[key].get("fit_status", "unassessed")
            self.store.update(save)
            count += len(descriptions)
        return f"Added collection context and fit notes to {count} collections."

    def apply_improvement(self, identity, progress):
        state = self.store.read()
        draft = collection_by_id(state, identity)
        if draft.get("origin") != "improve" or draft["status"] not in {"draft", "kept"}:
            raise DomainError("Choose an unpublished improvement draft.")
        source = collection_by_id(state, draft.get("source_collection_id"))
        if source["status"] != "published" or not source.get("managed"):
            raise DomainError("Choose Manage rotation here on the original Plex collection before applying changes to it.")
        if draft.get("source_revision") != membership_revision(source):
            raise DomainError("The original changed after this draft was made. Create a fresh improvement draft.")
        replacement = copy.deepcopy(source)
        replacement.update(items=draft["items"], missing=draft["missing"], available=draft.get("available", []), description=draft["description"],
                           thesis=draft["thesis"], origin="manual", permanent=True,
                           auto_add_arrivals=draft.get("auto_add_arrivals", source.get("auto_add_arrivals", False)))
        reconcile_suggestions(replacement, state["library"])
        self.store.backup()
        progress("Applying the reviewed membership changes to Plex…")
        providers.publish(state["settings"], replacement, state["server_id"], expected_source=source)
        def apply(current):
            target = collection_by_id(current, source["id"])
            target.update(replacement)
            target.pop("review", None)
            collection_by_id(current, identity).update(status="archived", applied_at=time.time())
        self.store.update(apply)
        return "Applied the reviewed changes to " + source["name"] + "."

    def import_legacy(self, progress):
        from .migration import import_legacy
        if not self.legacy_dir:
            raise DomainError("No desktop import folder is mounted. Set CM_LEGACY_DIR to a read-only source folder.")
        self.store.backup()
        progress("Importing your desktop collections…")
        result = self.store.update(lambda state: import_legacy(self.legacy_dir, state))
        return "Desktop import complete. Sync Plex to verify library ownership before publishing."

    def import_trakt(self, payload, progress):
        state = self.store.read()
        media_type = payload.get("media_type", "movie")
        if media_type not in {"movie", "show"}:
            raise DomainError("Choose movies or TV shows.")
        progress("Reading the Trakt list…")
        url = str(payload.get("url") or "")
        titles = providers.trakt_items(state["settings"], url, media_type)
        if not str(payload.get("name") or "").strip():
            payload = dict(payload, name=urlparse(url).path.rstrip("/").split("/")[-1].replace("-", " ").title())
        candidate = create_candidate(payload, state["library"], titles)
        candidate["origin"] = "trakt"
        self.store.update(lambda current: current["collections"].append(candidate))
        return "Imported " + candidate["name"] + " as a draft."

    def review_new_arrivals(self, progress):
        from .new_arrivals import review, save_review
        state = self.store.read()
        progress("Comparing new arrivals with your permanent collections...")
        try:
            suggestions, reviewed = review(state, lambda prompt: providers.call_llm(state['settings'], prompt))
        except Exception:
            self.store.update(lambda current: current.setdefault('new_arrivals', {}).update(
                error='Arrival review could not finish. Pending titles are retained; use Review new arrivals to retry.'))
            raise
        if not reviewed:
            return 'No new titles awaiting review, or no published permanent collections yet.'
        self.store.update(lambda current: save_review(current, suggestions, reviewed))
        return f"Reviewed {len(reviewed)} new titles; found {len(suggestions)} collection suggestions. Nothing added automatically."

    def configure_family_shelf(self, payload, progress):
        from .family_shelf import configure
        return configure(self, payload, progress)

    def refresh_family_shelf(self, progress):
        from .family_shelf import refresh
        return refresh(self, progress)

    def act_on_arrival(self, identity, action, progress):
        from .new_arrivals import theme_revision, shelves
        state = self.store.read()
        suggestion = next((s for s in state.get('new_arrivals', {}).get('suggestions', []) if s['id'] == identity), None)
        if not suggestion or suggestion['status'] != 'pending':
            raise DomainError('This suggestion has already been handled. Refresh the list.')
        if action not in {'add', 'dismiss'}:
            raise DomainError('Choose Add or Dismiss.')
        if action == 'add':
            candidate = collection_by_id(state, suggestion['collection_id'])
            item = next((i for i in state['library'] if str(i['id']) == suggestion['item_id']), None)
            if candidate not in shelves(state) or not item:
                raise DomainError('The collection or title is no longer available. Sync Plex first.')
            if theme_revision(candidate) != suggestion['theme_revision']:
                raise DomainError('This collection’s theme changed. Sync Plex, then review new arrivals again.')
            if item['media_type'] != candidate['media_type']:
                raise DomainError('The title no longer matches this collection’s library.')
            if str(item['id']) not in {str(i['id']) for i in candidate['items']}:
                self._add_items(candidate, [dict(item, reason=suggestion['reason'])], state, progress)
        def save(current):
            target = next(s for s in current['new_arrivals']['suggestions'] if s['id'] == identity)
            target.update(status='added' if action == 'add' else 'dismissed', handled_at=time.time())
        self.store.update(save)
        return ('Added ' if action == 'add' else 'Dismissed suggestion for ') + suggestion['title'] + ' / ' + suggestion['collection_name'] + '.'

    def schedule_once(self, now=None):
        state = self.store.read()
        settings = state["settings"]
        now = time.time() if now is None else now
        if self.busy:
            return
        attempts = state.get("schedule_attempts", {})
        automatic = settings["advanced"]["auto_publish"]
        last_drift = state["drift"].get("last_activated_at", 0) if automatic else state["drift"]["last_generated_at"]
        active = set(state["drift"].get("active_batch_ids", []))
        if automatic and state["drift"].get("last_activated_at", 0) and sum(c["id"] in active and self.temporary(c) and self.eligible(c) for c in state["collections"]) < settings["drift_slots"]:
            last_drift = 0  # Refill after promotion or a slot-count increase.
        generation_due = now - state["drift"].get("last_generated_at", 0) >= settings.get("drift_generation_hours", 168) * 3600
        drift_hours = settings["drift_interval_hours"] if automatic else settings.get("drift_generation_hours", 168)
        season_changed = context_key(active_events(settings, now)) != state["drift"].get("seasonal_context", "")
        if generation_due or season_changed:
            last_drift = 0
        pending_requests = any(item.get("requested_at") for c in state["collections"] if c["status"] != "archived"
                               for field in ("missing", "available") for item in c.get(field, []))
        due = [
            ("request status", pending_requests, state.get("requests_checked_at", 0), 1/60, self.refresh_requests),
            ("rotation", settings["advanced"]["schedule_enabled"] and settings["advanced"]["home_enabled"],
             state["last_rotation_at"], settings["rotation_hours"], self.rotate),
            ("Drift", settings["advanced"].get("drift_schedule_enabled") and settings.get("llm_model")
             and not state["drift"]["diagnostics"].get("needs_attention"),
             last_drift, drift_hours, self.refresh_drift),
            ("library sync", settings["advanced"].get("sync_enabled", True) and bool(settings.get("plex_token")),
             state["synced_at"], settings.get("library_sync_minutes", 10) / 60, self.sync),
            ("family shelf", state.get('family_shelf',{}).get('enabled')
             and state['synced_at'] > state.get('family_shelf',{}).get('last_checked_at',0),
             state.get('family_shelf',{}).get('last_checked_at',0), 0, self.refresh_family_shelf),
            ("new arrivals", settings["advanced"].get("new_arrival_suggestions") and bool(settings.get("llm_model"))
             and bool(state.get("new_arrivals", {}).get("pending")),
             state.get("new_arrivals", {}).get("last_review_at", 0), 24, self.review_new_arrivals),
        ]
        for kind, enabled, last_success, hours, operation in due:
            retry_delay = 60 if kind in {"library sync", "request status"} else 86400 if kind == "new arrivals" else 3600 if kind == 'family shelf' and state.get('family_shelf',{}).get('error') else 60 if kind == 'family shelf' else 900
            if not enabled or now - last_success < hours * 3600 or now - attempts.get(kind, 0) < retry_delay:
                continue
            def scheduled(progress, run=operation):
                if run not in {self.sync, self.refresh_requests} and time.time() - self.store.read()["synced_at"] > 6 * 3600:
                    self.sync(progress)
                return run(progress)
            self.submit("Scheduled " + kind, scheduled)
            self.store.update(lambda current: current.setdefault("schedule_attempts", {}).update({kind: now}))
            return

    def refresh_drift(self, progress):
        state = self.store.read()
        if context_key(active_events(state["settings"])) != state["drift"].get("seasonal_context", ""):
            return self.generate(progress)
        # Recover a partial publish without paying for another generation.
        if state["settings"]["advanced"]["auto_publish"]:
            current = state["drift"].get("current_batch_ids", [])
            active = set(state["drift"].get("active_batch_ids", []))
            batch = [c for c in state["collections"] if c["id"] in current and self.temporary(c) and c["status"] != "archived"]
            if (batch and (len(batch) >= state["settings"]["drift_slots"] or state["drift"].get("incremental")) and set(current) != active
                    and state["drift"]["last_generated_at"] > state["drift"].get("last_activated_at", 0)):
                return self.activate_drift(progress)
        pool = state["drift"].get("pool_ids", [])
        if pool and time.time() - state["drift"]["last_generated_at"] < state["settings"].get("drift_generation_hours", 168) * 3600:
            if not state["settings"]["advanced"]["auto_publish"]:
                return "The weekly Drift pool is ready for manual review."
            return self.switch_drift(progress)
        return self.generate(progress)

    def switch_drift(self, progress):
        state = self.store.read()
        if not state["drift"].get("pool_ids"):
            raise DomainError("Generate a weekly pool before switching its shelves.")
        selected = self._choose_pool(state)
        if not selected:
            raise DomainError("No unchanged reviewed shelves remain in this week's pool. Generate a new pool.")
        self.store.update(lambda s:s["drift"].update(current_batch_ids=selected, incremental=True))
        return self.activate_drift(progress)

    def start_scheduler(self):
        def loop():
            while not self.stop.wait(30):
                try:
                    self.schedule_once()
                except DomainError:
                    continue
                except Exception as exc:
                    LOG.error("Schedule check failed (%s)", type(exc).__name__)
        self.scheduler = threading.Thread(target=loop, name="schedule", daemon=True)
        self.scheduler.start()

    def close(self):
        self.stop.set()
        self.executor.shutdown(wait=True, cancel_futures=True)


def json_dumps(value):
    import json
    return json.dumps(value, ensure_ascii=False)
