"""Two-pass curation: creative freedom with deterministic publishing checks."""
import json
from collection_web.curation import generate_batch, validate_candidate
from test_web_curation import library_rows, proposal


def editor(prompt):
    p=json.loads(prompt.split("\nINPUT_JSON:\n")[1])
    if p["stage"]=="creative_pool":
        rows=library_rows(20)
        return {"concepts":[proposal(rows[i:i+5],i) for i in range(0,20,5)]}
    return {"reason":"Four distinct worthwhile shelves for different evenings.", "collections":[{
        "id":c["id"], "approved":True,"name":"Paper Trail "+c["items"][0]["id"],
        "description":"Investigative stories.","thesis":c["thesis"],"name_reason":"Documentary clues connect these stories.",
        "size_reason":"Five focused investigations.","coherence":8,"quality":8,"originality":8,
        "item_reviews":[{"id":i["id"],"fit":8,"reason":"Investigates institutional evidence."} for i in c["items"]]
    } for c in p["candidates"]]}


def test_editorial_pool_uses_two_passes_and_produces_valid_shelves():
    calls=[]
    def model(p):calls.append(p);return editor(p)
    result=generate_batch(library_rows(20),[],[],{"batch_size":4,"creative_editor":True},model)
    assert len(result["collections"])==4
    assert len(calls)==2
    assert all(not validate_candidate(c,library_rows(20)) for c in result["collections"])


def test_editor_cannot_invent_owned_ids_and_good_siblings_survive():
    def model(p):
        a=editor(p)
        if "collections" in a:a["collections"][0]["item_reviews"][0]["id"]="invented"
        return a
    result=generate_batch(library_rows(20),[],[],{"batch_size":4,"creative_editor":True},model)
    assert len(result["collections"])==3
    assert result["diagnostics"]["publishable"]


def test_editor_can_trim_an_outlier_without_another_paid_review():
    def model(p):
        a=editor(p)
        if "concepts" in a:
            rows=library_rows(20);a["concepts"][0]=proposal(rows[:6],0)
        if "collections" in a:a["collections"][0]["item_reviews"]=a["collections"][0]["item_reviews"][:5]
        return a
    result=generate_batch(library_rows(20),[],[],{"batch_size":4,"creative_editor":True},model)
    assert len(result["collections"])==4
    assert len(result["collections"][0]["items"])==5
    assert not validate_candidate(result["collections"][0],library_rows(20))


def test_complete_actor_credit_set_cannot_be_pruned_by_editor():
    def model(p):
        a=editor(p)
        if "concepts" in a:
            a["concepts"][0].update(source_family="person_creator",entity_axis="actor",entity_name="An Actor")
        if "collections" in a:a["collections"][0]["item_reviews"]=a["collections"][0]["item_reviews"][:5]
        return a
    result=generate_batch(library_rows(20),[],[],{"batch_size":4,"creative_editor":True},model)
    assert all(c['source_family']!='person_creator' for c in result['collections'])


def test_editor_review_cannot_turn_weak_items_into_publishable_shelf():
    def model(p):
        a=editor(p)
        if "collections" in a:
            for row in a['collections']:row['item_reviews'][0]['fit']=3
        return a
    result=generate_batch(library_rows(20),[],[],{"batch_size":4,"creative_editor":True},model)
    assert not result['collections']
    assert not result['diagnostics']['publishable']


def test_acquisition_keeps_name_separate_from_concept_and_description():
    def model(p):
        a=editor(p)
        if 'concepts' in a:
            a['concepts'][0]['ideal_titles'][0]['year']=1990
        if 'collections' in a:
            a['collections'][0].update(approved=False,acquisition=True,name='Follow the Paper',description='Investigators uncover buried secrets.')
        return a
    r=generate_batch(library_rows(20),[],[],{'batch_size':4,'creative_editor':True},model)
    idea=r['opportunities'][0]
    assert idea['name']=='Follow the Paper'
    assert idea['description']=='Investigators uncover buried secrets.'
    assert idea['concept'].startswith('Investigations')


def test_acquisition_cannot_silently_use_descriptor_when_name_is_missing():
    def model(p):
        a=editor(p)
        if 'concepts' in a:a['concepts'][0]['ideal_titles'][0]['year']=1990
        if 'collections' in a:
            a['collections'][0].update(approved=False,acquisition=True,name='')
        return a
    r=generate_batch(library_rows(20),[],[],{'batch_size':4,'creative_editor':True},model)
    assert not r['opportunities']
    assert any('name' in x['reason'] for x in r['diagnostics']['rejected'])
    assert len(r['collections'])==3


def test_editor_sees_full_existing_thesis_and_membership_for_semantic_duplicates():
    calls=[]
    existing={'id':'existing','name':'Existing shelf','media_type':'movie','thesis':'A precise connection. '*30,'items':library_rows(3)}
    def model(p):
        calls.append(json.loads(p.split('\nINPUT_JSON:\n')[1]));return editor(p)
    generate_batch(library_rows(20),[existing],[],{'batch_size':4,'creative_editor':True},model)
    payload=next(p for p in calls if p['stage']=='pool_editor')
    reference=payload['existing_collections'][0]
    assert reference['thesis']==existing['thesis']
    assert {i['id'] for i in reference['items']}=={'0','1','2'}
