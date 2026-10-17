"""Independent user authentication and tenant authorization. No external identity dependency."""
import hashlib
import hmac
import secrets
import time
from contextvars import ContextVar
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

principal = ContextVar('principal', default=None)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS user_clients(user_id TEXT NOT NULL REFERENCES users(id), client_id TEXT NOT NULL REFERENCES clients(id), PRIMARY KEY(user_id,client_id));
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(bucket TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS security_audit(id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, at TEXT NOT NULL, request_id TEXT NOT NULL);
'''

class UserIn(BaseModel):
    username: str = Field(pattern=r'^[a-zA-Z0-9_.@-]{3,120}$')
    password: str = Field(min_length=14, max_length=256)
    role: str = Field(pattern=r'^(admin|operator|approver|viewer)$')
    client_ids: list[str] = Field(default_factory=list, max_length=100)

class LoginIn(BaseModel):
    username: str = Field(min_length=1,max_length=120)
    password: str = Field(min_length=1,max_length=256)

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def password_hash(value, salt=None):
    salt = salt or secrets.token_hex(16)
    result = hashlib.pbkdf2_hmac('sha256', value.encode(), salt.encode(), 600000).hex()
    return salt + ':' + result

def verify(value, stored):
    salt = stored.split(':',1)[0]
    return hmac.compare_digest(password_hash(value,salt),stored)

def require_client(client_id):
    p=principal.get()
    if p and p['role']!='admin' and client_id not in p['clients']:
        raise HTTPException(404,'Client resource not found')

def require_role(*roles):
    p=principal.get()
    if not p or p['role'] not in roles:
        raise HTTPException(403,'Your role cannot perform this action')

def install(m):
    @m.app.middleware('http')
    async def request_context(request:Request, call_next):
        token=principal.set(None)
        request.state.request_id=secrets.token_hex(12)
        try:
            response=await call_next(request)
            if request.method not in ('GET','HEAD','OPTIONS'):
                try:
                    with m.conn() as db:
                        db.execute('INSERT INTO security_audit VALUES(?,?,?,?,?)',(m.ident(),getattr(request.state,'actor','unauthenticated'),request.method+' '+request.url.path+' '+str(response.status_code),m.now(),request.state.request_id))
                except Exception:
                    import logging
                    logging.getLogger(__name__).error('Request audit persistence failed; request_id=%s',request.state.request_id)
            response.headers['X-Request-ID']=request.state.request_id
            response.headers['X-Content-Type-Options']='nosniff'
            response.headers['Referrer-Policy']='same-origin'
            response.headers['Cache-Control']='no-store'
            if m.APP_ENV!='development': response.headers['Strict-Transport-Security']='max-age=31536000'
            return response
        finally: principal.reset(token)

    def authenticate(request:Request):
        key=request.headers.get('x-admin-key','')
        if key and m.ADMIN_KEY and hmac.compare_digest(key,m.ADMIN_KEY):
            with m.conn() as db:
                count=db.execute('SELECT COUNT(*) n FROM users').fetchone()['n']
            # The shared key can bootstrap the first user in staging, never operate campaigns.
            if m.APP_ENV=='development' or (count==0 and request.url.path=='/auth/users'):
                p={'id':'bootstrap','role':'admin','clients':[]}
            else: raise HTTPException(401,'Use individual sign-in; shared-key operations are disabled')
        else:
            bearer=request.headers.get('authorization','')
            raw=bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get('matrix_session','')
            if not raw: raise HTTPException(401,'Sign in to continue')
            with m.conn() as db:
                u=db.execute('SELECT u.id,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1',(digest(raw),time.time())).fetchone()
                if not u: raise HTTPException(401,'Session expired or invalid')
                clients=[r['client_id'] for r in db.execute('SELECT client_id FROM user_clients WHERE user_id=?',(u['id'],)).fetchall()]
                p={**dict(u),'clients':clients}
            if not bearer and request.method not in ('GET','HEAD','OPTIONS'):
                origin=request.headers.get('origin')
                if not origin or origin.rstrip('/')!=str(request.base_url).rstrip('/'):
                    raise HTTPException(403,'Same-origin request required')
        if request.method not in ('GET','HEAD','OPTIONS'):
            if p['role']=='viewer' and request.url.path not in ('/auth/logout','/auth/password'): raise HTTPException(403,'Read-only access')
            if request.url.path.endswith(('/approve','/enqueue')) and p['role'] not in ('admin','approver'):
                raise HTTPException(403,'Campaign approver access required')
        principal.set(p)
        request.state.actor=p['id']
        # Global and client-specific reads are filtered at query/resource boundaries.
        return p

    m.authorize_impl=authenticate

    @m.app.post('/auth/users',dependencies=[m.Depends(m.authorize)])
    def create_user(payload:UserIn,request:Request):
        require_role('admin')
        with m.conn() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM users').fetchone() and payload.role!='admin':
                raise HTTPException(422,'The first account must be an administrator')
            for cid in payload.client_ids: m.row_or_404(db,'clients',cid)
            if db.execute('SELECT 1 FROM users WHERE username=?',(payload.username.lower(),)).fetchone():
                raise HTTPException(409,'Username already exists')
            uid=m.ident()
            db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,payload.username.lower(),password_hash(payload.password),payload.role,1))
            for cid in payload.client_ids: db.execute('INSERT INTO user_clients VALUES(?,?)',(uid,cid))
            db.execute('INSERT INTO security_audit VALUES(?,?,?,?,?)',(m.ident(),principal.get()['id'],'user_created',m.now(),request.state.request_id))
        return {'id':uid,'username':payload.username.lower(),'role':payload.role,'client_ids':payload.client_ids}

    @m.app.post('/auth/login')
    def login(payload:LoginIn,request:Request):
        origin=request.headers.get('origin')
        if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
            raise HTTPException(403,'Same-origin login required')
        bucket=digest((request.client.host if request.client else 'unknown')+':'+payload.username.lower())
        with m.conn() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM login_attempts WHERE at<?',(time.time()-900,))
            attempts=db.execute('SELECT COUNT(*) n FROM login_attempts WHERE bucket=?',(bucket,)).fetchone()['n']
            if attempts>=8: raise HTTPException(429,'Too many attempts; wait 15 minutes')
            db.execute('INSERT INTO login_attempts VALUES(?,?)',(bucket,time.time()))
            user=db.execute('SELECT * FROM users WHERE username=? AND active=1',(payload.username.lower(),)).fetchone()
            valid=verify(payload.password,user['password_hash'] if user else password_hash('not-a-valid-password'))
            if user and valid:
                raw=secrets.token_urlsafe(40)
                db.execute('INSERT INTO sessions VALUES(?,?,?)',(digest(raw),user['id'],time.time()+28800))
                db.execute('DELETE FROM sessions WHERE expires_at<?',(time.time(),))
                db.execute('INSERT INTO security_audit VALUES(?,?,?,?,?)',(m.ident(),user['id'],'login',m.now(),request.state.request_id))
            else: raw=None
        # Raise outside transaction so failed attempts are retained.
        if not raw: raise HTTPException(401,'Invalid username or password')
        from fastapi.responses import JSONResponse
        response=JSONResponse({'authenticated':True,'role':user['role'],'expires_in':28800})
        response.set_cookie('matrix_session',raw,httponly=True,secure=m.APP_ENV!='development',samesite='strict',max_age=28800,path='/')
        return response

    @m.app.get('/auth/me',dependencies=[m.Depends(m.authorize)])
    def me(): return principal.get()

    @m.app.post('/auth/logout',dependencies=[m.Depends(m.authorize)])
    def logout(request:Request):
        raw=request.cookies.get('matrix_session','')
        bearer=request.headers.get('authorization','')
        if bearer.startswith('Bearer '): raw=bearer[7:]
        with m.conn() as db: db.execute('DELETE FROM sessions WHERE token_hash=?',(digest(raw),))
        from fastapi.responses import JSONResponse
        response=JSONResponse({'logged_out':True});response.delete_cookie('matrix_session',path='/');return response

    class PasswordIn(BaseModel):
        current_password:str = Field(min_length=1,max_length=256)
        new_password:str = Field(min_length=14,max_length=256)

    @m.app.post('/auth/password',dependencies=[m.Depends(m.authorize)])
    def change_password(payload:PasswordIn):
        uid=principal.get()['id']
        with m.conn() as db:
            user=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if not user or not verify(payload.current_password,user['password_hash']):raise HTTPException(401,'Current password is incorrect')
            db.execute('UPDATE users SET password_hash=? WHERE id=?',(password_hash(payload.new_password),uid))
            db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        return {'changed':True,'sign_in_required':True}

    class AccessIn(BaseModel):
        role:str = Field(pattern=r'^(admin|operator|approver|viewer)$')
        active:bool
        client_ids:list[str] = Field(default_factory=list,max_length=100)

    @m.app.put('/auth/users/{user_id}/access',dependencies=[m.Depends(m.authorize)])
    def change_access(user_id:str,payload:AccessIn):
        require_role('admin')
        with m.conn() as db:
            db.execute('BEGIN IMMEDIATE')
            user=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
            if not user:raise HTTPException(404,'User not found')
            if user['role']=='admin' and (not payload.active or payload.role!='admin'):
                if db.execute("SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1").fetchone()['n']<=1:raise HTTPException(409,'Cannot remove the last active administrator')
            for cid in payload.client_ids:m.row_or_404(db,'clients',cid)
            db.execute('UPDATE users SET role=?,active=? WHERE id=?',(payload.role,int(payload.active),user_id))
            db.execute('DELETE FROM user_clients WHERE user_id=?',(user_id,))
            for cid in payload.client_ids:db.execute('INSERT INTO user_clients VALUES(?,?)',(user_id,cid))
            db.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
        return {'updated':True,'sessions_revoked':True}
