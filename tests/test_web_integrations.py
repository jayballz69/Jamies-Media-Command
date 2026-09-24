"""Provider mutation guards: exact identities, ownership and destination checks."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from collection_web import integrations
from collection_web.store import DomainError


def shelf():
    return {"id": "local-shelf", "name": "Space isolation", "media_type": "movie",
            "library_id": "1",
            "items": [{"id": "12", "title": "Alien", "year": 1979, "library_id": "1"}]}


def server():
    result = MagicMock()
    result.machineIdentifier = "known-server"
    result.fetchItem.return_value = SimpleNamespace(ratingKey=12, title="Alien", year=1979,
                                                    type="movie", librarySectionID=1)
    result.library.sectionByID.return_value.collections.return_value = []
    return result


def test_publication_never_overwrites_an_unowned_same_name_collection():
    remote = server()
    remote.library.sectionByID.return_value.collections.return_value = [SimpleNamespace(title="Space isolation")]
    with patch.object(integrations, "plex", return_value=remote), pytest.raises(DomainError, match="already exists"):
        integrations.publish({}, shelf(), "known-server")
    remote.library.sectionByID.return_value.createCollection.assert_not_called()


def test_publication_rechecks_exact_live_title_year_and_library():
    remote = server()
    remote.fetchItem.return_value.year = 2000
    with patch.object(integrations, "plex", return_value=remote), pytest.raises(DomainError, match="changed"):
        integrations.publish({}, shelf(), "known-server")
    remote.library.sectionByID.return_value.createCollection.assert_not_called()


def test_visibility_cannot_mutate_another_server():
    remote = server()
    with patch.object(integrations, "plex", return_value=remote), pytest.raises(DomainError, match="server changed"):
        integrations.set_visibility({}, {**shelf(), "plex_id": "44"}, True, "different-server")
    remote.fetchItem.assert_not_called()


def test_remote_labels_are_required_before_existing_collection_changes():
    remote = server()
    remote.fetchItem.return_value = SimpleNamespace(type="collection", librarySectionID=1, labels=[])
    with pytest.raises(DomainError, match="managed elsewhere"):
        integrations.resolve_owned(remote, {**shelf(), "plex_id": "44"})


def test_plex_capitalized_ownership_labels_still_identify_managed_collection():
    remote = server()
    collection = SimpleNamespace(type="collection", librarySectionID=1,
                                 labels=[SimpleNamespace(tag="Collection-manager:local-shelf")])
    remote.fetchItem.return_value = collection
    assert integrations.is_owned(collection.labels, shelf())
    assert integrations.resolve_owned(remote, {**shelf(), "plex_id": "44"}) is collection


def existing_collection():
    collection = MagicMock()
    collection.ratingKey = 44
    collection.title = "Space isolation"
    collection.type = "collection"
    collection.librarySectionID = 1
    collection.labels = [SimpleNamespace(tag="Collection-manager:local-shelf")]
    collection.items.return_value = [SimpleNamespace(ratingKey=12)]
    return collection


@pytest.mark.parametrize("changed", ["added", "removed", "renamed"])
def test_improvement_rejects_external_plex_changes_before_any_mutation(changed):
    remote, collection = server(), existing_collection()
    if changed == "added":
        collection.items.return_value.append(SimpleNamespace(ratingKey=99))
    elif changed == "removed":
        collection.items.return_value = []
    else:
        collection.title = "Changed in Plex"
    item = remote.fetchItem.return_value
    remote.fetchItem.side_effect = lambda key: collection if key == 44 else item
    candidate = {**shelf(), "plex_id": "44", "description": "Reviewed edit"}
    with patch.object(integrations, "plex", return_value=remote), pytest.raises(DomainError, match="original changed"):
        integrations.publish({}, candidate, "known-server", expected_source={**shelf(), "plex_id": "44"})
    collection.addItems.assert_not_called()
    collection.removeItems.assert_not_called()
    collection.editSummary.assert_not_called()


def test_unchanged_original_can_receive_reviewed_improvement():
    remote, collection = server(), existing_collection()
    item = remote.fetchItem.return_value
    remote.fetchItem.side_effect = lambda key: collection if key == 44 else item
    candidate = {**shelf(), "plex_id": "44", "description": "Reviewed edit"}
    with patch.object(integrations, "plex", return_value=remote):
        assert integrations.publish({}, candidate, "known-server", expected_source={**shelf(), "plex_id": "44"}) == "44"
    collection.editSummary.assert_called_once_with("Reviewed edit")
    collection.addItems.assert_not_called()
    collection.removeItems.assert_not_called()


@pytest.mark.parametrize("field,value", [("type", "movie"), ("librarySectionID", 9)])
def test_owned_label_does_not_override_collection_type_or_library(field, value):
    remote, collection = server(), existing_collection()
    setattr(collection, field, value)
    remote.fetchItem.return_value = collection
    with pytest.raises(DomainError, match="collection identity"):
        integrations.resolve_owned(remote, {**shelf(), "plex_id": "44"})


def test_improvement_cannot_silently_create_a_new_shelf():
    remote = server()
    with patch.object(integrations, "plex", return_value=remote), pytest.raises(DomainError, match="original changed"):
        integrations.publish({}, shelf(), "known-server", expected_source={**shelf(), "plex_id": "44"})
    remote.library.sectionByID.return_value.createCollection.assert_not_called()


def test_owned_collection_requires_library_evidence():
    remote, collection = server(), existing_collection()
    remote.fetchItem.return_value = collection
    candidate = {**shelf(), "plex_id": "44", "library_id": "", "items": []}
    with pytest.raises(DomainError, match="collection identity"):
        integrations.resolve_owned(remote, candidate)


def test_arr_requests_require_one_exact_match_and_skip_existing():
    calls = []
    def request(settings, service, method, path, **kwargs):
        calls.append((method, path))
        return {"/rootfolder": [{"path": "/movies"}], "/qualityprofile": [{"id": 1}],
                "/movie/lookup": [{"title": "Alien", "year": 1979, "tmdbId": 348}],
                "/movie": [{"tmdbId": 348}]}[path]
    with patch.object(integrations, "arr_request", side_effect=request):
        result = integrations.request_title({"radarr_root": "/movies", "radarr_profile": 1},
                {"title": "Alien", "year": 1979, "media_type": "movie"})
    assert result == "Already in Radarr"
    assert all(method == "GET" for method, path in calls)


def test_trakt_cannot_request_arbitrary_urls():
    with patch.object(integrations, "http") as http, pytest.raises(DomainError):
        integrations.trakt_items({"trakt_client_id": "private"}, "http://internal/admin", "movie")
    http.assert_not_called()


def test_credit_exhaustion_is_reported_without_private_provider_error_text():
    response = MagicMock(status_code=429)
    response.json.return_value = {"error": {"code": "credit_balance_exhausted", "type": "insufficient_quota", "message": "PRIVATE"}}
    with patch.object(integrations.requests, "Session") as factory:
        factory.return_value.__enter__.return_value.request.return_value = response
        with pytest.raises(DomainError, match="credit") as caught:
            integrations.http("POST", "https://example.test/chat/completions")
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("media_type,service,path,identity", [
    ("movie", "radarr", "/movie/lookup", "tmdbId"), ("show", "sonarr", "/series/lookup", "tvdbId")])
def test_title_metadata_lookup_only_reads_exact_arr_identity(media_type, service, path, identity):
    row = {"title": "The Example", "year": 2020, identity: 123,
           "overview": "Verified plot from the metadata provider.", "genres": ["Thriller"], "studio": "A Studio"}
    with patch.object(integrations, "arr_request", return_value=[row]) as lookup:
        result = integrations.title_metadata({}, {"title": "The Example", "year": 2020, "media_type": media_type})
    lookup.assert_called_once_with({}, service, "GET", path, params={"term": "The Example"})
    assert result["id"] == "external:123"
    assert result["summary"] == row["overview"]
    assert result["genres"] == ["Thriller"]
    assert result["media_type"] == media_type
    assert "library_id" not in result


@pytest.mark.parametrize("results", [[], [{"title": "The Example", "year": 2021, "tmdbId": 1}],
                                    [{"title": "Different", "year": 2020, "tmdbId": 1}],
                                    [{"title": "The Example", "year": 2020, "tmdbId": 1},
                                     {"title": "The Example", "year": 2020, "tmdbId": 2}]])
def test_title_metadata_does_not_borrow_wrong_or_ambiguous_plot(results):
    with patch.object(integrations, "arr_request", return_value=results):
        assert integrations.title_metadata({}, {"title": "The Example", "year": 2020, "media_type": "movie"}) is None


def test_optional_title_metadata_lookup_failure_does_not_trigger_download_or_fail_proposal():
    with patch.object(integrations, "arr_request", side_effect=DomainError("Not connected")) as lookup:
        assert integrations.title_metadata({}, {"title": "The Example", "year": 2020, "media_type": "movie"}) is None
    assert lookup.call_args.args[2] == "GET"


def test_luna_calls_are_bounded_and_report_provider_usage():
    response=MagicMock()
    response.json.return_value={"choices":[{"message":{"content":'{"ok":true}'}}],
                                "usage":{"prompt_tokens":123,"completion_tokens":45}}
    usage=[]
    with patch.object(integrations,"http",return_value=response) as call:
        assert integrations.call_llm({"llm_url":"https://example.test/v1","llm_model":"gpt-6-luna","llm_key":""},"JSON",usage)=={"ok":True}
    assert call.call_args.kwargs['json']['max_completion_tokens']==16000
    assert call.call_args.kwargs['json']['reasoning_effort']=='low'
    assert usage==[{"prompt_tokens":123,"completion_tokens":45}]


def test_catalog_year_suffix_resolves_jericho_without_confusing_editions():
    item = {"title": "Jericho", "year": 2006, "media_type": "show"}
    rows = [{"title": "Jericho", "year": 1966, "tvdbId": 1},
            {"title": "Jericho (2006)", "year": 2006, "tvdbId": 79330, "overview": "A town after nuclear attacks."}]
    with patch.object(integrations, "arr_request", return_value=rows):
        result = integrations.title_metadata({}, item)
    assert result["id"] == "external:79330"
    assert result["title"] == "Jericho"
    assert result["catalog_title"] == "Jericho (2006)"
    assert not integrations.catalog_title_matches({"title": "3", "year": 2016}, {"title": "3%", "year": 2016})
