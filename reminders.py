import datetime,json,os,sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
DB=Path(os.getenv('DATABASE_PATH',str(Path(__file__).resolve().parent/'studyflow.db')))
def validate_time(v):
 if not isinstance(v,str) or len(v)!=5 or v[2]!=':' or not v[:2].isdigit() or not v[3:].isdigit() or int(v[:2])>23 or int(v[3:])>59:raise ValueError('Время должно быть в формате ЧЧ:ММ')
 return v
def validate_timezone(v):
 try:ZoneInfo(v)
 except (ZoneInfoNotFoundError,ValueError,KeyError):raise ValueError('Неизвестный часовой пояс')
 return v
def connection():
 DB.parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=20000');return c
def schedule_message(c,u,day):
 ls=c.execute('SELECT * FROM lessons WHERE user_id=? AND weekday=? ORDER BY period,id',(u,day.weekday())).fetchall();head=f'📅 Расписание на завтра — {day:%d.%m.%Y}\n'
 return head+('🎉 Уроков нет!' if not ls else '\n'.join(f"{x['period']}. {x['subject']}"+(f" · {x['start']}–{x['finish']}" if x['start'] and x['finish'] else '')+(f" · каб. {x['room']}" if x['room'] else '') for x in ls))
def homework_message(c,u):
 ts=c.execute('SELECT * FROM tasks WHERE user_id=? AND done=0 ORDER BY CASE WHEN due="" THEN 1 ELSE 0 END,due,id',(u,)).fetchall()
 if not ts:return None
 return '📚 Невыполненное домашнее задание\n'+'\n'.join(f"• #{x['id']} {x['subject']+': ' if x['subject'] else ''}{x['title']}"+(f" (до {x['due']})" if x['due'] else '') for x in ts[:30])+'\n\nОтметить: /task ID'
def tick(send):
 with connection() as c:
  users=c.execute('SELECT id FROM users').fetchall();now_utc=datetime.datetime.now(datetime.timezone.utc)
  for row in users:
   u=row['id'];cfgv={**{'timezone':'Europe/Stockholm','schedule_enabled':True,'schedule_time':'20:00','homework_enabled':True,'homework_time':'18:00','schedule_interval':1440,'homework_interval':1440}}
   for x in c.execute('SELECT key,value FROM settings WHERE user_id=?',(u,)).fetchall():
    try:cfgv[x['key']]=json.loads(x['value'])
    except:pass
   now=now_utc.astimezone(ZoneInfo(cfgv['timezone']))
   for kind in ('schedule','homework'):
    if not cfgv.get(kind+'_enabled'):continue
    interval=int(cfgv.get(kind+'_interval',1440));base=cfgv.get(kind+'_time','20:00');h,m=map(int,base.split(':'));anchor=now.replace(hour=h,minute=m,second=0,microsecond=0)
    elapsed=int((now-anchor).total_seconds()//60)
    if elapsed<0 or elapsed%interval!=0 or now.second>20:continue
    slot=f'{now.date().isoformat()}-{elapsed//interval}'
    if c.execute('SELECT 1 FROM notification_log WHERE user_id=? AND kind=? AND slot=?',(u,kind,slot)).fetchone():continue
    link=c.execute('SELECT chat_id FROM bot_links WHERE user_id=? AND chat_id IS NOT NULL',(u,)).fetchone()
    if not link:continue
    msg=schedule_message(c,u,now.date()+datetime.timedelta(days=1)) if kind=='schedule' else homework_message(c,u)
    if not msg:continue
    try:send(link['chat_id'],msg);c.execute('INSERT OR IGNORE INTO notification_log(user_id,kind,slot) VALUES(?,?,?)',(u,kind,slot));c.commit()
    except Exception:pass
