import http.server
import json
import os
from pathlib import Path
import threading
import webbrowser
import time
import msvcrt
import secrets
import subprocess
import sys
from urllib.parse import urlparse,parse_qs
from paper import Paper
from strategy import calculate, apply_buy_policy
from event_strategy import plan_events
from app_config import DATA, ASSETS, GAME, GAME_MANAGED, VERSION, CONFIG, prepare_runtime, worker_command

ROOT = DATA
EXPORT = GAME/'mods/export'
paper = None
control_lock = threading.Lock()
worker = None
csrf_token = secrets.token_urlsafe(32)
trend_lock=threading.Lock()
try:
    trend=json.loads((ROOT/'资金走势.json').read_text(encoding='utf-8'))[-720:]
except (OSError,ValueError): trend=[]

def record_trend(m):
    now=int(time.time()*1000); stamp=int(m.get('writtenAtMs') or 0)
    if not m.get('marketReady') or abs(now-stamp)>15000: return
    with trend_lock:
        if trend and stamp-int(trend[-1]['time'])<10000: return
        p=m.get('player') or {}
        trend.append({'time':stamp,'cash':p.get('cash'),'netWorth':p.get('netWorth')})
        del trend[:-720]
        tmp=ROOT/'资金走势.tmp'
        tmp.write_text(json.dumps(trend,ensure_ascii=False),encoding='utf-8')
        os.replace(tmp,ROOT/'资金走势.json')

def read_json(name):
    return json.loads((EXPORT / name).read_text(encoding='utf-8-sig'))

def trading_locked():
    with (ROOT/'自动交易.lock').open('a+b') as f:
        try:
            f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:return True
        f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        return False

def monitor():
    while True:
        try:
            market = read_json('market.json')
            record_trend(market)
            try:
                history = read_json('history.json')
            except (OSError, ValueError):
                history = {}
            paper.update(market, history)
        except (OSError, ValueError) as e:
            with paper.lock:
                paper.latest = {'message': '读取行情或保存记录失败：' + str(e), 'signals': []}
        time.sleep(2)

