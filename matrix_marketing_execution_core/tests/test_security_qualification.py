import importlib
import pytest
from fastapi.testclient import TestClient
from test_workflow import api,post

H={'X-Admin-Key':'test-key'}
PW='synthetic-acceptance-password'

def user(api,name,role,clients):
    r=api.post('/auth/users',headers=H,json={'username':name,'password':PW,'role':role,'client_ids':clients})
    assert r.status_code==200,r.text

def session(api,name):
    r=api.post('/auth/login',json={'username':name,'password':PW})
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+api.cookies.get('matrix_session')}

def test_real_user_isolation_roles_and_logout(api):
    post(api,'/clients',{'id':'client-a','name':'A Client'})
    post(api,'/clients',{'id':'client-b','name':'B Client'})
    cid=post(api,'/campaigns',{'client_id':'client-b','name':'Private campaign','channel':'email','message':'Synthetic'})['id']
    user(api,'alice','operator',['client-a']);user(api,'bob','viewer',['client-b'])
    alice=session(api,'alice')
    assert api.get('/console-data',headers=alice).json()['clients'][0]['id']=='client-a'
    assert api.get('/console-data',headers=alice).json()['campaigns']==[]
    for path in [f'/campaigns/{cid}',f'/campaigns/{cid}/report',f'/campaigns/{cid}/audit',f'/campaigns/{cid}/audience','/clients/client-b/leads']:
        assert api.get(path,headers=alice).status_code==404,path
    assert api.post('/leads/import',headers=alice,json={'client_id':'client-b','leads':[{'source_id':'test','external_id':'1'}]}).status_code==404
    assert api.post(f'/campaigns/{cid}/approve',headers=alice).status_code==403
    assert api.post('/auth/users',headers=alice,json={'username':'escalate','password':PW,'role':'admin'}).status_code==403
    bob=session(api,'bob')
    assert api.post('/campaigns',headers=bob,json={'client_id':'client-b','name':'No writes','channel':'email','message':'Synthetic'}).status_code==403
    assert api.post('/auth/logout',headers=bob).status_code==200
    assert api.get('/auth/me',headers=bob).status_code==401

@pytest.mark.parametrize('profile,use,location,budget',[
    ('lakhotia','manufacturing','howrah',None),
    ('shree-heights','apartment','chandannagar',70),
    ('vrindavan-greens','bungalow','kalyani',50)])
def test_profile_to_campaign_requires_review(api,profile,use,location,budget):
    post(api,'/clients',{'id':profile,'name':profile})
    assert api.put(f'/clients/{profile}/profile',headers=H,json={'profile_id':profile}).status_code==200
    post(api,'/leads/import',{'client_id':profile,'leads':[{'source_id':'test','external_id':'one','email':'synthetic@example.test','email_allowed':True}]})
    lid=api.get(f'/clients/{profile}/leads',headers=H).json()['leads'][0]['id']
    assert api.put(f'/leads/{lid}/facts',headers=H,json={'use':use,'location':location,'budget_lakh':budget,'decision_maker':True,'land_requirement_bigha':1,'evidence':'Synthetic explicit enquiry'}).status_code==200
    q=post(api,f'/clients/{profile}/qualify')['results'][0]
    assert q['score']==100 and q['decision']=='review'
    cid=post(api,'/campaigns',{'client_id':profile,'name':'Qualified campaign','channel':'email','message':'Synthetic'})['id']
    post(api,f'/campaigns/{cid}/validate',expected=422)
    post(api,f'/leads/{lid}/review',{'decision':'qualified','reason':'Reviewed synthetic requirement'})
    assert post(api,f'/campaigns/{cid}/validate')['eligible']==1
    post(api,f'/campaigns/{cid}/approve')
    assert post(api,f'/campaigns/{cid}/dispatch-mock')['new_mock_deliveries']==1


def test_staging_shared_key_bootstrap_only(tmp_path,monkeypatch):
    monkeypatch.setenv('MATRIX_ENV','staging');monkeypatch.setenv('MATRIX_EXEC_KEY','s'*32)
    monkeypatch.setenv('MATRIX_EXEC_DB',str(tmp_path/'secure.db'))
    import app.main
    m=importlib.reload(app.main)
    with TestClient(m.app,base_url='https://testserver') as api:
        h={'X-Admin-Key':'s'*32}
        assert api.get('/console-data',headers=h).status_code==401
        assert api.post('/auth/users',headers=h,json={'username':'owner','password':PW,'role':'admin'}).status_code==200
        assert api.post('/auth/users',headers=h,json={'username':'other','password':PW,'role':'admin'}).status_code==401
        assert api.post('/auth/login',json={'username':'owner','password':PW}).status_code==200
        assert api.get('/console-data').status_code==200
        assert api.post('/clients',json={'id':'blocked','name':'Blocked'}).status_code==403
        assert api.post('/clients',headers={'Origin':'https://testserver'},json={'id':'allowed','name':'Allowed'}).status_code==200
