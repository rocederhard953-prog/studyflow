"""Single-host launcher: website and Telegram polling share one SQLite database."""
import os,threading
import uvicorn

def run_bot():
    from bot import main
    main()

if __name__=='__main__':
    if os.getenv('TELEGRAM_BOT_TOKEN'):
        threading.Thread(target=run_bot,daemon=True,name='telegram-bot').start()
    else:
        print('TELEGRAM_BOT_TOKEN is missing: web only, no reminders',flush=True)
    uvicorn.run('app:app',host='0.0.0.0',port=int(os.getenv('PORT','8000')),workers=1)
