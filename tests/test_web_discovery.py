"""Library discovery, improvement intent, and friendly pasted lists."""
from copy import deepcopy

import pytest

from collection_web.discovery import parse_collection_text, propose_collection, propose_improvement
from collection_web.store import DomainError


def state():
    return {"library": [{"id": str(i), "title": title, "year": year, "media_type": "movie", "library_id": "1"}
                        for i, (title, year) in enumerate([("Alien", 1979), ("Aliens", 1986), ("The Thing", 1982)])],
            "collections": [], "history": []}


def model(prompt):
    import json
    payload = json.loads(prompt.split("\nINPUT_JSON:\n")[1])
    if payload["stage"] == "review":
        return {"approved": True, "name": "Unwanted Passengers", "description": "Hostile life in enclosed spaces.",
                "thesis": "Creatures turn enclosed spaces into a trap.", "name_reason": "Intruders drive each survival story.",
                "keep": list(range(len(payload["suggestions"]))), "reason": "All selected titles fit the stated premise."}
    return {"concept": "Creatures in enclosed spaces", "thesis": "Creatures turn enclosed spaces into a trap.",
            "description": "Hostile life in enclosed spaces.", "titles": [
                {"title": "Alien", "year": 1979, "reason": "A creature hunts a spaceship crew."},
                {"title": "The Thing", "year": 1982, "reason": "An alien stalks an isolated station."},
                {"title": "Life", "year": 2017, "reason": "A station crew faces an alien organism."}]}


def test_paste_understands_collection_header_bullets_and_years():
    result = parse_collection_text("# Unwanted Passengers\n\n1. Alien (1979)\n- The Thing - 1982\n* Life (2017) — Space-station horror")
    assert result["name"] == "Unwanted Passengers"
    assert result["titles"] == "Alien (1979)\nThe Thing (1982)\nLife (2017)"


def test_paste_reports_unrecognized_lines_instead_of_silently_dropping():
    with pytest.raises(DomainError, match="year"):
        parse_collection_text("My shelf\nAlien\nAliens (1986)")


def test_library_only_improvement_never_proposes_external_downloads():
    st = state();source = {"id": "source", "name": "Space Horror", "media_type": "movie", "items": st["library"][:1], "missing": []}
    draft = propose_improvement(st, source, {"mode": "library", "goal": "expand"}, model)
    assert not draft["missing"]
    assert {i["title"] for i in draft["items"]} == {"Alien", "The Thing"}
    assert draft["source_collection_id"] == "source"
    assert draft["changes"]["added"] == ["The Thing"]


def test_external_improvement_keeps_missing_fit_reasons_for_review():
    st = state();source = {"id": "source", "name": "Space Horror", "media_type": "movie", "items": st["library"][:1], "missing": []}
    draft = propose_improvement(st, source, {"mode": "expand", "goal": "gaps"}, model)
    assert draft["missing"][0]["title"] == "Life"
    assert "station" in draft["missing"][0]["reason"]


def test_prompt_generation_is_a_separate_draft_with_verified_owned_items():
    draft = propose_collection(state(), {"prompt": "Creatures in enclosed spaces", "media_type": "movie", "mode": "library"}, model)
    assert draft["name"] == "Unwanted Passengers"
    assert draft["status"] == "draft" and draft["origin"] == "discover"
    assert not draft["missing"]


def test_rejected_review_and_unknown_selection_fail_without_a_draft():
    for review in ({"approved": False}, {"approved": True, "keep": [999], "name": "Fake", "reason": "Bad"}):
        def reject(prompt):
            return review if '"stage":"review"' in prompt else model(prompt)
        with pytest.raises(DomainError):
            propose_collection(state(), {"media_type": "movie", "mode": "expand"}, reject)


def removal_model(prompt):
    result = model(prompt)
    if '"stage":"review"' in prompt:
        result.update(keep=[], remove=[0], reason="The proposed removal breaks the precise containment premise.")
    else:
        result.update(titles=[], remove_titles=[{"title": "Aliens", "year": 1986, "reason": "This test removal has a stated factual mismatch."}])
    return result


def source_for(st):
    return {"id": "source", "name": "Space Horror", "media_type": "movie", "items": deepcopy(st["library"][:2]), "missing": []}


def test_quality_can_propose_only_an_independently_reviewed_removal():
    st = state(); source = source_for(st)
    before = deepcopy(source)
    draft = propose_improvement(st, source, {"mode": "library", "goal": "quality"}, removal_model)
    assert [row["title"] for row in draft["items"]] == ["Alien"]
    assert draft["changes"] == {"added": [], "removed": ["Aliens"]}
    assert draft["removal_reasons"][0]["title"] == "Aliens"
    assert source == before


@pytest.mark.parametrize("malformed", [None, {}, "not a list", [{"title": "Aliens", "year": "bad", "reason": "Mismatch"}],
                                       [{"title": "Unknown film", "year": 2000, "reason": "Mismatch"}]])
def test_malformed_or_unknown_removals_fail_with_user_facing_error(malformed):
    st = state()
    def invalid(prompt):
        result = model(prompt)
        if '"stage":"propose"' in prompt:
            result["remove_titles"] = malformed
        return result
    with pytest.raises(DomainError):
        propose_improvement(st, source_for(st), {"mode": "library", "goal": "quality"}, invalid)


