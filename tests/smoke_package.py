"""Exercise the frozen server/worker against a synthetic game directory only."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

def wait_for(check, timeout=45):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            result=check()
            if result:return result
        except (OSError,ValueError,KeyError):pass
        time.sleep(.2)
    raise AssertionError('Timed out waiting for expected application state')

def main():
    exe=Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='ssa-smoke-') as tmp:
        root=Path(tmp).resolve();data=root/'data';game=root/'fake-game';exports=game/'mods/export';commands=game/'mods/commands/$TEST'
        exports.mkdir(parents=True);commands.mkdir(parents=True)
        for name in ['buymax','buypercent','shortpercent','sellmax','covermax']:
            (commands/(name+'.json')).write_text('{"execute":false}',encoding='utf-8')
        now=int(time.time()*1000)
        market={'gameVersion':'1.0.13','formatVersion':1,'marketReady':True,'sequence':1,'writtenAtMs':now,'serverTimeMs':now,'lastPriceUpdateAtMs':now,'stocks':[{'stockId':'$TEST','name':'$TEST','unlocked':True,'price':90,'basePrice':10,'priceCap':1000,'availableShares':'100'}],'player':{'cash':'1000000','netWorth':'1000000','level':1,'ipoCount':0,'buyCooldownSeconds':90,'nextBuyAtMs':0,'nextShortAtMs':0,'positions':[]},'scheduledNews':[],'commandResults':[]}
        def publish():
            stamp=int(time.time()*1000)
            market.update(writtenAtMs=stamp,serverTimeMs=stamp,lastPriceUpdateAtMs=stamp)
            (exports/'market.json').write_text(json.dumps(market),encoding='utf-8')
            h={'writtenAtMs':stamp,'stocks':[{'stockId':'$TEST','samples':[{'timeMs':stamp-180000+i*3000,'price':100} for i in range(61)]}]}
            (exports/'history.json').write_text(json.dumps(h),encoding='utf-8')
        publish()
        env=dict(os.environ,SSA_DATA_DIR=str(data),SSA_GAME_DIR=str(game),SSA_NO_BROWSER='1')
        proc=subprocess.Popen([str(exe)],env=env)
        base=None;token=None
        def request(path,post=False,authorized=True):
            headers={'Origin':base,'X-Assistant-Token':token} if authorized and post else {}
            req=urllib.request.Request(base+path,data=b'' if post else None,headers=headers)
            with urllib.request.urlopen(req,timeout=30) as r:return r.read()
        try:
            launch=wait_for(lambda:json.loads((data/'launcher.json').read_text(encoding='utf-8')))
            base=f"http://127.0.0.1:{launch['port']}"
            assert json.loads(request('/health'))['app']=='screen-stocks-assistant'
            info=json.loads(request('/info'))
            assert Path(info['gameDir']).resolve()==game and Path(info['dataDir']).resolve()==data, 'Never run trading against a real game during this test'
            html=request('/').decode('utf-8');token=re.search("controlToken='([^']+)'",html).group(1)
            assert len(request('/charts.js'))>100 and len(request('/events_details.js'))>100
            assert not json.loads((commands/'buymax.json').read_text())['execute'], 'Opening the app must not start trading'
            try:
                request('/auto/start',True,False)
                raise AssertionError('Unauthorized POST was accepted')
            except urllib.error.HTTPError as e:assert e.code==403
            second=subprocess.Popen([str(exe)],env=env);assert second.wait(timeout=20)==0
            assert json.loads((data/'launcher.json').read_text())['pid']==launch['pid'], 'Duplicate launcher created a second server'
            publish();request('/auto/start',True)
            state=wait_for(lambda:(lambda s:s if s.get('pending') else None)(json.loads((data/'自动交易记录.json').read_text(encoding='utf-8'))),timeout=20)
            time.sleep(.1)
            assert json.loads((commands/'buymax.json').read_text())['execute'], 'Worker did not deliver the pending test command'
            pending=state['pending'];assert pending['id']=='$TEST/buymax'
            finished=int(time.time()*1000)
            market['sequence']=2
            market['player']['positions']=[{'stockId':'$TEST','sharesOwned':'100','averageBuyPrice':90,'sharesShorted':'0','averageShortPrice':0}]
            market['player']['nextBuyAtMs']=finished+90000
            market['commandResults']=[{'id':'$TEST/buymax','run':1,'receivedAtMs':pending['sentAt'],'finishedAtMs':finished,'status':'done','reason':''}]
            (commands/'buymax.json').write_text('{"execute":false}',encoding='utf-8')
            time.sleep(.02);publish()
            wait_for(lambda:json.loads((data/'自动交易记录.json').read_text(encoding='utf-8'))['pending'] is None,timeout=10)
            request('/auto/stop',True)
            wait_for(lambda:json.loads((data/'自动交易记录.json').read_text(encoding='utf-8')).get('runtimeStatus')=='已停止',timeout=25)
            request('/app/quit',True)
            assert proc.wait(timeout=15)==0
            print('PASS: packaged launch, assets, no automatic trading, local request validation, duplicate launcher, worker order and acknowledgement, stop and quit')
        except Exception:
            for name in ('启动日志.txt','自动交易运行日志.txt','自动交易记录.json'):
                path=data/name
                if path.exists():
                    print(name+': '+path.read_text(encoding='utf-8')[-12000:])
            raise
        finally:
            if proc.poll() is None:
                (data/'停止自动交易.flag').write_text('stop',encoding='utf-8')
                if base and token:
                    try:request('/app/quit',True)
                    except Exception:pass
                try:proc.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    proc.wait(timeout=10)

if __name__=='__main__':main()
