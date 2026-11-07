import os,sqlite3,hashlib,hmac,secrets,time,datetime,json,uuid
from pathlib import Path
from contextlib import contextmanager
from fastapi import FastAPI,Request,HTTPException,UploadFile,File,Form
from fastapi.responses import FileResponse,JSONResponse,FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from reminders import validate_time,validate_timezone
BASE=Path(__file__).resolve().parent; DB=Path(os.getenv('DATABASE_PATH',str(BASE/'studyflow.db'))); SECRET=os.getenv('SESSION_SECRET','')
if not SECRET: raise RuntimeError('Set SESSION_SECRET environment variable.')
DATA=Path(os.getenv('DATA_DIR',str(DB.parent))); UPLOADS=DATA/'uploads'; UPLOADS.mkdir(parents=True,exist_ok=True)
app=FastAPI(title='StudyFlow'); app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')
@contextmanager
def conn():
 DB.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=30000'); c.execute('PRAGMA journal_mode=WAL')
 try: yield c; c.commit()
 finally:c.close()
def stamp(): return datetime.date.today().isoformat()
def init():
 with conn() as c:
  existing={r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if 'settings' in existing and 'user_id' not in [x['name'] for x in c.execute('PRAGMA table_info(settings)')]:
   c.execute('ALTER TABLE settings RENAME TO legacy_settings')
  c.executescript('''
  CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS habits(id INTEGER PRIMARY KEY,user_id INTEGER,name TEXT NOT NULL,days TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',created TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS habit_checks(habit_id INTEGER NOT NULL,day TEXT NOT NULL,PRIMARY KEY(habit_id,day));
  CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY,user_id INTEGER,title TEXT NOT NULL,subject TEXT NOT NULL DEFAULT '',due TEXT NOT NULL DEFAULT '',done INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY,user_id INTEGER,weekday INTEGER NOT NULL,period INTEGER NOT NULL,subject TEXT NOT NULL,start TEXT NOT NULL DEFAULT '',finish TEXT NOT NULL DEFAULT '',room TEXT NOT NULL DEFAULT '');
  CREATE TABLE IF NOT EXISTS grades(id INTEGER PRIMARY KEY,user_id INTEGER,subject TEXT NOT NULL,grade INTEGER NOT NULL,day TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS settings(user_id INTEGER,key TEXT,value TEXT,PRIMARY KEY(user_id,key));
  CREATE TABLE IF NOT EXISTS bot_links(chat_id TEXT PRIMARY KEY,user_id INTEGER UNIQUE,code TEXT,expires REAL);
  CREATE TABLE IF NOT EXISTS task_images(id INTEGER PRIMARY KEY,user_id INTEGER,task_id INTEGER,path TEXT,original_name TEXT);
  CREATE TABLE IF NOT EXISTS cheatsheets(id INTEGER PRIMARY KEY,user_id INTEGER,title TEXT,subject TEXT,description TEXT,path TEXT,original_name TEXT,created TEXT);
  CREATE TABLE IF NOT EXISTS notification_log(user_id INTEGER,kind TEXT,slot TEXT,PRIMARY KEY(user_id,kind,slot));
  ''')
  for table in ('habits','tasks','lessons','grades'):
   cols=[r['name'] for r in c.execute(f'PRAGMA table_info({table})')]
   if 'user_id' not in cols:c.execute(f'ALTER TABLE {table} ADD COLUMN user_id INTEGER')
init()
def pw_hash(p,s=None):
 s=s or secrets.token_hex(16); return s+'$'+hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),240000).hex()
def pw_ok(p,h):
 try:s,v=h.split('$',1);return hmac.compare_digest(pw_hash(p,s).split('$',1)[1],v)
 except:return False
def sign(v):return hmac.new(SECRET.encode(),v.encode(),hashlib.sha256).hexdigest()
def current(request):
 try:
  uid,exp,sig=request.cookies.get('sf_session','').split('.'); raw=f'{uid}.{exp}'
  if int(exp)<time.time() or not hmac.compare_digest(sig,sign(raw)):return None
  with conn() as c:r=c.execute('SELECT id,username FROM users WHERE id=?',(int(uid),)).fetchone()
  return dict(r) if r else None
 except:return None
@app.middleware('http')
async def guard(request,call_next):
 if request.url.path.startswith('/api/') and request.url.path not in ('/api/register','/api/login','/api/health') and not current(request):return JSONResponse({'error':'Требуется вход'},401)
 return await call_next(request)
def uid(request):
 u=current(request)
 if not u:raise HTTPException(401,'Требуется вход')
 return u['id']
def rows(sql,args=()):
 with conn() as c:return [dict(x) for x in c.execute(sql,args).fetchall()]
def execute(sql,args=()):
 with conn() as c:return c.execute(sql,args).lastrowid
