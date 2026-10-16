from fastapi.testclient import TestClient
import pytest

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('MATRIX_EXEC_KEY','console-test-key')
    monkeypatch.setenv('MATRIX_EXEC_DB',str(tmp_path/'console.sqlite'))
    import importlib,app.main
    m=importlib.reload(app.main)
    with TestClient(m.app) as api: yield api

def test_console_load_and_auth(client):
    r=client.get('/console')
    assert r.status_code==200
    assert 'SIMULATION ONLY' in r.text
    assert 'Mayur data engine' in r.text
    assert client.get('/console-data').status_code==401
    r=client.get('/console-data',headers={'X-Admin-Key':'console-test-key'})
    assert r.status_code==200 and r.json()['live_messages_sent']==0

def test_console_workflow(client):
    h={'X-Admin-Key':'console-test-key'}
    r=client.post('/clients',headers=h,json={'id':'demo-client','name':'Demo Client'})
    assert r.status_code==200
    assert len(client.get('/console-data',headers=h).json()['clients'])==1
    r=client.post('/leads/import',headers=h,json={'client_id':'demo-client','leads':[{'source_id':'independent-local-fixture','external_id':'abc','name':'Test Name','email':'test@example.test','email_allowed':True}]})
    assert r.json()['added']==1
    rows=client.get('/clients/demo-client/leads',headers=h)
    assert rows.status_code==200 and len(rows.json()['leads'])==1
    r=client.post('/campaigns',headers=h,json={'client_id':'demo-client','name':'Demo campaign','channel':'email','message':'Test only'})
    cid=r.json()['id']
    for action in ['validate','approve','dispatch-mock']:
        assert client.post(f'/campaigns/{cid}/{action}',headers=h).status_code==200
    report=client.get(f'/campaigns/{cid}/report',headers=h).json()
    assert report['live_messages_sent']==0 and report['mock_jobs']==1
    assert client.get('/clients/unknown/leads',headers=h).status_code==404
    assert client.get('/clients/demo-client/leads').status_code==401
