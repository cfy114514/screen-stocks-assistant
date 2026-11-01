"""Trade only through Screen Stocks' local commands. Never connects to the server."""
import json, os, re, time, traceback
from pathlib import Path
from strategy import calculate, dec, apply_buy_policy
from event_strategy import plan_events
from decimal import localcontext
from app_config import DATA, GAME, GAME_MANAGED, CONFIG, prepare_runtime
from game_session import GameSessionProbe

ROOT=DATA
MODS=GAME/'mods'
STATE=ROOT/'自动交易记录.json'
DIAGNOSTICS=ROOT/'自动交易运行日志.txt'
STOP=ROOT/'停止自动交易.flag'

def diagnostic(message):
    line = time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + message
    print(line, flush=True)
    with DIAGNOSTICS.open('a', encoding='utf-8') as f:
        f.write(line+'\n')

def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def save(path, data, retry_replace=False):
    tmp=path.with_suffix('.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
    # Windows readers may briefly deny replacement even after our file is closed.
    # Retry only a failed rename of the already-written state snapshot, never a
    # successfully submitted game command.
    attempts=8 if retry_replace else 1
    for attempt in range(attempts):
        try:
            os.replace(tmp,path)
            break
        except PermissionError:
            if attempt==attempts-1:raise
            time.sleep(min(.02*2**attempt,.2))

class Trader:
    def __init__(self, root=MODS, statepath=STATE, session_provider=None):
        self.root=Path(root); self.path=Path(statepath)
        self.state=load(self.path) if self.path.exists() else {'version':1,'pending':None,'active':[],'log':[]}
        self.state.setdefault('manageExistingShorts',bool(CONFIG.get('manage_existing_shorts',False)))
        self.paused=False
        self.last_save=0
        self.last_sequence=None
        self.session_provider=session_provider
        self.restart_probe=None

    def session(self):
        try:return self.session_provider() if self.session_provider else None
        except OSError:return None

    def waiting(self, message, phase='awaiting_result'):
        self.state['runtimePhase']=phase
        self.state['recoveryStatus']=message
        self.state['stopReason']=''
        self.persist(force=False)

    def pending_log(self, pending):
        for entry in reversed(self.state['log']):
            if entry.get('id')==pending['id'] and entry.get('time')==pending['sentAt']:return entry
        # Older versions may not have a corresponding complete log entry.
        entry={'id':pending['id'],'time':pending['sentAt'],'status':'旧指令待核对'}
        self.state['log'].append(entry)
        return entry

    def recover_restarted_game(self, market, now, session):
        pending=self.state['pending']
        parts=pending['id'].split('/')
        actions={'buymax','buypercent','shortmax','shortpercent','sellmax','sellpercent','covermax','coverpercent','closemax','closepercent'}
        if len(parts)!=2 or not re.fullmatch(r'\$[A-Za-z0-9_]+',parts[0]) or parts[1] not in actions:
            self.waiting('旧指令格式无法识别，需要核对记录');return
        sid,action=parts
        player=market.get('player')
        if not isinstance(player,dict) or not isinstance(player.get('positions'),list):
            self.waiting('游戏已重启，等待完整持仓快照','recovering_session');return
        try:command=load(self.root/'commands'/sid/(action+'.json'))
        except (OSError,ValueError):
            self.waiting('游戏已重启，旧命令文件暂时不可读；没有重发指令','recovering_session');return
        if command.get('execute') is not False:
            self.waiting('游戏已重启，但旧命令仍可能待执行；等待游戏读取，不重发','recovering_session');return
        pos=next((p for p in player['positions'] if p.get('stockId')==sid),{})
        if dec(pos.get('sharesOwned'))>0 and dec(pos.get('sharesShorted'))>0:
            self.waiting('游戏已重启，旧指令股票存在双向持仓，需要核对');return
        started=int(session['startedAtMs']);written=int(market['writtenAtMs'])
        if now-started<5000 or written<started:
            self.waiting('等待重启后的新行情完成同步','recovering_session');return
        position_signature=tuple(str(dec(pos.get(k))) for k in ('sharesOwned','sharesShorted','averageBuyPrice','averageShortPrice'))
        signature=(session['pid'],started,position_signature)
        if not self.restart_probe or self.restart_probe['signature']!=signature:
            self.restart_probe={'signature':signature,'observedAt':now,'writtenAt':written,'sequence':market.get('sequence')}
            self.waiting('已识别游戏重启，核对连续持仓快照；旧指令不会重发','recovering_session');return
        probe=self.restart_probe
        if now-probe['observedAt']<1000 or written<=probe['writtenAt'] or market.get('sequence')==probe['sequence']:
            self.waiting('等待第二份独立的新行情确认持仓','recovering_session');return
        record=dict(pending,recoveredAt=now,outcome='unknown-after-game-restart',replayed=False,newGameSession=session,currentPosition=pos,exportSequence=market.get('sequence'))
        self.state.setdefault('archivedPending',[]).append(record)
        self.pending_log(pending).update(status='旧游戏会话回报不可确认，已归档且未重发',recoveredAt=now)
        if pending.get('eventId'):
            self.state.setdefault('recoveryBlockedStocks',{})[sid]='旧事件指令结果无法确认，请先核对该股的事件仓位'
            book=self.state.get('eventBooks',{}).get(pending['eventId'])
            if book:book['recoveryRequired']=True
        self.state.setdefault('entryBlockedUntil',{})[sid]=now+90000
        self.state['active']=[key for key in self.state['active'] if not key.startswith(sid+'|')]
        self.state['pending']=None
        self.state['runtimePhase']='watching'
        self.state['recoveryStatus']='旧会话指令已归档为结果未知，未重发；继续按当前账户状态判断'
        self.state['lastRecovery']=record
        self.last_sequence=None;self.restart_probe=None
        self.persist()
    def persist(self, force=True):
        if not force and time.monotonic()-self.last_save<2: return
        self.state['heartbeat']=int(time.time()*1000)
        try:
            save(self.path,self.state,retry_replace=True)
        except PermissionError:
            if force:raise
            # A heartbeat is not an order. The next attempt includes the full
            # state; every submission still requires a successful forced save.
            return
        self.last_save=time.monotonic()
    def step(self, market, history, now):
        state=self.state; pending=state['pending']
        if pending:
            if market.get('gameVersion')!='1.0.13' or not market.get('marketReady') or abs(now-int(market.get('writtenAtMs') or 0))>15000:
                self.waiting('保留旧指令，等待新鲜且兼容的游戏行情；不会重发','waiting_for_market');return
            session=self.session()
            old_session=pending.get('gameSession')
            changed=not old_session or (session and (session.get('pid'),session.get('startedAtMs'))!=(old_session.get('pid'),old_session.get('startedAtMs')))
            restarted=bool(session and changed and int(session.get('startedAtMs') or 0)>int(pending['sentAt'])+1000)
            matches=[r for r in market.get('commandResults',[]) if r.get('id')==pending['id'] and int(r.get('run') or 0)>pending['baseline'] and int(r.get('receivedAtMs') or 0)>=pending['sentAt']-5000 and (not restarted or int(r.get('receivedAtMs') or 0)<session['startedAtMs'])]
            if restarted and not matches:
                self.recover_restarted_game(market,now,session);return
            if matches:
                result=max(matches,key=lambda r:int(r.get('run') or 0))
                if result.get('status') in ('done','failed') and int(result.get('finishedAtMs') or 0)>0:
                    self.pending_log(pending).update(status=result['status'],reason_result=result.get('reason',''),run=result.get('run'),finishedAt=result['finishedAtMs'])
                    if result['status']=='failed':
                        state['pending']=None; state['stopReason']='游戏返回交易失败：'+str(result.get('reason','未知原因')); self.paused=True; self.persist(); return
                    pending['finishedAt']=int(result['finishedAtMs'])
            if pending.get('finishedAt'):
                sid, action=pending['id'].split('/')
                pos=next((p for p in (market.get('player') or {}).get('positions',[]) if p.get('stockId')==sid),{})
                field='sharesShorted' if action in ('shortpercent','covermax') else 'sharesOwned'
                before=dec(pending.get('beforeShares'))
                after=dec(pos.get(field))
                updated=after>before if action in ('buypercent','buymax','shortpercent') else after<before
                if 'beforeShares' not in pending:
                    state['stopReason']='旧指令缺少持仓基线，需要核对后处理';self.paused=True; self.persist(); return
                if int(market.get('writtenAtMs') or 0)>pending['finishedAt'] and updated:
                    event=pending.get('eventId')
                    if event and pending.get('eventManaged'):
                        book=state['eventBooks'][event]
                        if action in ('buypercent','buymax'):
                            with localcontext() as ctx:
                                ctx.prec=50
                                cost=max(dec(0),after*dec(pos.get('averageBuyPrice'))-before*dec(pending.get('beforeAverage')))
                                book['spent']=str(dec(book['spent'])+cost)
                                book['grossSpent']=str(dec(book.get('grossSpent'))+cost)
                            if pending.get('signalAction')=='事件波段买回':book['waveOut']=False
                            else:book['coreShares']=str(max(dec(book.get('coreShares')),after/2))
                        elif action=='sellpercent':
                            book['spent']=str(max(dec(0),dec(book['spent'])-(before-after)*dec(pending.get('beforeAverage'))))
                            if pending.get('signalAction')=='事件波段卖出':
                                book['waveOut']=True;book['waveExitPrice']=pending['quotedPrice']
                            else:book['halfTaken']=True
                        elif action=='sellmax':book['closed']=True
                    elif event:
                        if pending.get('signalAction')=='重置扫货':
                            state.setdefault('resetDone',[]).append(event); state['resetHold']=True
                        else: state.setdefault('eventDone',[]).append(event)
                    if action in ('sellmax','covermax') or pending.get('signalAction')=='事件波段卖出':
                        state.setdefault('entryBlockedUntil',{})[sid]=now+90000
                    if action=='sellmax' and sid=='$PLAIN': state['resetHold']=False
                    state['pending']=None;state['runtimePhase']='watching';state['recoveryStatus']='';self.persist(); return
            if state['pending'] and now-pending['sentAt']>20000:
                self.waiting('指令超过20秒未确认：正在核对结果，暂不提交新交易，也不会重发')
            elif state['pending']:
                self.waiting('等待游戏成交回报及持仓更新')
            return
        if self.paused: return
        if market.get('gameVersion')!='1.0.13' or not market.get('marketReady') or abs(now-int(market.get('writtenAtMs') or 0))>15000:
            self.waiting('等待新鲜且兼容的游戏行情','waiting_for_market')
            return
        state['runtimePhase']='watching';state['recoveryStatus']=''
        sequence=(market.get('sequence'),market.get('writtenAtMs'))
        if market.get('sequence') is not None and sequence==self.last_sequence:
            self.persist(force=False); return
        self.last_sequence=sequence
        for pos in (market.get('player') or {}).get('positions',[]):
            if dec(pos.get('sharesOwned'))>0 and dec(pos.get('sharesShorted'))>0:
                state['log'].append({'time':now,'id':pos.get('stockId'),'status':'暂停：同一股票同时存在多空持仓'}); self.paused=True; self.persist(); return
        if 'protectedShorts' not in state:
            state['protectedShorts']=[p['stockId'] for p in (market.get('player') or {}).get('positions',[]) if dec(p.get('sharesShorted'))>0] if not state.get('manageExistingShorts',False) else []
        books=state.setdefault('eventBooks',{})
        known={e['occurrenceId']:e for e in state.get('knownEvents',[]) if e.get('occurrenceId') and (int(e.get('scheduledAtMs') or 0)>now-15*60*1000 or (e['occurrenceId'] in books and not books[e['occurrenceId']].get('closed')))}
        current_events={e['occurrenceId']:e for e in market.get('scheduledNews',[]) if e.get('occurrenceId')}
        for eid,e in known.items():
            if eid not in current_events and now<int(e.get('scheduledAtMs') or 0)-2000:e['_missingBefore']=True
        known.update({eid:dict(e,_lastSeenAt=now,_missingBefore=False) for eid,e in current_events.items()})
        state['knownEvents']=list(known.values())
        signals=calculate(market,history,now,include_short=True,entry_percent=25,protected_shorts=state['protectedShorts'],known_events=state['knownEvents'],reset_done=state.get('resetDone',[]),reset_hold=state.get('resetHold',False),event_done=state.get('eventDone',[]))['signals']
        event_signals,event_stocks=plan_events(market,state['knownEvents'],books,now,GAME_MANAGED,history)
        signals=[s for s in signals if s['id'] not in event_stocks]+event_signals
        active=set(state['active']); current={s['id']+'|'+s['action'] for s in signals}
        active.intersection_update(current)
        state['active']=sorted(active)
        def priority(x):
            return (0 if x['action'] in ('卖出提示','平空提示','事件平空','事件分批止盈','事件清仓','事件波段卖出') else 1 if x['action']=='重置扫货' else 2 if x.get('eventId') else 3, x['id'])
        for sig in sorted(signals,key=priority):
            sig=apply_buy_policy(sig,market)
            key=sig['id']+'|'+sig['action']
            if key in active and not sig.get('eventManaged'): continue
            sid=sig['id']
            if sid in state.get('recoveryBlockedStocks',{}):continue
            if sid in GAME_MANAGED: continue
            if not re.fullmatch(r'\$[A-Za-z0-9_]+',sid): continue
            mapping={'买入观察':'buypercent','重置扫货':'buypercent','卖出提示':'sellmax','做空观察':'shortpercent','平空提示':'covermax','事件建仓':'buypercent','事件波段买回':'buypercent','事件波段卖出':'sellpercent','事件平空':'covermax','事件分批止盈':'sellpercent','事件清仓':'sellmax'}
            action=mapping.get(sig['action'])
            if not action: continue
            if action=='buypercent':action=sig['commandAction']
            opening=action in ('buypercent','buymax','shortpercent')
            pos=next((p for p in (market.get('player') or {}).get('positions',[]) if p.get('stockId')==sid),{})
            long=dec(pos.get('sharesOwned')); short=dec(pos.get('sharesShorted'))
            if (action in ('buypercent','buymax') and short>0) or (action=='shortpercent' and long>0) or (action in ('sellmax','sellpercent') and long<=0) or (action=='covermax' and short<=0):
                continue
            if opening and now < state.get('entryBlockedUntil',{}).get(sid,0): continue
            cooldown='nextShortAtMs' if action=='shortpercent' else 'nextBuyAtMs'
            if opening and (int((market.get('player') or {}).get(cooldown) or 0)>now or dec(sig['size'])<=0):
                if sig.get('eventId'):
                    continue
                skipped=state.setdefault('skipLoggedAt',{})
                if now-skipped.get(key,0)>=60000:
                    state['log'].append({'time':now,'id':sid,'action':action,'status':'等待：冷却或可开仓股数，下一行情重新判断','signal':sig['reason']}); skipped[key]=now
                continue
            cmdid=sid+'/'+action; path=self.root/'commands'/sid/(action+'.json')
            if not path.is_file() or load(path).get('execute') is not False:
                self.paused=True; state['log'].append({'time':now,'id':cmdid,'status':'暂停：命令文件不可用或有未处理指令'}); self.persist(); return
            baseline=max([int(x.get('run') or 0) for x in market.get('commandResults',[]) if x.get('id')==cmdid] or [0])
            state['pending']={'id':cmdid,'sentAt':now,'baseline':baseline,'beforeShares':str(short if action in ('shortpercent','covermax') else long),'beforeAverage':str(pos.get('averageBuyPrice',0)),'quotedPrice':sig['price'],'eventId':sig.get('eventId'),'eventManaged':sig.get('eventManaged',False),'signalAction':sig['action'],'gameSession':self.session(),'exportSequence':market.get('sequence')}
            active.add(key); state['active']=sorted(active)
            state['log'].append({'time':now,'id':cmdid,'status':'已提交，等待游戏结果','signal':sig['reason'],'quotedPrice':sig['price']})
            self.persist() # On crash, never submit the same order again automatically.
            save(path,{'execute':True, **({'percent':float(sig.get('percent') or 25)} if action in ('buypercent','shortpercent','sellpercent') else {})})
            print('已提交',cmdid,'，等待游戏确认。',flush=True)
            return
        self.persist(force=False)

def main():
    prepare_runtime()
    import msvcrt
    lock=(ROOT/'自动交易.lock').open('a+b')
    try:
        lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError: raise RuntimeError('自动交易已经运行。')
    trader=Trader(session_provider=GameSessionProbe())
    trader.state['runtimeStatus']='运行中'
    trader.state['stopReason']=''
    trader.state['runtimePhase']='awaiting_result' if trader.state['pending'] else 'watching'
    trader.persist()
    print('自动交易已启动：每次开仓使用现金的 25%，分别管理多头与空头。',flush=True)
    print('关闭此窗口停止。每笔结果见 自动交易记录.json。',flush=True)
    read_error = False
    history_key=None
    history={}
    while not trader.paused:
        now=int(time.time()*1000)
        if STOP.exists():
            trader.state['stopReason']='已按停止请求退出；未确认指令记录仍保留' if trader.state['pending'] else '已按停止请求退出'
            diagnostic('收到停止请求，自动交易已停止。')
            break
        try:
            market=load(MODS/'export/market.json')
        except (OSError,ValueError,KeyError) as e:
            if not read_error:
                diagnostic('行情暂时无法读取，等待下一次更新：'+repr(e)); read_error=True
            trader.waiting('行情暂时不可读，等待恢复；未确认指令仍保留','waiting_for_market')
            time.sleep(1)
            continue
        if read_error:
            diagnostic('行情读取已恢复。'); read_error=False
        next_key=(market.get('sequence'),market.get('writtenAtMs'))
        if next_key!=history_key:
            try:
                history=load(MODS/'export/history.json')
                history_key=next_key
            except (OSError,ValueError):
                history={}
        try:
            trader.step(market,history,now)
        except Exception:
            trader.state['stopReason']='处理行情或保存记录失败，已停止提交新指令；详情见运行日志'
            diagnostic('处理或写入失败，已停止：\n'+traceback.format_exc())
            break
        time.sleep(0.25)
    trader.state['runtimeStatus']='已停止'
    try: trader.persist()
    except OSError: diagnostic('无法更新停止状态。')
    STOP.unlink(missing_ok=True)
    print('自动交易已停止。',flush=True)
    lock.close()

if __name__=='__main__':
    try: main()
    except Exception:
        diagnostic('自动交易未启动：\n'+traceback.format_exc())