class Handler(http.server.BaseHTTPRequestHandler):
    def valid_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def reply_json(self, data, status=200):
        body=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        global worker
        expected=f'http://127.0.0.1:{self.server.server_port}'
        if not self.valid_host() or self.headers.get('Origin') != expected or self.headers.get('X-Assistant-Token') != csrf_token:
            self.reply_json({'error':'请求未通过本地验证'},403); return
        if self.path not in ('/auto/start','/auto/stop','/app/quit'):
            self.reply_json({'error':'未知操作'},404); return
        with control_lock:
            if self.path == '/app/quit':
                (ROOT/'停止自动交易.flag').write_text('stop',encoding='utf-8')
                deadline=time.monotonic()+25
                while trading_locked() and time.monotonic()<deadline:time.sleep(.2)
                if trading_locked():
                    self.reply_json({'error':'交易进程仍在核对指令，暂未退出；请稍后重试。'},409);return
                self.reply_json({'message':'交易进程已停止，助手已退出。此页可以关闭。'})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if self.path == '/auto/stop':
                (ROOT/'停止自动交易.flag').write_text('stop',encoding='utf-8')
                self.reply_json({'message':'已请求停止；正在等待已提交的指令结果。'}); return
            if worker and worker.poll() is None:
                self.reply_json({'message':'自动交易已在运行。'}); return
            try:
                state=json.loads((ROOT/'自动交易记录.json').read_text(encoding='utf-8'))
                if state.get('runtimeStatus')!='已停止' and abs(int(time.time()*1000)-int(state.get('heartbeat') or 0))<8000:
                    self.reply_json({'message':'自动交易已在运行。'}); return
            except (OSError,ValueError):
                pass
            (ROOT/'停止自动交易.flag').unlink(missing_ok=True)
            try:
                m=read_json('market.json')
                if m.get('gameVersion')!='1.0.13' or not m.get('marketReady') or abs(int(time.time()*1000)-int(m.get('writtenAtMs') or 0))>15000:
                    self.reply_json({'error':'需要已就绪且持续更新的 1.0.13 游戏行情；请检查游戏和 Mod 设置。'},409);return
            except (OSError,ValueError):
                self.reply_json({'error':'无法读取行情，请先按启用教程打开游戏的 Mod 支持。'},409);return
            worker=subprocess.Popen(worker_command(),cwd=str(ROOT),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.reply_json({'message':'启动请求已发送。'})

    def do_GET(self):
        if not self.valid_host():
            self.send_error(403); return
        if self.path == '/':
            body = (ASSETS / 'index.html').read_text(encoding='utf-8').replace('__CSRF_TOKEN__', csrf_token).encode('utf-8')
            kind = 'text/html; charset=utf-8'
        elif self.path in ('/charts.js','/events_details.js'):
            body=(ASSETS/self.path[1:]).read_bytes()
            kind='text/javascript; charset=utf-8'
        elif self.path == '/auto':
            try:
                data = json.loads((ROOT / '自动交易记录.json').read_text(encoding='utf-8'))
                data['running'] = data.get('runtimeStatus') != '已停止' and abs(int(time.time()*1000)-int(data.get('heartbeat') or 0)) < 8000
                data['stopping'] = (ROOT/'停止自动交易.flag').exists()
            except (OSError, ValueError):
                data = {'running': False, 'log': []}
            try:
                m=read_json('market.json')
                try: h=read_json('history.json')
                except (OSError,ValueError): h={}
                data['liveSignals']=calculate(m,h,include_short=True,entry_percent=25,protected_shorts=data.get('protectedShorts',[]),known_events=data.get('knownEvents',[]),reset_done=data.get('resetDone',[]),reset_hold=data.get('resetHold',False),event_done=data.get('eventDone',[]))['signals']
                data['liveSignals']=[s for s in data['liveSignals'] if s['id'] not in GAME_MANAGED]
                merged={e['occurrenceId']:e for e in list(data.get('knownEvents',[]))+list(m.get('scheduledNews',[])) if e.get('occurrenceId')}
                event_signals,event_stocks=plan_events(m,list(merged.values()),data.setdefault('eventBooks',{}),int(time.time()*1000),GAME_MANAGED,h)
                data['liveSignals']=[s for s in data['liveSignals'] if s['id'] not in event_stocks]+event_signals
                data['liveSignals']=[apply_buy_policy(s,m) for s in data['liveSignals']]
            except (OSError,ValueError):
                data['liveSignals']=[]
            data['logCount']=len(data.get('log',[]))
            data['gameManagedStocks']=sorted(GAME_MANAGED)
            data['log']=data.get('log',[])[-100:]
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            kind = 'application/json; charset=utf-8'
        elif self.path == '/trend':
            with trend_lock: body=json.dumps(trend).encode('utf-8')
            kind='application/json; charset=utf-8'
        elif self.path == '/health':
            self.reply_json({'app':'screen-stocks-assistant','version':VERSION});return
        elif self.path == '/info':
            self.reply_json({'version':VERSION,'gameDir':str(GAME),'dataDir':str(DATA),'gameManagedStocks':sorted(GAME_MANAGED),'manageExistingShorts':bool(CONFIG.get('manage_existing_shorts',False))});return
        elif urlparse(self.path).path == '/history':
            sid=parse_qs(urlparse(self.path).query).get('stock',[''])[0]
            try:
                h=read_json('history.json')
                data={'writtenAtMs':h.get('writtenAtMs'),'samples':next((s.get('samples',[]) for s in h.get('stocks',[]) if s.get('stockId')==sid),[])}
            except (OSError,ValueError):data={'samples':[]}
            body=json.dumps(data).encode('utf-8');kind='application/json; charset=utf-8'
        elif self.path in ('/market', '/strategy'):
            try:
                data = read_json('market.json')
                if self.path == '/strategy':
                    data = paper.view(data)
                    data['logCount']=len(data.get('log',[]))
                    data['log']=data.get('log',[])[:100]
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            except (OSError, ValueError) as e:
                body = json.dumps({'error': str(e)}, ensure_ascii=False).encode('utf-8')
            kind = 'application/json; charset=utf-8'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

def main():
    global paper
    prepare_runtime()
    if '--trade-worker' in sys.argv:
        from auto_trade import main as trade_main, diagnostic
        try: trade_main()
        except Exception:
            import traceback
            diagnostic('自动交易无法运行：\n'+traceback.format_exc())
        return
    paper=Paper(ROOT/'模拟交易记录.json')
    lockfile = (ROOT / 'assistant.lock').open('a+b')
    try:
        lockfile.seek(0)
        msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        try:
            import urllib.request
            port=int(json.loads((ROOT/'launcher.json').read_text(encoding='utf-8'))['port'])
            url=f'http://127.0.0.1:{port}/'
            with urllib.request.urlopen(url+'health',timeout=2) as response:
                health=json.load(response)
            if health.get('app')=='screen-stocks-assistant' and os.environ.get('SSA_NO_BROWSER')!='1':webbrowser.open(url)
        except (OSError,ValueError,KeyError):print('助手已经运行，请使用已打开的页面。')
        return
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}/'
    (ROOT/'launcher.json').write_text(json.dumps({'port':server.server_port,'pid':os.getpid()}),encoding='utf-8')
    print('股神盯盘助手已启动：' + url, flush=True)
    print('保持此窗口打开；关闭窗口即可退出。', flush=True)
    threading.Thread(target=monitor, daemon=True).start()
    if os.environ.get('SSA_NO_BROWSER')!='1':threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    try: main()
    except Exception:
        prepare_runtime()
        import traceback
        print(traceback.format_exc(),file=sys.stderr)
        raise