def set_cookie(res,user):
 exp=str(int(time.time())+30*86400); raw=f"{user['id']}.{exp}";res.set_cookie('sf_session',raw+'.'+sign(raw),max_age=30*86400,httponly=True,samesite='lax',secure=os.getenv('COOKIE_SECURE','1')=='1')
@app.get('/')
def home():return FileResponse(BASE/'static'/'index.html')
@app.get('/api/health')
def health():return {'ok':True}
class Auth(BaseModel):username:str=Field(min_length=3,max_length=32,pattern=r'^[a-zA-Z0-9_.-]+$');password:str=Field(min_length=8,max_length=128)
@app.post('/api/register')
def register(d:Auth):
 with conn() as c:
  if c.execute('SELECT 1 FROM users WHERE username=?',(d.username.lower(),)).fetchone():raise HTTPException(409,'Такой логин уже занят')
  cur=c.execute('INSERT INTO users(username,password_hash,created) VALUES(?,?,?)',(d.username.lower(),pw_hash(d.password),stamp())); user={'id':cur.lastrowid,'username':d.username.lower()}
  # Safely migrate legacy single-user data to the first account only.
  if c.execute('SELECT COUNT(*) n FROM users').fetchone()['n']==1:
   for t in ('habits','tasks','lessons','grades'):c.execute(f'UPDATE {t} SET user_id=? WHERE user_id IS NULL',(user['id'],))
   legacy=c.execute('SELECT key,value FROM legacy_settings').fetchall() if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_settings'").fetchone() else []
   for item in legacy:c.execute('INSERT OR REPLACE INTO settings(user_id,key,value) VALUES(?,?,?)',(user['id'],item['key'],item['value']))
   old_chat=next((x['value'] for x in legacy if x['key']=='telegram_chat_id'),None)
   if old_chat:c.execute('INSERT OR IGNORE INTO bot_links(chat_id,user_id) VALUES(?,?)',(old_chat,user['id']))
 res=JSONResponse({'ok':True,'username':user['username']});set_cookie(res,user);return res
@app.post('/api/login')
def login(d:Auth):
 u=rows('SELECT * FROM users WHERE username=?',(d.username.lower(),))
 if not u or not pw_ok(d.password,u[0]['password_hash']):raise HTTPException(401,'Неверный логин или пароль')
 res=JSONResponse({'ok':True,'username':u[0]['username']});set_cookie(res,u[0]);return res
@app.post('/api/logout')
def logout():
 r=JSONResponse({'ok':True});r.delete_cookie('sf_session');return r
@app.get('/api/me')
def me(request:Request):return {'ok':True,**current(request)}
class Habit(BaseModel):name:str=Field(min_length=1,max_length=100);days:list[int]=Field(default_factory=lambda:list(range(7)))
@app.get('/api/habits')
def habits(request:Request):
 u=uid(request); hs=rows('SELECT * FROM habits WHERE user_id=? ORDER BY id DESC',(u,)); checks=rows('SELECT hc.* FROM habit_checks hc JOIN habits h ON h.id=hc.habit_id WHERE h.user_id=?',(u,)); by={}
 for x in checks:by.setdefault(x['habit_id'],[]).append(x['day'])
 for h in hs:h['days']=[int(x) for x in h['days'].split(',') if x];h['checks']=by.get(h['id'],[])
 return hs
@app.post('/api/habits')
def add_habit(d:Habit,request:Request):
 if any(x not in range(7) for x in d.days):raise HTTPException(422,'Некорректные дни')
 return {'id':execute('INSERT INTO habits(user_id,name,days,created) VALUES(?,?,?,?)',(uid(request),d.name.strip(),','.join(map(str,sorted(set(d.days)))),stamp()))}
@app.delete('/api/habits/{i}')
def del_habit(i:int,request:Request):
 u=uid(request);execute('DELETE FROM habit_checks WHERE habit_id IN (SELECT id FROM habits WHERE id=? AND user_id=?)',(i,u));execute('DELETE FROM habits WHERE id=? AND user_id=?',(i,u));return {'ok':True}
class Check(BaseModel):day:datetime.date;done:bool
@app.put('/api/habits/{i}/check')
def check(i:int,d:Check,request:Request):
 u=uid(request)
 if not rows('SELECT id FROM habits WHERE id=? AND user_id=?',(i,u)):raise HTTPException(404,'Привычка не найдена')
 if d.done:execute('INSERT OR IGNORE INTO habit_checks(habit_id,day) VALUES(?,?)',(i,d.day.isoformat()))
 else:execute('DELETE FROM habit_checks WHERE habit_id=? AND day=?',(i,d.day.isoformat()))
 return {'ok':True}
