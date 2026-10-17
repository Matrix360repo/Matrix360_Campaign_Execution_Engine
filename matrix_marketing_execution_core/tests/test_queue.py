import time
from concurrent.futures import ThreadPoolExecutor
from test_workflow import api,post
from test_approval_safety import setup_campaign


def queued(api):
    cid,_=setup_campaign(api)
    post(api,f'/campaigns/{cid}/approve')
    assert post(api,f'/campaigns/{cid}/enqueue')['new_jobs']==1
    return cid


def test_pause_restart_and_concurrent_workers(api):
    cid=queued(api)
    import app.main as m
    from app import execution
    post(api,f'/campaigns/{cid}/pause')
    assert execution.run_once(m)==0
    post(api,f'/campaigns/{cid}/resume')
    m.init_db() # existing pending queue remains intact
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _:execution.run_once(m),range(2)))==1
    assert post(api,f'/campaigns/{cid}/enqueue')['new_jobs']==0
    r=api.get(f'/campaigns/{cid}/report',headers={'X-Admin-Key':'test-key'}).json()
    assert r['mock_jobs']==1 and r['event_counts']['mock_delivered']==1


def test_failure_retries_bound_and_safe_logging(api,monkeypatch):
    cid=queued(api)
    import app.main as m
    from app import execution
    def fail(*args):raise RuntimeError('sensitive-contact-or-provider-secret')
    monkeypatch.setattr(execution,'simulated_adapter',fail)
    for attempt in range(1,4):
        assert execution.run_once(m)==1
        with m.conn() as db:
            q=db.execute('SELECT * FROM execution_queue WHERE campaign_id=?',(cid,)).fetchone()
            assert q['attempts']==attempt
            assert q['last_error']=='RuntimeError'
            db.execute('UPDATE execution_queue SET available_at=? WHERE campaign_id=?',(time.time()-1,cid))
    assert execution.run_once(m)==0
    with m.conn() as db:
        assert db.execute('SELECT status FROM campaigns WHERE id=?',(cid,)).fetchone()['status']=='failed'
        assert db.execute('SELECT COUNT(*) n FROM jobs').fetchone()['n']==0


def test_revoke_after_enqueue(api):
    cid=queued(api)
    import app.main as m
    from app import execution
    with m.conn() as db:db.execute('UPDATE leads SET suppressed=1')
    assert execution.run_once(m)==1
    with m.conn() as db:
        assert db.execute('SELECT status FROM execution_queue').fetchone()['status']=='skipped'
        assert db.execute('SELECT COUNT(*) n FROM jobs').fetchone()['n']==0
