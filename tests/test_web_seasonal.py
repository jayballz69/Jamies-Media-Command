from datetime import date, datetime, timezone
from collection_web.seasonal import active_events, context_key, seasonal_rank

SETTINGS = {"advanced": {"seasonal_enabled": True}}

def test_halloween_inclusive_window_and_switch_off():
    assert not any(e["id"].startswith("halloween") for e in active_events(SETTINGS, date(2026,10,16)))
    events = active_events(SETTINGS, date(2026,10,17))
    assert any(e["id"]=="halloween:2026-10-17" for e in events)
    assert any(e["id"]=="halloween:2026-10-17" for e in active_events(SETTINGS,date(2026,10,31)))
    assert not active_events(SETTINGS,date(2026,11,1))
    assert active_events({"advanced":{"seasonal_enabled":False}},date(2026,10,20)) == []

def test_qld_holidays_easter_and_brisbane_midnight():
    assert any(e["kind"]=="qld_school" for e in active_events(SETTINGS,date(2026,9,24)))
    assert not any(e["kind"]=="qld_school" for e in active_events(SETTINGS,date(2026,10,6)))
    assert any(e["kind"]=="easter" for e in active_events(SETTINGS,date(2027,3,28)))
    assert any(e["kind"]=="halloween" for e in active_events(SETTINGS,datetime(2026,10,16,14,tzinfo=timezone.utc)))
    assert not any(e["kind"]=="qld_school" for e in active_events(SETTINGS,date(2031,7,1)))

def test_only_current_reviewed_event_tags_receive_priority():
    events=active_events(SETTINGS,date(2026,10,20))
    seasonal={"seasonal_events":[{"id":"halloween:2026-10-17","reason":"Supernatural scares supported by the selected stories."}]}
    ordinary={}
    assert seasonal_rank(seasonal,events) < seasonal_rank(ordinary,events)
    assert seasonal_rank(seasonal,[]) == seasonal_rank(ordinary,[])
    assert context_key(events) != context_key([])


def test_editor_receives_calendar_and_only_keeps_supplied_event_tags():
    import json
    from collection_web.curation import generate_batch
    from test_web_curation import library_rows
    from test_web_editorial import editor
    events=active_events(SETTINGS,date(2026,10,20)); calls=[]
    def model(prompt):
        data=json.loads(prompt.split("\nINPUT_JSON:\n")[1]);calls.append(data)
        result=editor(prompt)
        for row in result.get("collections",[]):
            row["seasonal_events"]=[{"id":events[0]["id"],"reason":"Specific supernatural stories fit Halloween."},{"id":"invented","reason":"No"}]
        return result
    result=generate_batch(library_rows(20),[],[],{"batch_size":4,"creative_editor":True,"seasonal_events":events},model)
    assert all(call["seasonal_events"]==events for call in calls)
    assert all(c["seasonal_events"]==[{"id":events[0]["id"],"reason":"Specific supernatural stories fit Halloween."}] for c in result["collections"])
