"""Durable simulation queue. Atomic job/event commits; safe retries after interruption."""
import hashlib
import json
import time
from fastapi import HTTPException
from .qualification import eligible_clause

SCHEMA='''
CREATE TABLE IF NOT EXISTS execution_queue(campaign_id TEXT NOT NULL REFERENCES campaigns(id), lead_id TEXT NOT NULL REFERENCES leads(id), status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL, last_error TEXT, PRIMARY KEY(campaign_id,lead_id));
CREATE TABLE IF NOT EXISTS approved_content(campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id), fingerprint TEXT NOT NULL);
'''

def fingerprint(c):
    return hashlib.sha256(json.dumps([c['channel'],c['message']],ensure_ascii=False).encode()).hexdigest()

def simulated_adapter(c,lead):
    # Deliberately no network I/O or provider-accepted claims.
    return 'mock_delivered'

def run_once(m,limit=100):
    processed=0
    for _ in range(limit):
        with m.conn() as db:
            db.execute('BEGIN IMMEDIATE')
            task=db.execute("SELECT q.* FROM execution_queue q JOIN campaigns c ON c.id=q.campaign_id WHERE q.status IN ('pending','retry') AND q.available_at<=? AND c.status='queued' ORDER BY q.available_at,q.campaign_id,q.lead_id LIMIT 1",(time.time(),)).fetchone()
            if not task:break
            cid,lid=task['campaign_id'],task['lead_id']
            c=db.execute('SELECT * FROM campaigns WHERE id=?',(cid,)).fetchone()
            lead=db.execute('SELECT * FROM leads WHERE id=?',(lid,)).fetchone()
            approved=db.execute('SELECT fingerprint FROM approved_content WHERE campaign_id=?',(cid,)).fetchone()
            flag='email_allowed' if c['channel']=='email' else 'whatsapp_allowed'
            destination='email' if c['channel']=='email' else 'phone'
            q,qp=eligible_clause(c['client_id'],db)
            allowed=db.execute(f'SELECT id FROM leads WHERE id=? {q}',(lid,*qp)).fetchone()
            valid=lead and lead['client_id']==c['client_id'] and lead[flag] and not lead['suppressed'] and (lead[destination] or '').strip() and allowed
            if not valid:
                db.execute("UPDATE execution_queue SET status='skipped',last_error='Eligibility revoked' WHERE campaign_id=? AND lead_id=?",(cid,lid))
                m.audit(db,cid,'job_skipped',json.dumps({'lead_id':lid,'reason':'eligibility revoked'}))
            elif not approved or fingerprint(c)!=approved['fingerprint']:
                db.execute("UPDATE execution_queue SET status='failed',last_error='Approved content mismatch' WHERE campaign_id=? AND lead_id=?",(cid,lid))
                m.audit(db,cid,'job_failed','Approved content mismatch')
            else:
                db.execute('SAVEPOINT execute_job')
                try:
                    kind=simulated_adapter(c,lead)
                    if kind!='mock_delivered':raise RuntimeError('Unexpected simulation adapter result')
                    jid=m.ident()
                    inserted=db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?)',(jid,cid,lid,'mock_delivered',m.now()))
                    if inserted.rowcount:
                        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(m.ident(),cid,lid,jid,kind,m.now()))
                    db.execute("UPDATE execution_queue SET status='done',attempts=attempts+1,last_error=NULL WHERE campaign_id=? AND lead_id=?",(cid,lid))
                    db.execute('RELEASE SAVEPOINT execute_job')
                    m.audit(db,cid,'job_simulated',json.dumps({'lead_id':lid,'duplicate_prevented':not bool(inserted.rowcount)}))
                except Exception as exc:
                    db.execute('ROLLBACK TO SAVEPOINT execute_job');db.execute('RELEASE SAVEPOINT execute_job')
                    attempt=task['attempts']+1
                    state='failed' if attempt>=3 else 'retry'
                    # Persist error class only. Provider/adapter exceptions may contain secrets or PII.
                    db.execute('UPDATE execution_queue SET status=?,attempts=?,available_at=?,last_error=? WHERE campaign_id=? AND lead_id=?',(state,attempt,time.time()+min(300,2**attempt),type(exc).__name__,cid,lid))
                    m.audit(db,cid,'job_'+state,json.dumps({'lead_id':lid,'attempt':attempt,'error_type':type(exc).__name__}))
            pending=db.execute("SELECT COUNT(*) n FROM execution_queue WHERE campaign_id=? AND status IN ('pending','retry')",(cid,)).fetchone()['n']
            if not pending:
                failures=db.execute("SELECT COUNT(*) n FROM execution_queue WHERE campaign_id=? AND status='failed'",(cid,)).fetchone()['n']
                db.execute('UPDATE campaigns SET status=? WHERE id=?',('failed' if failures else 'completed',cid))
            processed+=1
    return processed


