from test_workflow import api, post


def setup_campaign(api):
    post(api,'/clients',{'id':'lakhotia','name':'Lakhotia'})
    lead={'source_id':'test','external_id':'one','email':'one@example.test','email_allowed':True}
    post(api,'/leads/import',{'client_id':'lakhotia','leads':[lead]})
    cid=post(api,'/campaigns',{'client_id':'lakhotia','name':'Snapshot test','channel':'email','message':'Synthetic'})['id']
    post(api,f'/campaigns/{cid}/validate')
    return cid,lead


def test_later_import_excluded_and_snapshot_persists(api):
    cid,lead=setup_campaign(api)
    post(api,f'/campaigns/{cid}/approve')
    post(api,'/leads/import',{'client_id':'lakhotia','leads':[{**lead,'external_id':'two','email':'two@example.test'}]})
    from app.main import conn,init_db
    init_db()
    with conn() as db:
        assert db.execute('SELECT COUNT(*) FROM campaign_audience WHERE campaign_id=?',(cid,)).fetchone()[0]==1
    assert post(api,f'/campaigns/{cid}/dispatch-mock')['new_mock_deliveries']==1
    assert post(api,f'/campaigns/{cid}/dispatch-mock')['new_mock_deliveries']==0
    report=api.get(f'/campaigns/{cid}/report',headers={'X-Admin-Key':'test-key'}).json()
    assert report['execution_mode']=='simulated'
    assert report['provider_delivered']==0


def test_suppression_after_approval_and_no_regrant(api):
    cid,lead=setup_campaign(api)
    post(api,f'/campaigns/{cid}/approve')
    post(api,'/leads/import',{'client_id':'lakhotia','leads':[{**lead,'suppressed':True,'email_allowed':False}]})
    post(api,'/leads/import',{'client_id':'lakhotia','leads':[lead]})
    assert post(api,f'/campaigns/{cid}/dispatch-mock')['new_mock_deliveries']==0


def test_empty_approval_refused(api):
    cid,lead=setup_campaign(api)
    post(api,'/leads/import',{'client_id':'lakhotia','leads':[{**lead,'suppressed':True}]})
    post(api,f'/campaigns/{cid}/approve',expected=422)


def test_legacy_campaign_cannot_expand_audience(api):
    cid,_=setup_campaign(api)
    from app.main import conn
    with conn() as db:
        db.execute("UPDATE campaigns SET status='approved' WHERE id=?",(cid,))
    post(api,f'/campaigns/{cid}/dispatch-mock',expected=409)
