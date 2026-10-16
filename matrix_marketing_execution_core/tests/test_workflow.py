import os
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def api(tmp_path,monkeypatch):
    monkeypatch.setenv('MATRIX_EXEC_DB',str(tmp_path/'test.sqlite'))
    monkeypatch.setenv('MATRIX_EXEC_KEY','test-key')
    import importlib,app.main
    m=importlib.reload(app.main)
    with TestClient(m.app) as client: yield client

def post(api,path,payload=None,expected=200):
    r=api.post(path,json=payload,headers={'X-Admin-Key':'test-key'})
    assert r.status_code==expected,(r.status_code,r.text)
    return r.json()

def test_full_cycle(api):
    assert api.get('/health').json()['live_sending'] is False
    assert api.post('/clients',json={'id':'lakhotia','name':'Lakhotia'}).status_code==401
    post(api,'/clients',{'id':'lakhotia','name':'Lakhotia'})
    campaign=post(api,'/campaigns',{'client_id':'lakhotia','name':'Industrial awareness','channel':'email','message':'Approved example'})
    cid=campaign['id']
    data={'client_id':'lakhotia','leads':[
       {'source_id':'pilot','external_id':'1','name':'A','email':'a@example.test','email_allowed':True},
       {'source_id':'pilot','external_id':'2','name':'B','email':'b@example.test','email_allowed':False},
       {'source_id':'pilot','external_id':'3','name':'C','email':'c@example.test','email_allowed':True,'suppressed':True},
       {'source_id':'pilot','external_id':'4','name':'D','email':None,'email_allowed':True},
    ]}
    assert post(api,'/leads/import',data)['added']==4
    assert post(api,'/leads/import',data)['already_present']==4
    assert post(api,f'/campaigns/{cid}/dispatch-mock',expected=409)['detail']
    validation=post(api,f'/campaigns/{cid}/validate')
    assert validation['eligible']==1 and validation['excluded']==3
    assert post(api,f'/campaigns/{cid}/approve')['status']=='approved'
    first=post(api,f'/campaigns/{cid}/dispatch-mock')
    assert first['new_mock_deliveries']==1 and first['live_messages_sent']==0
    assert post(api,f'/campaigns/{cid}/dispatch-mock')['new_mock_deliveries']==0
    lead_id=api.get(f'/campaigns/{cid}/audience',headers={'X-Admin-Key':'test-key'}).json()['eligible_leads'][0]['id']
    post(api,f'/campaigns/{cid}/events',{'lead_id':lead_id,'kind':'interested'})
    report=api.get(f'/campaigns/{cid}/report',headers={'X-Admin-Key':'test-key'}).json()
    assert report['mock_jobs']==1 and report['event_counts']['interested']==1
    assert report['live_messages_sent']==0

def test_no_eligible_and_cross_client(api):
    post(api,'/clients',{'id':'client-a','name':'Client A'})
    post(api,'/clients',{'id':'client-b','name':'Client B'})
    c=post(api,'/campaigns',{'client_id':'client-a','name':'Test campaign','channel':'whatsapp','message':'Test'})
    assert post(api,f"/campaigns/{c['id']}/validate",expected=422)['detail']
    post(api,'/leads/import',{'client_id':'client-a','leads':[{'source_id':'form','external_id':'1','phone':'+919900000000','whatsapp_allowed':True}]})
    post(api,f"/campaigns/{c['id']}/validate")
    post(api,f"/campaigns/{c['id']}/approve")
    post(api,f"/campaigns/{c['id']}/dispatch-mock")
    post(api,'/leads/import',{'client_id':'client-b','leads':[{'source_id':'form','external_id':'2','phone':'+919900000001','whatsapp_allowed':True}]})
    # Other tenant lead ID must never be accepted.
    from app.main import conn
    with conn() as db:
        other=db.execute("SELECT id FROM leads WHERE client_id='client-b'").fetchone()['id']
    post(api,f"/campaigns/{c['id']}/events",{'lead_id':other,'kind':'converted'},expected=422)
