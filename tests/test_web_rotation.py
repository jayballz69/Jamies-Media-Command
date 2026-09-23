"""Permanent shelves and temporary Drift occupy independent Home slots."""

from unittest.mock import patch

import pytest

from collection_web.service import Service
from collection_web.store import DomainError, Store


def shelf(identity, kind="movie", drift=False, home=False):
    return {"id": identity, "name": identity, "media_type": kind, "status": "published", "managed": True,
            "origin": "drift" if drift else "manual", "permanent": not drift, "rotation_enabled": True,
            "home": home, "items": [], "last_rotated_at": 1 if home else 0}


@pytest.fixture
def runtime(tmp_path):
    store = Store(tmp_path)
    service = Service(store)
    rows = [shelf(f"movie-{i}") for i in range(4)] + [shelf(f"tv-{i}", "show") for i in range(4)]
    rows += [shelf(f"drift-{i}", drift=True, home=True) for i in range(4)]
    store.update(lambda s: s.update(collections=rows, server_id="plex", synced_at=1))
    store.update(lambda s: s["drift"].update(active_batch_ids=[f"drift-{i}" for i in range(4)], last_activated_at=123))
    yield store, service
    service.close()


def test_rotation_shows_two_movies_two_shows_plus_four_unchanged_drift(runtime):
    store, service = runtime
    with patch("collection_web.curation.validate_candidate", return_value=[]), patch("collection_web.integrations.set_visibility"):
        service.rotate(lambda _: None)
    homes = [c for c in store.read()["collections"] if c["home"]]
    assert len(homes) == 8
    assert sum(c["permanent"] and c["media_type"] == "movie" for c in homes) == 2
    assert sum(c["permanent"] and c["media_type"] == "show" for c in homes) == 2
    assert sum(not c["permanent"] for c in homes) == 4
    assert store.read()["drift"]["last_activated_at"] == 123


def test_permanent_shortage_never_fills_tv_slots_with_movies_or_drift(runtime):
    store, service = runtime
    store.update(lambda s: s.update(collections=[c for c in s["collections"] if c["media_type"] != "show"]))
    with patch("collection_web.curation.validate_candidate", return_value=[]), patch("collection_web.integrations.set_visibility"):
        service.rotate(lambda _: None)
    assert sum(c["home"] and c["permanent"] for c in store.read()["collections"]) == 2


def test_activation_publishes_full_drift_replacement_without_swapping_permanent(runtime):
    store, service = runtime
    store.update(lambda s: [c.update(home=c["id"] in {"movie-0", "movie-1", "tv-0", "tv-1"} or c["home"]) for c in s["collections"]])
    new = [dict(shelf(f"new-{i}", drift=True), status="draft") for i in range(4)]
    store.update(lambda s: s["collections"].extend(new))
    store.update(lambda s: s["drift"].update(current_batch_ids=[c["id"] for c in new]))
    store.update(lambda s: s.update(last_rotation_at=321))
    calls = []
    with patch("collection_web.curation.validate_candidate", return_value=[]), \
            patch("collection_web.integrations.publish", return_value="remote"), \
            patch("collection_web.integrations.set_visibility", side_effect=lambda cfg, c, active, sid: calls.append((c["id"], active))):
        service.activate_drift(lambda _: None)
    st = store.read()
    assert set(st["drift"]["active_batch_ids"]) == {c["id"] for c in new}
    assert st["last_rotation_at"] == 321
    assert {c["id"] for c in st["collections"] if c["home"] and c["permanent"]} == {"movie-0", "movie-1", "tv-0", "tv-1"}
    assert all(c["status"] == "archived" for c in st["collections"] if c["id"].startswith("drift-"))
    assert max(i for i, call in enumerate(calls) if call[1]) < min(i for i, call in enumerate(calls) if not call[1])


def test_partial_publication_keeps_old_drift_home_and_active_identity(runtime):
    store, service = runtime
    new = [dict(shelf(f"new-{i}", drift=True), status="draft") for i in range(4)]
    store.update(lambda s: s["collections"].extend(new))
    store.update(lambda s: s["drift"].update(current_batch_ids=[c["id"] for c in new]))
    with patch("collection_web.curation.validate_candidate", return_value=[]), \
            patch("collection_web.integrations.publish", side_effect=["remote", DomainError("Unavailable")]), \
            patch("collection_web.integrations.set_visibility") as visibility, pytest.raises(DomainError):
        service.activate_drift(lambda _: None)
    visibility.assert_not_called()
    st = store.read()
    assert st["drift"]["active_batch_ids"] == [f"drift-{i}" for i in range(4)]
    assert all(c["home"] for c in st["collections"] if c["id"].startswith("drift-"))


