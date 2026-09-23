"""Workflow boundaries against temporary state and mocked external services."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch

from collection_web.curation import generate_batch, validate_candidate
from collection_web.service import Service, membership_revision, reconcile_suggestions
from collection_web.store import DomainError, Store
from test_web_curation import Curator, library_rows, proposal


class ServiceWorkflowTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.store = Store(Path(self.directory.name))
        self.service = Service(self.store)
        self.rows = library_rows(20)
        self.ideas = [proposal(self.rows[i:i + 5], i) for i in range(0, 20, 5)]
        self.store.update(lambda state: state.update(library=deepcopy(self.rows), synced_at=1, server_id="test-server"))
        self.store.update(lambda state: state["settings"].update(drift_tv_slots=0))

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def drafts(self):
        return generate_batch(self.rows, [], [], {"batch_size": 4}, Curator(self.ideas))["collections"]

    def add(self, rows):
        self.store.update(lambda state: state["collections"].extend(deepcopy(rows)))

    def test_generation_preserves_failed_batch_and_persists_diagnostics(self):
        saved = self.drafts()
        self.add(saved)
        with patch("collection_web.integrations.call_llm", return_value={"concepts": []}), \
                patch("collection_web.integrations.watch_counts", return_value={}), self.assertRaises(DomainError):
            self.service.generate(lambda _: None)
        state = self.store.read()
        self.assertEqual(state["collections"], saved)
        self.assertFalse(state["drift"]["diagnostics"]["publishable"])
        self.assertEqual(state["drift"]["last_generated_at"], 0)

    def test_default_generation_creates_reviewable_drafts_without_publishing(self):
        curator = Curator(self.ideas)
        with patch("collection_web.integrations.call_llm", side_effect=lambda settings, prompt: curator(prompt)), \
                patch("collection_web.integrations.watch_counts", return_value={}), \
                patch("collection_web.integrations.publish") as publish:
            self.service.generate(lambda _: None)
        publish.assert_not_called()
        state = self.store.read()
        self.assertEqual(len(state["collections"]), 4)
        self.assertTrue(all(row["status"] == "draft" for row in state["collections"]))
        self.assertTrue(all(validate_candidate(row, state["library"]) == [] for row in state["collections"]))
        self.assertNotIn("PRIVATE-VIEWER", "".join(curator.prompts))

    def test_unavailable_optional_watching_does_not_block_generation(self):
        curator = Curator(self.ideas)
        with patch("collection_web.integrations.call_llm", side_effect=lambda settings, prompt: curator(prompt)), \
                patch("collection_web.integrations.watch_counts", side_effect=DomainError("Not connected")):
            self.service.generate(lambda _: None)
        state = self.store.read()
        self.assertEqual(state["drift"]["diagnostics"]["watch_signal_status"], "unavailable")
        self.assertEqual(len(state["collections"]), 4)

    def test_acquisition_ideas_survive_later_generation(self):
        idea = {"id": "saved-idea", "concept": "Family adventures", "thesis": "Families explore together.",
                "media_type": "movie", "missing": [{"title": "Missing", "year": 2020}], "items": []}
        self.store.update(lambda s: s["drift"].update(opportunities=[idea]))
        with patch("collection_web.curation.generate_batch", return_value={"collections": self.drafts(),
                "opportunities": [], "diagnostics": {}}):
            self.service.generate(lambda _: None)
        self.assertEqual(self.store.read()["drift"]["opportunities"], [idea])

    def test_publish_refuses_changed_drift_items_before_any_remote_write(self):
        draft = self.drafts()[0]
        draft["items"] = self.rows[5:10]
        self.add([draft])
        with patch("collection_web.integrations.publish") as publish, self.assertRaises(DomainError):
            self.service.publish(draft["id"], lambda _: None)
        publish.assert_not_called()

    def test_successful_publish_keeps_review_and_records_owned_remote_id(self):
        draft = self.drafts()[0]
        self.add([draft])
        with patch("collection_web.integrations.publish", return_value="remote-id"):
            self.service.publish(draft["id"], lambda _: None)
        row = self.store.read()["collections"][0]
        self.assertEqual(row["plex_id"], "remote-id")
        self.assertTrue(row["rotation_enabled"])
        self.assertEqual(validate_candidate(row, self.rows), [])

    def test_rotation_reveals_new_shelves_before_hiding_existing(self):
        rows = [{"id": "old", "name": "Old", "origin": "manual", "status": "published", "managed": True,
                 "rotation_enabled": True, "home": True, "last_rotated_at": 5, "items": []},
                {"id": "new", "name": "New", "origin": "manual", "status": "published", "managed": True,
                 "rotation_enabled": True, "home": False, "last_rotated_at": 0, "items": []}]
        self.add(rows)
        self.store.update(lambda state: state["settings"].update(permanent_movie_slots=1, permanent_show_slots=0))
        calls = []
        with patch("collection_web.integrations.set_visibility", side_effect=lambda s, c, active, server_id: calls.append((c["id"], active))):
            self.service.rotate(lambda _: None)
        self.assertEqual(calls, [("new", True), ("old", False)])

    def test_partial_rotation_failure_keeps_previous_home_and_records_successes(self):
        rows = [{"id": name, "name": name, "origin": "manual", "status": "published", "managed": True,
                 "rotation_enabled": True, "home": name == "old", "last_rotated_at": 5 if name == "old" else 0,
                 "items": []} for name in ("old", "new-a", "new-b")]
        self.add(rows)
        self.store.update(lambda state: state["settings"].update(permanent_movie_slots=2, permanent_show_slots=0))
        calls = []
        def visibility(settings, candidate, active, server_id):
            calls.append((candidate["id"], active))
            if candidate["id"] == "new-b":
                raise DomainError("Remote request failed")
        with patch("collection_web.integrations.set_visibility", side_effect=visibility), self.assertRaises(DomainError):
            self.service.rotate(lambda _: None)
        state = self.store.read()
        homes = {row["id"] for row in state["collections"] if row["home"]}
        self.assertEqual(homes, {"old", "new-a"})
        self.assertNotIn(("old", False), calls)
        self.assertEqual(state["last_rotation_at"], 0)

    def test_stale_drift_cannot_rotate_onto_home(self):
        candidate = self.drafts()[0]
        candidate.update(status="published", managed=True, rotation_enabled=True, home=False)
        candidate["items"] = self.rows[5:10]
        self.add([candidate])
        with patch("collection_web.integrations.set_visibility") as visibility, self.assertRaises(DomainError):
            self.service.rotate(lambda _: None)
        visibility.assert_not_called()

    def test_rotation_clears_last_excluded_owned_home_shelf(self):
        self.add([{"id": "last", "name": "Last", "status": "published", "managed": True,
                   "rotation_enabled": False, "home": True, "items": []},
                  {"id": "other", "name": "Other", "status": "published", "managed": False,
                   "rotation_enabled": False, "home": True, "items": []}])
        with patch("collection_web.integrations.set_visibility") as visibility:
            self.service.rotate(lambda _: None)
        self.assertEqual(visibility.call_count, 1)
        self.assertEqual(visibility.call_args.args[1]["id"], "last")
        self.assertFalse(visibility.call_args.args[2])
        self.assertFalse(self.store.read()["collections"][0]["home"])
        self.assertTrue(self.store.read()["collections"][1]["home"])

    def test_rotation_retires_old_drift_only_after_full_replacement_is_published(self):
        rows = [{"id": cid, "name": cid, "status": "published", "managed": True, "origin": "drift",
                 "permanent": cid == "kept", "rotation_enabled": True, "home": cid == "old", "items": []}
                for cid in ("old", "kept", "new-a", "new-b")]
        self.add(rows)
        self.store.update(lambda state: state["drift"].update(current_batch_ids=["new-a", "new-b"]))
        self.store.update(lambda state: state["settings"].update(drift_slots=2))
        calls = []
        with patch("collection_web.curation.validate_candidate", return_value=[]), \
                patch("collection_web.integrations.set_visibility", side_effect=lambda s, c, a, sid: calls.append((c["id"], a))):
            self.service.rotate(lambda _: None)
        result = {c["id"]: c for c in self.store.read()["collections"]}
        self.assertEqual(result["old"]["status"], "archived")
        self.assertEqual(result["kept"]["status"], "published")
        self.assertNotIn(("old", True), calls)
        self.assertGreater(calls.index(("old", False)), calls.index(("new-a", True)))

    def test_partial_drift_replacement_keeps_old_shelves_available(self):
        rows = [{"id": cid, "name": cid, "status": "draft" if cid == "new-b" else "published", "managed": True,
                 "origin": "drift", "permanent": False, "rotation_enabled": True, "home": False, "items": []}
                for cid in ("old", "new-a", "new-b")]
        self.add(rows)
        self.store.update(lambda state: state["drift"].update(active_batch_ids=["old"]))
        self.store.update(lambda state: state["drift"].update(current_batch_ids=["new-a", "new-b"]))
        self.store.update(lambda state: state["settings"].update(drift_slots=2))
        with patch("collection_web.curation.validate_candidate", return_value=[]), patch("collection_web.integrations.set_visibility"):
            self.service.rotate(lambda _: None)
        self.assertEqual(self.store.read()["collections"][0]["status"], "published")

    def test_successful_generation_preserves_reviewed_drafts_and_published_shelves(self):
        rows = [{"id": cid, "name": cid, "status": "published" if cid == "published" else "draft",
                 "origin": "drift", "permanent": cid == "kept", "items": []} for cid in ("old-draft", "kept", "published")]
        self.add(rows)
        curator = Curator(self.ideas)
        with patch("collection_web.integrations.call_llm", side_effect=lambda settings, prompt: curator(prompt)), \
                patch("collection_web.integrations.watch_counts", return_value={}):
            self.service.generate(lambda _: None)
        state = self.store.read()
        self.assertEqual([row["status"] for row in state["collections"][:3]], ["draft", "draft", "published"])
        self.assertEqual(len(state["drift"]["current_batch_ids"]), 4)

    def test_sync_does_not_silently_add_new_items_to_a_reviewed_drift_draft(self):
        candidate = self.drafts()[0]
        candidate["missing"] = [{"title": self.rows[10]["title"], "year": self.rows[10]["year"], "media_type": "movie"}]
        self.add([candidate])
        with patch("collection_web.integrations.scan_library", return_value=(self.rows, [], "test-server")):
            self.service.sync(lambda _: None)
        after = self.store.read()["collections"][0]
        self.assertEqual({row["id"] for row in after["items"]}, {row["id"] for row in candidate["items"]})

    def test_stale_job_recovery_reports_interruption(self):
        self.store.update(lambda state: state["jobs"].append({"id": "interrupted", "status": "running"}))
        recovered = Service(self.store)
        try:
            job = self.store.read()["jobs"][0]
            self.assertEqual(job["status"], "failed")
            self.assertIn("restarted", job["message"])
        finally:
            recovered.close()

    def test_improvement_cannot_overwrite_an_original_that_changed(self):
        source = {"id": "source", "name": "Original", "items": self.rows[:5], "status": "published", "managed": True}
        draft = {"id": "edit", "origin": "improve", "status": "draft", "source_collection_id": "source",
                 "source_revision": membership_revision(source)}
        source["items"] = self.rows[5:10]
        self.add([source, draft])
        with patch("collection_web.integrations.publish") as publish, self.assertRaises(DomainError):
            self.service.apply_improvement("edit", lambda _: None)
        publish.assert_not_called()

    def test_failed_scans_are_durable_and_escalate_without_losing_library(self):
        with patch("collection_web.integrations.scan_library", side_effect=DomainError("No library")):
            for _ in range(3):
                with self.assertRaises(DomainError):
                    self.service.sync(lambda _: None)
        state = self.store.read()
        self.assertEqual(state["library"], self.rows)
        self.assertTrue(state["library_diagnostics"]["needs_attention"])

    def test_sync_removes_excluded_personal_collection_tags_before_curating(self):
        from collection_web.migration import _name_hash
        rows = deepcopy(self.rows)
        rows[0]["collections"] = ["PRIVATE-VIEWER Picks", "Space"]
        self.store.update(lambda state: state.update(migration={"excluded_name_hashes": [_name_hash("PRIVATE-VIEWER Picks", "movie")]}))
        with patch("collection_web.integrations.scan_library", return_value=(rows, [], "test-server")):
            self.service.sync(lambda _: None)
        self.assertEqual(self.store.read()["library"][0]["collections"], ["Space"])

    def test_schedules_are_independent_and_failure_backoff_does_not_starve_drift(self):
        self.store.update(lambda state: state["settings"]["advanced"].update(schedule_enabled=True, drift_schedule_enabled=True))
        self.store.update(lambda state: state["settings"].update(llm_model="test"))
        with patch.object(self.service, "submit") as submit:
            self.service.schedule_once(now=1_000_000)
            self.assertEqual(submit.call_args.args[0], "Scheduled rotation")
            self.service.schedule_once(now=1_000_030)
            self.assertEqual(submit.call_args.args[0], "Scheduled Drift")
            self.service.schedule_once(now=1_000_060)
            self.assertEqual(submit.call_count, 2)

    def test_repeated_degraded_drift_pauses_automatic_paid_retries(self):
        self.store.update(lambda state: state["settings"]["advanced"].update(drift_schedule_enabled=True))
        self.store.update(lambda state: state["settings"].update(llm_model="test"))
        self.store.update(lambda state: state["drift"]["diagnostics"].update(needs_attention=True))
        with patch.object(self.service, "submit") as submit:
            self.service.schedule_once(now=1_000_000)
        submit.assert_not_called()

    def test_requested_arrivals_join_published_manual_shelf_with_live_conflict_guard(self):
        candidate = {"id": "tracked", "name": "Tracked", "media_type": "movie", "origin": "manual", "status": "published",
                     "managed": True, "auto_add_arrivals": True, "items": self.rows[:5], "plex_id": "remote",
                     "missing": [{**self.rows[5], "requested_at": 123}, self.rows[6]]}
        self.add([candidate])
        with patch("collection_web.integrations.publish", return_value="remote") as publish:
            self.service.reconcile_arrivals(lambda _: None)
        after = self.store.read()["collections"][0]
        self.assertEqual({r["id"] for r in after["items"]}, {r["id"] for r in self.rows[:6]})
        self.assertEqual(after["missing"], [])
        self.assertEqual(after["available"], [self.rows[6]])
        self.assertEqual(publish.call_args.kwargs["expected_source"]["items"], self.rows[:5])

    def test_arrival_conflict_keeps_requested_title_pending(self):
        candidate = {"id": "tracked", "name": "Tracked", "media_type": "movie", "origin": "manual", "status": "published",
                     "managed": True, "auto_add_arrivals": True, "items": self.rows[:5], "plex_id": "remote",
                     "missing": [{**self.rows[5], "requested_at": 123}]}
        self.add([candidate])
        with patch("collection_web.integrations.publish", side_effect=DomainError("Original changed")):
            self.service.reconcile_arrivals(lambda _: None)
        self.assertEqual(self.store.read()["collections"][0]["missing"], candidate["missing"])

    def test_failed_drift_arrival_review_does_not_repeat_paid_checks_every_sync(self):
        candidate = self.drafts()[0]
        candidate.update(status="published", managed=True, auto_add_arrivals=True,
                         missing=[{**self.rows[15], "requested_at": 123}])
        self.add([candidate])
        with patch("collection_web.arrivals.review_drift_additions", side_effect=DomainError("Weak fit")) as review, \
                patch("collection_web.integrations.publish") as publish:
            self.service.reconcile_arrivals(lambda _: None)
            self.service.reconcile_arrivals(lambda _: None)
        self.assertEqual(review.call_count, 1)
        publish.assert_not_called()
        after = self.store.read()["collections"][0]
        self.assertEqual(after["items"], candidate["items"])
        self.assertEqual(after["missing"][0]["arrival_review_error"], "Weak fit")

    def test_sync_preserves_pending_requests_and_fit_notes_in_drafts(self):
        candidate = {"id": "pending", "name": "Pending", "media_type": "movie", "origin": "discover", "status": "draft",
                     "items": [{**self.rows[0], "reason": "A strong thematic fit"}],
                     "missing": [{"title": "Missing film", "year": 2025, "media_type": "movie", "reason": "Completes the series",
                                  "requested_at": 123, "request_status": "Requested"}]}
        self.add([candidate])
        with patch("collection_web.integrations.scan_library", return_value=(self.rows, [], "test-server")):
            self.service.sync(lambda _: None)
        after = self.store.read()["collections"][0]
        self.assertEqual(after["missing"], candidate["missing"])
        self.assertEqual(after["items"][0]["reason"], "A strong thematic fit")

    def test_background_sync_obeys_ten_minute_interval(self):
        self.store.update(lambda state: state["settings"].update(plex_token="test", library_sync_minutes=10))
        self.store.update(lambda state: state.update(synced_at=1000, schedule_attempts={"library sync": 1000}))
        with patch.object(self.service, "submit") as submit:
            self.service.schedule_once(now=1599)
            submit.assert_not_called()
            self.service.schedule_once(now=1600)
            self.assertEqual(submit.call_args.args[0], "Scheduled library sync")

    def test_discovery_requests_missing_only_with_explicit_external_opt_in(self):
        candidate = {"id": "idea", "name": "Idea", "media_type": "movie", "items": [], "missing": [{"title": "Missing", "year": 2025}]}
        for mode, automatic, expected in (("library", False, 0), ("library", True, 0), ("expand", False, 0), ("expand", True, 1)):
            with patch("collection_web.discovery.propose_collection", return_value=deepcopy(candidate)), \
                    patch.object(self.service, "request_missing") as request:
                self.service.discover({"mode": mode, "auto_request": automatic}, lambda _: None)
                self.assertEqual(request.call_count, expected)

    def test_published_suggestions_remove_members_and_reclassify_owned_titles(self):
        candidate = {"media_type": "movie", "items": self.rows[:1], "missing": [
            {**self.rows[0], "reason": "Duplicate member"}, {**self.rows[1], "reason": "Fits this theme", "requested_at": 123},
            {"title": "Missing", "year": 2025, "reason": "Future addition"}], "available": []}
        reconcile_suggestions(candidate, self.rows)
        self.assertEqual([row["title"] for row in candidate["missing"]], ["Missing"])
        self.assertEqual([row["id"] for row in candidate["available"]], [self.rows[1]["id"]])
        self.assertEqual(candidate["available"][0]["reason"], "Fits this theme")
        self.assertEqual(candidate["available"][0]["requested_at"], 123)

    def test_request_rechecks_owned_titles_before_arr(self):
        candidate = {"id": "stale", "name": "Stale", "media_type": "movie", "origin": "manual", "status": "published",
                     "items": self.rows[:1], "missing": [self.rows[0], self.rows[1]], "managed": True}
        self.add([candidate])
        with patch("collection_web.integrations.request_title") as request:
            self.service.request_missing("stale", {"all": True}, lambda _: None)
        request.assert_not_called()
        after = self.store.read()["collections"][0]
        self.assertEqual(after["missing"], [])
        self.assertEqual([row["id"] for row in after["available"]], [self.rows[1]["id"]])

    def test_nearby_year_or_alias_is_flagged_for_matching_not_requested(self):
        candidate = {"id": "ambiguous", "name": "Ambiguous", "media_type": "movie", "origin": "manual", "status": "published",
                     "items": [], "missing": [{"title": self.rows[0]["title"], "year": self.rows[0]["year"] + 1,
                                               "reason": "Thematic fit"}], "managed": True}
        self.add([candidate])
        with patch("collection_web.integrations.request_title") as request:
            self.service.request_missing("ambiguous", {"all": True}, lambda _: None)
        request.assert_not_called()
        row = self.store.read()["collections"][0]["missing"][0]
        self.assertEqual(row["reason"], "Ambiguous library match")
        self.assertEqual(row["fit_reason"], "Thematic fit")
        self.assertEqual(row["possible_matches"][0]["title"], self.rows[0]["title"])

    def test_add_owned_suggestion_checks_live_source_and_preserves_rotation(self):
        candidate = {"id": "existing", "name": "Existing", "media_type": "movie", "origin": "manual", "status": "published",
                     "managed": True, "home": True, "items": self.rows[:1], "available": [self.rows[1]], "missing": []}
        self.add([candidate])
        with patch("collection_web.integrations.publish", return_value="remote") as publish:
            self.service.add_available("existing", {"item_id": self.rows[1]["id"]}, lambda _: None)
        after = self.store.read()["collections"][0]
        self.assertTrue(after["home"])
        self.assertEqual(len(after["items"]), 2)
        self.assertEqual(after["available"], [])
        self.assertEqual(publish.call_args.kwargs["expected_source"]["items"], self.rows[:1])


if __name__ == "__main__":
    main()
