import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from auto_trade import Trader
from game_session import process_started_ms

N=1793509000000

def snapshot(now, seq=1, owned='0', results=None):
    return {'gameVersion':'1.0.13','marketReady':True,'writtenAtMs':now,'sequence':seq,'stocks':[],
            'player':{'cash':'10000','positions':[{'stockId':'$TEST','sharesOwned':owned,'sharesShorted':'0','averageBuyPrice':'10','averageShortPrice':'0'}]},'commandResults':results or []}

class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.command=self.root/'commands/$TEST/buymax.json';self.command.parent.mkdir(parents=True)
        self.command.write_text('{"execute":false}')
        self.session={'pid':2,'startedAtMs':N+30000}
        self.pending={'id':'$TEST/buymax','sentAt':N,'baseline':500,'beforeShares':'0','beforeAverage':'0','eventId':None,'signalAction':'买入观察'}
    def tearDown(self):self.tmp.cleanup()
    def trader(self, provider=True):
        state={'version':1,'pending':copy.deepcopy(self.pending),'active':['$TEST|买入观察'],'log':[{'id':'$TEST/buymax','time':N,'status':'已提交，等待游戏结果'}]}
        self.path=self.root/'state.json';self.path.write_text(json.dumps(state))
        return Trader(self.root,self.path,session_provider=(lambda:self.session) if provider else None)
    def recover(self,t):
        t.step(snapshot(N+100000,1),{},N+100000)
        t.step(snapshot(N+101100,2),{},N+101100)
    def test_legacy_pending_after_restart_is_archived_without_replay(self):
        t=self.trader();self.recover(t)
        self.assertIsNone(t.state['pending']);self.assertFalse(t.paused)
        record=t.state['archivedPending'][0]
        self.assertEqual(record['outcome'],'unknown-after-game-restart');self.assertFalse(record['replayed'])
        self.assertEqual(json.loads(self.command.read_text()),{'execute':False})
        self.assertGreater(t.state['entryBlockedUntil']['$TEST'],N+101100)
        self.assertIsNone(json.loads(self.path.read_text(encoding='utf-8'))['pending'])
    def test_same_game_late_result_is_confirmed_without_restart_loop(self):
        self.session={'pid':1,'startedAtMs':N-30000};t=self.trader()
        t.step(snapshot(N+100000),{},N+100000)
        self.assertFalse(t.paused);self.assertEqual(t.state['runtimePhase'],'awaiting_result');self.assertIsNotNone(t.state['pending'])
        r={'id':'$TEST/buymax','run':501,'receivedAtMs':N+90000,'finishedAtMs':N+100100,'status':'done'}
        t.step(snapshot(N+101100,2,owned='10',results=[r]),{},N+101100)
        self.assertIsNone(t.state['pending']);self.assertNotIn('archivedPending',t.state)
        self.assertEqual(t.state['log'][0]['status'],'done')
    def test_no_session_evidence_never_discards_pending(self):
        t=self.trader(provider=False);self.recover(t)
        self.assertIsNotNone(t.state['pending']);self.assertNotIn('archivedPending',t.state);self.assertFalse(t.paused)
    def test_stale_market_is_not_recovery_evidence(self):
        t=self.trader();t.step(snapshot(N+1000),{},N+100000)
        self.assertIsNotNone(t.state['pending']);self.assertEqual(t.state['runtimePhase'],'waiting_for_market')
    def test_command_still_executable_is_never_rewritten_or_archived(self):
        self.command.write_text('{"execute":true}');t=self.trader();self.recover(t)
        self.assertIsNotNone(t.state['pending']);self.assertTrue(json.loads(self.command.read_text())['execute'])
    def test_missing_command_file_blocks_recovery(self):
        self.command.unlink();t=self.trader();self.recover(t);self.assertIsNotNone(t.state['pending'])
    def test_repeated_same_export_does_not_count_as_two_snapshots(self):
        t=self.trader();m=snapshot(N+100000)
        t.step(m,{},N+100000);t.step(m,{},N+101100);self.assertIsNotNone(t.state['pending'])
    def test_changing_position_requires_new_stable_baseline(self):
        t=self.trader();t.step(snapshot(N+100000,1),{},N+100000)
        t.step(snapshot(N+101100,2,owned='5'),{},N+101100);self.assertIsNotNone(t.state['pending'])
        t.step(snapshot(N+102200,3,owned='5'),{},N+102200);self.assertIsNone(t.state['pending'])
        self.assertEqual(t.state['archivedPending'][0]['currentPosition']['sharesOwned'],'5')
    def test_new_game_result_cannot_be_attributed_to_old_order(self):
        t=self.trader();r={'id':'$TEST/buymax','run':999,'receivedAtMs':N+90000,'finishedAtMs':N+91000,'status':'done'}
        t.step(snapshot(N+100000,1,results=[r]),{},N+100000)
        t.step(snapshot(N+101100,2,results=[r]),{},N+101100)
        self.assertIsNone(t.state['pending']);self.assertIn('结果',t.state['log'][0]['status'] if '结果' in t.state['log'][0]['status'] else t.state['recoveryStatus'])
        self.assertNotEqual(t.state['log'][0]['status'],'done')
    def test_event_unknown_requires_symbol_review_but_not_global_deadlock(self):
        self.pending['eventId']='event:x';self.pending['eventManaged']=True
        t=self.trader();t.state['eventBooks']={'event:x':{'stockId':'$TEST','spent':'0'}};self.recover(t)
        self.assertIsNone(t.state['pending']);self.assertFalse(t.paused)
        self.assertIn('$TEST',t.state['recoveryBlockedStocks']);self.assertTrue(t.state['eventBooks']['event:x']['recoveryRequired'])
    def test_pid_reuse_still_detected_by_creation_time(self):
        self.pending['gameSession']={'pid':2,'startedAtMs':N-10000}
        t=self.trader();self.recover(t);self.assertIsNone(t.state['pending'])
    def test_same_recorded_process_is_not_a_restart(self):
        self.pending['gameSession']=dict(self.session)
        t=self.trader();self.recover(t)
        self.assertIsNotNone(t.state['pending']);self.assertNotIn('archivedPending',t.state)
    def test_original_log_entry_is_updated_not_unrelated_last_entry(self):
        t=self.trader();t.state['log'].append({'id':'other','time':N+1,'status':'unchanged'});self.recover(t)
        self.assertEqual(t.state['log'][-1]['status'],'unchanged');self.assertIn('未重发',t.state['log'][0]['status'])
    @unittest.skipUnless(os.name=='nt','Windows process identity')
    def test_native_process_creation_time(self):
        started=process_started_ms(os.getpid());self.assertIsNotNone(started)
        self.assertLessEqual(started,int(time.time()*1000));self.assertGreater(started,1600000000000)

if __name__=='__main__':unittest.main()
