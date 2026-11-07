"""Morgin read authorization and projections against isolated synthetic state."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
from unittest.mock import Mock

import pytest

from collection_web.app import create_app
from collection_web import morgin_grants as grants
from collection_web import morgin_read as bridge


TOKEN = 's' * 43
HEADERS = {'Authorization': 'Bearer ' + TOKEN}
CANARY = 'synthetic-private-provider-credential'


def grant_row(now=None, **changes):
    now = time.time() if now is None else now
    row = {'grantId': 'fixture-grant', 'principal': 'morgin', 'installationId': 'fixture-owner-runtime',
           'tokenHash': hashlib.sha256(TOKEN.encode()).hexdigest(), 'issuedAt': grants.iso(now - 60),
           'expiresAt': grants.iso(now + 3600), 'revokedAt': None, 'capabilities': list(grants.CAPABILITIES)}
    row.update(changes)
    return row


def write_grants(directory, rows=None):
    filename = directory / grants.STORE_NAME
    filename.write_text(json.dumps({'version': 1, 'grants': rows if rows is not None else [grant_row()]}), encoding='utf-8')
    filename.chmod(0o600)
    return filename


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path, password='synthetic-owner-password')
    app.config['TESTING'] = True
    write_grants(tmp_path)
    now = time.time()
    state = app.extensions['collection_store'].read()
    state.update(synced_at=now - 120, server_id=CANARY,
                 library=[{'id': CANARY, 'title': 'Unrelated private library title', 'media_type': 'movie', 'file': '/private/raw-library'}],
                 collections=[{'id': 'safe-collection', 'name': 'Space tension', 'description': 'Fictional space adventures.',
                               'thesis': CANARY, 'plex_id': CANARY, 'media_type': 'movie', 'status': 'published',
                               'managed': True, 'home': False, 'rotation_enabled': True, 'permanent': True, 'created_at': now - 500,
                               'items': [{'id': CANARY, 'title': 'Unexported member title', 'play_count': 999}],
                               'missing': [{'title': 'Requested fixture film', 'year': 2020, 'requested_at': now - 300,
                                            'request_checked_at': now - 180, 'request_progress': 'Downloading 25%',
                                            'request_error': '', 'metadata': {'token': CANARY}, 'external_ids': {'id': CANARY}}]}],
                 jobs=[{'id': 'fixture-job', 'kind': 'Sync library', 'status': 'succeeded', 'started_at': now - 200,
                        'finished_at': now - 180, 'message': CANARY}])
    state['settings'].update(plex_token=CANARY, plex_url='http://secret.invalid/' + CANARY, radarr_key=CANARY)
    app.extensions['collection_store'].update(lambda current: current.update(deepcopy(state)))
    yield app, app.test_client(), tmp_path, state
    app.extensions['collection_service'].close()


def query(client, capability='overview', input_value=None, **kwargs):
    return client.post(bridge.PREFIX + '/query', json={'capability': capability, 'input': input_value}, headers=HEADERS, **kwargs)


def test_connection_exact_contract_without_loading_app_state(setup, monkeypatch):
    app, client, directory, _ = setup
    monkeypatch.setattr(app.extensions['collection_store'], 'read', Mock(side_effect=AssertionError('Data read')))
    response = client.get(bridge.PREFIX + '/connection', headers=HEADERS)
    assert response.status_code == 200
    assert set(response.json) == {'protocol', 'appId', 'grantId', 'expiresAt', 'sourceMode', 'capabilities'}
    assert response.json['protocol'] == 'morgin-read-v1'
    assert response.json['appId'] == 'media'
    assert response.json['sourceMode'] == 'cached'
    assert response.json['grantId'] == 'fixture-grant'
    assert {c['id'] for c in response.json['capabilities']} == set(grants.CAPABILITIES)
    assert all(set(c) == {'id', 'title', 'description', 'input'} for c in response.json['capabilities'])
    assert len(response.data) < 16 * 1024
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'Set-Cookie' not in response.headers
    assert TOKEN.encode() not in response.data


@pytest.mark.parametrize('header', [None, '', 'Basic abc', 'Bearer wrong', 'Bearer ' + 'x' * 43, 'bearer ' + TOKEN, 'Bearer ' + TOKEN + ', Bearer ' + TOKEN])
def test_denied_credentials_never_load_app_state(setup, monkeypatch, header):
    app, client, _, _ = setup
    read = Mock(side_effect=AssertionError('Data read'))
    monkeypatch.setattr(app.extensions['collection_store'], 'read', read)
    response = client.get(bridge.PREFIX + '/connection', headers={} if header is None else {'Authorization': header})
    assert response.status_code == 401
    assert set(response.json) == {'error'}
    read.assert_not_called()


def test_duplicate_authorization_headers_are_rejected(setup):
    _, client, _, _ = setup
    response = client.get(bridge.PREFIX + '/connection', headers=[('Authorization', 'Bearer ' + TOKEN), ('Authorization', 'Bearer ' + TOKEN)])
    assert response.status_code == 401


@pytest.mark.parametrize('changes', [{'expiresAt': grants.iso(time.time() - 1)}, {'revokedAt': grants.iso(time.time())}])
def test_expiry_and_revocation_survive_restart(setup, changes):
    app, client, directory, _ = setup
    write_grants(directory, [grant_row(**changes)])
    assert client.get(bridge.PREFIX + '/connection', headers=HEADERS).status_code == 401
    app.extensions['collection_service'].close()
    restarted = create_app(directory, password='synthetic-owner-password')
    try:
        assert restarted.test_client().get(bridge.PREFIX + '/connection', headers=HEADERS).status_code == 401
    finally:
        restarted.extensions['collection_service'].close()


@pytest.mark.parametrize('bad', [None, '{', ' ' * (grants.MAX_STORE_BYTES + 1), '{"version":1,"version":1,"grants":[]}', '{"version":1,"grants":[],"extra":true}'], ids=['missing', 'malformed', 'oversize', 'duplicate', 'extra'])
def test_missing_or_corrupt_store_fails_closed_without_repair(setup, monkeypatch, bad):
    app, client, directory, _ = setup
    filename = directory / grants.STORE_NAME
    if bad is None:
        filename.unlink()
    else:
        filename.write_text(bad)
    read = Mock()
    monkeypatch.setattr(app.extensions['collection_store'], 'read', read)
    assert client.get(bridge.PREFIX + '/connection', headers=HEADERS).status_code == 503
    read.assert_not_called()
    assert not filename.exists() if bad is None else filename.read_text() == bad


def test_scopes_are_enforced_before_reads_and_after_restart(setup, monkeypatch):
    app, client, directory, _ = setup
    write_grants(directory, [grant_row(capabilities=['overview'])])
    read = Mock(side_effect=AssertionError('Data read'))
    monkeypatch.setattr(app.extensions['collection_store'], 'read', read)
    assert client.get(bridge.PREFIX + '/connection', headers=HEADERS).json['capabilities'] == [grants.CAPABILITIES['overview']]
    assert query(client, 'requests').status_code == 403
    read.assert_not_called()
    with pytest.raises(grants.GrantFailure):
        grants.GrantStore(directory).authenticate('Bearer ' + 'x' * 43)
    assert grants.GrantStore(directory).authenticate('Bearer ' + TOKEN)['capabilities'] == ['overview']


@pytest.mark.parametrize('body', [
    {'capability': 'delete', 'input': None}, {'capability': 'overview', 'input': 'anything'},
    {'capability': 'overview', 'input': None, 'appId': 'other'}, {'capability': 'overview'},
    {'capability': 'search-collections', 'input': ''}, {'capability': 'search-collections', 'input': 'x' * 161},
    {'capability': 'search-collections', 'input': 'bad\x00input'}, {'capability': 'collection', 'input': '/private/file'},
    {'capability': 'collection', 'input': 'https://secret.invalid'}, {'capability': ['overview'], 'input': None},
])
def test_exact_query_validation_precedes_data_loading(setup, monkeypatch, body):
    app, client, _, _ = setup
    read = Mock()
    monkeypatch.setattr(app.extensions['collection_store'], 'read', read)
    assert client.post(bridge.PREFIX + '/query', json=body, headers=HEADERS).status_code == 400
    read.assert_not_called()


def test_body_size_encoding_and_duplicate_json_are_bounded(setup):
    _, client, _, _ = setup
    for body in [b' ' * 4097, b'\xff', b'{"capability":"overview","capability":"requests","input":null}']:
        assert client.post(bridge.PREFIX + '/query', data=body, content_type='application/json', headers=HEADERS).status_code == 400
    assert client.post(bridge.PREFIX + '/query', json={'capability': 'overview', 'input': None}, headers={**HEADERS, 'Content-Encoding': 'gzip'}).status_code == 400
    assert client.post(bridge.PREFIX + '/query', data='{}', content_type='text/plain', headers=HEADERS).status_code == 400


@pytest.mark.parametrize('method,path,expected', [
    ('GET', '/unknown', 404), ('POST', '/connection', 405), ('GET', '/query', 405),
    ('HEAD', '/connection', 405), ('OPTIONS', '/query', 405), ('DELETE', '/query', 405),
    ('GET', '/connection?token=forbidden', 400), ('GET', '/connection?', 400), ('GET', '/%63onnection', 400),
])
def test_fixed_routes_have_no_redirect_or_method_fallback(setup, method, path, expected):
    _, client, _, _ = setup
    response = client.open(bridge.PREFIX + path, method=method, headers=HEADERS,
                           environ_overrides={'RAW_URI': bridge.PREFIX + path})
    assert response.status_code == expected
    assert 'Location' not in response.headers
    assert response.headers['Cache-Control'] == 'no-store'


def test_bridge_grants_do_not_authorize_browser_routes_and_sessions_do_not_authorize_bridge(setup):
    _, client, _, _ = setup
    assert client.get('/api/state', headers=HEADERS).status_code == 401
    assert client.post('/api/library/sync', json={}, headers=HEADERS).status_code == 401
    login = client.post('/api/login', json={'password': 'synthetic-owner-password'})
    assert login.status_code == 200
    assert client.get('/api/state').status_code == 200
    assert client.get(bridge.PREFIX + '/connection').status_code == 401
    assert client.post('/api/settings', json={'permanent_movie_slots': 3}).status_code == 403
    assert client.post('/api/settings', json={'permanent_movie_slots': 3}, headers={'X-CSRF-Token': login.json['csrf']}).status_code == 200
    assert client.get(bridge.PREFIX + '/connection', headers={**HEADERS, 'Origin': 'https://browser.invalid'}).status_code == 403


@pytest.mark.parametrize('capability,input_value', [('overview', None), ('search-collections', 'space'), ('collection', 'safe-collection'), ('requests', None)])
def test_all_reads_are_projected_cached_and_side_effect_free(setup, monkeypatch, capability, input_value):
    from collection_web import integrations
    app, client, directory, _ = setup
    store = app.extensions['collection_store']
    database_before = store.path.read_bytes()
    grant_before = (directory / grants.STORE_NAME).read_bytes()
    update = Mock(side_effect=AssertionError('Write attempted'))
    submit = Mock(side_effect=AssertionError('Task attempted'))
    external = Mock(side_effect=AssertionError('External request attempted'))
    monkeypatch.setattr(store, 'update', update)
    monkeypatch.setattr(store, 'backup', update)
    monkeypatch.setattr(app.extensions['collection_service'], 'submit', submit)
    monkeypatch.setattr(integrations, 'http', external)
    monkeypatch.setattr(integrations, 'arr_request', external)
    monkeypatch.setattr(integrations, 'scan_library', external)
    response = query(client, capability, input_value)
    assert response.status_code == 200
    assert set(response.json) == {'appId', 'capability', 'status', 'sourceMode', 'source', 'observedAt', 'retrievedAt', 'summary', 'facts', 'items', 'limitations'}
    assert response.json['appId'] == 'media'
    assert response.json['sourceMode'] == 'cached'
    assert response.json['observedAt'] != response.json['retrievedAt']
    assert len(response.data) < bridge.MAX_RESULT
    for forbidden in [CANARY, 'Unrelated private library title', 'Unexported member title', 'play_count', 'plex_url', 'plex_token', '/private/', 'secret.invalid']:
        assert forbidden.encode() not in response.data
    for mocked in [update, submit, external]:
        mocked.assert_not_called()
    assert store.path.read_bytes() == database_before
    assert (directory / grants.STORE_NAME).read_bytes() == grant_before


def test_requests_report_existing_progress_without_exporting_messages(setup):
    _, client, _, _ = setup
    response = query(client, 'requests')
    assert any('Downloading 25%' in row['description'] for row in response.json['items'])
    assert any(row['title'] == 'Sync library' and 'succeeded' in row['description'] for row in response.json['items'])
    assert CANARY.encode() not in response.data


def test_unknown_ids_and_missing_or_stale_observations_are_honest(setup):
    app, client, _, _ = setup
    assert query(client, 'collection', 'missing').json['status'] == 'unavailable'
    store = app.extensions['collection_store']
    for observed in [0, time.time() - 7200]:
        store.update(lambda state: state.update(synced_at=observed))
        result = query(client).json
        assert result['status'] == 'partial'
        assert result['observedAt'] is not None if observed else result['observedAt'] is None


def test_utf8_response_and_row_limits_disclose_omissions(setup):
    app, client, _, state = setup
    rows = [dict(deepcopy(state['collections'][0]), id='fixture-' + str(i), name='水' * 300, description='水' * 2000) for i in range(80)]
    app.extensions['collection_store'].update(lambda current: current.update(collections=rows))
    response = query(client, 'search-collections', '水')
    assert response.status_code == 200
    assert len(response.data) < 24 * 1024
    assert response.json['status'] == 'partial'
    assert len(response.json['items']) <= 20
    assert any('omitted' in text for text in response.json['limitations'])


def test_private_links_paths_and_control_characters_in_allowed_text_are_omitted(setup):
    app, client, _, _ = setup
    app.extensions['collection_store'].update(lambda state: state['collections'][0].update(description='Note https://private.invalid/token /srv/private/file C:\\private\\file\x00'))
    response = query(client, 'collection', 'safe-collection')
    assert response.json['status'] == 'partial'
    for forbidden in ['https://', '/srv/', 'C:\\', 'private.invalid']:
        assert forbidden.encode() not in response.data


def test_data_errors_are_generic_and_do_not_write_diagnostics(setup, monkeypatch):
    from collection_web import diagnostics
    app, client, _, _ = setup
    monkeypatch.setattr(app.extensions['collection_store'], 'read', Mock(side_effect=RuntimeError(CANARY)))
    record = Mock()
    monkeypatch.setattr(diagnostics, 'record_error', record)
    response = query(client)
    assert response.status_code == 503
    assert CANARY.encode() not in response.data
    record.assert_not_called()


def test_missing_app_database_does_not_create_an_empty_file(setup, monkeypatch, tmp_path):
    app, client, _, _ = setup
    missing = tmp_path / 'missing.sqlite3'
    monkeypatch.setattr(app.extensions['collection_store'], 'path', missing)
    assert query(client).status_code == 503
    assert not missing.exists()


def test_revocation_during_read_prevents_result_delivery(setup, monkeypatch):
    app, client, directory, state = setup
    def read():
        write_grants(directory, [grant_row(revokedAt=grants.iso(time.time()))])
        return state
    monkeypatch.setattr(app.extensions['collection_store'], 'read', read)
    response = query(client)
    assert response.status_code == 401
    assert b'Space tension' not in response.data


def test_owner_cli_hash_only_issue_list_revoke_and_private_artifact(tmp_path, monkeypatch, capsys):
    # Test owner operations on Windows too; real CLI refuses NTFS enrollment.
    # POSIX permission enforcement has its own test and is used by the container.
    monkeypatch.setattr(grants, '_owner_directory', lambda directory: Path(directory).resolve())
    output = tmp_path / 'morgin-enrollment-fixture.json'
    expiry = grants.iso(time.time() + 3600)
    arguments = ['--data-dir', str(tmp_path), 'issue', '--installation-id', 'fixture-runtime', '--expires-at', expiry,
                 '--origin', 'http://127.0.0.1:39000', '--output', str(output), '--capability', 'overview']
    assert grants.main(arguments) == 0
    receipt = json.loads(output.read_text())
    assert set(receipt) == {'version', 'appId', 'bridgeOrigin', 'grantId', 'expiresAt', 'token'}
    assert receipt['bridgeOrigin'] == 'http://127.0.0.1:39000'
    assert receipt['appId'] == 'media' and receipt['version'] == 1
    assert grants.TOKEN.fullmatch(receipt['token'])
    capture = capsys.readouterr()
    assert receipt['token'] not in capture.out + capture.err
    assert receipt['token'] not in (tmp_path / grants.STORE_NAME).read_text()
    assert grants.GrantStore(tmp_path).authenticate('Bearer ' + receipt['token'])['capabilities'] == ['overview']
    assert grants.main(['--data-dir', str(tmp_path), 'list']) == 0
    assert receipt['token'] not in capsys.readouterr().out
    assert grants.main(['--data-dir', str(tmp_path), 'revoke', '--grant-id', receipt['grantId']]) == 0
    assert receipt['token'] not in capsys.readouterr().out
    with pytest.raises(grants.GrantFailure) as failure:
        grants.GrantStore(tmp_path).authenticate('Bearer ' + receipt['token'])
    assert failure.value.status == 401
    if os.name == 'posix':
        assert output.stat().st_mode & 0o077 == 0
        assert (tmp_path / grants.STORE_NAME).stat().st_mode & 0o077 == 0


def test_owner_cli_rejects_unsafe_storage_output_scope_lifetime_and_origin(tmp_path, monkeypatch):
    with pytest.raises((grants.EnrollmentFailure, FileNotFoundError)):
        grants._owner_directory(tmp_path / 'missing')
    monkeypatch.setattr(grants, '_owner_directory', lambda directory: Path(directory).resolve())
    args = {'installation_id': 'fixture', 'expires_at': grants.iso(time.time() + 3600), 'origin': 'http://127.0.0.1:39000',
            'output': tmp_path / 'morgin-enrollment-fixture.json', 'capabilities': ['overview']}
    for change in [{'expires_at': grants.iso(time.time() + 31 * 86400)}, {'expires_at': grants.iso(time.time() - 1)},
                   {'origin': 'http://192.0.2.1:8780'}, {'origin': 'http://localhost:39000'}, {'origin': 'http://127.0.0.1:39000/'},
                   {'capabilities': ['write']}, {'output': tmp_path / 'tracked.json'}, {'output': tmp_path.parent / 'morgin-enrollment-other.json'}]:
        with pytest.raises(grants.EnrollmentFailure):
            grants.issue(tmp_path, **dict(args, **change))
    args['output'].write_text('existing file')
    with pytest.raises(FileExistsError):
        grants.issue(tmp_path, **args)
    assert args['output'].read_text() == 'existing file'
    assert not (tmp_path / grants.STORE_NAME).exists()


def test_enrollment_fails_closed_on_corruption_and_contested_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(grants, '_owner_directory', lambda directory: Path(directory).resolve())
    args = {'installation_id': 'fixture', 'expires_at': grants.iso(time.time() + 3600), 'origin': 'http://127.0.0.1:39000',
            'output': tmp_path / 'morgin-enrollment-fixture.json', 'capabilities': ['overview']}
    (tmp_path / grants.STORE_NAME).write_text('{')
    with pytest.raises(grants.GrantFailure):
        grants.issue(tmp_path, **args)
    assert not args['output'].exists()
    (tmp_path / (grants.STORE_NAME + '.lock')).write_text('')
    with pytest.raises(FileExistsError):
        grants.issue(tmp_path, **args)
    assert (tmp_path / grants.STORE_NAME).read_text() == '{'


def test_failed_grant_commit_does_not_leave_a_secret_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(grants, '_owner_directory', lambda directory: Path(directory).resolve())
    monkeypatch.setattr(grants, '_save', Mock(side_effect=OSError('synthetic disk failure')))
    output = tmp_path / 'morgin-enrollment-fixture.json'
    with pytest.raises(OSError):
        grants.issue(tmp_path, installation_id='fixture', expires_at=grants.iso(time.time() + 3600), origin='http://127.0.0.1:39000', output=output, capabilities=['overview'])
    assert not output.exists()
    assert not (tmp_path / (grants.STORE_NAME + '.lock')).exists()


@pytest.mark.skipif(os.name != 'posix', reason='Actual private POSIX owner gate runs on Linux')
def test_posix_owner_enrollment_permissions_and_symlink_denial(tmp_path):
    tmp_path.chmod(0o700)
    database = tmp_path / 'collection-manager.sqlite3'
    database.write_bytes(b'synthetic-existing-database-marker')
    output = tmp_path / 'morgin-enrollment-posix.json'
    args = {'installation_id': 'fixture-runtime', 'expires_at': grants.iso(time.time() + 3600),
            'origin': 'http://127.0.0.1:39000', 'output': output, 'capabilities': ['overview']}
    issued = grants.issue(tmp_path, **args)
    assert database.read_bytes() == b'synthetic-existing-database-marker'
    assert output.stat().st_mode & 0o077 == 0
    filename = tmp_path / grants.STORE_NAME
    assert filename.stat().st_mode & 0o077 == 0
    receipt = json.loads(output.read_text())
    filename.chmod(0o644)
    with pytest.raises(grants.GrantFailure):
        grants.GrantStore(tmp_path).authenticate('Bearer ' + receipt['token'])
    filename.chmod(0o600)
    grants.revoke(tmp_path, issued['grantId'])
    assert grants.GrantStore(tmp_path).read()['grants'][0]['revokedAt']
    tmp_path.chmod(0o755)
    with pytest.raises(grants.EnrollmentFailure):
        grants._owner_directory(tmp_path)
    tmp_path.chmod(0o700)
    filename.unlink()
    filename.symlink_to(tmp_path / 'missing-target')
    with pytest.raises(grants.GrantFailure):
        grants.issue(tmp_path, **dict(args, output=tmp_path / 'morgin-enrollment-second.json'))
    assert filename.is_symlink()


def test_chunked_query_body_is_bounded_before_app_read(setup, monkeypatch):
    from io import BytesIO
    app, client, _, _ = setup
    read = Mock()
    monkeypatch.setattr(app.extensions['collection_store'], 'read', read)
    response = client.open(bridge.PREFIX + '/query', method='POST', headers=HEADERS,
                           environ_overrides={'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': '',
                                              'wsgi.input': BytesIO(b' ' * 4097), 'wsgi.input_terminated': True})
    assert response.status_code == 400
    read.assert_not_called()
