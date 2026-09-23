"""Desktop migration boundaries, membership proof, privacy and repeatability."""

from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from collection_web.migration import import_legacy, is_excluded_legacy_shelf
from collection_web.store import DomainError, Store, public_state


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "legacy"
        self.source.mkdir()
        self.state = Store(self.root / "web").read()
        self.config = {"plex_url": "http://plex:32400", "plex_token": "private-plex",
                       "radarr_key": "private-arr", "ai_discover_provider": "OpenAI",
                       "ai_discover_model": "test-model", "openai_api_key": "private-curator",
                       "user_nicknames": {"private-login": "PrivateViewer"},
                       "weekly_drift": {"users": ["PrivateViewer"]}}
        self.shelves = {"After Hours": {"type": "movie", "description": "One difficult night.",
                                       "_rotation": {"in_pool": True, "active": True},
                                       "items": [{"title": "Night, Again", "year": 2001, "found": False},
                                                 {"title": "Imaginary Hit", "year": 2010, "found": True}]}}
        self.write_json("collection_manager_config.json", self.config)
        self.write_json("collections_data.json", self.shelves)
        with closing(sqlite3.connect(self.source / "media_cache.db")) as db:
            db.execute("CREATE TABLE media (media_type TEXT,title TEXT,release_year INTEGER,"
                       "plex_rating_key TEXT,genres_json TEXT,plot_summary TEXT,plex_view_count INTEGER)")
            db.executemany("INSERT INTO media VALUES (?,?,?,?,?,?,?)", [
                ("movie", "Night, Again", 2001, "42", '["Thriller"]', "A midnight encounter.", 7),
                ("movie", "Imaginary Hit", 2010, None, "[]", "", 0),
                ("show", "Night, Again", 2001, "43", "[]", "", 2),
                ("movie", "Night, Again", 2002, "44", "[]", "", 1)])
            db.execute("CREATE TABLE watch_signals (user_key TEXT,total_plays INTEGER)")
            db.execute("INSERT INTO watch_signals VALUES ('private-watch-user',999)")
            db.commit()

    def write_json(self, filename, value):
        (self.source / filename).write_text(json.dumps(value), encoding="utf-8")

    def test_matching_uses_exact_title_year_type_and_actual_plex_key(self):
        report = import_legacy(self.source, self.state)
        collection = self.state["collections"][0]
        self.assertEqual([row["id"] for row in collection["items"]], ["42"])
        self.assertEqual(collection["missing"][0]["title"], "Imaginary Hit")
        self.assertEqual(report["library_imported"], 3)
        self.assertEqual(report["library_skipped"], 1)
        self.assertEqual(report["items_missing"], 1)
        self.assertEqual(collection["status"], "draft")
        self.assertEqual(collection["origin"], "import")
        self.assertFalse(collection["managed"])
        self.assertFalse(collection["home"])
        self.assertFalse(collection["active"])
        self.assertTrue(collection["permanent"])
        self.assertTrue(collection["rotation_enabled"])
        self.assertEqual(self.state["synced_at"], 0)
        self.assertTrue(all(row["library_id"] == "" for row in self.state["library"]))

    def test_automation_is_disabled_and_secrets_and_identities_stay_private(self):
        self.state["settings"]["advanced"].update(schedule_enabled=True, auto_publish=True)
        report = import_legacy(self.source, self.state)
        self.assertFalse(self.state["settings"]["advanced"]["schedule_enabled"])
        self.assertFalse(self.state["settings"]["advanced"]["auto_publish"])
        self.assertEqual(self.state["settings"]["llm_url"], "https://api.openai.com/v1")
        self.assertEqual(self.state["settings"]["llm_key"], "private-curator")
        public = json.dumps(public_state(self.state))
        for secret in ("private-curator", "private-plex", "private-arr", "PrivateViewer",
                       "private-login", "private-watch-user"):
            self.assertNotIn(secret, public)
            self.assertNotIn(secret, json.dumps(report))
        self.assertEqual(next(r for r in self.state["library"] if r["id"] == "42")["play_count"], 7)

    def test_personal_and_unreviewed_drift_are_excluded_even_if_marker_missing(self):
        base = self.shelves["After Hours"]
        self.shelves.update({
            "Secret Picks": {**base, "_ai": {"source_user": "PrivateViewer"}},
            "Personal lane": {**base, "lane": "for_you"},
            "PrivateViewer's Shelf": deepcopy(base),
            "Lost personal marker": deepcopy(base),
            "Lost drift marker": deepcopy(base),
            "Temporary": {**base, "_weekly_drift": {"temporary": True}},
        })
        self.write_json("collections_data.json", self.shelves)
        self.write_json("weekly_drift.json", {"pool": [
            {"name": "Lost personal marker", "source_user": "PrivateViewer"},
            {"name": "Lost drift marker", "source": "Discover"}]})
        report = import_legacy(self.source, self.state)
        self.assertEqual([c["name"] for c in self.state["collections"]], ["After Hours"])
        self.assertEqual(report["personal_skipped"], 4)
        self.assertEqual(report["drift_skipped"], 2)
        self.assertNotIn("PrivateViewer", json.dumps(self.state))
        self.assertTrue(is_excluded_legacy_shelf(self.state, "Lost personal marker", "movie"))
        self.assertTrue(is_excluded_legacy_shelf(self.state, "Temporary", "show"))
        self.assertFalse(is_excluded_legacy_shelf(self.state, "After Hours", "movie"))
        self.assertNotIn("excluded_name_hashes", report)

    def test_repeated_import_is_idempotent_and_preserves_configured_secrets(self):
        import_legacy(self.source, self.state)
        self.state["settings"]["plex_token"] = "replacement-token"
        self.state["settings"]["llm_key"] = "replacement-curator"
        self.state["collections"][0]["name"] = "Edited name"
        report = import_legacy(self.source, self.state)
        self.assertEqual(len(self.state["collections"]), 1)
        self.assertEqual(len(self.state["library"]), 3)
        self.assertEqual(report["collections_existing"], 1)
        self.assertEqual(report["collections_imported"], 0)
        self.assertEqual(self.state["settings"]["plex_token"], "replacement-token")
        self.assertEqual(self.state["settings"]["llm_key"], "replacement-curator")
        self.assertEqual(self.state["collections"][0]["name"], "Edited name")

    def test_source_files_and_ledger_are_not_modified(self):
        self.write_json("weekly_drift.json", {"pool": []})
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.iterdir()}
        import_legacy(self.source, self.state)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.iterdir()}
        self.assertEqual(before, after)

    def test_unknown_provider_is_not_silently_replaced(self):
        for provider in ("Auto", "Copilot", ""):
            with self.subTest(provider=provider):
                state = Store(self.root / ("web-" + (provider or "empty"))).read()
                self.write_json("collection_manager_config.json", {**self.config, "ai_discover_provider": provider})
                report = import_legacy(self.source, state)
                self.assertFalse(report["llm_configured"])
                self.assertEqual(state["settings"]["llm_url"], "")
                self.assertEqual(state["settings"]["llm_key"], "")

    def test_explicit_ollama_uses_compatible_endpoint_without_openai_key(self):
        self.write_json("collection_manager_config.json", {**self.config, "ai_discover_provider": "Ollama",
                                                           "ai_ollama_url": "http://models:11434/"})
        import_legacy(self.source, self.state)
        self.assertEqual(self.state["settings"]["llm_url"], "http://models:11434/v1")
        self.assertEqual(self.state["settings"]["llm_key"], "")

    def test_existing_provider_is_never_mixed_with_legacy_credentials(self):
        self.state["settings"].update(llm_url="http://local:11434/v1", llm_model="local-model")
        import_legacy(self.source, self.state)
        self.assertEqual(self.state["settings"]["llm_url"], "http://local:11434/v1")
        self.assertEqual(self.state["settings"]["llm_key"], "")

    def test_credential_bearing_urls_are_not_exposed_in_public_settings(self):
        self.write_json("collection_manager_config.json", {**self.config, "plex_url": "http://secret:password@plex:32400"})
        import_legacy(self.source, self.state)
        self.assertEqual(self.state["settings"]["plex_url"], "")
        self.assertNotIn("password", json.dumps(public_state(self.state)))

    def test_missing_or_invalid_source_leaves_state_unchanged(self):
        before = deepcopy(self.state)
        with self.assertRaises(DomainError):
            import_legacy(self.root / "missing", self.state)
        self.assertEqual(self.state, before)
        self.write_json("collections_data.json", [])
        with self.assertRaises(DomainError):
            import_legacy(self.source, self.state)
        self.assertEqual(self.state, before)

    def test_live_metadata_is_not_replaced_with_cached_rows(self):
        live = {"id": "42", "title": "Night, Again", "year": 2001, "media_type": "movie",
                "library_id": "9", "summary": "Fresh", "play_count": 11}
        self.state["library"] = [live]
        import_legacy(self.source, self.state)
        self.assertEqual(self.state["library"][0], live)


if __name__ == "__main__":
    unittest.main()
