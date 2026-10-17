import sqlite3
import pytest
from test_workflow import api,post
from app.backup import backup_sqlite,restore_sqlite


def test_cross_source_dedup_and_permission_revocation(api):
    post(api,'/clients',{'id':'dedup','name':'Dedup Client'})
    lead={'source_id':'first','external_id':'one','email':'Test@Example.test','email_allowed':True}
    assert post(api,'/leads/import',{'client_id':'dedup','leads':[lead]})['added']==1
    assert post(api,'/leads/import',{'client_id':'dedup','leads':[{**lead,'source_id':'second','external_id':'two','email':'test@example.test','suppressed':True}]})['already_present']==1
    from app import main
    with main.conn() as db:
        assert db.execute('SELECT COUNT(*) n FROM leads').fetchone()['n']==1
        assert db.execute('SELECT suppressed FROM leads').fetchone()['suppressed']==1
    post(api,'/leads/import',{'client_id':'dedup','leads':[{**lead,'external_id':'invalid','email':'bad-address'}]},expected=422)


def test_database_backup_restoration_preserves_records(tmp_path):
    source=tmp_path/'source.db';backup=tmp_path/'backup.db';restored=tmp_path/'restored.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT)')
        db.execute("INSERT INTO jobs VALUES('synthetic-job','mock_delivered')")
    backup_sqlite(source,backup);restore_sqlite(backup,restored)
    with sqlite3.connect(restored) as db:
        assert db.execute('SELECT * FROM jobs').fetchall()==[('synthetic-job','mock_delivered')]
    with pytest.raises(ValueError):restore_sqlite(backup,source)


def test_permission_evidence_and_suppression_are_independent(api):
    post(api,'/clients',{'id':'permission','name':'Permission Client'})
    post(api,'/leads/import',{'client_id':'permission','leads':[{'source_id':'test','external_id':'one','email':'synthetic@example.test','suppressed':True}]})
    h={'X-Admin-Key':'test-key'}
    lid=api.get('/clients/permission/leads',headers=h).json()['leads'][0]['id']
    post(api,f'/leads/{lid}/permission',{'channel':'email','allowed':True,'evidence':'Synthetic permission evidence'})
    rows=api.get('/clients/permission/leads',headers=h).json()['leads']
    assert rows[0]['email_allowed']==rows[0]['suppressed']==1
    post(api,f'/leads/{lid}/suppression',{'suppressed':False,'reason':'Synthetic administrator review'})
    from app import main
    with main.conn() as db:
        assert db.execute('SELECT COUNT(*) n FROM permission_history').fetchone()['n']==1
        assert db.execute('SELECT COUNT(*) n FROM suppression_history').fetchone()['n']==1
