from copy import deepcopy
from unittest.mock import patch

import pytest
from collection_web import new_arrivals as arrivals
from collection_web.service import Service
from collection_web.store import Store, DomainError, public_state


def item(identity, kind='movie'):
    return dict(id=identity, title='Title '+identity, year=2020, media_type=kind,
                library_id='1' if kind == 'movie' else '2', summary='An undercover investigator.', genres=['Comedy'])


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    service = Service(store)
    def seed(s):
        s.update(library=[item('1')], synced_at=1, server_id='server')
        s['settings']['advanced']['new_arrival_suggestions'] = True
        s['settings'].update(llm_model='test', llm_url='http://example.invalid')
        s['collections'] = [dict(id='c', name='Undercover', media_type='movie',
            library_id='1', description='Comedic disguises', thesis='Comedic disguises',
            status='published', origin='manual', permanent=True, managed=True,
            items=[item('1')], missing=[], plex_id='100')]
        arrivals.observe(s, [item('1'), item('2'), item('3','show')])
        s['library'] += [item('2'), item('3','show')]
    store.update(seed)
    yield store, service
    service.close()


def response():
    return {'arrivals': [dict(id='2', matches=[dict(collection_id='c', reason='The undercover disguise drives the comedy.')]),
                         dict(id='3', matches=[])]}


def test_accept_all_keeps_successes_and_failed_suggestions(setup):
    store, service = setup
    with patch('collection_web.integrations.call_llm', return_value=response()):
        service.review_new_arrivals(lambda _: None)
    first = store.read()['new_arrivals']['suggestions'][0]
    store.update(lambda s: s['new_arrivals']['suggestions'].append(dict(first, id='bad', theme_revision='outdated')))
    with patch('collection_web.integrations.publish') as publish:
        with pytest.raises(DomainError, match='Accepted 1 additions; 1 could not finish'):
            arrivals.accept_additions(service, ['bad', first['id'], first['id']], lambda _: None)
    assert publish.call_count == 1
    rows = store.read()['new_arrivals']['suggestions']
    assert rows[0]['status'] == 'added'
    assert rows[1]['status'] == 'pending'
    with patch.object(service, 'act_on_arrival') as add:
        assert '0 additions' in arrivals.accept_additions(service, [first['id']], lambda _: None)
        add.assert_not_called()


def test_rejected_arrival_is_not_marked_accepted(setup):
    store, service = setup
    with patch('collection_web.integrations.call_llm', return_value=response()):
        service.review_new_arrivals(lambda _: None)
    identity = store.read()['new_arrivals']['suggestions'][0]['id']
    with patch.object(service, '_add_items', return_value=0):
        with pytest.raises(DomainError, match='fit review'):
            service.act_on_arrival(identity, 'add', lambda _: None)
    assert store.read()['new_arrivals']['suggestions'][0]['status'] == 'pending'


@pytest.mark.parametrize('ids', [None, [], 'all', [1], ['x'] * 1001])
def test_accept_all_rejects_invalid_snapshot(setup, ids):
    _, service = setup
    with patch.object(service, 'act_on_arrival') as add:
        with pytest.raises(DomainError):
            arrivals.accept_additions(service, ids, lambda _: None)
        add.assert_not_called()


def test_baseline_disabled_and_reimports_are_not_new(setup):
    store, _ = setup
    s = store.read()
    assert s['new_arrivals']['pending'] == ['2','3']
    arrivals.observe(s, s['library']+[dict(item('1'), id='99')])
    assert s['new_arrivals']['pending'] == ['2','3']
    s['settings']['advanced']['new_arrival_suggestions'] = False
    arrivals.observe(s, s['library']+[item('4')])
    assert '4' not in s['new_arrivals']['pending']
    fresh = dict(library=[], settings=s['settings'], collections=[])
    arrivals.observe(fresh, [item('5')])
    assert fresh['new_arrivals']['pending'] == []
    assert 'seen' not in public_state(store.read())['new_arrivals']


