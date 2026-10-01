"""Request identity, tracking and catalogue-conflict regressions."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from collection_web.service import Service, membership_revision
from collection_web.store import Store, DomainError
from collection_web import integrations


def title(name, ident=None):
    return dict(title=name, year=2020, media_type='movie', external_ids={'tmdb':str(ident)} if ident else {})


@pytest.fixture
def fixture(tmp_path):
    store = Store(tmp_path)
    service = Service(store)
    source = dict(id='s',name='Shelf',status='published',managed=True,origin='manual',media_type='movie',
                  items=[],missing=[title('Alpha',111), title('Beta',222)],description='Theme',thesis='Theme')
    store.update(lambda s: s['collections'].append(source))
    yield store, service
    service.close()


def test_reordered_confirmation_requests_original_title_only(fixture):
    store, service = fixture
    selection = deepcopy(store.read()['collections'][0]['missing'][0])
    store.update(lambda s:s['collections'][0]['missing'].reverse())
    with patch.object(integrations,'request_title',return_value='Requested') as request:
        service.request_missing('s',{'selections':[selection]},lambda _:None)
    assert request.call_args.args[1]['title'] == 'Alpha'


def test_stale_bulk_selection_rejects_entire_batch_before_requests(fixture):
    store, service = fixture
    selected = store.read()['collections'][0]['missing']
    store.update(lambda s:s['collections'][0]['missing'].pop())
    with patch.object(integrations,'request_title') as request:
        with pytest.raises(DomainError,match='list changed'):
            service.request_missing('s',{'selections':selected},lambda _:None)
        request.assert_not_called()


@pytest.mark.parametrize('removed', [False, True])
def test_old_draft_preserves_newer_request_tracking(fixture, removed):
    store, service = fixture
    source = store.read()['collections'][0]
    draft = dict(deepcopy(source),id='draft',origin='improve',status='draft',source_collection_id='s',
                 source_revision=membership_revision(source), auto_add_arrivals=False)
    if removed: draft['missing'] = draft['missing'][1:]
    def update(s):
        s['collections'].append(draft)
        s['collections'][0]['missing'][0].update(requested_at=123, request_progress='Downloading 50%',request_status='Requested')
    store.update(update)
    with patch.object(integrations,'publish'):
        service.apply_improvement('draft',lambda _:None)
    updated=store.read()['collections'][0]
    alpha=next(i for i in updated['missing'] if i['title']=='Alpha')
    assert alpha['requested_at']==123 and alpha['request_progress']=='Downloading 50%'
    assert updated['auto_add_arrivals']


@pytest.mark.parametrize('media,provider,key', [('movie','radarr','tmdbId'),('show','sonarr','tvdbId')])
@pytest.mark.parametrize('returned', [111,999])
def test_arr_request_honours_known_id(media,provider,key,returned):
    item=title('Alpha');item.update(media_type=media,external_ids={'tmdb' if media=='movie' else 'tvdb':'111'})
    def arr(settings,service,method,path,**kwargs):
        if path=='/rootfolder': return [{'path':'/media'}]
        if path=='/qualityprofile': return [{'id':1}]
        if path.endswith('/lookup'): return [{'title':'Alpha','year':2020,key:returned}]
        return []
    with patch.object(integrations,'arr_request',side_effect=arr) as request:
        settings={provider+'_root':'/media',provider+'_profile':1}
        if returned==999:
            with pytest.raises(DomainError): integrations.request_title(settings,item)
            assert not any(c.args[2]=='POST' for c in request.call_args_list)
        else:
            integrations.request_title(settings,item)
            assert request.call_args.kwargs['json'][key]==111