class Task(BaseModel):title:str=Field(min_length=1,max_length=200);subject:str=Field(default='',max_length=100);due:str=''
@app.get('/api/tasks')
def tasks(request:Request):
 u=uid(request);ts=rows('SELECT * FROM tasks WHERE user_id=? ORDER BY done,CASE WHEN due="" THEN 1 ELSE 0 END,due,id DESC',(u,))
 for t in ts:t['images']=rows('SELECT id,original_name FROM task_images WHERE task_id=? AND user_id=?',(t['id'],u))
 return ts
@app.post('/api/tasks')
def add_task(d:Task,request:Request):
 if d.due:datetime.date.fromisoformat(d.due)
 return {'id':execute('INSERT INTO tasks(user_id,title,subject,due,created) VALUES(?,?,?,?,?)',(uid(request),d.title.strip(),d.subject.strip(),d.due,stamp()))}
class Done(BaseModel):done:bool
@app.put('/api/tasks/{i}')
def set_task(i:int,d:Done,request:Request):
 if not rows('SELECT id FROM tasks WHERE id=? AND user_id=?',(i,uid(request))):raise HTTPException(404,'Задание не найдено')
 execute('UPDATE tasks SET done=? WHERE id=? AND user_id=?',(int(d.done),i,uid(request)));return {'ok':True}
@app.delete('/api/tasks/{i}')
def del_task(i:int,request:Request):
 u=uid(request);execute('DELETE FROM task_images WHERE task_id=? AND user_id=?',(i,u));execute('DELETE FROM tasks WHERE id=? AND user_id=?',(i,u));return {'ok':True}
def save_image(file:UploadFile,user:int):
 ext=Path(file.filename or '').suffix.lower(); allowed={'.jpg','.jpeg','.png','.webp'}
 if ext not in allowed:raise HTTPException(415,'Разрешены JPG, PNG и WEBP')
 data=file.file.read(8*1024*1024+1)
 if len(data)>8*1024*1024:raise HTTPException(413,'Максимальный размер изображения — 8 МБ')
 name=f'{user}_{uuid.uuid4().hex}{ext}';(UPLOADS/name).write_bytes(data);return name
@app.post('/api/tasks/{i}/images')
def task_image(i:int,request:Request,file:UploadFile=File(...)):
 u=uid(request)
 if not rows('SELECT id FROM tasks WHERE id=? AND user_id=?',(i,u)):raise HTTPException(404,'Задание не найдено')
 name=save_image(file,u);return {'id':execute('INSERT INTO task_images(user_id,task_id,path,original_name) VALUES(?,?,?,?)',(u,i,name,file.filename or name))}
@app.get('/api/images/{i}')
def get_image(i:int,request:Request):
 r=rows('SELECT path FROM task_images WHERE id=? AND user_id=?',(i,uid(request)))
 if not r:raise HTTPException(404,'Файл не найден')
 return FileResponse(UPLOADS/r[0]['path'])
@app.delete('/api/tasks/images/{i}')
def del_task_image(i:int,request:Request):
 u=uid(request);r=rows('SELECT path FROM task_images WHERE id=? AND user_id=?',(i,u))
 if r:
  (UPLOADS/r[0]['path']).unlink(missing_ok=True);execute('DELETE FROM task_images WHERE id=? AND user_id=?',(i,u))
 return {'ok':True}
class Lesson(BaseModel):weekday:int=Field(ge=0,le=6);period:int=Field(ge=1,le=20);subject:str=Field(min_length=1,max_length=100);start:str='';finish:str='';room:str=''
@app.get('/api/lessons')
def lessons(request:Request):return rows('SELECT * FROM lessons WHERE user_id=? ORDER BY weekday,period,id',(uid(request),))
@app.post('/api/lessons')
def add_lesson(d:Lesson,request:Request):return {'id':execute('INSERT INTO lessons(user_id,weekday,period,subject,start,finish,room) VALUES(?,?,?,?,?,?,?)',(uid(request),d.weekday,d.period,d.subject.strip(),d.start,d.finish,d.room))}
@app.delete('/api/lessons/{i}')
def del_lesson(i:int,request:Request):execute('DELETE FROM lessons WHERE id=? AND user_id=?',(i,uid(request)));return {'ok':True}
class Grade(BaseModel):subject:str=Field(min_length=1,max_length=100);grade:int=Field(ge=1,le=5);day:datetime.date
@app.get('/api/grades')
def grades(request:Request):return rows('SELECT * FROM grades WHERE user_id=? ORDER BY day DESC,id DESC',(uid(request),))
@app.post('/api/grades')
def add_grade(d:Grade,request:Request):return {'id':execute('INSERT INTO grades(user_id,subject,grade,day) VALUES(?,?,?,?)',(uid(request),d.subject.strip(),d.grade,d.day.isoformat()))}
@app.delete('/api/grades/{i}')
def del_grade(i:int,request:Request):execute('DELETE FROM grades WHERE id=? AND user_id=?',(i,uid(request)));return {'ok':True}
@app.get('/api/stats')
def stats(request:Request):
 u=uid(request);hs=rows('SELECT id,days,created FROM habits WHERE user_id=?',(u,));checks={(x['habit_id'],x['day']) for x in rows('SELECT hc.* FROM habit_checks hc JOIN habits h ON h.id=hc.habit_id WHERE h.user_id=?',(u,))};today=datetime.date.today();out=[]
 for n in range(29,-1,-1):
  day=today-datetime.timedelta(days=n);ds=day.isoformat();eligible=[h for h in hs if h['created']<=ds and str(day.weekday()) in h['days'].split(',')];done=sum((h['id'],ds) in checks for h in eligible);out.append({'day':ds,'done':done,'total':len(eligible),'percent':round(done/len(eligible)*100) if eligible else 0})
 return {'history':out,'tasks':rows('SELECT done,COUNT(*) count FROM tasks WHERE user_id=? GROUP BY done',(u,)),'grades':rows('SELECT subject,ROUND(AVG(grade),2) average FROM grades WHERE user_id=? GROUP BY subject ORDER BY subject',(u,))}
