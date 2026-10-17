"""Matrix 360 Marketing Execution Core: mock-only, local MVP."""
import csv
import io
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager, asynccontextmanager
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Header, HTTPException, Depends, Request
from .security import principal, require_client, require_role, SCHEMA as AUTH_SCHEMA
from .qualification import SCHEMA as QUAL_SCHEMA, eligible_clause
from .execution import SCHEMA as EXEC_SCHEMA, fingerprint
from .permissions import SCHEMA as PERMISSION_SCHEMA
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

DB_PATH = os.getenv('MATRIX_EXEC_DB', './marketing_execution.db')
DATABASE_URL = os.getenv('DATABASE_URL','')
ADMIN_KEY = os.getenv('MATRIX_EXEC_KEY', '')
APP_ENV = os.getenv('MATRIX_ENV', 'development').strip().lower()

@asynccontextmanager
async def lifespan(app):
    startup()
    async def background_worker():
        while True:
            try: await asyncio.to_thread(execution.run_once,sys.modules[__name__],20)
            except Exception as exc: logging.getLogger(__name__).error('Worker error: %s',type(exc).__name__)
            await asyncio.sleep(1)
    task=asyncio.create_task(background_worker()) if os.getenv('MATRIX_EMBEDDED_WORKER')=='1' else None
    try: yield
    finally:
        if task:
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass

app = FastAPI(lifespan=lifespan,title='Matrix 360 Marketing Execution Core', version='0.4.0', description='Local sandbox / mock dispatch only. No live messages are sent.')

def now(): return datetime.now(timezone.utc).isoformat()
def ident(): return uuid.uuid4().hex

async def authorize(request: Request):
    return authorize_impl(request)

@contextmanager
def conn():
    if DATABASE_URL:
        from .storage import Postgres
        db=Postgres(DATABASE_URL)
    else:
        p=Path(DB_PATH); p.parent.mkdir(parents=True, exist_ok=True)
        db=sqlite3.connect(str(p), timeout=20)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback(); raise
    finally: db.close()

