import os, sqlite3, hashlib, hmac, secrets, time, datetime, json
from pathlib import Path
from contextlib import contextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from reminders import get_config,validate_time,validate_timezone

BASE=Path(__file__).resolve().parent
DB=Path(os.getenv('DATABASE_PATH',str(BASE/'studyflow.db')))
PASSWORD=os.getenv('APP_PASSWORD','')
SECRET=os.getenv('SESSION_SECRET','')
if not PASSWORD or not SECRET:
    raise RuntimeError('Set APP_PASSWORD and SESSION_SECRET environment variables (see README).')
app=FastAPI(title='StudyFlow')
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')

@contextmanager
def conn():
    DB.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB,timeout=15)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    try:
        yield c
        c.commit()
    finally:c.close()

def init():
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS habits(id INTEGER PRIMARY KEY,name TEXT NOT NULL,days TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS habit_checks(habit_id INTEGER NOT NULL,day TEXT NOT NULL,PRIMARY KEY(habit_id,day));
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY,title TEXT NOT NULL,subject TEXT NOT NULL DEFAULT '',due TEXT NOT NULL DEFAULT '',done INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY,weekday INTEGER NOT NULL,period INTEGER NOT NULL,subject TEXT NOT NULL,start TEXT NOT NULL DEFAULT '',finish TEXT NOT NULL DEFAULT '',room TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS grades(id INTEGER PRIMARY KEY,subject TEXT NOT NULL,grade INTEGER NOT NULL,day TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
init()

def stamp():return datetime.date.today().isoformat()
def rows(sql,args=()):
    with conn() as c:return [dict(x) for x in c.execute(sql,args).fetchall()]
def execute(sql,args=()):
    with conn() as c:
        cur=c.execute(sql,args)
        return cur.lastrowid

def signature(value):return hmac.new(SECRET.encode(),value.encode(),hashlib.sha256).hexdigest()
def authorized(request):
    cookie=request.cookies.get('sf_session','')
    try:
        exp,sig=cookie.split('.')
        return int(exp)>time.time() and hmac.compare_digest(sig,signature(exp))
    except (ValueError,TypeError):return False

@app.middleware('http')
async def guard(request:Request,call_next):
    path=request.url.path
    if path.startswith('/api/') and path not in ('/api/login','/api/health') and not authorized(request):
        return JSONResponse({'error':'Требуется вход'},status_code=401)
    return await call_next(request)

@app.get('/')
def home():return FileResponse(BASE/'static'/'index.html')
@app.get('/api/health')
def health():return {'ok':True}
class Login(BaseModel):password:str
@app.post('/api/login')
def login(data:Login):
    if not hmac.compare_digest(data.password,PASSWORD):raise HTTPException(401,'Неверный пароль')
    exp=str(int(time.time())+30*86400)
    res=JSONResponse({'ok':True})
    res.set_cookie('sf_session',exp+'.'+signature(exp),max_age=30*86400,httponly=True,samesite='lax',secure=os.getenv('COOKIE_SECURE','0')=='1')
    return res
@app.post('/api/logout')
def logout():
    r=JSONResponse({'ok':True});r.delete_cookie('sf_session');return r
@app.get('/api/me')
def me():return {'ok':True}

class Habit(BaseModel):name:str=Field(min_length=1,max_length=100);days:list[int]=Field(default_factory=lambda:list(range(7)))
@app.get('/api/habits')
def habits():
    hs=rows('SELECT * FROM habits ORDER BY id DESC')
    checks=rows('SELECT * FROM habit_checks')
    by={}
    for x in checks:by.setdefault(x['habit_id'],[]).append(x['day'])
    for h in hs:h['days']=[int(d) for d in h['days'].split(',') if d!=''];h['checks']=by.get(h['id'],[])
    return hs
@app.post('/api/habits')
def add_habit(h:Habit):
    if any(d not in range(7) for d in h.days):raise HTTPException(422,'Некорректные дни')
    return {'id':execute('INSERT INTO habits(name,days,created) VALUES(?,?,?)',(h.name.strip(),','.join(map(str,sorted(set(h.days)))),stamp()))}
@app.delete('/api/habits/{id}')
def del_habit(id:int):
    execute('DELETE FROM habit_checks WHERE habit_id=?',(id,));execute('DELETE FROM habits WHERE id=?',(id,));return {'ok':True}
class Check(BaseModel):day:datetime.date;done:bool
@app.put('/api/habits/{id}/check')
def check(id:int,v:Check):
    if not rows('SELECT id FROM habits WHERE id=?',(id,)):raise HTTPException(404,'Привычка не найдена')
    if v.done:execute('INSERT OR IGNORE INTO habit_checks(habit_id,day) VALUES(?,?)',(id,v.day.isoformat()))
    else:execute('DELETE FROM habit_checks WHERE habit_id=? AND day=?',(id,v.day.isoformat()))
    return {'ok':True}

class Task(BaseModel):title:str=Field(min_length=1,max_length=200);subject:str=Field(default='',max_length=100);due:str=''
@app.get('/api/tasks')
def tasks():return rows('SELECT * FROM tasks ORDER BY done ASC,CASE WHEN due="" THEN 1 ELSE 0 END,due ASC,id DESC')
@app.post('/api/tasks')
def add_task(t:Task):
    if t.due:datetime.date.fromisoformat(t.due)
    return {'id':execute('INSERT INTO tasks(title,subject,due,created) VALUES(?,?,?,?)',(t.title.strip(),t.subject.strip(),t.due,stamp()))}
class Done(BaseModel):done:bool
@app.put('/api/tasks/{id}')
def set_task(id:int,v:Done):execute('UPDATE tasks SET done=? WHERE id=?',(int(v.done),id));return {'ok':True}
@app.delete('/api/tasks/{id}')
def del_task(id:int):execute('DELETE FROM tasks WHERE id=?',(id,));return {'ok':True}

class Lesson(BaseModel):weekday:int=Field(ge=0,le=6);period:int=Field(ge=1,le=20);subject:str=Field(min_length=1,max_length=100);start:str='';finish:str='';room:str=''
@app.get('/api/lessons')
def lessons():return rows('SELECT * FROM lessons ORDER BY weekday,period,id')
@app.post('/api/lessons')
def add_lesson(l:Lesson):return {'id':execute('INSERT INTO lessons(weekday,period,subject,start,finish,room) VALUES(?,?,?,?,?,?)',(l.weekday,l.period,l.subject.strip(),l.start,l.finish,l.room))}
@app.delete('/api/lessons/{id}')
def del_lesson(id:int):execute('DELETE FROM lessons WHERE id=?',(id,));return {'ok':True}
class Grade(BaseModel):subject:str=Field(min_length=1,max_length=100);grade:int=Field(ge=1,le=5);day:datetime.date
@app.get('/api/grades')
def grades():return rows('SELECT * FROM grades ORDER BY day DESC,id DESC')
@app.post('/api/grades')
def add_grade(g:Grade):return {'id':execute('INSERT INTO grades(subject,grade,day) VALUES(?,?,?)',(g.subject.strip(),g.grade,g.day.isoformat()))}
@app.delete('/api/grades/{id}')
def del_grade(id:int):execute('DELETE FROM grades WHERE id=?',(id,));return {'ok':True}

@app.get('/api/stats')
def stats():
    hs=rows('SELECT id,days,created FROM habits'); checks={(x['habit_id'],x['day']) for x in rows('SELECT * FROM habit_checks')}
    today=datetime.date.today();out=[]
    for n in range(29,-1,-1):
        day=today-datetime.timedelta(days=n); ds=day.isoformat(); wd=day.weekday()
        eligible=[h for h in hs if h['created']<=ds and str(wd) in h['days'].split(',')]
        count=sum((h['id'],ds) in checks for h in eligible)
        out.append({'day':ds,'done':count,'total':len(eligible),'percent':round(count/len(eligible)*100) if eligible else 0})
    return {'history':out,'tasks':rows('SELECT done,COUNT(*) AS count FROM tasks GROUP BY done'),'grades':rows('SELECT subject,ROUND(AVG(grade),2) AS average FROM grades GROUP BY subject ORDER BY subject')}

@app.get('/api/bot/link')
def bot_link():
    token=secrets.token_urlsafe(8)
    execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('link_code',json.dumps({'code':token,'expires':time.time()+600})))
    return {'code':token,'expires_seconds':600}
@app.get('/api/bot/status')
def bot_status():return {'linked':bool(rows('SELECT value FROM settings WHERE key="telegram_chat_id"'))}

class ReminderSettings(BaseModel):
    timezone:str
    schedule_enabled:bool
    schedule_time:str
    homework_enabled:bool
    homework_time:str

@app.get('/api/reminders')
def reminders_get():return get_config()

@app.put('/api/reminders')
def reminders_put(data:ReminderSettings):
    values=data.model_dump()
    try:
        validate_timezone(values['timezone'])
        validate_time(values['schedule_time'])
        validate_time(values['homework_time'])
    except ValueError as exc:raise HTTPException(422,str(exc))
    with conn() as c:
        for key,value in values.items():
            c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value,ensure_ascii=False)))
    return values
