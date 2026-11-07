"""Telegram long-polling bot for multi-user StudyFlow."""
import os,time,json,datetime,urllib.request,urllib.parse,sqlite3
from pathlib import Path
from reminders import tick
TOKEN=os.environ['TELEGRAM_BOT_TOKEN'];DB=Path(os.getenv('DATABASE_PATH',str(Path(__file__).resolve().parent/'studyflow.db')));API=f'https://api.telegram.org/bot{TOKEN}/'
def call(method,params):
 data=urllib.parse.urlencode(params).encode()
 with urllib.request.urlopen(urllib.request.Request(API+method,data=data),timeout=45) as r:
  obj=json.load(r)
  if not obj.get('ok'):raise RuntimeError(obj)
  return obj['result']
def db(sql,args=(),fetch=False):
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row
 try:x=c.execute(sql,args);r=[dict(y) for y in x.fetchall()] if fetch else None;c.commit();return r
 finally:c.close()
def send(chat,text):call('sendMessage',{'chat_id':chat,'text':text})
def user_for(chat):
 r=db('SELECT user_id FROM bot_links WHERE chat_id=?',(str(chat),),True);return r[0]['user_id'] if r else None
def handle(msg):
 chat=str(msg['chat']['id']);txt=msg.get('text','').strip()
 if txt.startswith('/link '):
  code=txt.split(' ',1)[1].strip();r=db('SELECT user_id FROM bot_links WHERE code=? AND expires>? AND chat_id IS NULL',(code,time.time()),True)
  if not r:send(chat,'Код недействителен или устарел. Создай новый на сайте.');return
  db('UPDATE bot_links SET chat_id=?,code=NULL,expires=NULL WHERE user_id=?',(chat,r[0]['user_id']));send(chat,'✅ Аккаунт StudyFlow привязан! Напиши /help.');return
 u=user_for(chat)
 if not u:send(chat,'🔒 Сначала привяжи бота в личном кабинете StudyFlow.');return
 today=datetime.date.today().isoformat()
 if txt in ('/start','/help'):send(chat,'StudyFlow 📘\n/today — обзор дня\n/habits — привычки\n/done ID — отметить привычку\n/undo ID — снять отметку\n/homework — домашка\n/task ID — выполнить домашку\n/schedule — уроки сегодня\n/tomorrow — расписание завтра');return
 if txt=='/habits':
  hs=db('SELECT * FROM habits WHERE user_id=? ORDER BY id',(u,),True);checks={x['habit_id'] for x in db('SELECT hc.habit_id FROM habit_checks hc JOIN habits h ON h.id=hc.habit_id WHERE h.user_id=? AND hc.day=?',(u,today),True)};wd=datetime.date.today().weekday();hs=[h for h in hs if str(wd) in h['days'].split(',')];send(chat,'Привычки на сегодня:\n'+'\n'.join(f"{'✅' if h['id'] in checks else '⬜'} {h['id']}. {h['name']}" for h in hs) if hs else 'Сегодня привычек нет.');return
 if txt.startswith(('/done ','/undo ')):
  try:i=int(txt.split()[1])
  except:send(chat,'Укажи ID привычки.');return
  if not db('SELECT id FROM habits WHERE id=? AND user_id=?',(i,u),True):send(chat,'Привычка не найдена.');return
  if txt.startswith('/done '):db('INSERT OR IGNORE INTO habit_checks(habit_id,day) VALUES(?,?)',(i,today))
  else:db('DELETE FROM habit_checks WHERE habit_id=? AND day=?',(i,today))
  send(chat,'Готово!');return
 if txt=='/homework':
  ts=db('SELECT * FROM tasks WHERE user_id=? AND done=0 ORDER BY due,id',(u,),True);send(chat,'Домашние задания:\n'+'\n'.join(f"{x['id']}. {x['subject']} — {x['title']} (до {x['due'] or 'без срока'})" for x in ts) if ts else 'Всё выполнено!');return
 if txt.startswith('/task '):
  try:i=int(txt.split()[1])
  except:send(chat,'Укажи ID задания.');return
  db('UPDATE tasks SET done=1 WHERE id=? AND user_id=?',(i,u));send(chat,'Задание отмечено выполненным.');return
 if txt in ('/schedule','/tomorrow'):
  from reminders import connection,schedule_message
  d=datetime.date.today()+(datetime.timedelta(days=1) if txt=='/tomorrow' else datetime.timedelta())
  with connection() as c:send(chat,schedule_message(c,u,d))
  return
 if txt=='/today':
  n=db('SELECT COUNT(*) n FROM tasks WHERE user_id=? AND done=0',(u,),True)[0]['n'];send(chat,f'📅 {today}\nНевыполненных заданий: {n}\n/habits /schedule /homework');return
 send(chat,'Неизвестная команда. Напиши /help')
def main():
 offset=0;print('StudyFlow multi-user Telegram bot running',flush=True)
 while True:
  try:
   tick(send);updates=call('getUpdates',{'offset':offset,'timeout':10,'allowed_updates':json.dumps(['message'])})
   for x in updates:
    offset=x['update_id']+1
    if 'message' in x:
     try:handle(x['message'])
     except Exception as e:print('Message error:',repr(e),flush=True)
  except Exception as e:print('Polling error:',repr(e),flush=True);time.sleep(5)
if __name__=='__main__':main()
