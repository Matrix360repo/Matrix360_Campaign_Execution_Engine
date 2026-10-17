"""Transparent, versioned business-fit rules. Scores are priorities, never consent evidence."""
import json
from fastapi import HTTPException
from pydantic import BaseModel, Field
from .security import principal

PROFILES = {
 'lakhotia': {'name':'Lakhotia Industrial Complex','version':'1','uses':['manufacturing','warehousing','logistics','distribution'],'locations':['west bengal','howrah','kolkata'],'minimum_budget_lakh':None},
 'shree-heights': {'name':'Shree Heights','version':'1','uses':['apartment','residential'],'locations':['chandannagar','chandannagore','chandan nagar'],'minimum_budget_lakh':70},
 'vrindavan-greens': {'name':'Vrindavan Greens','version':'1','uses':['bungalow','residential'],'locations':['madanpur','kalyani'],'minimum_budget_lakh':50}
}
SCHEMA='''
CREATE TABLE IF NOT EXISTS client_profiles(client_id TEXT PRIMARY KEY REFERENCES clients(id), profile_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lead_facts(lead_id TEXT PRIMARY KEY REFERENCES leads(id), facts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS qualifications(lead_id TEXT PRIMARY KEY REFERENCES leads(id), profile_id TEXT NOT NULL, profile_version TEXT NOT NULL, score INTEGER NOT NULL, decision TEXT NOT NULL, reasons TEXT NOT NULL, reviewed_by TEXT, at TEXT NOT NULL);
'''

class ProfileIn(BaseModel):
    profile_id:str

class FactsIn(BaseModel):
    use: str = Field(default='',max_length=120)
    location: str = Field(default='',max_length=120)
    budget_lakh: float | None = Field(default=None,ge=0,le=100000000)
    decision_maker: bool = False
    land_requirement_bigha: float | None = Field(default=None,ge=0,le=100000)
    evidence: str = Field(min_length=3,max_length=1000)

class ReviewIn(BaseModel):
    decision:str = Field(pattern=r'^(qualified|rejected|review)$')
    reason:str = Field(min_length=5,max_length=1000)


def score(profile_id,facts):
    p=PROFILES[profile_id];points=0;reasons=[]
    use=facts.get('use','').strip().lower();location=facts.get('location','').strip().lower()
    if use in p['uses']:points+=50;reasons.append('Stated property use matches this project (+50)')
    else:reasons.append('Property use is missing or does not match; human review required')
    if location in p['locations']:points+=20;reasons.append('Stated location matches project market (+20)')
    else:reasons.append('Location connection is not established')
    if profile_id=='lakhotia':
        if facts.get('decision_maker'):points+=20;reasons.append('Decision-making role explicitly recorded (+20)')
        if (facts.get('land_requirement_bigha') or 0)>0:points+=10;reasons.append('Land requirement explicitly recorded (+10)')
    elif facts.get('budget_lakh') is not None:
        if facts['budget_lakh']>=p['minimum_budget_lakh']:points+=30;reasons.append('Stated budget meets published starting price (+30)')
        else:reasons.append('Stated budget is below published starting price')
    else:reasons.append('Budget is unknown; do not infer affordability')
    return points,reasons


def eligible_clause(client_id,db):
    profile=db.execute('SELECT profile_id FROM client_profiles WHERE client_id=?',(client_id,)).fetchone()
    if not profile:return '',()
    return " AND id IN (SELECT lead_id FROM qualifications WHERE decision='qualified' AND profile_id=?)",(profile['profile_id'],)


def install(m):
    @m.app.get('/qualification/profiles',dependencies=[m.Depends(m.authorize)])
    def profiles():
        return {'profiles':PROFILES,'policy':'Scores prioritize business fit. An authorized reviewer must explicitly qualify a lead. Permission is checked separately.'}

    @m.app.put('/clients/{client_id}/profile',dependencies=[m.Depends(m.authorize)])
    def set_profile(client_id:str,payload:ProfileIn):
        from .security import require_role
        require_role('admin','approver')
        if payload.profile_id not in PROFILES:raise HTTPException(422,'Unknown qualification profile')
        with m.conn() as db:
            m.row_or_404(db,'clients',client_id)
            if db.execute("SELECT 1 FROM campaigns WHERE client_id=? AND status!='draft'",(client_id,)).fetchone():
                raise HTTPException(409,'Cannot change profile after campaign validation')
            db.execute('INSERT INTO client_profiles VALUES(?,?) ON CONFLICT(client_id) DO UPDATE SET profile_id=excluded.profile_id',(client_id,payload.profile_id))
            db.execute('DELETE FROM qualifications WHERE lead_id IN (SELECT id FROM leads WHERE client_id=?)',(client_id,))
        return {'client_id':client_id,'profile_id':payload.profile_id,'version':'1'}

    @m.app.put('/leads/{lead_id}/facts',dependencies=[m.Depends(m.authorize)])
    def facts(lead_id:str,payload:FactsIn):
        with m.conn() as db:
            m.row_or_404(db,'leads',lead_id)
            db.execute('INSERT INTO lead_facts VALUES(?,?) ON CONFLICT(lead_id) DO UPDATE SET facts=excluded.facts',(lead_id,json.dumps(payload.model_dump())))
            db.execute('DELETE FROM qualifications WHERE lead_id=?',(lead_id,))
        return {'saved':True,'qualification_invalidated':True}

    @m.app.post('/clients/{client_id}/qualify',dependencies=[m.Depends(m.authorize)])
    def qualify(client_id:str):
        with m.conn() as db:
            m.row_or_404(db,'clients',client_id)
            profile=db.execute('SELECT profile_id FROM client_profiles WHERE client_id=?',(client_id,)).fetchone()
            if not profile:raise HTTPException(422,'Select a qualification profile first')
            output=[]
            for lead in db.execute('SELECT id FROM leads WHERE client_id=?',(client_id,)).fetchall():
                f=db.execute('SELECT facts FROM lead_facts WHERE lead_id=?',(lead['id'],)).fetchone()
                points,reasons=score(profile['profile_id'],json.loads(f['facts']) if f else {})
                # Recalculation never promotes to qualified or overwrites a human decision.
                db.execute('INSERT INTO qualifications VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(lead_id) DO NOTHING',(lead['id'],profile['profile_id'],'1',points,'review',json.dumps(reasons),None,m.now()))
                output.append(dict(db.execute('SELECT * FROM qualifications WHERE lead_id=?',(lead['id'],)).fetchone()))
        return {'client_id':client_id,'results':output,'outreach_authorized':False}

    @m.app.post('/leads/{lead_id}/review',dependencies=[m.Depends(m.authorize)])
    def review(lead_id:str,payload:ReviewIn):
        from .security import require_role
        require_role('admin','approver')
        with m.conn() as db:
            lead=m.row_or_404(db,'leads',lead_id)
            q=db.execute('SELECT * FROM qualifications WHERE lead_id=?',(lead_id,)).fetchone()
            if not q:raise HTTPException(409,'Score this lead before review')
            reasons=json.loads(q['reasons']);reasons.append('Human review: '+payload.reason)
            db.execute('UPDATE qualifications SET decision=?,reasons=?,reviewed_by=?,at=? WHERE lead_id=?',(payload.decision,json.dumps(reasons),principal.get()['id'],m.now(),lead_id))
            for c in db.execute('SELECT id FROM campaigns WHERE client_id=?',(lead['client_id'],)).fetchall():
                m.audit(db,c['id'],'qualification_review',json.dumps({'lead_id':lead_id,'decision':payload.decision}))
        return {'lead_id':lead_id,'decision':payload.decision,'outreach_authorized':False}
