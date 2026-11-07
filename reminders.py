import datetime, json, os, sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DB=Path(os.getenv('DATABASE_PATH',str(Path(__file__).resolve().parent/'studyflow.db')))
DEFAULTS={'timezone':'Europe/Moscow','schedule_enabled':True,'schedule_time':'20:00','homework_enabled':True,'homework_time':'18:00'}

def validate_time(value):
    if not isinstance(value,str) or len(value)!=5 or value[2]!=':': raise ValueError('Время должно быть в формате ЧЧ:ММ')
    hh,mm=value[:2],value[3:]
    if not hh.isdigit() or not mm.isdigit() or int(hh)>23 or int(mm)>59: raise ValueError('Некорректное время')
    return value

def validate_timezone(value):
    if not isinstance(value,str) or len(value)>100: raise ValueError('Некорректный часовой пояс')
    try: ZoneInfo(value)
    except (ZoneInfoNotFoundError,ValueError,KeyError): raise ValueError('Неизвестный часовой пояс')
    return value

def connection():
    DB.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB,timeout=15)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=15000')
    return c

def get_config():
    with connection() as c:
        r=c.execute("SELECT key,value FROM settings WHERE key IN ('timezone','schedule_enabled','schedule_time','homework_enabled','homework_time')").fetchall()
    result=DEFAULTS.copy()
    for row in r:
        try: result[row['key']]=json.loads(row['value'])
        except (ValueError,TypeError): pass
    return result

def local_now(config=None):
    config=config or get_config()
    return datetime.datetime.now(ZoneInfo(config['timezone']))

def schedule_message(day):
    with connection() as c:
        lessons=c.execute('SELECT * FROM lessons WHERE weekday=? ORDER BY period,id',(day.weekday(),)).fetchall()
    header=f'📅 Расписание на завтра — {day.strftime("%d.%m.%Y")}\n'
    if not lessons: return header+'🎉 Уроков нет!'
    return header+'\n'.join(f"{l['period']}. {l['subject']}"+(f" · {l['start']}–{l['finish']}" if l['start'] and l['finish'] else f" · {l['start']}" if l['start'] else '')+(f" · каб. {l['room']}" if l['room'] else '') for l in lessons)

def homework_message():
    with connection() as c:
        tasks=c.execute('SELECT * FROM tasks WHERE done=0 ORDER BY CASE WHEN due="" THEN 1 ELSE 0 END,due,id').fetchall()
    if not tasks: return None
    lines=['📚 Напоминание: невыполненное домашнее задание']
    for t in tasks[:30]:
        lines.append(f"• #{t['id']} {t['subject']+': ' if t['subject'] else ''}{t['title']}"+(f" (до {t['due']})" if t['due'] else ''))
    if len(tasks)>30:lines.append(f'… и ещё {len(tasks)-30} заданий')
    lines.append('\nОтметить выполнение: /task ID')
    return '\n'.join(lines)

def tick(send):
    config=get_config()
    now=local_now(config)
    minute=now.strftime('%H:%M')
    date=now.date().isoformat()
    for kind in ('schedule','homework'):
        if not config.get(kind+'_enabled') or minute!=config.get(kind+'_time'):continue
        with connection() as c:
            chat=c.execute('SELECT value FROM settings WHERE key=?',('telegram_chat_id',)).fetchone()
            if not chat:continue
            key=f'last_notification_{kind}'
            old=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            if old and old['value']==date:continue
            message=schedule_message(now.date()+datetime.timedelta(days=1)) if kind=='schedule' else homework_message()
            if not message:continue
            # Mark after a successful send, so transient failures retry within the minute.
            send(chat['value'],message)
            c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,date))
            c.commit()
