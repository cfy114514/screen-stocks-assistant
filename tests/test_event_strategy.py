import sys,unittest,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from event_strategy import plan_events
from auto_trade import Trader
N=1793509000000
E={'occurrenceId':'event:q','stockId':'$QUIK','direction':'pump','targetPrice':300,'scheduledAtMs':N+10800000}
def market(price=20,owned='0',avg=20,short='0',available='1000',now=N):
    return {'gameVersion':'1.0.13','marketReady':True,'writtenAtMs':now,'stocks':[{'stockId':'$QUIK','name':'$QUIK','unlocked':True,'price':price,'basePrice':10,'availableShares':available}],'player':{'cash':'1000000','positions':[{'stockId':'$QUIK','sharesOwned':owned,'sharesShorted':short,'averageBuyPrice':avg,'averageShortPrice':20}]},'scheduledNews':[E],'commandResults':[]}
class Events(unittest.TestCase):
    def test_wave_then_freeze_boundary(self):
        books={};m=market(price=110,owned='100',avg=90,available='0')
        h={'writtenAtMs':N,'stocks':[{'stockId':'$QUIK','samples':[{'timeMs':N-180000+i*3000,'price':100} for i in range(61)]}]}
        s,_=plan_events(m,[E],books,N,history=h);self.assertEqual(s[0]['action'],'事件波段卖出');self.assertEqual(s[0]['percent'],50);self.assertEqual(float(books['event:q']['coreShares']),50)
        now=E['scheduledAtMs']-90*60000
        m=market(price=110,owned='100',avg=90,now=now)
        s,managed=plan_events(m,[E],books,now,history=h);self.assertEqual(s,[]);self.assertIn('$QUIK',managed);self.assertIn('冻结',books['event:q']['status'])
    def test_freeze_blocks_empty_entry_and_short_exit(self):
        now=E['scheduledAtMs']-1000
        for m in [market(now=now),market(short='100',now=now)]:
            s,managed=plan_events(m,[E],{},now);self.assertEqual(s,[]);self.assertIn('$QUIK',managed)
    def test_old_peak_does_not_liquidate_on_event_start(self):
        books={};plan_events(market(price=20,owned='100',available='0'),[E],books,N)
        plan_events(market(price=102,owned='100',available='0'),[E],books,N+1000)
        now=E['scheduledAtMs'];s,_=plan_events(market(price=14,owned='100',available='0',now=now),[E],books,now)
        self.assertEqual(s,[]);self.assertIn('已到计划时间',books['event:q']['status'])
    def test_missing_future_announcement_never_opens(self):
        m=market();m['scheduledNews']=[]
        s,_=plan_events(m,[E],{},N);self.assertEqual(s,[])
    def test_float_capped_budget(self):
        books={};s,_=plan_events(market(),[E],books,N);self.assertEqual(s[0]['commandAction'],'buymax');self.assertEqual(float(s[0]['size']),1000);self.assertEqual(float(books['event:q']['budget']),1000000)
    def test_existing_long_hold_no_mean_exit(self):
        s,managed=plan_events(market(price=40,owned='100',available='0'),[E],{},N);self.assertEqual(s,[]);self.assertIn('$QUIK',managed)
    def test_half_then_trailing(self):
        books={};s,_=plan_events(market(price=290,owned='100',available='0'),[E],books,N)
        # A book is created only when first seen with sufficient upside.
        plan_events(market(price=20,owned='100',available='0'),[E],books,N)
        s,_=plan_events(market(price=290,owned='100',available='0'),[E],books,N);self.assertEqual(s,[])
        now=E['scheduledAtMs'];s,_=plan_events(market(price=290,owned='100',available='0',now=now),[E],books,now);self.assertEqual(s[0]['action'],'事件分批止盈');self.assertEqual(s[0]['percent'],50)
        books['event:q']['halfTaken']=True
        s,_=plan_events(market(price=265,owned='50',available='0',now=now+1000),[E],books,now+1000);self.assertEqual(s[0]['action'],'事件清仓')
    def test_deadline_exit(self):
        books={};plan_events(market(owned='100',available='0'),[E],books,N)
        now=E['scheduledAtMs']+900000
        s,_=plan_events(market(price=19,owned='100',available='0',now=now),[E],books,now);self.assertEqual(s[0]['action'],'事件清仓')
    def test_short_is_covered_not_bought(self):
        s,_=plan_events(market(short='100'),[E],{},N);self.assertEqual(s[0]['action'],'事件平空')
    def test_confirmed_cost_and_partial_execution(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'commands/$QUIK';p.mkdir(parents=True)
            for a in ['buypercent','buymax','sellpercent']:(p/(a+'.json')).write_text('{"execute":false}')
            t=Trader(root,root/'state.json');m=market();t.step(m,{},N);self.assertEqual(t.state['eventBooks']['event:q']['spent'],'0')
            m=market(owned='1000',now=N+2000);m['commandResults']=[{'id':'$QUIK/buymax','run':1,'receivedAtMs':N,'finishedAtMs':N+100,'status':'done'}];t.step(m,{},N+2000);self.assertEqual(float(t.state['eventBooks']['event:q']['spent']),20000)
            m=market(price=290,owned='1000',available='0',now=N+3000);m['scheduledNews']=[dict(E,scheduledAtMs=N+2500)];t.step(m,{},N+3000);self.assertEqual(json.loads((p/'sellpercent.json').read_text())['percent'],50);self.assertFalse(t.state['eventBooks']['event:q']['halfTaken'])
            m=market(price=290,owned='500',available='0',now=N+4000);m['commandResults']=[{'id':'$QUIK/sellpercent','run':2,'receivedAtMs':N+3000,'finishedAtMs':N+3100,'status':'done'}];t.step(m,{},N+4000);self.assertTrue(t.state['eventBooks']['event:q']['halfTaken'])
if __name__=='__main__':unittest.main()