def install(m):
    @m.app.post('/campaigns/{campaign_id}/enqueue',dependencies=[m.Depends(m.authorize)])
    def enqueue(campaign_id:str):
        with m.conn() as db:
            db.execute('BEGIN IMMEDIATE')
            c=m.row_or_404(db,'campaigns',campaign_id)
            if c['status'] not in ('approved','queued','completed'):raise HTTPException(409,'Approve the campaign first')
            if not db.execute('SELECT 1 FROM approval_snapshots WHERE campaign_id=?',(campaign_id,)).fetchone():raise HTTPException(409,'Approved snapshot missing')
            schedule=db.execute('SELECT due_at FROM campaign_schedule WHERE campaign_id=?',(campaign_id,)).fetchone()
            due=max(time.time(),schedule['due_at'] if schedule else 0)
            inserted=db.execute("INSERT OR IGNORE INTO execution_queue SELECT campaign_id,lead_id,'pending',0,?,NULL FROM campaign_audience WHERE campaign_id=?",(due,campaign_id)).rowcount
            if inserted: db.execute("UPDATE campaigns SET status='queued' WHERE id=?",(campaign_id,))
            m.audit(db,campaign_id,'simulation_enqueued',json.dumps({'new_jobs':inserted}))
        return {'campaign_id':campaign_id,'new_jobs':inserted,'execution_mode':'simulated','live_messages_sent':0}

    @m.app.post('/campaigns/{campaign_id}/pause',dependencies=[m.Depends(m.authorize)])
    def pause(campaign_id:str):
        with m.conn() as db:
            db.execute('BEGIN IMMEDIATE');c=m.row_or_404(db,'campaigns',campaign_id)
            if c['status']!='queued':raise HTTPException(409,'Only queued campaigns can pause')
            db.execute("UPDATE campaigns SET status='paused' WHERE id=?",(campaign_id,));m.audit(db,campaign_id,'paused')
        return {'status':'paused'}

    @m.app.post('/campaigns/{campaign_id}/resume',dependencies=[m.Depends(m.authorize)])
    def resume(campaign_id:str):
        from .security import require_role
        require_role('admin','approver')
        with m.conn() as db:
            db.execute('BEGIN IMMEDIATE');c=m.row_or_404(db,'campaigns',campaign_id)
            if c['status']!='paused':raise HTTPException(409,'Campaign is not paused')
            db.execute("UPDATE campaigns SET status='queued' WHERE id=?",(campaign_id,));m.audit(db,campaign_id,'resumed')
        return {'status':'queued'}

    @m.app.get('/campaigns/{campaign_id}/jobs',dependencies=[m.Depends(m.authorize)])
    def jobs(campaign_id:str):
        with m.conn() as db:
            m.row_or_404(db,'campaigns',campaign_id)
            return {'jobs':[dict(r) for r in db.execute('SELECT * FROM execution_queue WHERE campaign_id=? ORDER BY lead_id LIMIT 1000',(campaign_id,)).fetchall()]}
