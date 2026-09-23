"""Existing-collection explanations must never manufacture titles or mutate shelves."""
from copy import deepcopy
import json

import pytest

from collection_web.context import describe_collections
from collection_web.store import DomainError


def library():
    return [{"id": "1", "title": "Alien", "year": 1979, "media_type": "movie",
             "summary": "A creature hunts a commercial spaceship crew.", "genres": ["Horror"],
             "directors": ["Ridley Scott"], "play_count": 99, "private_field": "must not be sent"}]


def shelf(identity="shelf"):
    return {"id": identity, "name": "Unwanted Passengers", "media_type": "movie", "origin": "plex",
            "description": "Uninvited creatures in enclosed spaces.", "thesis": "",
            "items": [{"id": "1", "title": "Alien", "year": 1979}],
            "missing": [{"title": "Life", "year": 2017, "reason": "An organism threatens a station."}],
            "managed": True, "review": {"fingerprint": "untouched"}}


def data(prompt):
    return json.loads(prompt.split("\nINPUT_JSON:\n", 1)[1])


def valid_answer(prompt):
    return {"collections": [{"id": row["id"], "thesis": "Creatures turn enclosed spacecraft into traps.",
                             "notes": [{"index": title["index"], "reason": "A dangerous organism threatens the crew."}
                                       for title in row["titles"] if title["requires_note"]]}
                            for row in data(prompt)["collections"]]}


def test_explanations_preserve_exact_source_identity_and_all_inputs():
    collections, owned = [shelf()], library()
    before = deepcopy((collections, owned))
    prompts = []

    def llm(prompt):
        prompts.append(prompt)
        answer = valid_answer(prompt)
        answer["collections"][0]["name"] = "Unrequested rename"
        answer["collections"][0]["notes"][0]["title"] = "Invented title"
        return answer

    output = describe_collections(collections, owned, llm)
    assert (collections, owned) == before
    assert output[0]["id"] == "shelf"
    assert set(output[0]) == {"id", "thesis", "notes"}
    assert [(n["title"], n["year"], n["media_type"]) for n in output[0]["notes"]] == [
        ("Alien", 1979, "movie"), ("Life", 2017, "movie")]
    payload = data(prompts[0])["collections"][0]
    assert payload["metadata_sample"][0]["summary"] == owned[0]["summary"]
    assert [row["availability"] for row in payload["titles"]] == ["available", "missing"]
    assert "play_count" not in prompts[0] and "must not be sent" not in prompts[0]


def test_batches_four_sequentially_with_progress_and_skips_drift_and_empty():
    collections = [shelf(str(i)) for i in range(9)]
    collections.extend([{**shelf("drift"), "origin": "drift"}, {**shelf("empty"), "items": [], "missing": []}])
    calls, progress = [], []

    def llm(prompt):
        calls.append([row["id"] for row in data(prompt)["collections"]])
        return valid_answer(prompt)

    result = describe_collections(collections, library(), llm, progress.append)
    assert calls == [["0", "1", "2", "3"], ["4", "5", "6", "7"], ["8"]]
    assert [row["id"] for row in result] == [str(i) for i in range(9)]
    assert len(progress) == 3 and "9-9 of 9" in progress[-1]


def test_full_title_inventory_survives_eight_row_metadata_sample():
    rows = [{"id": str(i), "title": f"Title {i}", "year": 2000 + i, "media_type": "show",
             "summary": "A" * 2000} for i in range(12)]
    source = {**shelf(), "media_type": "show", "items": rows, "missing": []}

    def llm(prompt):
        payload = data(prompt)["collections"][0]
        assert len(payload["titles"]) == 12
        assert payload["title_inventory_is_complete"] is True
        assert len(payload["metadata_sample"]) == 8
        assert payload["metadata_is_complete"] is False
        assert all(len(row["summary"]) == 1200 for row in payload["metadata_sample"])
        return valid_answer(prompt)

    assert len(describe_collections([source], rows, llm)[0]["notes"]) == 12


@pytest.mark.parametrize("change", [
    lambda answer: answer.update(collections="wrong"),
    lambda answer: answer["collections"][0].update(id="invented"),
    lambda answer: answer["collections"].append(deepcopy(answer["collections"][0])),
    lambda answer: answer["collections"].clear(),
    lambda answer: answer["collections"][0].update(thesis=" "),
    lambda answer: answer["collections"][0].update(thesis="x" * 1501),
    lambda answer: answer["collections"][0]["notes"][0].update(index=999),
    lambda answer: answer["collections"][0]["notes"][0].update(index=True),
    lambda answer: answer["collections"][0]["notes"][0].update(reason=" "),
    lambda answer: answer["collections"][0]["notes"][0].update(reason="x" * 501),
    lambda answer: answer["collections"][0]["notes"].pop(),
    lambda answer: answer["collections"][0]["notes"].append(deepcopy(answer["collections"][0]["notes"][0])),
])
def test_bad_or_invented_explanations_reject_the_result(change):
    def llm(prompt):
        answer = valid_answer(prompt)
        change(answer)
        return answer

    with pytest.raises(DomainError):
        describe_collections([shelf()], library(), llm)