class ReminderSettings(BaseModel):timezone:str='Europe/Stockholm';schedule_enabled:bool=True;schedule_time:str='20:00';homework_enabled:bool=True;homework_time:str='18:00';schedule_interval:int=1440;homework_interval:int=1440
DEFAULT={'timezone':'Europe/Stockholm','schedule_enabled':True,'schedule_time':'20:00','homework_enabled':True,'homework_time':'18:00','schedule_interval':1440,'homework_interval':1440}
def config(u):
 r=rows('SELECT key,value FROM settings WHERE user_id=?',(u,));d=DEFAULT.copy()
 for x in r:
  try:d[x['key']]=json.loads(x['value'])
  except:pass
 return d
def save_settings(u,d):
 with conn() as c:
  for k,v in d.items():c.execute('INSERT INTO settings(user_id,key,value) VALUES(?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value',(u,k,json.dumps(v)))
@app.get('/api/reminders')
def reminders_get(request:Request):return config(uid(request))
@app.put('/api/reminders')
def reminders_put(d:ReminderSettings,request:Request):
 v=d.model_dump();validate_timezone(v['timezone']);validate_time(v['schedule_time']);validate_time(v['homework_time'])
 for k in ('schedule_interval','homework_interval'):
  if v[k] not in (60,120,180,240,360,480,720,1440):raise HTTPException(422,'Недопустимый интервал')
 save_settings(uid(request),v);return v
@app.get('/api/bot/link')
def bot_link(request:Request):
 u=uid(request);code=secrets.token_urlsafe(9)
 with conn() as c:
  c.execute('DELETE FROM bot_links WHERE user_id=? AND chat_id IS NULL',(u,))
  c.execute('INSERT INTO bot_links(chat_id,user_id,code,expires) VALUES(NULL,?,?,?)',(u,code,time.time()+600))
 return {'code':code,'expires_seconds':600}
@app.get('/api/bot/status')
def bot_status(request:Request):return {'linked':bool(rows('SELECT 1 FROM bot_links WHERE user_id=? AND chat_id IS NOT NULL',(uid(request),)))}
@app.get('/api/cheatsheets')
def cheatsheets(request:Request):return rows('SELECT id,title,subject,description,original_name,created FROM cheatsheets WHERE user_id=? ORDER BY id DESC',(uid(request),))
@app.post('/api/cheatsheets')
def add_cheatsheet(request:Request,title:str=Form(...),subject:str=Form(''),description:str=Form(''),file:UploadFile=File(...)):
 u=uid(request);name=save_image(file,u);return {'id':execute('INSERT INTO cheatsheets(user_id,title,subject,description,path,original_name,created) VALUES(?,?,?,?,?,?,?)',(u,title[:120],subject[:100],description[:500],name,file.filename or name,stamp()))}
@app.get('/api/cheatsheets/{i}/image')
def cheatsheet_image(i:int,request:Request):
 r=rows('SELECT path FROM cheatsheets WHERE id=? AND user_id=?',(i,uid(request)))
 if not r:raise HTTPException(404,'Шпаргалка не найдена')
 return FileResponse(UPLOADS/r[0]['path'])
@app.delete('/api/cheatsheets/{i}')
def del_cheatsheet(i:int,request:Request):
 u=uid(request);r=rows('SELECT path FROM cheatsheets WHERE id=? AND user_id=?',(i,u))
 if r:(UPLOADS/r[0]['path']).unlink(missing_ok=True);execute('DELETE FROM cheatsheets WHERE id=? AND user_id=?',(i,u))
 return {'ok':True}
