"""Real server-process restart with persisted individual session and pending campaign work."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import httpx


def test_staging_session_and_pending_queue_survive_process_restart(tmp_path):
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    env={**os.environ,'MATRIX_ENV':'staging','MATRIX_EXEC_KEY':'synthetic-process-bootstrap-key-123','MATRIX_EXEC_DB':str(tmp_path/'persistent.db'),'DATABASE_URL':'','RENDER':'','MATRIX_EMBEDDED_WORKER':'0'}
    base=f'http://127.0.0.1:{port}'
    def start():
        process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],cwd=Path(__file__).resolve().parents[1],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if httpx.get(base+'/ready',timeout=.3,trust_env=False).status_code==200:return process
            except httpx.HTTPError:pass
            if process.poll() is not None:raise AssertionError('Server exited during startup')
            time.sleep(.05)
        process.terminate();process.wait(timeout=5);raise AssertionError('Server did not become ready')
    process=start()
    with httpx.Client(base_url=base,timeout=3,trust_env=False) as api:
        try:
            r=api.post('/auth/users',headers={'X-Admin-Key':env['MATRIX_EXEC_KEY']},json={'username':'restart-owner','password':'synthetic-restart-password','role':'admin'})
            assert r.status_code==200,r.text
            r=api.post('/auth/login',json={'username':'restart-owner','password':'synthetic-restart-password'})
            assert r.status_code==200
            api.headers['Authorization']='Bearer '+r.cookies['matrix_session']
            def post(path,payload=None):
                r=api.post(path,json=payload)
                assert r.status_code==200,r.text
                return r.json()
            post('/clients',{'id':'lakhotia','name':'Synthetic Lakhotia'})
            assert api.put('/clients/lakhotia/profile',json={'profile_id':'lakhotia'}).status_code==200
            post('/leads/import',{'client_id':'lakhotia','leads':[{'source_id':'restart','external_id':'one','email':'synthetic@example.test','email_allowed':True}]})
            lid=api.get('/clients/lakhotia/leads').json()['leads'][0]['id']
            assert api.put(f'/leads/{lid}/facts',json={'use':'manufacturing','location':'howrah','evidence':'Synthetic stated requirement'}).status_code==200
            post('/clients/lakhotia/qualify');post(f'/leads/{lid}/review',{'decision':'qualified','reason':'Synthetic evidence reviewed'})
            cid=post('/campaigns',{'client_id':'lakhotia','name':'Restart campaign','channel':'email','message':'Synthetic'})['id']
            for action in ['validate','approve','enqueue']:post(f'/campaigns/{cid}/{action}')
            assert api.get(f'/campaigns/{cid}/jobs').json()['jobs'][0]['status']=='pending'
        finally:process.terminate();process.wait(timeout=5)
        env['MATRIX_EMBEDDED_WORKER']='1';process=start()
        try:
            assert api.get('/auth/me').status_code==200
            for _ in range(50):
                report=api.get(f'/campaigns/{cid}/report').json()
                if report['campaign']['status']=='completed':break
                time.sleep(.1)
            assert report['mock_jobs']==1
            assert report['event_counts']['mock_delivered']==1
            assert report['provider_delivered']==report['live_messages_sent']==0
            assert post(f'/campaigns/{cid}/enqueue')['new_jobs']==0
        finally:process.terminate();process.wait(timeout=5)
