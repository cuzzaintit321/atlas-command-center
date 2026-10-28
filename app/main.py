from __future__ import annotations
import csv, io, json, os, secrets, sqlite3, asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('ATLAS_DB', str(ROOT.parent / 'data' / 'atlas.db')))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
API_KEY = os.getenv('ATLAS_API_KEY', '')
TABLES = {'projects','tasks','incidents','assets','automations','activity'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '', status TEXT DEFAULT 'active', color TEXT DEFAULT '#6d7dff', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '', project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL, status TEXT DEFAULT 'backlog', priority TEXT DEFAULT 'medium', assignee TEXT DEFAULT '', due_date TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS incidents (id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '', severity TEXT DEFAULT 'medium', status TEXT DEFAULT 'open', service TEXT DEFAULT '', resolution TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assets (id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT DEFAULT 'server', host TEXT DEFAULT '', environment TEXT DEFAULT 'production', status TEXT DEFAULT 'healthy', notes TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS automations (id INTEGER PRIMARY KEY, name TEXT NOT NULL, trigger_event TEXT NOT NULL, condition_field TEXT DEFAULT '', condition_value TEXT DEFAULT '', action TEXT NOT NULL, action_value TEXT DEFAULT '', enabled INTEGER DEFAULT 1, executions INTEGER DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id INTEGER NOT NULL, event TEXT NOT NULL, details TEXT DEFAULT '{}', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity(created_at);
'''
FIELDS = {
 'projects': {'name','description','status','color'},
 'tasks': {'title','description','project_id','status','priority','assignee','due_date'},
 'incidents': {'title','description','severity','status','service','resolution'},
 'assets': {'name','kind','host','environment','status','notes'},
 'automations': {'name','trigger_event','condition_field','condition_value','action','action_value','enabled'}
}
REQUIRED = {'projects':'name','tasks':'title','incidents':'title','assets':'name','automations':'name'}
DEFAULTS = {
 'projects': {'description':'','status':'active','color':'#6d7dff'},
 'tasks': {'description':'','status':'backlog','priority':'medium','assignee':'','due_date':''},
 'incidents': {'description':'','severity':'medium','status':'open','service':'','resolution':''},
 'assets': {'kind':'server','host':'','environment':'production','status':'healthy','notes':''},
 'automations': {'condition_field':'','condition_value':'','action_value':'','enabled':1}
}
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def connect():
 c=sqlite3.connect(DB_PATH, timeout=15)
 c.row_factory=sqlite3.Row
 c.execute('PRAGMA foreign_keys=ON')
 c.execute('PRAGMA journal_mode=WAL')
 return c

def initialize():
 with connect() as c:
  c.executescript(SCHEMA)
  if not c.execute('SELECT COUNT(*) FROM projects').fetchone()[0]:
   stamp=now()
   c.execute('INSERT INTO projects(name,description,status,color,created_at,updated_at) VALUES(?,?,?,?,?,?)',('Infrastructure Modernization','Servers, automation and deployment reliability','active','#7c8cff',stamp,stamp))
   c.execute('INSERT INTO projects(name,description,status,color,created_at,updated_at) VALUES(?,?,?,?,?,?)',('Product Launch','Plan, build, test and ship new products','active','#3cd6b7',stamp,stamp))
   c.executemany('INSERT INTO tasks(title,description,project_id,status,priority,assignee,due_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',[
    ('Harden reverse proxy','Review TLS and security headers',1,'in_progress','high','Operations','',stamp,stamp),
    ('Deploy monitoring agents','Track CPU, RAM and disk status',1,'review','medium','Engineering','',stamp,stamp),
    ('Write release checklist','Document rollbacks and verification',2,'todo','medium','Product','',stamp,stamp),
    ('Validate backups','Perform restore rehearsal',1,'done','critical','Operations','',stamp,stamp),
    ('Plan launch milestones','Coordinate engineering and content',2,'backlog','low','Product','',stamp,stamp)])
   c.executemany('INSERT INTO assets(name,kind,host,environment,status,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',[
    ('Primary Linux Server','server','yeet','production','healthy','Ubuntu host',stamp,stamp),
    ('Development Workstation','workstation','xps-8940','development','healthy','Fedora desktop',stamp,stamp),
    ('Public Reverse Proxy','service','caddy','production','warning','Verify certificate renewals',stamp,stamp)])
   c.execute('INSERT INTO incidents(title,description,severity,status,service,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',('Reverse proxy latency spike','Intermittent slow requests during peak traffic','medium','investigating','Caddy',stamp,stamp))
   c.execute('INSERT INTO automations(name,trigger_event,condition_field,condition_value,action,action_value,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',('Escalate critical incidents','incidents.created','severity','critical','set_priority','',stamp,stamp))

class Payload(BaseModel):
 data: dict[str,Any] = Field(default_factory=dict)

clients: set[WebSocket] = set()
async def broadcast(payload):
 dead=[]
 for ws in list(clients):
  try: await ws.send_json(payload)
  except Exception: dead.append(ws)
 for ws in dead: clients.discard(ws)

def require_table(table):
 if table not in FIELDS: raise HTTPException(404,'Unknown collection')

def validate(table, data, creation=False):
 require_table(table)
 invalid=set(data)-FIELDS[table]
 if invalid: raise HTTPException(422,f'Unknown fields: {", ".join(sorted(invalid))}')
 if creation and not str(data.get(REQUIRED[table],'')).strip(): raise HTTPException(422,f'{REQUIRED[table]} is required')
 if 'status' in data:
  statuses={'projects':{'active','paused','completed','archived'},'tasks':{'backlog','todo','in_progress','review','done'},'incidents':{'open','investigating','mitigated','resolved'},'assets':{'healthy','warning','critical','offline'}}
  if table in statuses and data['status'] not in statuses[table]: raise HTTPException(422,'Invalid status')
 if table=='tasks' and 'priority' in data and data['priority'] not in {'low','medium','high','critical'}: raise HTTPException(422,'Invalid priority')
 if table=='incidents' and 'severity' in data and data['severity'] not in {'low','medium','high','critical'}: raise HTTPException(422,'Invalid severity')
 if table=='automations':
  if 'trigger_event' in data and data['trigger_event'] not in {'tasks.created','tasks.updated','incidents.created','incidents.updated','assets.created','assets.updated'}: raise HTTPException(422,'Invalid trigger')
  if 'action' in data and data['action'] not in {'log','set_priority','set_status'}: raise HTTPException(422,'Invalid action')
 return data

def log(c,entity,entity_id,event,details=None):
 c.execute('INSERT INTO activity(entity,entity_id,event,details,created_at) VALUES(?,?,?,?,?)',(entity,entity_id,event,json.dumps(details or {}),now()))

def automations_run(c,entity,event,row,depth=0):
 if depth>0: return
 triggered=[]
 for rule in c.execute('SELECT * FROM automations WHERE enabled=1 AND trigger_event=?',(f'{entity}.{event}',)).fetchall():
  r=dict(rule)
  if r['condition_field'] and str(row.get(r['condition_field'],''))!=r['condition_value']: continue
  effect={'rule':r['name'],'action':r['action']}
  if r['action']=='set_priority' and entity=='tasks':
   value=r['action_value'] if r['action_value'] in {'low','medium','high','critical'} else 'critical'
   c.execute('UPDATE tasks SET priority=?,updated_at=? WHERE id=?',(value,now(),row['id']))
   effect['value']=value
  elif r['action']=='set_status':
   allowed={'tasks':{'backlog','todo','in_progress','review','done'},'incidents':{'open','investigating','mitigated','resolved'},'assets':{'healthy','warning','critical','offline'}}
   if r['action_value'] in allowed.get(entity,set()):
    c.execute(f'UPDATE {entity} SET status=?,updated_at=? WHERE id=?',(r['action_value'],now(),row['id']))
    effect['value']=r['action_value']
  c.execute('UPDATE automations SET executions=executions+1 WHERE id=?',(r['id'],))
  log(c,entity,row['id'],'automation',effect)
  triggered.append(effect)
 return triggered

@asynccontextmanager
async def lifespan(app):
 initialize()
 yield
app=FastAPI(title='Atlas Command Center',version='1.0.0',lifespan=lifespan)
@app.middleware('http')
async def auth(request:Request,call_next):
 if API_KEY and request.url.path.startswith('/api/') and request.headers.get('x-api-key')!=API_KEY:
  from fastapi.responses import JSONResponse
  return JSONResponse({'detail':'Unauthorized'},status_code=401)
 return await call_next(request)

@app.get('/api/health')
def health(): return {'status':'ok','version':'1.0.0','time':now()}
@app.get('/api/{table}')
def listing(table:str, q:str='',limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0)):
 if table not in TABLES: raise HTTPException(404,'Unknown collection')
 with connect() as c:
  if table=='activity':
   rows=c.execute('SELECT * FROM activity ORDER BY id DESC LIMIT ? OFFSET ?',(limit,offset)).fetchall()
   total=c.execute('SELECT COUNT(*) FROM activity').fetchone()[0]
  else:
   key=REQUIRED[table]
   where=f' WHERE {key} LIKE ?' if q else ''
   params=('%'+q+'%',) if q else ()
   rows=c.execute(f'SELECT * FROM {table}{where} ORDER BY id DESC LIMIT ? OFFSET ?',(*params,limit,offset)).fetchall()
   total=c.execute(f'SELECT COUNT(*) FROM {table}{where}',params).fetchone()[0]
  return {'items':[dict(x) for x in rows],'total':total}
@app.post('/api/{table}',status_code=201)
async def create(table:str,payload:Payload):
 data=validate(table,payload.data,True)
 data={**DEFAULTS[table],**data}
 stamp=now(); data.update(created_at=stamp,updated_at=stamp)
 fields=list(data)
 with connect() as c:
  try:
   cur=c.execute(f'INSERT INTO {table} ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})',[data[k] for k in fields])
  except sqlite3.IntegrityError as e: raise HTTPException(422,str(e))
  row=dict(c.execute(f'SELECT * FROM {table} WHERE id=?',(cur.lastrowid,)).fetchone())
  log(c,table,row['id'],'created',row)
  automations_run(c,table,'created',row)
  row=dict(c.execute(f'SELECT * FROM {table} WHERE id=?',(row['id'],)).fetchone())
 await broadcast({'type':'change','entity':table,'event':'created','id':row['id']})
 return row
@app.patch('/api/{table}/{record_id}')
async def update(table:str,record_id:int,payload:Payload):
 data=validate(table,payload.data)
 if not data: raise HTTPException(422,'No fields provided')
 data['updated_at']=now()
 with connect() as c:
  if not c.execute(f'SELECT id FROM {table} WHERE id=?',(record_id,)).fetchone(): raise HTTPException(404,'Not found')
  try:
   c.execute(f'UPDATE {table} SET '+','.join(f'{k}=?' for k in data)+' WHERE id=?',(*data.values(),record_id))
  except sqlite3.IntegrityError as e: raise HTTPException(422,str(e))
  row=dict(c.execute(f'SELECT * FROM {table} WHERE id=?',(record_id,)).fetchone())
  log(c,table,record_id,'updated',data)
  automations_run(c,table,'updated',row)
  row=dict(c.execute(f'SELECT * FROM {table} WHERE id=?',(record_id,)).fetchone())
 await broadcast({'type':'change','entity':table,'event':'updated','id':record_id})
 return row
@app.delete('/api/{table}/{record_id}')
async def delete(table:str,record_id:int):
 require_table(table)
 with connect() as c:
  if not c.execute(f'SELECT id FROM {table} WHERE id=?',(record_id,)).fetchone(): raise HTTPException(404,'Not found')
  c.execute(f'DELETE FROM {table} WHERE id=?',(record_id,))
  log(c,table,record_id,'deleted')
 await broadcast({'type':'change','entity':table,'event':'deleted','id':record_id})
 return {'deleted':True}
@app.get('/api/meta/overview')
def overview():
 with connect() as c:
  count=lambda t:c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
  grouped=lambda t,k:[dict(x) for x in c.execute(f'SELECT {k} AS label,COUNT(*) AS count FROM {t} GROUP BY {k} ORDER BY count DESC').fetchall()]
  return {'counts':{t:count(t) for t in FIELDS},'tasks_by_status':grouped('tasks','status'),'tasks_by_priority':grouped('tasks','priority'),'incidents_by_severity':grouped('incidents','severity'),'assets_by_status':grouped('assets','status'),'recent_activity':[dict(x) for x in c.execute('SELECT * FROM activity ORDER BY id DESC LIMIT 12').fetchall()],'completed_tasks':c.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0],'open_incidents':c.execute("SELECT COUNT(*) FROM incidents WHERE status!='resolved'").fetchone()[0]}
@app.get('/api/meta/export')
def export_data(format:str='json'):
 with connect() as c:
  data={t:[dict(x) for x in c.execute(f'SELECT * FROM {t}').fetchall()] for t in TABLES}
 if format=='json': return data
 if format=='csv':
  import zipfile
  f=io.BytesIO()
  with zipfile.ZipFile(f,'w',zipfile.ZIP_DEFLATED) as z:
   for table,rows in data.items():
    out=io.StringIO(); writer=csv.DictWriter(out,fieldnames=(list(rows[0]) if rows else ['id']))
    writer.writeheader();writer.writerows(rows);z.writestr(f'{table}.csv',out.getvalue())
  f.seek(0)
  return StreamingResponse(f,media_type='application/zip',headers={'Content-Disposition':'attachment; filename=atlas-export.zip'})
 raise HTTPException(422,'Unsupported format')
@app.websocket('/ws')
async def websocket(ws:WebSocket):
 if API_KEY and ws.query_params.get('key')!=API_KEY:
  await ws.close(code=1008);return
 await ws.accept();clients.add(ws)
 try:
  while True:
   data=await ws.receive_text()
   if data=='ping': await ws.send_text('pong')
 except WebSocketDisconnect: pass
 finally: clients.discard(ws)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.get('/')
def index(): return FileResponse(ROOT/'static'/'index.html')