def test_kept_drift_joins_permanent_pool_and_never_expires(runtime):
    store, service = runtime
    store.update(lambda s: next(c for c in s["collections"] if c["id"] == "drift-0").update(permanent=True))
    with patch("collection_web.curation.validate_candidate", return_value=[]), patch("collection_web.integrations.set_visibility"):
        service.rotate(lambda _: None)
    st = store.read()
    assert next(c for c in st["collections"] if c["id"] == "drift-0")["status"] == "published"
    assert "drift-0" not in st["drift"]["active_batch_ids"]
    assert sum(c["home"] and c["permanent"] and c["media_type"] == "movie" for c in st["collections"]) == 2


def test_automatic_generation_publishes_and_activates_extra_shelves(runtime):
    store, service = runtime
    new = [dict(shelf(f"new-{i}", drift=True), status="draft") for i in range(4)]
    store.update(lambda s: s["settings"]["advanced"].update(auto_publish=True, watch_inspiration=False))
    with patch("collection_web.curation.generate_batch", return_value={"collections": new, "diagnostics": {"publishable": True}}) as generate, \
            patch("collection_web.curation.validate_candidate", return_value=[]), \
            patch("collection_web.integrations.publish", return_value="remote"), \
            patch("collection_web.integrations.set_visibility"):
        service.generate(lambda _: None)
    st = store.read()
    assert len([c for c in st["collections"] if c["home"]]) == 8
    assert st["drift"]["last_activated_at"] > 0
    assert generate.call_args.args[3]["batch_size"] == 12
    assert generate.call_args.args[3]["minimum_batch_size"] == 4


def test_partial_publish_retry_reuses_proven_batch_without_new_model_calls(runtime):
    store, service = runtime
    rows = [dict(shelf(f"new-{i}", drift=True), status="draft") for i in range(4)]
    store.update(lambda s: s["collections"].extend(rows))
    store.update(lambda s: s["drift"].update(current_batch_ids=[c["id"] for c in rows], last_generated_at=456))
    store.update(lambda s: s["settings"]["advanced"].update(auto_publish=True))
    with patch.object(service, "activate_drift") as activate, patch.object(service, "generate") as generate:
        service.refresh_drift(lambda _: None)
    activate.assert_called_once()
    generate.assert_not_called()


def test_changed_counts_take_effect_in_each_pool_without_cross_filling(runtime):
    store, service = runtime
    store.update(lambda s: s["settings"].update(permanent_movie_slots=1, permanent_show_slots=3, drift_slots=2))
    with patch("collection_web.curation.validate_candidate", return_value=[]), patch("collection_web.integrations.set_visibility"):
        service.rotate(lambda _: None)
    homes = [c for c in store.read()["collections"] if c["home"]]
    assert len(homes) == 6
    assert sum(c["permanent"] and c["media_type"] == "movie" for c in homes) == 1
    assert sum(c["permanent"] and c["media_type"] == "show" for c in homes) == 3
    assert sum(not c["permanent"] for c in homes) == 2


def test_partial_generation_adds_good_shelves_and_retains_old_for_unfilled_slots(runtime):
    store, service = runtime
    new = [dict(shelf("new-movie", drift=True), status="draft"),
           dict(shelf("new-tv", "show", drift=True), status="draft")]
    store.update(lambda s: s["settings"]["advanced"].update(auto_publish=True, watch_inspiration=False))
    with patch("collection_web.curation.generate_batch", return_value={"collections": new, "diagnostics": {"publishable": True, "partial": True}}), \
            patch("collection_web.curation.validate_candidate", return_value=[]), \
            patch("collection_web.integrations.publish", return_value="remote"), \
            patch("collection_web.integrations.set_visibility"):
        service.generate(lambda _: None)
    state = store.read()
    active = state["drift"]["active_batch_ids"]
    assert "new-movie" in active and "new-tv" in active
    assert len(active) == 4
    assert sum(c["status"] == "archived" for c in state["collections"] if c["id"].startswith("drift-")) == 2


def test_incremental_first_run_retry_reuses_smaller_reviewed_batch(runtime):
    store, service = runtime
    rows = [dict(shelf("first-tv", "show", drift=True), status="draft")]
    store.update(lambda s: s.update(collections=rows))
    store.update(lambda s: s["drift"].update(current_batch_ids=["first-tv"], active_batch_ids=[], incremental=True, last_generated_at=456))
    store.update(lambda s: s["settings"]["advanced"].update(auto_publish=True))
    with patch.object(service, "activate_drift") as activate, patch.object(service, "generate") as generate:
        service.refresh_drift(lambda _: None)
    activate.assert_called_once()
    generate.assert_not_called()


