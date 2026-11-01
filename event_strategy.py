"""Persistent event-specific plans, using exported data only."""
from decimal import Decimal, localcontext
from strategy import dec

def plan_events(market, events, books, now, excluded=(), history=None):
    signals=[]; managed=set()
    if not market.get('marketReady') or abs(now-int(market.get('writtenAtMs') or 0))>15000:
        return signals,managed
    if market.get('lastPriceUpdateAtMs') and abs(now-int(market['lastPriceUpdateAtMs']))>15000:
        return signals,managed
    with localcontext() as ctx:
        ctx.prec=50
        stocks={s['stockId']:s for s in market.get('stocks',[])}
        positions={p['stockId']:p for p in (market.get('player') or {}).get('positions',[])}
        cash=max(dec((market.get('player') or {}).get('cash')),Decimal(0))
        candidates=sorted([e for e in events if str(e.get('occurrenceId','')).startswith('event:') and e.get('direction')=='pump'],key=lambda e:int(e.get('scheduledAtMs') or 0))
        for e in candidates:
            eid=e['occurrenceId'];sid=e.get('stockId');at=int(e.get('scheduledAtMs') or 0)
            if sid in excluded or sid in managed or not at:continue
            stock=stocks.get(sid,{})
            if not stock.get('unlocked'):continue
            price=dec(stock.get('price'));target=dec(e.get('targetPrice'))
            if price<=0 or target<=0:continue
            book=books.get(eid)
            live=any(x.get('occurrenceId')==eid and x.get('scheduledAtMs')==at and dec(x.get('targetPrice'))==target for x in market.get('scheduledNews',[]))
            if book is None and not live:continue
            freeze=0<at-now<=90*60000
            if book is None and (now>=at or (target<price*2 and not freeze)):continue
            pos=positions.get(sid,{})
            owned=dec(pos.get('sharesOwned'));short=dec(pos.get('sharesShorted'));avg=dec(pos.get('averageBuyPrice'))
            if book is None:
                book={'stockId':sid,'budget':str(cash+owned*avg),'spent':str(owned*avg),'peak':str(price),'halfTaken':False,'closed':False}
                books[eid]=book
            managed.add(sid)
            if book.get('scheduledAt') and book['scheduledAt']!=at:book.pop('postEventPeak',None)
            book.update(target=str(target),scheduledAt=at,freezeStart=at-90*60000,price=str(price),currentlyAnnounced=live,budget=str(dec(book['spent'])+cash),budgetMode='available-cash')
            if book.get('closed'):continue
            book.setdefault('coreShares',str(owned/2))
            book['peak']=str(max(dec(book.get('peak')),price) if owned>0 else price)
            if now>=at:
                book['postEventPeak']=str(max(dec(book.get('postEventPeak')),price))
            action=None;reason='可做波段，等待价格条件' if now<at else '已到计划时间，观察实际价格；尚未证明事件完成';percent=None;size=Decimal(0);command=None
            if now<at and not live:
                book['status']='公告已不在当前游戏导出中，暂停加仓；保留记录待核对'
                continue
            if freeze:
                book['status']='排期前90分钟冻结：暂停该股自动买卖，保留当前持仓'
                continue
            samples=[]
            if history and abs(now-int(history.get('writtenAtMs') or 0))<=15000:
                samples=[x for s in history.get('stocks',[]) if s.get('stockId')==sid for x in s.get('samples',[]) if now-15*60000<=int(x.get('timeMs') or 0)<=now and dec(x.get('price'))>0]
            mean=None
            if len(samples)>=60 and max(x['timeMs'] for x in samples)-min(x['timeMs'] for x in samples)>=180000 and now-max(x['timeMs'] for x in samples)<=15000:
                mean=sum((dec(x['price']) for x in samples),Decimal(0))/len(samples)
            risk=any(x.get('stockId')==sid and x.get('direction')=='crash' and 0<int(x.get('scheduledAtMs') or 0)-now<=90*60000 for x in events)
            if risk:
                if owned>0:action='事件清仓';reason='下跌或重置事件临近，退出事件仓位';size=owned
                else:book['closed']=True
            elif short>0:
                action='事件平空';reason='已有拉升排期，先退出相反方向空头';size=short
            elif owned>0:
                if now<at-90*60000 and mean and not book.get('waveOut') and owned>dec(book['coreShares']) and price>=mean*Decimal('1.05') and price>avg:
                    size=min(owned/2,owned-dec(book['coreShares']));percent=float(size/owned*100);action='事件波段卖出';reason='冻结窗口之外高于均价5%，兑现波段仓，保留事件底仓'
                elif now>=at and not book['halfTaken'] and price>=target*Decimal('0.95'):
                    action='事件分批止盈';percent=50;size=owned/2;reason='达到排期目标的95%，先兑现一半持仓'
                elif now>=at and price<=dec(book.get('postEventPeak'))*Decimal('0.92') and dec(book.get('postEventPeak'))>=avg*Decimal('1.20'):
                    action='事件清仓';size=owned;reason='事件高点已出现，较高点回撤8%，退出剩余持仓'
                elif now>=at+15*60000:
                    action='事件清仓';size=owned;reason='排期时间过去15分钟，结束本次事件交易'
            elif dec(book['spent'])>0 or now>=at:
                book['closed']=True
            wave_buy=bool(book.get('waveOut') and mean and price<=mean*Decimal('0.95') and price<dec(book.get('waveExitPrice')))
            if not action and not book['closed'] and not book['halfTaken'] and now<at-90*60000 and not risk and short<=0 and target>=price*2 and (not book.get('waveOut') or wave_buy):
                available=max(Decimal(0),dec(stock.get('availableShares')))
                cost=cash*Decimal('0.25')
                if cost>0 and cash>0 and available>0:
                    full=available*price*Decimal('1.05')<=cash
                    percent=None if full else 25;command='buymax' if full else 'buypercent'
                    action='事件波段买回' if wave_buy else '事件建仓';size=available if full else min(cost/price,available);reason=('冻结窗口之外低于均价5%，买回波段仓；' if wave_buy else '优先使用买入冷却；')+('按当前报价现金足以覆盖全部浮筹，最大买入' if full else '现金不足以覆盖全部浮筹，按25%现金分批')
            book['status']=reason if not book['closed'] else '本次事件已结束'
            if action:
                entry=action in ('事件建仓','事件波段买回')
                profit=(target-price)*size if entry else (price-avg)*size if action!='事件平空' else (dec(pos.get('averageShortPrice'))-price)*size
                signals.append({'stock':stock.get('name') or sid,'id':sid,'action':action,'reason':reason,'price':str(price),'target':str(target) if entry else None,'size':str(size),'profit':str(profit),'percent':percent,'commandAction':command,'eventId':eid,'eventManaged':True})
        return signals,managed
