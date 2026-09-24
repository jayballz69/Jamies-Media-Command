"""New Drift arrivals must earn a fresh approval without changing the shelf."""
from copy import deepcopy
import json

import pytest

from collection_web.arrivals import review_drift_additions
from collection_web.curation import generate_batch, validate_candidate
from collection_web.store import DomainError
from test_web_curation import Curator, library_rows, proposal


def setup():
    library = library_rows(25)
    ideas = [proposal(library[i:i + 5], i) for i in range(0, 20, 5)]
    batch = generate_batch(library, [], [], {"batch_size": 4}, Curator(ideas))["collections"]
    source = batch[0]
    replacement = deepcopy(source)
    replacement["items"].append(library[20])
    return library, batch, source, replacement, Curator(ideas)


def test_new_arrival_gets_full_item_and_batch_review_without_changing_name():
    library, batch, source, replacement, model = setup()
    result = review_drift_additions(source, replacement, {"library": library, "collections": batch}, model)
    assert len(result["items"]) == 6
    assert result["name"] == source["name"] and result["thesis"] == source["thesis"]
    assert result["review"]["fingerprint"] != source["review"]["fingerprint"]
    assert validate_candidate(result, library) == []
    assert len(source["items"]) == 5
    assert [json.loads(p.split("\nINPUT_JSON:\n")[1])["stage"] for p in model.prompts] == ["locked_review", "batch_review"]


@pytest.mark.parametrize("failure", ["weak_fit", "rename", "batch", "missing_id"])
def test_failed_review_does_not_approve_or_mutate_the_source(failure):
    library, batch, source, replacement, model = setup()
    before = deepcopy(source)
    def reject(prompt):
        answer = model(prompt)
        if '"stage":"locked_review"' in prompt:
            if failure == "weak_fit": answer["item_reviews"][-1]["fit"] = 3
            if failure == "rename": answer["name"] = "Something different"
            if failure == "missing_id": answer["item_reviews"].pop()
        elif failure == "batch": answer["approved"] = False
        return answer
    with pytest.raises(DomainError):
        review_drift_additions(source, replacement, {"library": library, "collections": batch}, reject)
    assert source == before


def test_unverified_arrival_is_blocked_before_provider_call():
    library, batch, source, replacement, model = setup()
    replacement["items"][-1] = {**replacement["items"][-1], "id": "unknown"}
    with pytest.raises(DomainError):
        review_drift_additions(source, replacement, {"library": library, "collections": batch}, model)
    assert model.prompts == []


def test_actor_arrival_review_uses_the_same_verified_credit_rule_as_generation():
    library = library_rows(25)
    for row in library[5:20]:
        row["actors"] = ["Another Actor"]
    ideas = [proposal(library[:5], 0, source_family="person_creator", entity_axis="actor", entity_name="An Actor")]
    ideas += [proposal(library[i:i + 5], i) for i in range(5, 20, 5)]
    batch = generate_batch(library[:20], [], [], {"batch_size": 4}, Curator(ideas))["collections"]
    source = next(c for c in batch if c["source_family"] == "person_creator")
    replacement = deepcopy(source)
    replacement["items"].append(library[20])
    model = Curator(ideas)
    review_drift_additions(source, replacement, {"library": library, "collections": batch}, model)
    payload = json.loads(model.prompts[0].split("\nINPUT_JSON:\n")[1])["candidate"]
    assert payload["scoped"] is False
    assert payload["inclusion_basis"] == "verified_entity_credit"


def test_arrival_editor_reviews_only_changed_shelf_with_neighbours_as_context():
    library, batch, source, replacement, model = setup()
    source['permanent'] = True
    review_drift_additions(source,replacement,{'library':library,'collections':batch},model)
    payload=json.loads(model.prompts[-1].split('\nINPUT_JSON:\n')[1])
    assert [c['id'] for c in payload['candidates']]==[source['id']]
    assert len(payload['neighbours'])==len(batch)-1
    assert payload['new_ids']==[library[20]['id']]


def test_one_weak_addition_keeps_strong_additions_after_fresh_subset_review():
    library,batch,source,replacement,model=setup()
    replacement['items'].append(library[21])
    weak=library[21]['id']
    def mixed(prompt):
        answer=model(prompt)
        if '"stage":"locked_review"' in prompt:
            for row in answer['item_reviews']:
                if row['id']==weak:
                    row.update(fit=3,reason='Identity theft is not the same as living as someone else.')
                    answer['approved']=False
        return answer
    result=review_drift_additions(source,replacement,{'library':library,'collections':batch},mixed)
    assert len(result['items'])==6
    assert weak not in {i['id'] for i in result['items']}
    assert result['arrival_rejections'][weak].startswith('Identity theft')
    assert validate_candidate(result,library)==[]
