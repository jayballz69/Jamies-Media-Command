"""First-run account setup and persistent username/password authentication."""
from collection_web.app import create_app


def test_first_run_account_setup_persists_and_cannot_be_overwritten(tmp_path):
    app=create_app(tmp_path);client=app.test_client()
    try:
        state=client.get('/api/session').json
        assert state['account_setup_required']
        payload={'username':'Jamie','password':'a sufficiently long password','confirm_password':'a sufficiently long password'}
        assert client.post('/api/setup',json=payload).status_code==403
        headers={'X-CSRF-Token':state['csrf']}
        result=client.post('/api/setup',json=payload,headers=headers)
        assert result.status_code==201
        assert client.get('/api/state').status_code==200
        assert client.post('/api/setup',json=payload,headers=headers).status_code==409
        client.post('/api/logout',json={},headers={'X-CSRF-Token':result.json['csrf']})
        assert client.post('/api/login',json={'username':'wrong','password':payload['password']}).status_code==401
        assert client.post('/api/login',json={'password':payload['password']}).status_code==401
        assert client.post('/api/login',json={'username':'Jamie','password':payload['password']}).status_code==200
        assert not (tmp_path/'admin-password.txt').exists()
        assert payload['password'].encode() not in (tmp_path/'collection-manager.sqlite3').read_bytes()
    finally:app.extensions['collection_service'].close()
    restarted=create_app(tmp_path)
    try:
        c=restarted.test_client()
        assert not c.get('/api/session').json['account_setup_required']
        assert c.post('/api/login',json={'username':'Jamie','password':payload['password']}).status_code==200
    finally:restarted.extensions['collection_service'].close()


def test_setup_rejects_cross_origin_short_password_and_mismatched_confirmation(tmp_path):
    app=create_app(tmp_path);c=app.test_client()
    try:
        headers={'X-CSRF-Token':c.get('/api/session').json['csrf']}
        payload={'username':'friend','password':'good long password','confirm_password':'good long password'}
        assert c.post('/api/setup',json=payload,headers={**headers,'Origin':'https://evil.test'}).status_code==403
        for change in ({'password':'short','confirm_password':'short'},{'confirm_password':'different'},{'username':''}):
            assert c.post('/api/setup',json={**payload,**change},headers=headers).status_code==400
        assert c.get('/api/session').json['account_setup_required']
    finally:app.extensions['collection_service'].close()
