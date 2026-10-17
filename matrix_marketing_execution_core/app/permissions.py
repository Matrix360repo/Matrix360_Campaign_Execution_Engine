"""Explicit, auditable permission changes. Evidence records are assertions requiring review."""
from fastapi import HTTPException
from pydantic import BaseModel, Field
from .security import require_role,principal

SCHEMA='''CREATE TABLE IF NOT EXISTS permission_history(id TEXT PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),channel TEXT NOT NULL,allowed INTEGER NOT NULL,evidence TEXT NOT NULL,actor TEXT NOT NULL,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS suppression_history(id TEXT PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),suppressed INTEGER NOT NULL,reason TEXT NOT NULL,actor TEXT NOT NULL,at TEXT NOT NULL);'''

class PermissionIn(BaseModel):
    channel:str=Field(pattern=r'^(email|whatsapp)$')
    allowed:bool
    evidence:str=Field(min_length=10,max_length=1500)

class SuppressionIn(BaseModel):
    suppressed:bool=True
    reason:str=Field(min_length=5,max_length=1000)


def install(m):
    @m.app.post('/leads/{lead_id}/permission',dependencies=[m.Depends(m.authorize)])
    def permission(lead_id:str,payload:PermissionIn):
        require_role('admin','approver')
        with m.conn() as db:
            db.execute('BEGIN IMMEDIATE');m.row_or_404(db,'leads',lead_id)
            field='email_allowed' if payload.channel=='email' else 'whatsapp_allowed'
            db.execute(f'UPDATE leads SET {field}=? WHERE id=?',(int(payload.allowed),lead_id))
            db.execute('INSERT INTO permission_history VALUES(?,?,?,?,?,?,?)',(m.ident(),lead_id,payload.channel,int(payload.allowed),payload.evidence,principal.get()['id'],m.now()))
        return {'lead_id':lead_id,'channel':payload.channel,'allowed':payload.allowed,'suppression_unchanged':True,'evidence_independently_verified':False}

    @m.app.post('/leads/{lead_id}/suppression',dependencies=[m.Depends(m.authorize)])
    def suppress(lead_id:str,payload:SuppressionIn):
        if not payload.suppressed:require_role('admin')
        with m.conn() as db:
            db.execute('BEGIN IMMEDIATE');m.row_or_404(db,'leads',lead_id)
            db.execute('UPDATE leads SET suppressed=? WHERE id=?',(int(payload.suppressed),lead_id))
            db.execute('INSERT INTO suppression_history VALUES(?,?,?,?,?,?)',(m.ident(),lead_id,int(payload.suppressed),payload.reason,principal.get()['id'],m.now()))
        return {'lead_id':lead_id,'suppressed':payload.suppressed}
