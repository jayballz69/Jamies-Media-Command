"""Support exports never include exception text, credentials or private state."""
import json
from unittest.mock import patch
import pytest
from collection_web.app import create_app
from collection_web.diagnostics import record_error, export_report
from collection_web.store import Store
from collection_web.service import Service


def test_diagnostics_allowlist_excludes_private_content_and_bounds_history(tmp_path):
    store = Store(tmp_path)
    secret = 'unique-private-canary'
    def seed(s):
        s['settings'].update(plex_token=secret, llm_key=secret, plex_url='http://'+secret, llm_model=secret)
        s['library'] = [{'title':secret}]
        s['collections'] = [{'name':secret}]
        s['activity'] = [{'message':secret}]
        s['jobs'] = [{'id':secret,'kind':secret,'status':'failed','message':secret,'started_at':secret}]
    store.update(seed)
    for i in range(103):
        try:
            raise TypeError(secret)
        except TypeError as error:
            record_error(store, error, job_id=secret)
    report = export_report(Store(tmp_path))
    assert len(report['errors']) == 100
    assert secret not in json.dumps(report)
    assert report['errors'][0]['type'] == 'TypeError'
    assert len(report['code_fingerprint']) == 64
    assert report['counts'] == {'library':1,'collections':1}


def test_failed_job_records_frames_without_exception_message(tmp_path):
    store = Store(tmp_path)
    service = Service(store)
    try:
        # TypeError originates inside service.json_dumps and has no credentials in its frames.
        from collection_web.service import json_dumps
        service.submit('Add owned suggestions', lambda progress: json_dumps(object()))
        service.close()
        report = export_report(store)
        assert report['errors'][0]['type'] == 'TypeError'
        assert any(f['function']=='json_dumps' for f in report['errors'][0]['frames'])
        assert report['errors'][0]['job_id'] == report['jobs'][0]['id']
        assert 'Diagnostic reference:' in store.read()['jobs'][0]['message']
    finally:
        service.close()


def test_export_requires_login_and_unexpected_api_errors_are_captured(tmp_path):
    app = create_app(tmp_path,password='test-password')
    client = app.test_client()
    try:
        assert client.get('/api/diagnostics/export').status_code == 401
        client.post('/api/login',json={'password':'test-password'})
        with patch.object(app.extensions['collection_store'], 'read', side_effect=TypeError('secret-url-key')):
            result = client.get('/api/state')
        assert result.status_code == 500
        assert 'secret-url-key' not in result.get_data(as_text=True)
        result = client.get('/api/diagnostics/export')
        assert result.status_code == 200
        assert 'attachment' in result.headers['Content-Disposition']
        assert result.headers['Cache-Control'] == 'no-store'
        assert result.json['errors'][0]['context'] == 'api'
        assert 'secret-url-key' not in result.get_data(as_text=True)
    finally:
        app.extensions['collection_service'].close()


def test_capture_failure_does_not_mask_task_failure_or_leave_busy(tmp_path):
    store=Store(tmp_path);service=Service(store)
    try:
        with patch('collection_web.diagnostics.record_error', side_effect=RuntimeError('storage unavailable')):
            service.submit('Add owned suggestions',lambda progress: 1/0)
            service.close()
        assert store.read()['jobs'][0]['status']=='failed'
        assert not service.busy
    finally:
        service.close()