def init_db():
    with conn() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS clients(id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS campaigns(id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id), name TEXT NOT NULL, channel TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, approved_at TEXT);
        CREATE TABLE IF NOT EXISTS leads(id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id), source_id TEXT NOT NULL, external_id TEXT NOT NULL, name TEXT, email TEXT, phone TEXT, email_allowed INTEGER NOT NULL DEFAULT 0, whatsapp_allowed INTEGER NOT NULL DEFAULT 0, suppressed INTEGER NOT NULL DEFAULT 0, UNIQUE(client_id,source_id,external_id));
        CREATE TABLE IF NOT EXISTS lead_aliases(client_id TEXT NOT NULL REFERENCES clients(id), source_id TEXT NOT NULL, external_id TEXT NOT NULL, lead_id TEXT NOT NULL REFERENCES leads(id), PRIMARY KEY(client_id,source_id,external_id));
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id), lead_id TEXT NOT NULL REFERENCES leads(id), status TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(campaign_id,lead_id));
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id), lead_id TEXT NOT NULL REFERENCES leads(id), job_id TEXT, kind TEXT NOT NULL, at TEXT NOT NULL, UNIQUE(job_id,kind));
        CREATE TABLE IF NOT EXISTS campaign_schedule(campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id), due_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS campaign_audience(campaign_id TEXT NOT NULL REFERENCES campaigns(id), lead_id TEXT NOT NULL REFERENCES leads(id), PRIMARY KEY(campaign_id,lead_id));
        CREATE TABLE IF NOT EXISTS approval_snapshots(campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id), action TEXT NOT NULL, at TEXT NOT NULL, details TEXT NOT NULL);
        ''')
        db.executescript(AUTH_SCHEMA)
        db.executescript(QUAL_SCHEMA)
        db.executescript(EXEC_SCHEMA)
        db.executescript(PERMISSION_SCHEMA)
        db.executescript('''CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS queue_ready_idx ON execution_queue(status,available_at);
        CREATE INDEX IF NOT EXISTS lead_client_idx ON leads(client_id);
        CREATE INDEX IF NOT EXISTS session_expiry_idx ON sessions(expires_at);
        ''')
        db.execute('INSERT OR IGNORE INTO schema_migrations VALUES(?,?)',(4,now()))

def row_or_404(db, table, rowid):
    row=db.execute(f'SELECT * FROM {table} WHERE id=?', (rowid,)).fetchone()
    if row is None: raise HTTPException(404, f'{table} record not found')
    if table=='clients': require_client(row['id'])
    elif table in ('leads','campaigns'): require_client(row['client_id'])
    return row

def dictrow(r): return dict(r)
def audit(db,cid,action,details=''):
    details=json.dumps({'actor':(principal.get() or {}).get('id','system'),'details':details})
    db.execute('INSERT INTO audit VALUES(?,?,?,?,?)',(ident(),cid,action,now(),details))

class ClientIn(BaseModel):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{1,63}$')
    name: str = Field(min_length=2,max_length=160)

class CampaignIn(BaseModel):
    client_id: str
    name: str = Field(min_length=3,max_length=160)
    channel: Literal['email','whatsapp']
    message: str = Field(min_length=1,max_length=3000)
    scheduled_at: datetime | None = None

    @field_validator('scheduled_at')
    @classmethod
    def aware_schedule(cls,value):
        if value is not None and value.tzinfo is None:raise ValueError('Schedule must include a timezone')
        return value

class LeadIn(BaseModel):
    source_id: str = Field(min_length=1,max_length=150)
    external_id: str = Field(min_length=1,max_length=150)
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    email_allowed: bool = False
    whatsapp_allowed: bool = False
    suppressed: bool = False

class ImportIn(BaseModel):
    client_id: str
    leads: list[LeadIn] = Field(min_length=1,max_length=1000)

class EventIn(BaseModel):
    lead_id: str
    kind: Literal['reply','interested','not_interested','appointment','converted']

def startup():
    if APP_ENV not in ('development', 'staging', 'production'):
        raise RuntimeError('MATRIX_ENV must be development, staging or production')
    if APP_ENV == 'production':
        raise RuntimeError('Production is blocked: SQLite and single shared-key auth are not production approved')
    if APP_ENV == 'staging' and (not ADMIN_KEY or len(ADMIN_KEY) < 24):
        raise RuntimeError('Staging requires MATRIX_EXEC_KEY with at least 24 characters')
    if os.getenv('RENDER') and not DATABASE_URL:
        raise RuntimeError('Hosted staging requires DATABASE_URL; ephemeral SQLite is refused')
    init_db()

@app.get('/health')
def health():
    return {'status':'alive','environment':APP_ENV,'mode':'mock-only','live_sending':False}

@app.get('/ready')
def ready():
    try:
        with conn() as db:
            db.execute('SELECT COUNT(*) FROM clients').fetchone()
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(503, 'Persistence unavailable') from exc
    return {'status':'ready','environment':APP_ENV,'persistence':'postgresql' if DATABASE_URL else 'sqlite-local','live_sending':False}

@app.post('/clients',dependencies=[Depends(authorize)])
def create_client(payload:ClientIn):
    require_role('admin')
    init_db()
    with conn() as db:
        try: db.execute('INSERT INTO clients VALUES(?,?)',(payload.id,payload.name))
        except sqlite3.IntegrityError: raise HTTPException(409,'Client already exists')
    return payload.model_dump()

@app.post('/campaigns',dependencies=[Depends(authorize)])
def create_campaign(payload: CampaignIn):
    with conn() as db:
        row_or_404(db,'clients',payload.client_id)
        if APP_ENV!='development' and not db.execute('SELECT 1 FROM client_profiles WHERE client_id=?',(payload.client_id,)).fetchone():
            raise HTTPException(422,'Select this client’s qualification profile before creating a campaign')
        cid=ident()
        db.execute('INSERT INTO campaigns VALUES(?,?,?,?,?,?,?,?)',(cid,payload.client_id,payload.name,payload.channel,payload.message,'draft',now(),None))
        if payload.scheduled_at:
            db.execute('INSERT INTO campaign_schedule VALUES(?,?)',(cid,payload.scheduled_at.timestamp()))
        audit(db,cid,'created')
        return dictrow(row_or_404(db,'campaigns',cid))

@app.get('/campaigns/{campaign_id}',dependencies=[Depends(authorize)])
def get_campaign(campaign_id: str):
    with conn() as db: return dictrow(row_or_404(db,'campaigns',campaign_id))

@app.post('/leads/import',dependencies=[Depends(authorize)])
def import_leads(payload:ImportIn):
    """Permission defaults to False. Existing permissions cannot be increased by repeat import."""
    with conn() as db:
        row_or_404(db,'clients',payload.client_id)
        db.execute('BEGIN IMMEDIATE')
        added=existing=0
        for lead in payload.leads:
            if lead.email:
                lead.email=lead.email.strip().lower()
                if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',lead.email): raise HTTPException(422,'Invalid email destination')
            if lead.phone:
                lead.phone=re.sub(r'[\s().-]','',lead.phone)
                if not re.fullmatch(r'\+[1-9][0-9]{7,14}',lead.phone):raise HTTPException(422,'Phone must include country code, for example +91…')
            # Do not overwrite permission fields on duplicate import: explicit permission-management workflow required.
            before=db.execute('SELECT id FROM leads WHERE client_id=? AND source_id=? AND external_id=?',(payload.client_id,lead.source_id,lead.external_id)).fetchone()
            if not before:
                before=db.execute('SELECT lead_id AS id FROM lead_aliases WHERE client_id=? AND source_id=? AND external_id=?',(payload.client_id,lead.source_id,lead.external_id)).fetchone()
            if not before:
                before=db.execute("SELECT id FROM leads WHERE client_id=? AND ((COALESCE(?, '')!='' AND LOWER(TRIM(email))=?) OR (COALESCE(?, '')!='' AND phone=?)) ORDER BY id LIMIT 1",(payload.client_id,lead.email,lead.email,lead.phone,lead.phone)).fetchone()
            if before:
                db.execute('INSERT OR IGNORE INTO lead_aliases VALUES(?,?,?,?)',(payload.client_id,lead.source_id,lead.external_id,before['id']))
                # Suppression and revocation must take effect even on repeat imports.
                db.execute('UPDATE leads SET suppressed=MAX(suppressed,?), email_allowed=MIN(email_allowed,?), whatsapp_allowed=MIN(whatsapp_allowed,?) WHERE id=?',
                           (int(lead.suppressed),int(lead.email_allowed),int(lead.whatsapp_allowed),before['id']))
                existing+=1;continue
            db.execute('INSERT INTO leads VALUES(?,?,?,?,?,?,?,?,?,?)',(ident(),payload.client_id,lead.source_id,lead.external_id,lead.name,lead.email,lead.phone,int(lead.email_allowed),int(lead.whatsapp_allowed),int(lead.suppressed)))
            added+=1
    return {'added':added,'already_present':existing,'total_input':len(payload.leads)}

@app.post('/campaigns/{campaign_id}/validate',dependencies=[Depends(authorize)])
def validate_campaign(campaign_id: str):
    with conn() as db:
        c=row_or_404(db,'campaigns',campaign_id)
        if c['status']!='draft': raise HTTPException(409,'Only draft campaigns can be validated')
        flag='email_allowed' if c['channel']=='email' else 'whatsapp_allowed'
        address='email' if c['channel']=='email' else 'phone'
        qual,qp=eligible_clause(c['client_id'],db)
        counts=db.execute(f'''SELECT COUNT(*) total, SUM(CASE WHEN {flag}=1 AND suppressed=0 AND TRIM(COALESCE({address},''))!='' THEN 1 ELSE 0 END) eligible FROM leads WHERE client_id=? {qual}''',(c['client_id'],*qp)).fetchone()
        eligible=counts['eligible'] or 0
        if eligible==0: raise HTTPException(422,'No permission-eligible leads with destination address')
        db.execute('UPDATE campaigns SET status=? WHERE id=?',('validated',campaign_id))
        audit(db,campaign_id,'validated',json.dumps({'eligible':eligible,'total':counts['total']}))
        return {'campaign_id':campaign_id,'status':'validated','eligible':eligible,'excluded':counts['total']-eligible}

@app.post('/campaigns/{campaign_id}/approve',dependencies=[Depends(authorize)])
def approve_campaign(campaign_id: str):
    with conn() as db:
        db.execute('BEGIN IMMEDIATE')
        c=row_or_404(db,'campaigns',campaign_id)
        if c['status']!='validated': raise HTTPException(409,'Campaign must be validated before approval')
        flag='email_allowed' if c['channel']=='email' else 'whatsapp_allowed'
        address='email' if c['channel']=='email' else 'phone'
        qual,qp=eligible_clause(c['client_id'],db)
        db.execute(f"INSERT INTO campaign_audience SELECT ?,id FROM leads WHERE client_id=? AND {flag}=1 AND suppressed=0 AND TRIM(COALESCE({address},''))!='' {qual}",(campaign_id,c['client_id'],*qp))
        count=db.execute('SELECT COUNT(*) FROM campaign_audience WHERE campaign_id=?',(campaign_id,)).fetchone()[0]
        if not count: raise HTTPException(422,'No eligible audience remains; approval refused')
        db.execute('INSERT INTO approval_snapshots VALUES(?,?)',(campaign_id,now()))
        db.execute('INSERT INTO approved_content VALUES(?,?)',(campaign_id,fingerprint(c)))
        db.execute('UPDATE campaigns SET status=?,approved_at=? WHERE id=?',('approved',now(),campaign_id))
        audit(db,campaign_id,'approved','Approved for MOCK dispatch only')
        return {'campaign_id':campaign_id,'status':'approved','permission':'mock_only'}

@app.post('/campaigns/{campaign_id}/dispatch-mock',dependencies=[Depends(authorize)])
def mock_dispatch(campaign_id: str):
    if APP_ENV!='development':raise HTTPException(409,'Use the durable simulation queue in hosted staging')
    with conn() as db:
        db.execute('BEGIN IMMEDIATE')
        c=row_or_404(db,'campaigns',campaign_id)
        if c['status'] not in ('approved','completed'): raise HTTPException(409,'Campaign must be approved before dispatch')
        flag='email_allowed' if c['channel']=='email' else 'whatsapp_allowed'
        address='email' if c['channel']=='email' else 'phone'
        if not db.execute('SELECT 1 FROM approval_snapshots WHERE campaign_id=?',(campaign_id,)).fetchone():
            raise HTTPException(409,'Legacy campaign has no approved audience snapshot; create and approve a new campaign')
        qual,qp=eligible_clause(c['client_id'],db)
        leads=db.execute(f'''SELECT id FROM leads WHERE client_id=? AND {flag}=1 AND suppressed=0 AND TRIM(COALESCE({address},''))!='' AND id IN (SELECT lead_id FROM campaign_audience WHERE campaign_id=?) {qual} ORDER BY id''',(c['client_id'],campaign_id,*qp)).fetchall()
        created=0
        for lead in leads:
            jid=ident()
            res=db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?)',(jid,campaign_id,lead['id'],'mock_delivered',now()))
            if res.rowcount:
                created+=1
                db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(ident(),campaign_id,lead['id'],jid,'mock_delivered',now()))
        db.execute('UPDATE campaigns SET status=? WHERE id=?',('completed',campaign_id))
        audit(db,campaign_id,'mock_dispatched',json.dumps({'new_jobs':created}))
        return {'campaign_id':campaign_id,'status':'completed','new_mock_deliveries':created,'duplicates_prevented':len(leads)-created,'live_messages_sent':0}

@app.post('/campaigns/{campaign_id}/events',dependencies=[Depends(authorize)])
def add_event(campaign_id: str, event: EventIn):
    with conn() as db:
        c=row_or_404(db,'campaigns',campaign_id)
        if c['status']!='completed': raise HTTPException(409,'Simulated dispatch must complete first')
        lead=row_or_404(db,'leads',event.lead_id)
        if lead['client_id']!=c['client_id']: raise HTTPException(422,'Lead does not belong to campaign client')
        job=db.execute('SELECT id FROM jobs WHERE campaign_id=? AND lead_id=?',(campaign_id,event.lead_id)).fetchone()
        if job is None: raise HTTPException(422,'No delivery job for this lead')
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(ident(),campaign_id,event.lead_id,None,event.kind,now()))
        audit(db,campaign_id,'event_recorded',event.kind)
        return {'recorded':True,'evidence_source':'simulated','kind':event.kind,'lead_id':event.lead_id}

@app.get('/campaigns/{campaign_id}/report',dependencies=[Depends(authorize)])
def campaign_report(campaign_id: str):
    with conn() as db:
        c=row_or_404(db,'campaigns',campaign_id)
        jobs=db.execute('SELECT COUNT(*) n FROM jobs WHERE campaign_id=?',(campaign_id,)).fetchone()['n']
        rows=db.execute('SELECT kind,COUNT(*) n,COUNT(DISTINCT lead_id) unique_leads FROM events WHERE campaign_id=? GROUP BY kind',(campaign_id,)).fetchall()
        queue=db.execute('SELECT status,COUNT(*) n FROM execution_queue WHERE campaign_id=? GROUP BY status',(campaign_id,)).fetchall()
        return {'queue_counts':{r['status']:r['n'] for r in queue},'execution_mode':'simulated','provider_accepted':0,'provider_delivered':0,'real_engaged':0,'real_converted':0,'campaign':dictrow(c),'mock_jobs':jobs,'event_counts':{r['kind']:r['n'] for r in rows},'unique_leads_by_event':{r['kind']:r['unique_leads'] for r in rows},'live_messages_sent':0}

@app.get('/campaigns/{campaign_id}/audience',dependencies=[Depends(authorize)])
def audience(campaign_id: str):
    with conn() as db:
        c=row_or_404(db,'campaigns',campaign_id)
        flag='email_allowed' if c['channel']=='email' else 'whatsapp_allowed'
        address='email' if c['channel']=='email' else 'phone'
        snapshot=db.execute('SELECT 1 FROM approval_snapshots WHERE campaign_id=?',(campaign_id,)).fetchone()
        if c['status'] in ('approved','completed') and not snapshot:
            raise HTTPException(409,'Legacy campaign has no approved audience snapshot')
        scope=' AND id IN (SELECT lead_id FROM campaign_audience WHERE campaign_id=?)' if snapshot else ''
        params=(c['client_id'],campaign_id) if snapshot else (c['client_id'],)
        qual,qp=eligible_clause(c['client_id'],db)
        scope+=qual;params=(*params,*qp)
        rows=db.execute(f'''SELECT id,source_id,external_id,name,{address} destination FROM leads WHERE client_id=? AND suppressed=0 AND {flag}=1 AND TRIM(COALESCE({address},''))!='' {scope} ORDER BY id LIMIT 1000''',params).fetchall()
        return {'campaign_id':campaign_id,'eligible_leads':[dictrow(r) for r in rows], 'limit':1000}

@app.get('/campaigns/{campaign_id}/audit',dependencies=[Depends(authorize)])
def audit_trail(campaign_id:str):
    with conn() as db:
        row_or_404(db,'campaigns',campaign_id)
        return {'events':[dictrow(r) for r in db.execute('SELECT * FROM audit WHERE campaign_id=? ORDER BY at,id',(campaign_id,)).fetchall()]}


@app.get('/console', response_class=HTMLResponse, include_in_schema=False)
def console():
    return (Path(__file__).parent / 'console.html').read_text(encoding='utf-8')

@app.get('/console-data', dependencies=[Depends(authorize)])
def console_data():
    with conn() as db:
        p=principal.get()
        clients=[dictrow(r) for r in db.execute('SELECT * FROM clients ORDER BY name') if p['role']=='admin' or r['id'] in p['clients']]
        campaigns=[dictrow(r) for r in db.execute('SELECT * FROM campaigns ORDER BY created_at DESC') if p['role']=='admin' or r['client_id'] in p['clients']]
        return {'clients':clients,'campaigns':campaigns,'mode':'MOCK ONLY','live_messages_sent':0}

@app.get('/clients/{client_id}/leads',dependencies=[Depends(authorize)])
def client_leads(client_id:str):
    with conn() as db:
        row_or_404(db,'clients',client_id)
        rows=db.execute('SELECT id,source_id,external_id,name,email,phone,email_allowed,whatsapp_allowed,suppressed FROM leads WHERE client_id=? ORDER BY external_id LIMIT 1000',(client_id,)).fetchall()
        return {'client_id':client_id,'leads':[dictrow(r) for r in rows], 'limit':1000}

from . import security
import sys
security.install(sys.modules[__name__])

from . import qualification
qualification.install(sys.modules[__name__])

from . import execution
execution.install(sys.modules[__name__])

from . import permissions
permissions.install(sys.modules[__name__])
