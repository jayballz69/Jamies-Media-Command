import threading
from unittest.mock import patch
import pytest
from collection_web.app import create_app
from collection_web.service import Service, possible_matches
from collection_web.store import Store


def test_movie_alias_does_not_match_tv():
    movie=dict(title='The Same Title',year=2020,media_type='movie')
    show=dict(movie,id='tv',media_type='show')
    assert possible_matches(movie,[show])==[]
    assert possible_matches(movie,[dict(show,id='movie',media_type='movie')])[0]['id']=='movie'


@pytest.mark.parametrize('failure',['store','executor'])
def test_submission_failure_releases_busy(tmp_path,failure):
    service=Service(Store(tmp_path))
    try:
        obj,name=(service.store,'update') if failure=='store' else (service.executor,'submit')
        with patch.object(obj,name,side_effect=RuntimeError('Test failure')):
            with pytest.raises(RuntimeError):service.submit('Test',lambda _:None)
        assert not service.busy
        if failure=='executor':assert service.store.read()['jobs'][0]['status']=='failed'
        with patch.object(service.executor,'submit'):
            service.submit('Retry',lambda _:None)
    finally:service.close()


def test_edit_holds_same_lock_as_background_submission(tmp_path):
    app=create_app(tmp_path,password='isolated-test-password')
    service=app.extensions['collection_service'];store=app.extensions['collection_store']
    store.update(lambda s:s['collections'].append(dict(id='c',name='Original',description='',status='draft',origin='manual',items=[],missing=[])))
    entered=threading.Event();release=threading.Event();submitted=threading.Event();errors=[]
    original=store.update
    def delayed(change):
        if threading.current_thread().name=='edit-request':
            entered.set()
            assert release.wait(5)
        return original(change)
    def edit():
        try:
            with app.test_client() as client:
                token=client.post('/api/login',json={'password':'isolated-test-password'}).get_json()['csrf']
                response=client.patch('/api/collections/c',json={'name':'Edited'},headers={'X-CSRF-Token':token})
                assert response.status_code==200
        except BaseException as e:errors.append(e)
    def submit():
        try:service.submit('Publish',lambda _:None);submitted.set()
        except BaseException as e:errors.append(e)
    try:
        with patch.object(store,'update',side_effect=delayed),patch.object(service.executor,'submit') as dispatch:
            a=threading.Thread(target=edit,name='edit-request');a.start();assert entered.wait(5)
            b=threading.Thread(target=submit);b.start()
            assert not submitted.wait(.1)
            release.set();a.join(5);b.join(5)
            assert submitted.is_set() and not errors
            assert store.read()['collections'][0]['name']=='Edited'
            dispatch.assert_called_once()
    finally:release.set();service.close()
