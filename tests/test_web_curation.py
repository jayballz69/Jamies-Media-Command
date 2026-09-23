"""Headless Drift behavioral checkpoint; no providers or live Plex writes."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import hashlib
import unittest

from collection_web.curation import generate_batch, validate_candidate


def library_rows(count=20):
    return [
        {"id": str(i), "title": f"Film {i}", "year": 2000 + i,
         "media_type": "movie", "library_id": "movies", "genres": ["Thriller"],
         "summary": "An investigator follows a documented conspiracy.",
         "directors": ["A Director"], "actors": ["An Actor"], "studio": "Studio",
         "collections": ["Investigator"], "play_count": i,
         "username": "PRIVATE-VIEWER", "email": "private@example.test"}
        for i in range(count)
    ]


def proposal(rows, index, **updates):
    result = {
        "concept": f"Investigations revealing institutional secrecy, chapter {index}",
        "thesis": "Investigators uncover conspiracies through archival evidence.",
        "source_family": "micro", "media_type": "movie",
        "minimum_owned_hits": 5, "size_reason": "Five complete investigations prove the specific premise.",
        "ideal_titles": [{"title": r["title"], "year": r["year"], "media_type": r["media_type"],
                          "reason": "The investigation exposes a wider institutional conspiracy."} for r in rows],
    }
    result.update(updates)
    return result


class Curator:
    def __init__(self, proposals, reject_batch=False, counterfeit=False):
        self.proposals = proposals
        self.reject_batch = reject_batch
        self.counterfeit = counterfeit
        self.prompts = []
        self.batch_seen = 0

    def __call__(self, prompt):
        self.prompts.append(prompt)
        payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
        if payload["stage"] == "concepts":
            return {"concepts": deepcopy(self.proposals)}
        if payload["stage"] == "selection":
            row = payload["candidate"]
            return {"viable": True, "thesis": row["thesis"],
                    "selection_reason": "The central investigative premise is independently supported.",
                    "keep_ids": [item["id"] for item in row["items"]]}
        if payload["stage"] == "locked_review":
            row = payload["candidate"]
            first = row["items"][0]["id"]
            return {
                "approved": True, "name": f"Paper Trail {first}",
                "description": "Conspiracies exposed by patient investigators.",
                "thesis": row["thesis"], "name_reason": "Each film follows documentary clues into a conspiracy.",
                "coherence": 9, "quality": 8, "originality": 8,
                "item_reviews": [{"id": "hallucination" if self.counterfeit else item["id"],
                                  "fit": 9, "reason": "The plot centers on investigative evidence."}
                                 for item in row["items"]],
            }
        self.batch_seen = len(payload["candidates"])
        return {"approved": not self.reject_batch, "reason": "Varied, specific concepts with distinct items.",
                "keep_ids": [r["id"] for r in payload["candidates"]]}


class HeadlessCurationTests(unittest.TestCase):
    def setUp(self):
        self.rows = library_rows()
        self.ideas = [proposal(self.rows[i:i + 5], i) for i in range(0, 20, 5)]
        self.settings = {"batch_size": 2, "minimum_batch_size": 2}

    def generate(self, curator=None, **kwargs):
        return generate_batch(kwargs.pop("library", self.rows), kwargs.pop("existing", []),
                              kwargs.pop("history", []), kwargs.pop("settings", self.settings),
                              curator or Curator(self.ideas), **kwargs)

    def test_only_exact_verified_owned_items_and_reviewed_metadata_survive(self):
        result = self.generate()
        self.assertEqual(len(result["collections"]), 2)
        row = result["collections"][0]
        self.assertEqual(row["origin"], "drift")
        self.assertEqual(row["status"], "draft")
        self.assertTrue(row["thesis"] and row["name_reason"] and row["concept"])
        self.assertTrue(all(item in self.rows for item in row["items"]))
        self.assertEqual(validate_candidate(row, self.rows), [])

    def test_editor_reviews_surplus_before_final_trim(self):
        curator = Curator(self.ideas)
        self.generate(curator)
        self.assertEqual(curator.batch_seen, 4)

    def test_concept_pass_sees_compact_inventory_beyond_metadata_sample(self):
        rows = library_rows(180)
        curator = Curator(self.ideas)
        self.generate(curator, library=rows)
        payload = json.loads(curator.prompts[0].split("\nINPUT_JSON:\n", 1)[1])
        inventory = payload["library"].get("inventory", {})
        self.assertEqual(len(inventory.get("movie", [])), 180)
        self.assertLess(len(payload["library"]["sample"]), 180)

    def test_personal_proposals_are_rejected_and_identity_never_enters_prompts(self):
        bad = proposal(self.rows[:5], 0, source_family="for_you", source_user="PRIVATE-VIEWER")
        curator = Curator([bad, *self.ideas])
        result = self.generate(curator, history=[{"source_family": "for_you", "name": "PRIVATE-VIEWER"}],
                               settings={**self.settings, "watch_inspiration": True})
        self.assertEqual(len(result["collections"]), 2)
        self.assertNotIn("PRIVATE-VIEWER", "".join(curator.prompts))
        self.assertNotIn("private@example.test", "".join(curator.prompts))
        self.assertTrue(all(r["source_family"] != "for_you" for r in result["collections"]))

    def test_wrong_year_does_not_match_and_missing_becomes_opportunity(self):
        bad = proposal(self.rows[:5], 0)
        for title in bad["ideal_titles"]:
            title["year"] -= 1
        result = self.generate(Curator([bad, *self.ideas]))
        self.assertEqual(len(result["opportunities"]), 1)
        self.assertEqual(len(result["opportunities"][0]["missing"]), 5)

    def test_person_entity_keeps_every_verified_owned_match(self):
        person = proposal(self.rows[:5], 0, source_family="person_creator", entity_axis="actor", entity_name="An Actor")
        curator = Curator([person])
        result = self.generate(curator)
        # A one-shelf result is intentionally blocked, but the full set reaches review.
        review = next(json.loads(p.split("\nINPUT_JSON:\n")[1]) for p in curator.prompts
                      if '"stage":"locked_review"' in p)
        self.assertEqual(len(review["candidate"]["items"]), 20)
        self.assertEqual(result["collections"], [])
        self.assertFalse(result["diagnostics"]["publishable"])

    def test_actor_collection_can_use_item_grounded_catchphrase_name(self):
        rows = deepcopy(self.rows)
        for row in rows[5:]:
            row["actors"] = ["Another Actor"]
        ideas = [proposal(rows[:5], 0, source_family="person_creator", entity_axis="actor", entity_name="An Actor"),
                 proposal(rows[5:10], 5)]
        model = Curator(ideas)
        def named(prompt):
            answer = model(prompt)
            payload = json.loads(prompt.split("\nINPUT_JSON:\n")[1])
            if payload["stage"] == "locked_review" and payload["candidate"].get("entity_axis") == "actor":
                answer.update(name="You Know the Line", name_reason="A recognisable screen-persona reference earned by these credited performances.")
            return answer
        result = self.generate(named, library=rows)
        actor = next(row for row in result["collections"] if row["source_family"] == "person_creator")
        self.assertEqual(actor["name"], "You Know the Line")
        self.assertEqual(validate_candidate(actor, rows), [])

    def test_entity_axis_is_verified_and_cannot_use_actor_for_director(self):
        wrong = proposal(self.rows[:5], 0, source_family="person_creator", entity_axis="director", entity_name="An Actor")
        result = self.generate(Curator([wrong, *self.ideas]))
        self.assertTrue(any("metadata" in r["reason"] for r in result["diagnostics"]["rejected"]))

    def test_batch_rejection_never_returns_a_partial_batch(self):
        result = self.generate(Curator(self.ideas, reject_batch=True))
        self.assertEqual(result["collections"], [])
        self.assertFalse(result["diagnostics"]["publishable"])
        self.assertIn("Varied, specific concepts", result["diagnostics"]["editor_reason"])

    def test_hallucinated_review_keys_are_not_accepted(self):
        result = self.generate(Curator(self.ideas, counterfeit=True))
        self.assertEqual(result["collections"], [])

    def test_existing_and_recent_names_are_deduped(self):
        result = self.generate(existing=[{"name": "Paper Trail 0", "items": []}],
                               history=[{"name": "Paper Trail 5", "source_family": "micro"}])
        self.assertEqual({r["name"] for r in result["collections"]}, {"Paper Trail 10", "Paper Trail 15"})

    def test_duplicate_item_sets_are_rejected(self):
        result = self.generate(Curator([self.ideas[0], self.ideas[0], self.ideas[1]]))
        self.assertEqual(len(result["collections"]), 2)

    def test_post_review_mutation_invalidates_publish_gate(self):
        row = self.generate()["collections"][0]
        row["items"] = self.rows[5:10]
        self.assertTrue(validate_candidate(row, self.rows))

    def test_outlier_removal_requires_a_second_complete_locked_review(self):
        ideas = [proposal(self.rows[:6], 0), proposal(self.rows[6:12], 1)]
        base = Curator(ideas)
        def llm(prompt):
            result = base(prompt)
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "locked_review" and len(payload["candidate"]["items"]) == 6:
                result["approved"] = False
                result["coherence"] = 5
                result["item_reviews"][-1].update(fit=3, reason="This title breaks the concept.")
            return result
        result = self.generate(llm)
        self.assertEqual(len(result["collections"]), 2)
        self.assertTrue(all(len(row["items"]) == 5 for row in result["collections"]))
        reviews = [p for p in base.prompts if '"stage":"locked_review"' in p]
        self.assertEqual(len(reviews), 4)
        self.assertTrue(all(validate_candidate(row, self.rows) == [] for row in result["collections"]))

    def test_empty_library_escalates_in_durable_diagnostics_without_llm(self):
        curator = Curator(self.ideas)
        result = self.generate(curator, library=[], settings={"previous_degraded_count": 2})
        self.assertEqual(result["diagnostics"]["consecutive_degraded_count"], 3)
        self.assertTrue(result["diagnostics"]["needs_attention"])
        self.assertEqual(curator.prompts, [])

    def test_provider_failure_returns_safe_diagnostic(self):
        def fail(prompt):
            raise RuntimeError("secret_token=DO-NOT-RETURN")
        result = self.generate(fail)
        self.assertEqual(result["collections"], [])
        self.assertNotIn("DO-NOT-RETURN", json.dumps(result))

    def test_exhausted_credit_stops_selection_and_reports_actual_problem(self):
        from collection_web.store import DomainError
        model = Curator(self.ideas)
        calls = []
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n")[1])
            if payload["stage"] == "selection":
                calls.append(payload)
                raise DomainError("The curator API has run out of credit or reached its spending limit. Top up its account, then retry. Your collections are unchanged.")
            return model(prompt)
        result = self.generate(llm)
        self.assertEqual(len(calls), 1)
        self.assertIn("credit", result["diagnostics"]["reason"])
        self.assertEqual(result["collections"], [])

    def test_watch_influence_is_optional_and_capped(self):
        result = self.generate(settings={**self.settings, "watch_inspiration": True})
        self.assertTrue(all(0 <= r["review"]["watch_influence"] <= 0.1 for r in result["collections"]))
        no_watch = self.generate()
        self.assertTrue(all(r["review"]["watch_influence"] == 0 for r in no_watch["collections"]))

    def test_canonical_item_tampering_is_rejected(self):
        row = deepcopy(self.generate()["collections"][0])
        row["items"][0]["year"] = 1901
        self.assertTrue(any("library" in error for error in validate_candidate(row, self.rows)))

    def test_broad_permanent_collection_does_not_hide_specific_micro_shelves(self):
        result = self.generate(existing=[{"name": "All Thrillers", "items": self.rows}])
        self.assertEqual(len(result["collections"]), 2)

    def test_similar_membership_does_not_discard_a_distinct_micro_shelf(self):
        rows = library_rows(15)
        ideas = [proposal(rows[:7], 0), proposal(rows[8:13], 8)]
        result = self.generate(Curator(ideas), library=rows,
                               existing=[{"name": "Adjacent angle", "items": rows[:6]}])
        self.assertEqual(len(result["collections"]), 2)

    def test_identical_membership_is_still_duplicate(self):
        result = self.generate(settings={**self.settings, "allow_partial": True},
                               existing=[{"name": "Same titles", "items": self.rows[:5]}])
        self.assertFalse(any({i['id'] for i in c['items']} == {str(i) for i in range(5)}
                             for c in result['collections'] + result['reserve_collections']))

    def test_large_inventory_is_sampled_but_matching_still_uses_whole_library(self):
        many = library_rows(3500)
        for row in many:
            row["year"] = 2000
            row["summary"] = "private plot details " * 500
        ideas = [proposal(many[i:i + 5], i) for i in range(3300, 3320, 5)]
        curator = Curator(ideas)
        result = self.generate(curator, library=many)
        self.assertEqual(len(result["collections"]), 2)
        initial = json.loads(curator.prompts[0].split("\nINPUT_JSON:\n")[1])
        self.assertEqual(initial["library"]["count"], 3500)
        self.assertLessEqual(len(initial["library"]["sample"]), 120)
        self.assertTrue(all(len(prompt) <= 180000 for prompt in curator.prompts))

    def test_same_title_wrong_media_type_does_not_match(self):
        ideas = deepcopy(self.ideas)
        for idea in ideas:
            idea["media_type"] = "show"
            for title in idea["ideal_titles"]:
                title["media_type"] = "show"
        result = self.generate(Curator(ideas))
        self.assertEqual(result["collections"], [])
        self.assertEqual(len(result["opportunities"]), 4)

    def test_franchise_must_be_proven_by_metadata_not_shared_title_words(self):
        forged = proposal(self.rows[:5], 0, source_family="franchise", entity_axis="franchise", entity_name="Film")
        result = self.generate(Curator([forged, *self.ideas]))
        self.assertTrue(any("metadata" in r["reason"] for r in result["diagnostics"]["rejected"]))

    def test_publish_validator_reports_malformed_review_instead_of_crashing(self):
        row = self.generate()["collections"][0]
        row["review"]["batch"]["keep_ids"] = 3
        self.assertTrue(validate_candidate(row, self.rows))

    def test_maximum_score_and_all_item_facts_are_revalidated(self):
        row = deepcopy(self.generate()["collections"][0])
        row["review"]["quality"] = 100
        self.assertTrue(validate_candidate(row, self.rows))
        row["review"]["quality"] = 8
        row["items"][0]["summary"] += " Added unsupported fact."
        self.assertTrue(validate_candidate(row, self.rows))

    def test_input_library_and_saved_collections_are_not_mutated(self):
        before = deepcopy(self.rows)
        existing = [{"name": "A Different Shelf", "items": self.rows[:2]}]
        before_existing = deepcopy(existing)
        self.generate(existing=existing)
        self.assertEqual(self.rows, before)
        self.assertEqual(existing, before_existing)

    def test_plex_adding_own_collection_tag_does_not_invalidate_review(self):
        candidate = deepcopy(self.generate()["collections"][0])
        current = deepcopy(self.rows)
        for item in current:
            if item["id"] in {r["id"] for r in candidate["items"]}:
                item["collections"].append(candidate["name"])
        self.assertEqual(validate_candidate(candidate, current), [])
        lookup = {row["id"]: row for row in current}
        candidate["items"] = [lookup[item["id"]] for item in candidate["items"]]
        self.assertEqual(validate_candidate(candidate, current), [])

    def test_selection_refines_proposed_claim_before_independent_locked_review(self):
        base = Curator(self.ideas)
        selected = []
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            result = base(prompt)
            if payload["stage"] == "selection":
                result["thesis"] = "Archival evidence reveals an institutional conspiracy."
                selected.append(payload["candidate"]["id"])
            if payload["stage"] == "locked_review":
                self.assertIn(payload["candidate"]["id"], selected)
                self.assertEqual(payload["candidate"]["thesis"], "Archival evidence reveals an institutional conspiracy.")
            return result
        result = self.generate(llm)
        self.assertEqual(len(result["collections"]), 2)
        self.assertEqual(len(selected), 4)

    def test_selection_cannot_introduce_items_outside_proven_concept_canon(self):
        base = Curator(self.ideas)
        def llm(prompt):
            result = base(prompt)
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "selection":
                result["keep_ids"].append("invented")
            return result
        self.assertEqual(self.generate(llm)["collections"], [])

    def test_entity_selection_cannot_silently_trim_verified_filmography(self):
        person = proposal(self.rows[:5], 0, source_family="person_creator", entity_axis="actor", entity_name="An Actor")
        base = Curator([person])
        def llm(prompt):
            result = base(prompt)
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "selection":
                result["keep_ids"] = result["keep_ids"][:5]
            return result
        result = self.generate(llm)
        self.assertTrue(any("entity" in row["reason"].lower() for row in result["diagnostics"]["rejected"]))

    def test_concept_requests_are_bounded_to_six_at_a_time(self):
        curator = Curator(self.ideas)
        self.generate(curator, settings={"batch_size": 12})
        calls = [json.loads(p.split("\nINPUT_JSON:\n", 1)[1]) for p in curator.prompts
                 if '"stage":"concepts"' in p]
        self.assertTrue(all(row["requested_concepts"] <= 6 for row in calls))
        self.assertLessEqual(len(calls), 3)

    def test_daily_four_uses_twelve_proposals_and_requires_complete_batch(self):
        rows = library_rows(60)
        ideas = [proposal(rows[i:i + 5], i) for i in range(0, 60, 5)]
        curator = Curator(ideas)
        requested = []
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "concepts":
                requested.append(payload["requested_concepts"])
                offset = (len(requested) - 1) * 6
                return {"concepts": deepcopy(ideas[offset:offset + payload["requested_concepts"]])}
            return curator(prompt)
        result = self.generate(llm, library=rows, settings={"batch_size": 4, "minimum_batch_size": 4})
        self.assertEqual(requested, [6, 6])
        self.assertEqual(curator.batch_seen, 12)
        self.assertEqual(len(result["collections"]), 4)
        failed = self.generate(Curator(self.ideas[:3]), settings={"batch_size": 4, "minimum_batch_size": 4})
        self.assertEqual(failed["collections"], [])

    def test_other_shelves_collection_tags_do_not_break_review_after_sync(self):
        candidate = deepcopy(self.generate()["collections"][0])
        current = deepcopy(self.rows)
        for item in current:
            item["collections"] = ["A permanent shelf", "Another new Drift shelf"]
        self.assertEqual(validate_candidate(candidate, current), [])
        lookup = {row["id"]: row for row in current}
        candidate["items"] = [lookup[item["id"]] for item in candidate["items"]]
        self.assertEqual(validate_candidate(candidate, current), [])

    def test_franchise_proof_ignores_other_tags_but_requires_actual_franchise(self):
        rows = deepcopy(self.rows)
        for row in rows[5:]:
            row["collections"] = ["Other franchise"]
        franchise = proposal(rows[:5], 0, source_family="franchise", entity_axis="franchise", entity_name="Investigator")
        result = self.generate(Curator([franchise, proposal(rows[5:10], 5)]), library=rows)
        candidate = next(row for row in result["collections"] if row["source_family"] == "franchise")
        current = deepcopy(rows)
        for row in current:
            row["collections"].append("New unrelated shelf")
        self.assertEqual(validate_candidate(candidate, current), [])
        current[0]["collections"] = ["New unrelated shelf"]
        self.assertTrue(validate_candidate(candidate, current))

    def test_history_expires_after_sixty_days_but_current_names_do_not(self):
        old = (datetime.now(timezone.utc) - timedelta(days=61)).timestamp()
        result = self.generate(existing=[{"name": "Paper Trail 0", "items": []}],
                               history=[{"name": "Paper Trail 5", "source_family": "micro", "time": old}])
        self.assertEqual({row["name"] for row in result["collections"]}, {"Paper Trail 5", "Paper Trail 10"})

    def test_legacy_approval_survives_live_tag_changes_but_not_item_tampering(self):
        candidate = deepcopy(self.generate()["collections"][0])
        # Reproduce the persisted v1 hash format; do not use the production hash helper.
        claim = {key: candidate.get(key) for key in (
            "id", "name", "description", "thesis", "name_reason", "source_family", "concept", "media_type",
            "library_id", "minimum_owned_hits", "size_reason", "entity_name", "entity_axis", "scoped")}
        claim["items"] = [{key: row.get(key) for key in (
            "id", "title", "year", "media_type", "library_id", "summary", "genres", "directors", "actors",
            "studio", "collections")} for row in candidate["items"]]
        legacy = hashlib.sha256(json.dumps(claim, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        candidate["review"]["fingerprint"] = legacy
        current = deepcopy(self.rows)
        for row in current:
            row["collections"].append("Unrelated published shelf")
        self.assertEqual(validate_candidate(candidate, current), [])
        self.assertEqual(candidate["review"]["fingerprint"], legacy)
        candidate["items"][0]["title"] = "Tampered title"
        self.assertTrue(validate_candidate(candidate, current))

    def test_legacy_franchise_approval_still_requires_current_franchise_tag(self):
        from collection_web.curation import _fingerprint
        rows = deepcopy(self.rows)
        for row in rows[5:]:
            row["collections"] = ["Other franchise"]
        franchise = proposal(rows[:5], 0, source_family="franchise", entity_axis="franchise", entity_name="Investigator")
        result = self.generate(Curator([franchise, proposal(rows[5:10], 5)]), library=rows)
        candidate = next(row for row in result["collections"] if row["source_family"] == "franchise")
        candidate["review"]["fingerprint"] = _fingerprint(candidate, legacy=True)
        current = deepcopy(rows)
        current[0]["collections"] = ["Unrelated shelf"]
        self.assertTrue(validate_candidate(candidate, current))

    def balanced_fixture(self):
        rows = library_rows(60)
        for row in rows[30:]:
            row.update(media_type="show", library_id="tv", title=row["title"].replace("Film", "Show"))
        ideas = [proposal(rows[i:i + 5], i, media_type=rows[i]["media_type"]) for i in range(0, 60, 5)]
        base = Curator(ideas)
        calls = []
        used = {"movie": 0, "show": 0}
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "concepts":
                calls.append(payload)
                wanted = payload.get("requested_media_counts", {"movie": 6, "show": 0})
                selected = []
                for media, amount in wanted.items():
                    group = [idea for idea in ideas if idea["media_type"] == media]
                    selected.extend(group[used[media]:used[media] + amount])
                    used[media] += amount
                return {"concepts": deepcopy(selected)}
            return base(prompt)
        return rows, llm, base, calls

    def test_requested_two_movies_two_shows_survives_movie_first_editor_order(self):
        rows, llm, curator, calls = self.balanced_fixture()
        result = self.generate(llm, library=rows, settings={"batch_size": 4, "minimum_batch_size": 4,
                                                           "movie_slots": 2, "show_slots": 2})
        self.assertTrue(result["diagnostics"]["publishable"])
        self.assertEqual([c["media_type"] for c in result["collections"]].count("movie"), 2)
        self.assertEqual([c["media_type"] for c in result["collections"]].count("show"), 2)
        self.assertEqual(curator.batch_seen, 12)
        self.assertEqual(sum(c["requested_media_counts"].get("show", 0) for c in calls), 6)
        self.assertEqual(sum(c["requested_media_counts"].get("movie", 0) for c in calls), 6)

    def test_missing_strong_tv_preserves_previous_batch_despite_enough_movies(self):
        rows, llm, curator, calls = self.balanced_fixture()
        def reject_tv(prompt):
            result = llm(prompt)
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "locked_review" and payload["candidate"]["media_type"] == "show":
                result.update(approved=False, coherence=6)
            return result
        result = self.generate(reject_tv, library=rows, settings={"batch_size": 4, "minimum_batch_size": 4,
                                                                "movie_slots": 2, "show_slots": 2})
        self.assertEqual(result["collections"], [])
        self.assertFalse(result["diagnostics"]["publishable"])
        self.assertIn("TV", result["diagnostics"]["reason"])
        self.assertEqual(curator.batch_seen, 0)

    def test_batch_editor_cannot_drop_required_tv_and_substitute_movies(self):
        rows, llm, _, _ = self.balanced_fixture()
        def movies_only(prompt):
            result = llm(prompt)
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "batch_review":
                result["keep_ids"] = [c["id"] for c in payload["candidates"] if c["media_type"] == "movie"]
            return result
        result = self.generate(movies_only, library=rows, settings={"batch_size": 4, "minimum_batch_size": 4,
                                                                  "movie_slots": 2, "show_slots": 2})
        self.assertEqual(result["collections"], [])
        self.assertIn("TV", result["diagnostics"]["reason"])

    def test_invalid_media_slot_total_fails_without_model_calls(self):
        curator = Curator(self.ideas)
        result = self.generate(curator, settings={"batch_size": 4, "movie_slots": 3, "show_slots": 3})
        self.assertEqual(result["collections"], [])
        self.assertEqual(curator.prompts, [])

    def test_zero_tv_slots_and_inferred_movie_slots_are_supported(self):
        result = self.generate(settings={"batch_size": 4, "minimum_batch_size": 4, "show_slots": 0})
        self.assertEqual(len(result["collections"]), 4)
        self.assertEqual(result["diagnostics"]["required_media_counts"], {"movie": 4, "show": 0})
        self.assertEqual(result["diagnostics"]["selected_media_counts"], {"movie": 4})

    def test_required_tv_inventory_absence_fails_before_spending_model_calls(self):
        curator = Curator(self.ideas)
        result = self.generate(curator, settings={"batch_size": 4, "movie_slots": 2, "show_slots": 2})
        self.assertEqual(result["collections"], [])
        self.assertIn("TV", result["diagnostics"]["reason"])
        self.assertEqual(curator.prompts, [])

    def test_media_target_does_not_override_independent_quality_floor(self):
        rows, llm, _, _ = self.balanced_fixture()
        def weak_tv(prompt):
            result = llm(prompt)
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "locked_review" and payload["candidate"]["media_type"] == "show":
                result["quality"] = 6
            return result
        result = self.generate(weak_tv, library=rows, settings={"batch_size": 4, "movie_slots": 2, "show_slots": 2})
        self.assertEqual(result["collections"], [])
        self.assertFalse(result["diagnostics"]["publishable"])

    def test_entity_scope_reaches_selection_and_final_reviewer(self):
        person = proposal(self.rows[:5], 0, source_family="person_creator", entity_axis="actor", entity_name="An Actor")
        curator = Curator([person])
        self.generate(curator)
        reviews = [json.loads(p.split("\nINPUT_JSON:\n", 1)[1]) for p in curator.prompts
                   if '"stage":"selection"' in p or '"stage":"locked_review"' in p]
        self.assertTrue(reviews)
        self.assertTrue(all(row["candidate"]["scoped"] is False for row in reviews))
        self.assertTrue(all(row["candidate"]["inclusion_basis"] == "verified_entity_credit" for row in reviews))

    def replenishment_fixture(self, *, invalid=False, reject_final=False, drifted_thesis=False,
                              rejected_again=False, other_library=False):
        rows = library_rows(24)
        if other_library:
            rows[5]["library_id"] = "elsewhere"
            rows[13]["library_id"] = "elsewhere"
        ideas = [proposal(rows[:5], 0), proposal(rows[8:13], 8)]
        curator = Curator(ideas)
        replenishments, repaired_reviews = [], []
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "replenish":
                replenishments.append(payload)
                replacement = rows[5] if payload["candidate"]["items"][0]["id"] == "0" else rows[13]
                if rejected_again:
                    replacement = rows[4] if payload["candidate"]["items"][0]["id"] == "0" else rows[12]
                return {"additional_titles": [{"title": "Invented" if invalid else replacement["title"],
                                               "year": replacement["year"], "media_type": "movie",
                                               "reason": "An exact additional investigation proving the original thesis."}]}
            result = curator(prompt)
            if payload["stage"] == "locked_review":
                ids = {r["id"] for r in payload["candidate"]["items"]}
                if ids & {"4", "12"}:
                    result.update(approved=False, coherence=6)
                    for row in result["item_reviews"]:
                        if row["id"] in {"4", "12"}:
                            row.update(fit=3, reason="Different story mechanism; not this shelf.")
                else:
                    repaired_reviews.append(payload)
                    if reject_final:
                        result.update(approved=False, coherence=6)
                    if drifted_thesis:
                        result["thesis"] = "Comedies featuring unlikely friendships."
            return result
        return rows, llm, replenishments, repaired_reviews

    def test_replenishment_preserves_thesis_and_strong_items_then_requires_new_review(self):
        rows, llm, repairs, reviews = self.replenishment_fixture()
        result = self.generate(llm, library=rows)
        self.assertEqual(len(result["collections"]), 2)
        self.assertEqual(len(repairs), 2)
        self.assertEqual(len(reviews), 2)
        self.assertEqual(result["diagnostics"]["replenishment_attempts"], 2)
        self.assertEqual(result["diagnostics"]["replenishment_review_calls"], 2)
        self.assertTrue(all(len(r["candidate"]["items"]) == 4 for r in repairs))
        self.assertTrue(all(c["thesis"] == self.ideas[0]["thesis"] for c in result["collections"]))
        self.assertTrue(all(validate_candidate(c, rows) == [] for c in result["collections"]))

    def test_replenishment_cannot_hallucinate_ownership_or_skip_second_review(self):
        for options in ({"invalid": True}, {"reject_final": True}, {"drifted_thesis": True},
                        {"rejected_again": True}, {"other_library": True}):
            rows, llm, repairs, reviews = self.replenishment_fixture(**options)
            result = self.generate(llm, library=rows)
            self.assertEqual(result["collections"], [])
            self.assertLessEqual(len(repairs), 2)

    def test_replenishment_never_repairs_low_originality_or_whole_batch_without_bound(self):
        rows = library_rows(40)
        ideas = [proposal(rows[i:i + 5], i) for i in range(0, 30, 6)]
        curator = Curator(ideas)
        repair_count = []
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "replenish":
                repair_count.append(payload)
                return {"additional_titles": []}
            result = curator(prompt)
            if payload["stage"] == "locked_review":
                result.update(approved=False, coherence=6)
                result["item_reviews"][-1].update(fit=2)
                if payload["candidate"]["items"][0]["id"] == "0":
                    result["originality"] = 5
            return result
        self.generate(llm, library=rows)
        self.assertEqual(len(repair_count), 2)
        self.assertTrue(all(payload["candidate"]["items"][0]["id"] != "0" for payload in repair_count))

    def natural_size_fixture(self, *, owned=5, selection_size_reason=True,
                             review_size_reason=True, reject_review=False):
        rows = library_rows(20)
        ideas = [proposal(rows[i:i + owned], i, minimum_owned_hits=6,
                          size_reason="The proposed six-title canon initially looked substantial.")
                 for i in (0, 10)]
        curator, selections, reviews = Curator(ideas), [], []
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            result = curator(prompt)
            if payload["stage"] == "selection":
                selections.append(payload)
                result.update(minimum_owned_hits=5,
                              size_reason="Five independently complete investigations establish this narrow premise."
                              if selection_size_reason else "")
            elif payload["stage"] == "locked_review":
                reviews.append(payload)
                if review_size_reason:
                    result["size_reason"] = "These five strong investigations fully demonstrate the compact concept."
                if reject_review:
                    result.update(approved=False, quality=6)
            return result
        return rows, llm, selections, reviews

    def test_selection_can_review_five_owned_hits_before_renegotiating_proposed_six(self):
        rows, llm, selections, reviews = self.natural_size_fixture()
        result = self.generate(llm, library=rows)
        self.assertEqual(len(selections), 2, "The proposed size must not preempt actual owned selection.")
        self.assertEqual(len(reviews), 2)
        self.assertEqual(len(result["collections"]), 2)
        for review in reviews:
            self.assertEqual(review["candidate"]["minimum_owned_hits"], 5)
            self.assertEqual(len(review["candidate"]["items"]), 5)
        for row in result["collections"]:
            self.assertEqual(row["minimum_owned_hits"], 5)
            self.assertEqual(row["thesis"], self.ideas[0]["thesis"])
            self.assertEqual(validate_candidate(row, rows), [])

    def test_natural_size_reduction_requires_selection_and_independent_size_reasons(self):
        for options in ({"selection_size_reason": False}, {"review_size_reason": False},
                        {"reject_review": True}):
            with self.subTest(**options):
                rows, llm, selections, _ = self.natural_size_fixture(**options)
                result = self.generate(llm, library=rows)
                self.assertEqual(len(selections), 2)
                self.assertEqual(result["collections"], [])

    def test_five_strong_of_six_can_be_trimmed_only_after_fresh_size_and_item_review(self):
        rows = library_rows(20)
        ideas = [proposal(rows[i:i + 6], i, minimum_owned_hits=6) for i in (0, 10)]
        curator, reviews = Curator(ideas), []
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "replenish":
                self.fail("A five-item proven core should be reviewed for natural size before padding it.")
            result = curator(prompt)
            if payload["stage"] == "locked_review":
                reviews.append(payload)
                if len(payload["candidate"]["items"]) == 6:
                    result.update(approved=False, coherence=6)
                    result["item_reviews"][-1].update(fit=2, reason="The sixth title is an adjacent outlier.")
                else:
                    result["size_reason"] = "Five exact investigations prove the naturally compact premise."
            return result
        result = self.generate(llm, library=rows)
        self.assertEqual([len(r["candidate"]["items"]) for r in reviews], [6, 5, 6, 5])
        self.assertEqual(len(result["collections"]), 2)
        self.assertTrue(all(r["minimum_owned_hits"] == 5 for r in result["collections"]))
        self.assertTrue(all(len(r["items"]) == 5 for r in result["collections"]))

    def test_trimmed_natural_size_cannot_use_failed_review_as_approval(self):
        for missing_size_reason in (False, True):
            with self.subTest(missing_size_reason=missing_size_reason):
                ideas = [proposal(self.rows[i:i + 6], i, minimum_owned_hits=6) for i in (0, 10)]
                curator, retried = Curator(ideas), []
                def llm(prompt):
                    payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
                    result = curator(prompt)
                    if payload["stage"] == "locked_review":
                        if len(payload["candidate"]["items"]) == 6:
                            result.update(approved=False, coherence=6)
                            result["item_reviews"][-1].update(fit=2)
                        else:
                            retried.append(payload)
                            if not missing_size_reason:
                                result.update(approved=False, quality=6,
                                              size_reason="Five is sufficient, but the overall shelf is still weak.")
                    return result
                result = self.generate(llm)
                self.assertEqual(len(retried), 2)
                self.assertEqual(result["collections"], [])

    def test_scoped_entity_can_prune_nonmatching_canon_but_unscoped_credit_stays_complete(self):
        for scoped in (True, False):
            with self.subTest(scoped=scoped):
                rows = library_rows(20)
                for row in rows[6:]:
                    row["actors"] = ["Another Actor"]
                ideas = [proposal(rows[:6], 0, source_family="person_creator", entity_axis="actor",
                                  entity_name="An Actor", scoped=scoped), proposal(rows[10:15], 10)]
                curator, reviewed = Curator(ideas), []
                def llm(prompt):
                    payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
                    result = curator(prompt)
                    if payload["stage"] == "selection" and payload["candidate"]["source_family"] == "person_creator":
                        result["keep_ids"] = result["keep_ids"][:5]
                    elif payload["stage"] == "locked_review" and payload["candidate"]["source_family"] == "person_creator":
                        reviewed.append(payload)
                    return result
                result = self.generate(llm, library=rows)
                if scoped:
                    self.assertEqual(len(result["collections"]), 2)
                    self.assertEqual({r["id"] for r in reviewed[0]["candidate"]["items"]}, {str(i) for i in range(5)})
                    self.assertEqual(reviewed[0]["candidate"]["inclusion_basis"], "stated_narrative_scope")
                else:
                    self.assertEqual(reviewed, [])
                    self.assertEqual(result["collections"], [])

    def test_locked_review_cannot_rewrite_the_selected_inclusion_rule(self):
        curator = Curator(self.ideas)
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            result = curator(prompt)
            if payload["stage"] == "locked_review":
                result["thesis"] = "Characters make discoveries, whether or not they investigate a conspiracy."
            return result
        result = self.generate(llm)
        self.assertEqual(result["collections"], [])
        self.assertFalse(result["diagnostics"]["publishable"])

    def test_replenishment_budget_reserves_a_repair_for_required_tv(self):
        rows = library_rows(50)
        for row in rows[25:]:
            row.update(media_type="show", library_id="tv", title=row["title"].replace("Film", "Show"))
        ideas = [proposal(rows[i:i + 5], i, media_type=rows[i]["media_type"]) for i in (0, 6, 12, 25, 31, 37)]
        curator, repairs, used = Curator(ideas), [], {"movie": 0, "show": 0}
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "concepts":
                selected = []
                for media, amount in payload["requested_media_counts"].items():
                    choices = [p for p in ideas if p["media_type"] == media]
                    selected.extend(choices[used[media]:used[media] + amount])
                    used[media] += amount
                return {"concepts": deepcopy(selected)}
            if payload["stage"] == "replenish":
                repairs.append(payload["candidate"]["media_type"])
                return {"additional_titles": []}
            result = curator(prompt)
            if payload["stage"] == "locked_review":
                result.update(approved=False, coherence=6)
                result["item_reviews"][-1].update(fit=2)
            return result
        result = self.generate(llm, library=rows, settings={"batch_size": 4, "minimum_batch_size": 4,
                                                          "movie_slots": 2, "show_slots": 2})
        self.assertEqual(repairs, ["movie", "show"])
        self.assertEqual(result["diagnostics"]["replenishment_attempts"], 2)
        self.assertEqual(result["collections"], [])

    def test_twenty_proposals_are_reachable_in_four_bounded_concept_groups(self):
        rows = library_rows(100)
        ideas = [proposal(rows[i:i + 5], i) for i in range(0, 100, 5)]
        curator, requested, used = Curator(ideas), [], 0
        def llm(prompt):
            nonlocal used
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "concepts":
                amount = payload["requested_concepts"]
                requested.append(amount)
                selected = deepcopy(ideas[used:used + amount])
                used += amount
                return {"concepts": selected}
            return curator(prompt)
        result = self.generate(llm, library=rows, settings={"batch_size": 20, "minimum_batch_size": 20})
        self.assertEqual(requested, [6, 6, 6, 2])
        self.assertEqual(curator.batch_seen, 20)
        self.assertEqual(len(result["collections"]), 20)

    def repaired_outlier_fixture(self, *, reject_third=False, change_third_thesis=False):
        rows = library_rows(24)
        ideas = [proposal(rows[i:i + 5], i) for i in (0, 12)]
        curator, repairs, reviews = Curator(ideas), [], []
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            if payload["stage"] == "replenish":
                repairs.append(payload)
                offset = int(payload["candidate"]["items"][0]["id"])
                return {"additional_titles": [
                    {"title": row["title"], "year": row["year"], "media_type": "movie",
                     "reason": "Proposed additional investigation into institutional secrecy."}
                    for row in rows[offset + 5:offset + 8]]}
            result = curator(prompt)
            if payload["stage"] == "locked_review":
                reviews.append(payload)
                items = payload["candidate"]["items"]
                offset = int(items[0]["id"])
                ids = {item["id"] for item in items}
                if str(offset + 4) in ids:
                    # Original five contains four strong titles and an outlier.
                    weak = {str(offset + 4)}
                elif len(items) == 7:
                    # Three suggested replacements contain only one strong fit.
                    weak = {str(offset + 6), str(offset + 7)}
                else:
                    weak = set()
                    if reject_third:
                        result.update(approved=False, coherence=6)
                        result["item_reviews"][-1].update(fit=3, reason="Final review still finds a weak fit.")
                    if change_third_thesis:
                        result["thesis"] = "A broader shelf of characters learning hidden truths."
                if weak:
                    result.update(approved=False, coherence=6)
                    for item_review in result["item_reviews"]:
                        if item_review["id"] in weak:
                            item_review.update(fit=3, reason="This title does not involve the stated investigation.")
            return result
        return rows, llm, repairs, reviews

    def test_replenished_set_can_drop_two_outliers_only_after_third_complete_review(self):
        rows, llm, repairs, reviews = self.repaired_outlier_fixture()
        result = self.generate(llm, library=rows)
        self.assertEqual([len(r["candidate"]["items"]) for r in reviews], [5, 7, 5, 5, 7, 5])
        self.assertEqual(len(repairs), 2)
        self.assertEqual(len(result["collections"]), 2)
        for row in result["collections"]:
            offset = int(row["items"][0]["id"])
            self.assertEqual({r["id"] for r in row["items"]},
                             {str(offset + i) for i in (0, 1, 2, 3, 5)})
            self.assertEqual(validate_candidate(row, rows), [])

    def test_replenished_outlier_trim_cannot_skip_or_retry_failed_third_review(self):
        for options in ({"reject_third": True}, {"change_third_thesis": True}):
            with self.subTest(**options):
                rows, llm, repairs, reviews = self.repaired_outlier_fixture(**options)
                result = self.generate(llm, library=rows)
                self.assertEqual([len(r["candidate"]["items"]) for r in reviews], [5, 7, 5, 5, 7, 5])
                self.assertEqual(len(repairs), 2)
                self.assertEqual(result["collections"], [])

    def test_complete_entity_may_raise_declared_minimum_to_its_verified_owned_size(self):
        rows = library_rows(20)
        for row in rows[9:]:
            row["actors"] = ["Another Actor"]
        ideas = [proposal(rows[:5], 0, source_family="person_creator", entity_axis="actor",
                          entity_name="An Actor"), proposal(rows[10:15], 10)]
        curator, reviews = Curator(ideas), []
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])
            result = curator(prompt)
            if payload["stage"] == "selection" and payload["candidate"]["source_family"] == "person_creator":
                result.update(minimum_owned_hits=9,
                              size_reason="All nine verified performances comprise the complete owned filmography.")
            elif payload["stage"] == "locked_review" and payload["candidate"]["source_family"] == "person_creator":
                reviews.append(payload)
                result["size_reason"] = "Keeping all nine verified credits makes the promised filmography complete."
            return result
        result = self.generate(llm, library=rows)
        self.assertEqual(len(result["collections"]), 2)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["candidate"]["minimum_owned_hits"], 9)
        entity = next(row for row in result["collections"] if row["source_family"] == "person_creator")
        self.assertEqual({row["id"] for row in entity["items"]}, {str(i) for i in range(9)})
        self.assertEqual(validate_candidate(entity, rows), [])

    def test_partial_mode_retains_reviewed_shelves_despite_missing_tv_slot(self):
        rows = library_rows(15)
        for row in rows[10:]:
            row.update(media_type="show", library_id="tv")
        ideas = [proposal(rows[:5], 0), proposal(rows[5:10], 5), proposal(rows[10:], 10, media_type="show")]
        result = self.generate(Curator(ideas), library=rows, settings={"batch_size": 4,
            "minimum_batch_size": 4, "movie_slots": 2, "show_slots": 2, "allow_partial": True})
        self.assertEqual(len(result["collections"]), 3)
        self.assertTrue(result["diagnostics"]["partial"])
        self.assertTrue(all(validate_candidate(c, rows) == [] for c in result["collections"]))

    def test_incremental_surplus_stays_reviewed_and_available(self):
        result = self.generate(settings={**self.settings, "allow_partial": True})
        self.assertEqual(len(result["collections"]), 2)
        self.assertEqual(len(result["reserve_collections"]), 2)
        self.assertTrue(all(validate_candidate(c, self.rows) == [] for c in result["reserve_collections"]))

    def test_weekly_editor_preserves_worthy_reserves_and_excludes_rejected_shelves(self):
        model = Curator(self.ideas)
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n")[1])
            answer = model(prompt)
            if payload["stage"] == "batch_review":
                ids = answer["keep_ids"]
                answer.update(keep_ids=ids[:1], reserve_ids=ids[1:3])
            return answer
        result = self.generate(llm, settings={**self.settings, "allow_partial": True})
        saved = result["collections"] + result["reserve_collections"]
        self.assertEqual(len(saved), 3)
        self.assertTrue(all(validate_candidate(c, self.rows) == [] for c in saved))
        self.assertNotIn("Paper Trail 15", [c["name"] for c in saved])

    def test_weekly_editor_cannot_smuggle_unknown_or_duplicate_reserve_ids(self):
        for reserve in (["unknown"], ["same"]):
            model = Curator(self.ideas)
            def llm(prompt):
                payload = json.loads(prompt.split("\nINPUT_JSON:\n")[1])
                answer = model(prompt)
                if payload["stage"] == "batch_review":
                    answer["reserve_ids"] = [answer["keep_ids"][0]] if reserve == ["same"] else reserve
                return answer
            result = self.generate(llm, settings={**self.settings, "allow_partial": True})
            self.assertEqual(result["collections"], [])

    def test_borderline_review_gets_one_independent_adjudication_with_original_rule(self):
        model = Curator(self.ideas)
        appeals = []
        def llm(prompt):
            payload = json.loads(prompt.split("\nINPUT_JSON:\n")[1])
            if payload["stage"] == "replenish": return {"additional_titles": []}
            answer = model(prompt)
            if payload["stage"] == "locked_review":
                if payload.get("prior_review"):
                    appeals.append(payload)
                else:
                    answer.update(approved=False, coherence=6, decision_reason="Adds an unstated exclusive-plot requirement.")
                    answer["item_reviews"][0].update(fit=6, reason="Contains other themes too.")
            return answer
        result = self.generate(llm, settings={**self.settings, "allow_partial":True, "adjudicate":True})
        self.assertEqual(len(result["collections"]),2)
        self.assertTrue(appeals)
        self.assertTrue(all(p["candidate"]["thesis"] == self.ideas[0]["thesis"] for p in appeals))
        self.assertTrue(all(validate_candidate(c,self.rows)==[] for c in result["collections"]))

    def test_adjudication_never_overrides_a_second_failed_verdict(self):
        model=Curator(self.ideas)
        def llm(prompt):
            payload=json.loads(prompt.split("\nINPUT_JSON:\n")[1]);answer=model(prompt)
            if payload["stage"]=="replenish":return {"additional_titles":[]}
            if payload["stage"]=="locked_review":
                answer.update(approved=False,coherence=6)
                answer["item_reviews"][0].update(fit=6)
            return answer
        result=self.generate(llm,settings={**self.settings,"allow_partial":True,"adjudicate":True})
        self.assertEqual(result["collections"],[])


if __name__ == "__main__":
    unittest.main()
