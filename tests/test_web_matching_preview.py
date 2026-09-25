import copy
from types import SimpleNamespace
from unittest.mock import patch

from collection_web.app import create_app
from collection_web.integrations import normalize_item, title_metadata, trakt_items
from collection_web.service import match_titles, reconcile_suggestions


def movie(identity='1', **changes):
    return dict(dict(id=identity, title='Local title', year=2020, media_type='movie',
                     library_id='movies', external_ids={'tmdb':'123'}), **changes)


def test_aliases_match_by_catalogue_identity_and_deduplicate():
    rows = [movie(title='International name', year=2019), movie(title='Another name')]
    owned, missing = match_titles(rows, [movie()], 'movie')
    assert [i['id'] for i in owned] == ['1'] and not missing


def test_verified_legacy_metadata_resolves_alias_and_existing_member():
    suggestion = dict(title='Other name', year=2019, metadata_status='verified',
                      metadata={'external_source':'tmdb','id':'external:123'})
    shelf = dict(media_type='movie',items=[movie()],missing=[suggestion])
    reconcile_suggestions(shelf,[movie()])
    assert not shelf['missing'] and not shelf['available']


def test_duplicate_editions_and_conflicting_ids_are_not_auto_owned():
    for library in ([movie(),movie('2')], [movie(external_ids={'tmdb':'456'})],
                    [movie(media_type='show')]):
        owned, missing = match_titles([movie()],library,'movie')
        assert not owned and len(missing)==1
    row=movie(external_ids={'tmdb':'123','imdb':'tt1234567'})
    owned,_=match_titles([row],[movie(external_ids={'tmdb':'123','imdb':'tt7654321'})],'movie')
    assert not owned


def test_legacy_title_year_still_works_and_ids_survive_missing():
    row=movie(); row.pop('external_ids')
    assert match_titles([row],[movie()],'movie')[0]
    assert match_titles([movie()],[],'movie')[1][0]['external_ids']=={'tmdb':'123'}


def test_scan_normalization_and_import_preserve_provider_ids():
    item=SimpleNamespace(ratingKey=1,title='Local title',year=2020,type='movie',
                         guids=[SimpleNamespace(id='tmdb://123'),SimpleNamespace(id='imdb://tt1234567'),
                                SimpleNamespace(id='plex://movie/private'),SimpleNamespace(id='tvdb://bad')])
    assert normalize_item(item,'movies')['external_ids']=={'tmdb':'123','imdb':'tt1234567'}
    response=SimpleNamespace(headers={},json=lambda:[{'movie':{'title':'Alias','year':2020,'ids':{'tmdb':123}}}])
    with patch('collection_web.integrations.http',return_value=response):
        assert trakt_items({'trakt_client_id':'test'},'https://trakt.tv/users/a/lists/b','movie')[0]['external_ids']=={'tmdb':'123'}
    with patch('collection_web.integrations.arr_request',return_value=[{'title':'Local title','year':2020,'tmdbId':123,'imdbId':'tt1234567'}]):
        assert title_metadata({},movie())['external_ids']=={'tmdb':'123','imdb':'tt1234567'}


def test_preview_is_read_only_matches_selection_and_explains_exclusions(tmp_path):
    app=create_app(tmp_path,password='isolated-password'); service=app.extensions['collection_service']
    store=app.extensions['collection_store']
    def shelf(identity, **extra):
        return dict(dict(id=identity,name=identity,status='published',managed=True,rotation_enabled=True,
                         media_type='movie',origin='manual',permanent=True,items=[],home=False),**extra)
    rows=[shelf('on-home',home=True),shelf('next'),shelf('off',rotation_enabled=False),
          shelf('pinned',pinned_home=True),shelf('foreign',managed=False),shelf('draft',status='draft')]
    store.update(lambda s:s.update(collections=rows))
    store.update(lambda s:s['settings'].update(permanent_movie_slots=1))
    before=copy.deepcopy(store.read())
    try:
        with app.test_client() as client:
            assert client.get('/api/rotation/preview').status_code==401
            client.post('/api/login',json={'password':'isolated-password'})
            result=client.get('/api/rotation/preview')
        assert result.status_code==200
        preview=result.get_json()
        assert [r['id'] for r in preview['permanent']]==[c['id'] for c in service._permanent_choices(before)]==['next']
        assert [r['id'] for r in preview['pinned']]==['pinned']
        assert {r['id'] for r in preview['excluded']}=={'on-home','off','foreign','draft'}
        assert all(r['reason'] for r in preview['excluded'])
        assert store.read()==before
    finally:service.close()
