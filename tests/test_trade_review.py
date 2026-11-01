import sys, unittest, tempfile, json, importlib.util
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import strategy, auto_trade
N=1793509000000
def market(price=110,long='0',short='0',avg=100):
    return {'gameVersion':'1.0.13','marketReady':True,'writtenAtMs':N,'stocks':[{'stockId':'$X','name':'X','price':price,'basePrice':10,'unlocked':True,'availableShares':'10000'}],'player':{'cash':'10000','positions':[{'stockId':'$X','sharesOwned':long,'sharesShorted':short,'averageBuyPrice':avg,'averageShortPrice':avg}]},'scheduledNews':[],'commandResults':[]}
H={'writtenAtMs':N,'stocks':[{'stockId':'$X','samples':[{'timeMs':N-180000+i*3000,'price':100} for i in range(61)]}]}
class Review(unittest.TestCase):
    def test_base_low_price_sweeps_only_when_affordable(self):
        m=market(price=2);m['stocks'][0].update(stockId='$PLAIN',availableShares='1000');m['player']['cash']='5000'
        s={'id':'$PLAIN','action':'重置扫货','target':None,'percent':25}
        self.assertEqual(strategy.apply_buy_policy(s,m)['commandAction'],'buymax')
        m['stocks'][0]['price']=6
        a=strategy.apply_buy_policy(s,m);self.assertEqual(a['commandAction'],'buypercent');self.assertEqual(a['percent'],25)
    def test_max_buy_only_with_sufficient_cash(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);c=p/'commands/$X';c.mkdir(parents=True);(c/'buymax.json').write_text('{"execute":false}')
            t=auto_trade.Trader(p,p/'state.json');m=market(price=90);m['player']['cash']='1000000';m['stocks'][0].update(availableShares='100',priceCap=1000)
            t.step(m,H,N);self.assertEqual(t.state['pending']['id'],'$X/buymax');self.assertEqual(json.loads((c/'buymax.json').read_text()),{'execute':True})
    def test_persistent_signal_runs_after_cooldown(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);c=p/'commands/$X';c.mkdir(parents=True);(c/'shortpercent.json').write_text('{"execute":false}')
            t=auto_trade.Trader(p,p/'state.json');m=market();m['sequence']=1;m['player']['nextShortAtMs']=N+1000
            t.step(m,H,N);self.assertIsNone(t.state['pending']);self.assertNotIn('$X|做空观察',t.state['active'])
            m['sequence']=2;m['writtenAtMs']=N+2000;t.step(m,H,N+2000);self.assertEqual(t.state['pending']['id'],'$X/shortpercent')
    def test_game_managed_stock_never_gets_external_order(self):
        with tempfile.TemporaryDirectory() as d:
            t=auto_trade.Trader(Path(d),Path(d)/'state.json')
            fake={'id':'$BANK','action':'做空观察','size':'10','reason':'test','price':'110','percent':25}
            with patch.object(auto_trade,'calculate',return_value={'signals':[fake]}):t.step(market(),H,N)
            self.assertIsNone(t.state['pending']);self.assertFalse(t.paused)
    def signals(self,m,**kw):return strategy.calculate(m,H,N,include_short=True,**kw)['signals']
    def test_no_loss_disguised_as_take_profit(self):self.assertEqual(self.signals(market(long='10',avg=120)),[])
    def test_sell_not_short_when_long(self):self.assertEqual(self.signals(market(long='10'))[0]['action'],'卖出提示')
    def test_cover_not_buy_when_short(self):self.assertEqual(self.signals(market(price=90,short='10'))[0]['action'],'平空提示')
    def test_no_pyramiding(self):self.assertEqual(self.signals(market(price=90,long='10')),[])
    def test_pump_aftermath_blocks_short(self):
        m=market();m['scheduledNews']=[{'occurrenceId':'event:x','stockId':'$X','direction':'pump','targetPrice':300,'scheduledAtMs':N-1000}];self.assertEqual(self.signals(m),[])
    def test_crash_before_pump_exits(self):
        m=market(long='10');m['scheduledNews']=[{'occurrenceId':'event:c','stockId':'$X','direction':'crash','targetPrice':10,'scheduledAtMs':N+10000},{'occurrenceId':'event:p','stockId':'$X','direction':'pump','targetPrice':300,'scheduledAtMs':N+20000}];self.assertIn('下跌',self.signals(m)[0]['reason'])
    def test_execution_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);c=p/'commands/$X';c.mkdir(parents=True)
            for a in ['shortpercent','covermax']:(c/(a+'.json')).write_text('{"execute":false}')
            t=auto_trade.Trader(p,p/'state.json');m=market();t.step(m,H,N)
            self.assertEqual(json.loads((c/'shortpercent.json').read_text()),{'execute':True,'percent':25.0})
            m['commandResults']=[{'id':'$X/shortpercent','run':1,'receivedAtMs':N,'finishedAtMs':N+100,'status':'done'}];m['writtenAtMs']=N+1000
            t.step(m,H,N+1000);self.assertIsNotNone(t.state['pending'])
            m['player']['positions'][0]['sharesShorted']='10';t.step(m,H,N+1001);self.assertIsNone(t.state['pending'])
            m['stocks'][0]['price']=90;t.step(m,H,N+1002);self.assertEqual(t.state['pending']['id'],'$X/covermax')
            m['commandResults'].append({'id':'$X/covermax','run':2,'receivedAtMs':N+1002,'finishedAtMs':N+1003,'status':'done'});m['writtenAtMs']=N+2000;m['player']['positions'][0]['sharesShorted']='0'
            t.step(m,H,N+2000);self.assertGreater(t.state['entryBlockedUntil']['$X'],N+2000)
            m['stocks'][0]['price']=110;t.step(m,H,N+3000);self.assertIsNone(t.state['pending'])
    def test_failed_event_not_completed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);c=p/'commands/$X';c.mkdir(parents=True);(c/'buypercent.json').write_text('{"execute":false}')
            t=auto_trade.Trader(p,p/'state.json');m=market(price=20);m['scheduledNews']=[{'occurrenceId':'event:p','stockId':'$X','direction':'pump','targetPrice':300,'scheduledAtMs':N+10800000}];t.step(m,H,N)
            self.assertNotIn('event:p',t.state.get('eventDone',[]));m['commandResults']=[{'id':'$X/buypercent','run':1,'receivedAtMs':N,'finishedAtMs':N+100,'status':'failed','reason':'cooldown'}];t.step(m,H,N+1000)
            self.assertTrue(t.paused);self.assertNotIn('event:p',t.state.get('eventDone',[]))
    def test_mixed_positions_pause(self):
        with tempfile.TemporaryDirectory() as d:
            t=auto_trade.Trader(Path(d),Path(d)/'state.json');t.step(market(long='2',short='3'),H,N);self.assertTrue(t.paused)
    def test_price_stale_even_with_fresh_export(self):
        m=market();m['lastPriceUpdateAtMs']=N-30000;self.assertEqual(self.signals(m),[])
    def test_event_deadline_not_instant_sell(self):
        m=market(long='5');m['scheduledNews']=[{'occurrenceId':'event:x','stockId':'$X','direction':'pump','targetPrice':300,'scheduledAtMs':N-1000}];self.assertEqual(self.signals(m),[])
    def test_reset_sweep_has_no_guaranteed_profit(self):
        m=market(price=10);m['stocks'][0]['stockId']='$PLAIN';m['player']['positions']=[]
        e={'occurrenceId':'reset:base:1','stockId':'$PLAIN','direction':'crash','scheduledAtMs':N-1000,'targetPrice':10}
        a=strategy.calculate(m,{},N,include_short=True,known_events=[e])['signals'][0]
        self.assertEqual(a['action'],'重置扫货');self.assertEqual(a['percent'],25);self.assertIsNone(a['profit'])
        self.assertEqual(strategy.calculate(m,{},N,include_short=True,known_events=[e],reset_done=[e['occurrenceId']])['signals'],[])
    def test_unconfirmed_result_never_retries(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);c=p/'commands/$X';c.mkdir(parents=True);f=c/'shortpercent.json';f.write_text('{"execute":false}')
            t=auto_trade.Trader(p,p/'state.json');m=market();t.step(m,H,N);f.write_text('{"execute":false}')
            t.step(m,H,N+21000);self.assertTrue(t.paused);self.assertIsNotNone(t.state['pending']);self.assertFalse(json.loads(f.read_text())['execute'])
if __name__=='__main__':unittest.main()
