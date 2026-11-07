"""Optional Telegram long-polling bot; run as a separate process."""
import os,time,json,datetime,urllib.request,urllib.parse,sqlite3
from pathlib import Path
from reminders import tick,local_now,schedule_message
TOKEN=os.environ['TELEGRAM_BOT_TOKEN']
DB=Path(os.getenv('DATABASE_PATH',str(Path(__file__).resolve().parent/'studyflow.db')))
API=f'https://api.telegram.org/bot{TOKEN}/'
def call(method,params):
    data=urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(API+method,data=data),timeout=45) as r:return json.load(r)['result']
def db(sql,args=(),fetch=False):
    c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row
    try:
        x=c.execute(sql,args);r=[dict(y) for y in x.fetchall()] if fetch else None;c.commit();return r
    finally:c.close()
def setting(key):
    r=db('SELECT value FROM settings WHERE key=?',(key,),True);return r[0]['value'] if r else None
def send(chat,text):call('sendMessage',{'chat_id':chat,'text':text})
def handle(msg):
    chat=str(msg['chat']['id']);txt=msg.get('text','').strip();linked=setting('telegram_chat_id')
    if txt.startswith('/link '):
        raw=setting('link_code')
        try: info=json.loads(raw or '{}')
        except ValueError:info={}
        if info.get('code')==txt.split(' ',1)[1].strip() and info.get('expires',0)>time.time():
            db('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('telegram_chat_id',chat))
            db('DELETE FROM settings WHERE key="link_code"');send(chat,'✅ Аккаунт привязан! /help — команды.');return
        send(chat,'Код недействителен или устарел. Создай новый на сайте.');return
    if linked!=chat:
        send(chat,'🔒 Сначала привяжи бота в разделе Telegram на сайте.');return
    today=local_now().date().isoformat()
    if txt in ('/start','/help'):
        send(chat,'StudyFlow 📘\n/today — обзор дня\n/habits — привычки\n/done ID — отметить привычку\n/undo ID — снять отметку\n/homework — домашка\n/task ID — выполнить домашку\n/schedule — уроки сегодня');return
    if txt=='/habits':
        hs=db('SELECT * FROM habits ORDER BY id',fetch=True)
        checks={r['habit_id'] for r in db('SELECT habit_id FROM habit_checks WHERE day=?',(today,),True)}
        hs=[h for h in hs if str(local_now().weekday()) in h['days'].split(',')]
        send(chat,'Привычки на сегодня:\n'+'\n'.join(f"{'✅' if h['id'] in checks else '⬜'} {h['id']}. {h['name']}" for h in hs) if hs else 'Сегодня привычек нет.');return
    if txt.startswith(('/done ','/undo ')):
        try:id=int(txt.split()[1])
        except (ValueError,IndexError):send(chat,'Укажи ID, например /done 3');return
        if not db('SELECT id FROM habits WHERE id=?',(id,),True):send(chat,'Привычка не найдена.');return
        if txt.startswith('/done '):db('INSERT OR IGNORE INTO habit_checks(habit_id,day) VALUES(?,?)',(id,today))
        else:db('DELETE FROM habit_checks WHERE habit_id=? AND day=?',(id,today))
        send(chat,'Готово! Изменение видно на сайте.');return
    if txt=='/homework':
        ts=db('SELECT * FROM tasks WHERE done=0 ORDER BY due,id LIMIT 20',fetch=True)
        send(chat,'Домашние задания:\n'+'\n'.join(f"{t['id']}. {t['subject']} — {t['title']} (до {t['due'] or 'без срока'})" for t in ts) if ts else 'Всё выполнено!');return
    if txt.startswith('/task '):
        try:id=int(txt.split()[1])
        except (ValueError,IndexError):send(chat,'Укажи ID задания.');return
        db('UPDATE tasks SET done=1 WHERE id=?',(id,));send(chat,'Задание отмечено выполненным.');return
    if txt=='/tomorrow':
        send(chat,schedule_message(local_now().date()+datetime.timedelta(days=1)));return
    if txt=='/schedule':
        ls=db('SELECT * FROM lessons WHERE weekday=? ORDER BY period',(local_now().weekday(),),True)
        send(chat,'Уроки сегодня:\n'+'\n'.join(f"{l['period']}. {l['subject']} {l['start']}–{l['finish']} {l['room']}" for l in ls) if ls else 'Сегодня уроков нет.');return
    if txt=='/today':
        ts=db('SELECT COUNT(*) n FROM tasks WHERE done=0',fetch=True)[0]['n']
        hs=db('SELECT COUNT(*) n FROM habits',fetch=True)[0]['n']
        send(chat,f'📅 {today}\nПривычек всего: {hs}\nНевыполненных заданий: {ts}\n/habits /schedule /homework');return
    send(chat,'Неизвестная команда. Напиши /help')

def main():
    offset=0
    print('StudyFlow Telegram bot running',flush=True)
    while True:
        try:
            tick(send)
            updates=call('getUpdates',{'offset':offset,'timeout':10,'allowed_updates':json.dumps(['message'])})
            for u in updates:
                offset=u['update_id']+1
                if 'message' in u:
                    try:handle(u['message'])
                    except Exception as e:print('Message error:',repr(e),flush=True)
        except Exception as e:print('Polling error:',repr(e),flush=True);time.sleep(5)
if __name__=='__main__':main()