def test_partial_pool_retains_previous_shelves_by_media_type(runtime):
    store, service = runtime
    old = [shelf(f"old-{kind}-{i}", kind, drift=True, home=True)
           for kind in ("movie", "show") for i in range(2)]
    new = [dict(shelf(f"new-{kind}", kind, drift=True), status="draft") for kind in ("movie", "show")]
    store.update(lambda s: s.update(collections=old + new))
    store.update(lambda s: s["drift"].update(pool_ids=[c["id"] for c in new], active_batch_ids=[c["id"] for c in old]))
    with patch("collection_web.curation.validate_candidate", return_value=[]):
        ids = service._choose_pool(store.read())
    chosen = [c for c in old + new if c["id"] in ids]
    assert len(chosen) == 4
    assert sum(c["media_type"] == "show" for c in chosen) == 2


def test_generation_builds_larger_pool_but_only_displays_four(runtime):
    store, service = runtime
    new = [dict(shelf(f"pool-{kind}-{i}", kind, drift=True), status="draft")
           for i in range(4) for kind in ("movie", "show")]
    store.update(lambda s: s["settings"].update(drift_pool_size=12))
    store.update(lambda s: s["settings"]["advanced"].update(auto_publish=True, watch_inspiration=False))
    with patch("collection_web.curation.generate_batch", return_value={"collections":new,"diagnostics":{"publishable":True}}) as generate, \
            patch("collection_web.curation.validate_candidate", return_value=[]), \
            patch("collection_web.integrations.publish", return_value="remote"), \
            patch("collection_web.integrations.set_visibility"):
        message = service.generate(lambda _: None)
    assert "8 reviewed collections" in message
    state=store.read()
    assert generate.call_args.args[3]["batch_size"] == 12
    active=[c for c in state["collections"] if c["id"] in state["drift"]["active_batch_ids"]]
    assert len(active)==4
    assert sum(c["media_type"]=="show" for c in active)==2
    assert sum(c["status"]=="draft" and c["id"].startswith("pool-") for c in state["collections"])==4
    with patch("collection_web.curation.validate_candidate", return_value=[]), \
            patch("collection_web.integrations.publish", return_value="remote"), \
            patch("collection_web.integrations.set_visibility"), patch.object(service,"generate") as generate:
        service.refresh_drift(lambda _: None)
    generate.assert_not_called()
    state=store.read()
    assert set(state["drift"]["active_batch_ids"]).isdisjoint(c["id"] for c in active)


def test_weekly_pool_rotates_without_ai_and_expires_only_on_new_generation(runtime):
    store, service = runtime
    pool = [shelf(f"weekly-{kind}-{i}",kind,drift=True,home=i<2) for i in range(4) for kind in ("movie","show")]
    store.update(lambda s:s.update(collections=pool))
    import time
    store.update(lambda s:s["drift"].update(pool_ids=[c["id"] for c in pool],active_batch_ids=[c["id"] for c in pool if c["home"]],last_generated_at=time.time(),incremental=True))
    store.update(lambda s:s["settings"]["advanced"].update(auto_publish=True))
    with patch("collection_web.curation.validate_candidate",return_value=[]),patch("collection_web.integrations.set_visibility"),patch.object(service,"generate") as generate:
        service.refresh_drift(lambda _:None)
    generate.assert_not_called()
    state=store.read()
    assert all(c["status"]=="published" for c in state["collections"])
    assert set(state["drift"]["active_batch_ids"])=={c["id"] for c in pool if not c["home"]}


def test_permanent_rotation_does_not_postpone_weekly_pool_switch(runtime):
    store, service = runtime
    ids=[f"drift-{i}" for i in range(4)]
    store.update(lambda s:s["drift"].update(pool_ids=ids,current_batch_ids=ids,incremental=True,last_activated_at=123))
    with patch("collection_web.curation.validate_candidate",return_value=[]),patch("collection_web.integrations.set_visibility"):
        service.rotate(lambda _:None)
    assert store.read()["drift"]["last_activated_at"]==123


def test_new_week_archives_unshown_drafts_but_keeps_promoted_shelves(runtime):
    store, service = runtime
    old=[dict(shelf("old-week-draft",drift=True),status="draft"),dict(shelf("old-week-kept",drift=True),permanent=True)]
    store.update(lambda s:s["collections"].extend(old))
    store.update(lambda s:s["drift"].update(pool_ids=[c["id"] for c in old]))
    store.update(lambda s:s["settings"]["advanced"].update(auto_publish=True,watch_inspiration=False))
    new=[dict(shelf(f"week-new-{kind}-{i}",kind,drift=True),status="draft") for kind in ("movie","show") for i in range(2)]
    with patch("collection_web.curation.generate_batch",return_value={"collections":new,"diagnostics":{"publishable":True}}),patch("collection_web.curation.validate_candidate",return_value=[]),patch("collection_web.integrations.publish",return_value="remote"),patch("collection_web.integrations.set_visibility"):
        service.generate(lambda _:None)
    rows={c["id"]:c for c in store.read()["collections"]}
    assert rows["old-week-draft"]["status"]=="archived"
    assert rows["old-week-kept"]["status"]=="published"