def test_original_plex_members_survive_duplicate_library_editions_and_omission():
    st = state(); source = source_for(st)
    st["library"].append({**st["library"][0], "id": "duplicate-edition"})
    draft = propose_improvement(st, source, {"mode": "library", "goal": "expand"}, model)
    assert {row["id"] for row in draft["items"]} == {"0", "1", "2"}
    assert not draft["changes"]["removed"]
    assert not draft["missing"]


@pytest.mark.parametrize("change", ["missing", "wrong_year", "wrong_library"])
def test_changed_source_members_block_before_model_call(change):
    st = state(); source = source_for(st)
    if change == "missing":
        st["library"] = st["library"][1:]
    elif change == "wrong_year":
        st["library"][0]["year"] = 1980
    else:
        st["library"][0]["library_id"] = "different"
    calls = []
    def forbidden(prompt):
        calls.append(prompt)
        return model(prompt)
    with pytest.raises(DomainError, match="changed|Sync"):
        propose_improvement(st, source, {"mode": "library"}, forbidden)
    assert not calls


def test_library_mode_preserves_previous_requests_without_new_external_recommendations():
    st = state(); source = source_for(st)
    source["missing"] = [{"title": "Event Horizon", "year": 1997, "media_type": "movie",
                          "reason": "Previous approved suggestion", "requested_at": 123, "request_status": "Requested"}]
    draft = propose_improvement(st, source, {"mode": "library"}, model)
    assert len(draft["missing"]) == 1
    assert draft["missing"][0] == source["missing"][0]
    assert {row["title"] for row in draft["suggestion_reasons"]} == {"The Thing"}


def test_new_recommendations_are_filtered_against_existing_members_before_review():
    import json
    st = state(); source = source_for(st)
    observed = []
    def check(prompt):
        payload = json.loads(prompt.split("\nINPUT_JSON:\n")[1])
        if payload["stage"] == "review":
            observed.extend(row["title"] for row in payload["suggestions"])
        return model(prompt)
    draft = propose_improvement(st, source, {"mode": "expand"}, check)
    assert "Alien" not in observed
    assert draft["changes"]["added"] == ["The Thing"]


def test_removal_cannot_be_reintroduced_as_an_addition_in_same_proposal():
    st = state(); source = source_for(st)
    def conflicting(prompt):
        result = removal_model(prompt)
        if '"stage":"propose"' in prompt:
            result["titles"] = [{"title": "Aliens", "year": 1986, "reason": "Keep this too"}]
        return result
    draft = propose_improvement(st, source, {"mode": "library", "goal": "quality"}, conflicting)
    assert draft["changes"]["removed"] == ["Aliens"]


def test_review_needs_actual_metadata_for_removal_and_owned_additions():
    import json
    st = state()
    for row in st["library"]:
        row.update(summary="Verified plot summary", directors=["Actual Director"], actors=["Actual Performer"], studio="Actual Studio")
    source = source_for(st)
    observed = []
    def check(prompt):
        payload = json.loads(prompt.split("\nINPUT_JSON:\n")[1])
        if payload["stage"] == "review":
            observed.append(payload)
        return removal_model(prompt)
    propose_improvement(st, source, {"goal": "quality"}, check)
    assert observed[0]["removals"][0]["summary"] == "Verified plot summary"
    assert observed[0]["removals"][0]["directors"] == ["Actual Director"]


def test_library_mode_never_converts_ambiguous_new_owned_title_into_download():
    st = state()
    st["library"].append({**st["library"][2], "id": "duplicate-thing"})
    with pytest.raises(DomainError, match="No|no"):
        propose_improvement(st, source_for(st), {"mode": "library"}, model)


def test_external_review_uses_exact_provider_plot_without_claiming_ownership():
    import json
    st = state(); calls = []; observed = []
    def lookup(item):
        calls.append(item)
        return {"id": "external:123", "title": "Life", "year": 2017, "media_type": "movie",
                "summary": "A space station crew studies a dangerous extraterrestrial organism.", "genres": ["Horror", "Science Fiction"]}
    def inspect(prompt):
        payload = json.loads(prompt.split("\nINPUT_JSON:\n")[1])
        if payload["stage"] == "review":
            observed.extend(payload["suggestions"])
        return model(prompt)
    draft = propose_collection(st, {"mode": "expand"}, inspect, metadata_lookup=lookup)
    assert len(calls) == 1 and calls[0]["title"] == "Life"
    missing = next(row for row in observed if row["title"] == "Life")
    assert missing["owned"] is False
    assert missing["metadata_verified"] is True
    assert "extraterrestrial" in missing["summary"]
    assert draft["missing"][0]["metadata"]["summary"] == missing["summary"]


def test_external_metadata_wrong_year_is_not_used_and_library_mode_never_looks_up():
    import json
    calls = []; observed = []
    def lookup(item):
        calls.append(item)
        return {"id": "external:wrong", "title": "Life", "year": 1999, "media_type": "movie", "summary": "Wrong remake plot"}
    def inspect(prompt):
        payload = json.loads(prompt.split("\nINPUT_JSON:\n")[1])
        if payload["stage"] == "review":
            observed.extend(payload["suggestions"])
        return model(prompt)
    propose_collection(state(), {"mode": "expand"}, inspect, metadata_lookup=lookup)
    missing = next(row for row in observed if row["title"] == "Life")
    assert not missing.get("metadata_verified")
    assert "Wrong remake plot" not in str(missing)
    calls.clear()
    propose_collection(state(), {"mode": "library"}, model, metadata_lookup=lookup)
    assert not calls