def test_matching_never_borrows_metadata_from_another_year_or_media_type():
    source = shelf()
    source["items"][0]["year"] = 1980

    def llm(prompt):
        assert data(prompt)["collections"][0]["metadata_sample"] == []
        return valid_answer(prompt)

    describe_collections([source], library(), llm)


def test_duplicate_title_in_missing_is_explained_only_once_as_available():
    source = shelf()
    source["missing"].append({"title": "ALIEN", "year": 1979})
    result = describe_collections([source], library(), valid_answer)
    assert len(result[0]["notes"]) == 2


def test_failure_after_first_batch_returns_no_partial_results_and_never_mutates():
    collections = [shelf(str(i)) for i in range(5)]
    before = deepcopy(collections)
    count = 0

    def llm(prompt):
        nonlocal count
        count += 1
        return valid_answer(prompt) if count == 1 else {"collections": []}

    with pytest.raises(DomainError, match="every supplied collection"):
        describe_collections(collections, library(), llm)
    assert collections == before and count == 2


def test_oversized_inventory_fails_before_model_call(monkeypatch):
    monkeypatch.setattr("collection_web.context.MAX_PROMPT_CHARS", 5)
    with pytest.raises(DomainError, match="too large"):
        describe_collections([shelf()], library(), lambda prompt: pytest.fail("must not call model"))


def test_empty_input_requires_no_model_call():
    assert describe_collections([], [], lambda prompt: pytest.fail("must not call model")) == []


def test_unrelated_library_item_without_year_does_not_block_explanations():
    owned = library() + [{"id": "unmatched", "title": "Unmatched Plex item", "year": 0, "media_type": "movie"}]
    result = describe_collections([shelf()], owned, valid_answer)
    assert result[0]["thesis"]


def test_member_without_year_is_described_as_unverified_metadata():
    source = shelf()
    source["items"][0]["year"] = 0
    def model(prompt):
        title = data(prompt)["collections"][0]["titles"][0]
        assert title["year"] == 0
        assert data(prompt)["collections"][0]["metadata_sample"] == []
        return valid_answer(prompt)
    result = describe_collections([source], library(), model, include_member_notes=False)
    assert result[0]["thesis"]


def test_available_suggestions_are_explained_with_exact_identity_and_metadata():
    source, owned = shelf(), library()
    extra = {"id": "2", "title": "The Thing", "year": 1982, "media_type": "movie",
             "summary": "An Antarctic crew encounters an alien organism."}
    source["available"] = [extra]
    owned.append(extra)

    def llm(prompt):
        payload = data(prompt)["collections"][0]
        suggestion = payload["titles"][1]
        assert suggestion["title"] == "The Thing"
        assert suggestion["role"] == "suggestion" and suggestion["availability"] == "available"
        assert payload["metadata_sample"][1]["summary"] == extra["summary"]
        return valid_answer(prompt)

    result = describe_collections([source], owned, llm)
    assert [row["title"] for row in result[0]["notes"]] == ["Alien", "The Thing", "Life"]


def test_skip_member_notes_keeps_full_large_inventory_and_explains_only_suggestions():
    rows = [{"id": str(i), "title": f"Existing member {i}", "year": 2000, "media_type": "movie",
             "summary": f"Verified summary {i}"} for i in range(313)]
    available = {"id": "new", "title": "The Thing", "year": 1982, "media_type": "movie"}
    source = {**shelf(), "items": rows, "available": [available]}
    before = deepcopy(source)

    def llm(prompt):
        payload = data(prompt)["collections"][0]
        assert len(payload["titles"]) == 315
        assert payload["title_inventory_is_complete"] is True
        assert all(not row["requires_note"] for row in payload["titles"][:313])
        assert all(row["requires_note"] for row in payload["titles"][313:])
        assert len(payload["metadata_sample"]) == 8
        return valid_answer(prompt)

    result = describe_collections([source], rows + [available], llm, include_member_notes=False)
    assert result[0]["thesis"]
    assert [row["title"] for row in result[0]["notes"]] == ["The Thing", "Life"]
    assert source == before


def test_skip_member_notes_rejects_an_unsolicited_member_note():
    def llm(prompt):
        answer = valid_answer(prompt)
        answer["collections"][0]["notes"].append({"index": 0, "reason": "Unrequested member explanation."})
        return answer

    with pytest.raises(DomainError, match="invalid or duplicate title"):
        describe_collections([shelf()], library(), llm, include_member_notes=False)


def test_skip_member_notes_still_requires_each_available_and_missing_suggestion():
    source = shelf()
    source["available"] = [{"title": "The Thing", "year": 1982}]

    def llm(prompt):
        answer = valid_answer(prompt)
        answer["collections"][0]["notes"].pop()
        return answer

    with pytest.raises(DomainError, match="every required title"):
        describe_collections([source], library(), llm, include_member_notes=False)


def test_collection_with_only_members_can_receive_thesis_without_member_notes():
    source = {**shelf(), "missing": []}
    result = describe_collections([source], library(), valid_answer, include_member_notes=False)
    assert result[0]["thesis"] and result[0]["notes"] == []
