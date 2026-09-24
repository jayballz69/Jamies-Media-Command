from copy import deepcopy
from unittest.mock import patch
import pytest

from collection_web import family_shelf as family
from collection_web.service import Service
from collection_web.store import Store, DomainError


def movie(n):
    return dict(id=str(n),title='Family film '+str(n),year=2000+n,media_type='movie',
                library_id='1',added_at=n,genres=['Family'],content_rating='PG',summary='An imaginative family adventure.')


@pytest.fixture
def configured(tmp_path):
    store=Store(tmp_path); service=Service(store)
    def seed(s):
        s.update(library=[movie(i) for i in range(1,27)],synced_at=1,server_id='server')
        base=dict(media_type='movie',library_id='1',description='Family adventures',thesis='Family adventures',
            status='published',origin='manual',permanent=True,managed=True,home=True,rotation_enabled=False,missing=[])
        s['collections']=[dict(base,id='recent',plex_id='100',name=family.NAME,family_rolling=True,pinned_home=True,items=[movie(i) for i in range(1,26)]),
                          dict(base,id='archive',plex_id='101',name='Family Friendly Movies',items=[movie(1)])]
        s['family_shelf']=dict(enabled=True,shelf_id='recent',destination_id='archive',library_id='1',cap=25,
            decisions={str(i):dict(eligible=True,reason='Shared family adventure.',fingerprint=family.fingerprint(movie(i))) for i in range(1,27)},last_checked_at=0)
    store.update(seed)
    yield store,service
    service.close()


def test_newest_arrivals_not_release_dates_and_rated_adult_excluded(configured):
    store,_=configured;s=store.read()
    selected,pending=family.select(s['library'],s['family_shelf']['decisions'],'1')
    assert not pending
    assert [i['id'] for i in selected]==[str(i) for i in range(26,1,-1)]
    s['library'][-1]['content_rating']='R'
    assert family.select(s['library'],s['family_shelf']['decisions'],'1')[0][0]['id']=='25'
    s['library'][-2]['summary']='Metadata has changed'
    assert family.select(s['library'],s['family_shelf']['decisions'],'1')[1][0]['id']=='25'


def test_rollover_publishes_archive_first_preserving_its_members(configured):
    store,service=configured
    def archive_seed(s):
        s['collections'][1]['items']=[movie(999)]
        s['library'].append(dict(movie(999),added_at=0))
    store.update(archive_seed)
    calls=[]
    def publish(settings,candidate,server_id,**kwargs):
        calls.append(deepcopy(candidate));return candidate['plex_id']
    with patch('collection_web.integrations.publish',side_effect=publish),patch('collection_web.integrations.set_collection_order'),patch('collection_web.integrations.set_visibility'),patch('collection_web.integrations.call_llm') as llm:
        family.refresh(service,lambda _:None)
        llm.assert_not_called()
    assert [c['id'] for c in calls]==['archive','recent']
    assert [i['id'] for i in calls[0]['items']]==['999','1']
    assert len(calls[1]['items'])==25 and calls[1]['items'][0]['id']=='26'
    assert store.read()['collections'][0]['pinned_home']
    with patch('collection_web.integrations.publish') as publish,patch('collection_web.integrations.set_collection_order'),patch('collection_web.integrations.set_visibility'):
        family.refresh(service,lambda _:None)
        publish.assert_not_called()


def test_failed_destination_does_not_trim_the_recent_shelf(configured):
    store,service=configured
    with patch('collection_web.integrations.publish',side_effect=DomainError('Plex offline')) as publish:
        with pytest.raises(DomainError):family.refresh(service,lambda _:None)
        assert publish.call_args.args[1]['id']=='archive'
    assert store.read()['collections'][0]['items'][0]['id']=='1'
    assert store.read()['family_shelf']['error']


def test_pinned_shelf_survives_rotation_without_using_a_slot(configured):
    store,service=configured
    s=store.read()
    with patch('collection_web.integrations.set_visibility') as visibility:
        service._apply_home(s,[],lambda _:None,rotate_permanent=True,replacement=False)
    assert not any(c.args[1]['id']=='recent' and c.args[2] is False for c in visibility.call_args_list)
    assert service._permanent_choices(s)==[]
    with pytest.raises(DomainError,match='newest 25'):
        service._add_items(s['collections'][0],[movie(26)],s,lambda _:None)


def test_classifier_requires_complete_boolean_decisions():
    rows=[movie(1)]
    assert family.classify(rows,lambda _:dict(movies=[dict(id='1',eligible=True,reason='Family adventure')]))['1']['eligible']
    for result in [dict(movies=[]),dict(movies=[dict(id='1',eligible='true',reason='Guess')]),dict(movies=[dict(id='invented',eligible=True,reason='Guess')])]:
        with pytest.raises(DomainError):family.classify(rows,lambda _:result)


def test_audience_votes_source_preference_and_boundary(configured):
    assert family.audience_score({'ratings':{'imdb':{'value':5,'votes':99}}}) is None
    assert family.audience_score({'ratings':{'rottenTomatoes':{'value':99,'votes':500}}}) is None
    score=family.audience_score({'ratings':{'imdb':{'value':5,'votes':100},'tmdb':{'value':8,'votes':900}}})
    assert score['source']=='imdb' and score['score']==5
    assert family.audience_score({'ratings':{'imdb':{'value':5,'votes':1},'tmdb':{'value':6,'votes':100}}})['score']==6
    for value in [float('nan'),float('inf'),-1,11,True,'6']:
        assert family.audience_score({'ratings':{'imdb':{'value':value,'votes':100}}}) is None
    store,_=configured;s=store.read()
    chosen,_=family.select(s['library'],s['family_shelf']['decisions'],'1',{'26':score})
    assert len(chosen)==25 and chosen[0]['id']=='25' and chosen[-1]['id']=='1'
    chosen,_=family.select(s['library'],s['family_shelf']['decisions'],'1',{'26':dict(score,score=6)})
    assert chosen[0]['id']=='26'


def test_audience_cache_exact_identity_and_outage(configured):
    store,service=configured
    store.update(lambda s:s['settings'].update(radarr_url='http://example.invalid',radarr_key='test-only'))
    catalog=[dict(title='Family film 26',year=2026,ratings={'imdb':{'value':5,'votes':200}}),
             dict(title='Family film 25',year=1900,ratings={'imdb':{'value':2,'votes':200}})]
    with patch('collection_web.integrations.arr_request',return_value=catalog) as request:
        assert set(family.refresh_audience(service,store.read()))=={'26'}
        family.refresh_audience(service,store.read())
        assert request.call_count==1
    store.update(lambda s:s['family_shelf'].update(audience_checked_at=0))
    with patch('collection_web.integrations.arr_request',side_effect=DomainError('Offline')):
        assert family.refresh_audience(service,store.read())['26']['score']==5
    assert store.read()['family_shelf']['audience_error']
