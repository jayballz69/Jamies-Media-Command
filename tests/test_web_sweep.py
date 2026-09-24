from copy import deepcopy
from collection_web.sweep import review
from collection_web.store import DomainError
import pytest
from test_web_curation import library_rows

def test_sweep_advice_preserves_state_and_rejects_invented_or_existing_members():
    rows=library_rows(8)
    state={"library":rows,"collections":[{"id":"a","name":"Shelf","status":"published","origin":"manual","media_type":"movie","items":rows[:2]}]}
    original=deepcopy(state)
    result=review(state,lambda _: {"summary":"Distinct collections.","collections":[{"id":"a","flavour":"Investigations","advice":"Keep the precise focus.","additions":[{"id":rows[0]["id"],"reason":"Existing"},{"id":"invented","reason":"No"},{"id":rows[2]["id"],"reason":"Strong story connection"}]}]})
    assert state==original
    assert [i["id"] for i in result["collections"][0]["additions"]]==[rows[2]["id"]]
    with pytest.raises(DomainError,match="missed"):
        review(state,lambda _: {"summary":"Incomplete","collections":[]})