def test_review_is_bounded_and_checks_sibling_collections(setup):
    store, _ = setup
    s = store.read()
    s['collections'].append(dict(s['collections'][0], id='other', name='Spy thrillers', thesis='Serious espionage'))
    original = deepcopy(s)
    def model(prompt):
        assert 'Spy thrillers' in prompt and 'An undercover investigator.' in prompt
        return response()
    suggestions, reviewed = arrivals.review(s, model)
    assert s == original
    assert reviewed == ['2','3']
    assert len(suggestions) == 1
    bad = response()
    bad['arrivals'][1]['matches'] = [dict(collection_id='c',reason='Wrong media type')]
    assert len(arrivals.review(s, lambda _:bad)[0]) == 1
    with pytest.raises(DomainError, match='missed'):
        arrivals.review(s, lambda _:{'arrivals':[]})
    bad['arrivals'][0]['matches'][0]['collection_id'] = 'invented'
    with pytest.raises(DomainError, match='identify'):
        arrivals.review(s, lambda _:bad)


def test_failure_retains_queue_success_never_publishes(setup):
    store, service = setup
    with patch('collection_web.integrations.call_llm', side_effect=DomainError('Offline')):
        with pytest.raises(DomainError): service.review_new_arrivals(lambda _:None)
    assert store.read()['new_arrivals']['pending'] == ['2','3']
    assert store.read()['new_arrivals']['error']
    with patch('collection_web.integrations.call_llm', return_value=response()), patch('collection_web.integrations.publish') as publish:
        service.review_new_arrivals(lambda _:None)
        publish.assert_not_called()
    s = store.read()
    assert s['new_arrivals']['pending'] == []
    assert not s['new_arrivals']['error']
    suggestion = s['new_arrivals']['suggestions'][0]
    service.act_on_arrival(suggestion['id'], 'dismiss', lambda _:None)
    store.update(lambda s: arrivals.save_review(s,[suggestion],['2']))
    assert len(store.read()['new_arrivals']['suggestions']) == 1
    assert store.read()['new_arrivals']['suggestions'][0]['status'] == 'dismissed'


def test_add_preserves_members_and_stale_theme_is_blocked(setup):
    store, service = setup
    with patch('collection_web.integrations.call_llm', return_value=response()):
        service.review_new_arrivals(lambda _:None)
    suggestion = store.read()['new_arrivals']['suggestions'][0]
    with patch('collection_web.integrations.publish') as publish:
        service.act_on_arrival(suggestion['id'], 'add', lambda _:None)
        assert [i['id'] for i in publish.call_args.args[1]['items']] == ['1','2']
        assert publish.call_args.kwargs['expected_source']['id'] == 'c'
    assert store.read()['new_arrivals']['suggestions'][0]['status'] == 'added'
    with pytest.raises(DomainError, match='handled'):
        service.act_on_arrival(suggestion['id'], 'add', lambda _:None)
    def change(s):
        s['new_arrivals']['suggestions'][0]['status'] = 'pending'
        s['collections'][0]['items'] = [item('1')]
        s['collections'][0]['thesis'] = 'A different theme'
    store.update(change)
    with patch('collection_web.integrations.publish') as publish:
        with pytest.raises(DomainError, match='theme changed'):
            service.act_on_arrival(suggestion['id'], 'add', lambda _:None)
        publish.assert_not_called()
    store.update(lambda s: arrivals.observe(s,s['library']))
    assert store.read()['new_arrivals']['pending'] == ['2']


def test_schedule_daily_and_no_calls_when_empty_or_disabled(setup):
    store, service = setup
    now = 200000
    def ready(s):
        s['settings']['advanced'].update(sync_enabled=False)
        s['new_arrivals']['last_review_at'] = now-86401
    store.update(ready)
    with patch.object(service, 'submit') as submit:
        service.schedule_once(now)
        assert submit.call_args.args[0] == 'Scheduled new arrivals'
        submit.reset_mock()
        service.schedule_once(now+100)
        submit.assert_not_called()
        store.update(lambda s:s['settings']['advanced'].update(new_arrival_suggestions=False))
        service.schedule_once(now+90000)
        submit.assert_not_called()


def test_sync_detects_arrivals_and_failure_does_not_consume_them(setup):
    store, service = setup
    with patch('collection_web.integrations.scan_library', return_value=([item('1'),item('2'),item('3','show'),item('4')], [], 'server')):
        service.sync(lambda _:None)
    assert store.read()['new_arrivals']['pending'] == ['2','3','4']
    with patch('collection_web.integrations.scan_library', side_effect=DomainError('Offline')):
        with pytest.raises(DomainError): service.sync(lambda _:None)
    assert store.read()['new_arrivals']['pending'] == ['2','3','4']


def test_large_arrival_batch_leaves_overflow_for_later(setup):
    store, _ = setup
    s = store.read()
    s['library'] = [item(str(i)) for i in range(100,150)]
    s['new_arrivals']['pending'] = [i['id'] for i in s['library']]
    import json
    def no_matches(prompt):
        data = json.loads(prompt.split('INPUT_JSON:\n')[1])
        assert len(data['arrivals']) == 40
        return {'arrivals':[dict(id=i['id'],matches=[]) for i in data['arrivals']]}
    suggestions, reviewed = arrivals.review(s,no_matches)
    arrivals.save_review(s,suggestions,reviewed)
    assert len(s['new_arrivals']['pending']) == 10


def test_arrival_routes_require_authentication_and_csrf(tmp_path):
    from collection_web.app import create_app
    app = create_app(tmp_path,password='test-password-for-arrivals')
    service = app.extensions['collection_service']
    try:
        client = app.test_client()
        assert client.post('/api/arrivals/review',json={}).status_code == 401
        assert client.post('/api/arrivals/accept-all',json={'ids':['x']}).status_code == 401
        session = client.post('/api/login',json={'password':'test-password-for-arrivals'}).get_json()
        assert client.post('/api/arrivals/x/add',json={}).status_code == 403
        assert client.post('/api/arrivals/accept-all',json={'ids':['x']}).status_code == 403
        with patch.object(service,'submit',return_value={'id':'job'}) as submit:
            assert client.post('/api/arrivals/review',json={},headers={'X-CSRF-Token':session['csrf']}).status_code == 202
            assert submit.call_args.args[1] == service.review_new_arrivals
            assert client.post('/api/arrivals/x/dismiss',json={},headers={'X-CSRF-Token':session['csrf']}).status_code == 202
            with patch.object(arrivals, 'accept_additions', return_value='Accepted') as accept:
                assert client.post('/api/arrivals/accept-all',json={'ids':['x']},headers={'X-CSRF-Token':session['csrf']}).status_code == 202
                progress = lambda _: None
                submit.call_args.args[1](progress)
                accept.assert_called_once_with(service, ['x'], progress)
    finally:
        service.close()


@pytest.mark.parametrize('bulk', [False, True])
@pytest.mark.parametrize('failure', [False, True])
def test_imported_arrivals_adopt_on_accept_without_rotation(setup, bulk, failure):
    store, service = setup
    store.update(lambda s: s['collections'][0].update(managed=False, rotation_enabled=False, home=False))
    with patch('collection_web.integrations.call_llm', return_value=response()):
        service.review_new_arrivals(lambda _: None)
    identity = store.read()['new_arrivals']['suggestions'][0]['id']
    def accept():
        if bulk:
            return arrivals.accept_additions(service, [identity], lambda _: None)
        return service.act_on_arrival(identity, 'add', lambda _: None)
    with patch('collection_web.integrations.publish', side_effect=DomainError('Plex changed') if failure else None) as publish:
        if failure:
            with pytest.raises(DomainError): accept()
        else:
            accept()
        assert publish.call_args.kwargs['adopt_imported'] is True
        assert publish.call_args.kwargs['expected_source']['managed'] is False
    state = store.read()
    target = state['collections'][0]
    assert target['managed'] is (not failure)
    assert target['rotation_enabled'] is False
    assert target['home'] is False
    assert [i['id'] for i in target['items']] == (['1'] if failure else ['1', '2'])
    assert state['new_arrivals']['suggestions'][0]['status'] == ('pending' if failure else 'added')
