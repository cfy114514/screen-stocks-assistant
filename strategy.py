"""Read-only hypothetical signals; never sends commands to the game."""
from decimal import Decimal, InvalidOperation, localcontext
import time

def dec(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else Decimal(0)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)

def apply_buy_policy(signal, market):
    if signal.get('action') not in ('买入观察','重置扫货','事件建仓','事件波段买回'):return signal
    s=dict(signal)
    with localcontext() as ctx:
        ctx.prec=50
        stock=next((x for x in market.get('stocks',[]) if x.get('stockId')==s['id']),{})
        price=dec(stock.get('price'));available=max(dec(stock.get('availableShares')),Decimal(0));cash=max(dec((market.get('player') or {}).get('cash')),Decimal(0))
        if price<=0:return s
        full=available>0 and available*price*Decimal('1.05')<=cash
        s['commandAction']='buymax' if full else 'buypercent';s['percent']=None if full else 25
        size=available if full else min(available,cash*Decimal('0.25')/price)
        s['size']=str(size);s['profit']=str((dec(s['target'])-price)*size) if s.get('target') is not None else None
        return s

def calculate(market, history, now_ms=None, include_short=False, entry_percent=10, protected_shorts=(), known_events=(), reset_done=(), reset_hold=False, event_done=()):
    with localcontext() as ctx:
        ctx.prec = 50
        now = int(time.time() * 1000) if now_ms is None else now_ms
        written = int(market.get('writtenAtMs') or 0)
        if not market.get('marketReady') or not written or abs(now - written) > 15000:
            return {'signals': [], 'message': '行情未就绪或已过期，暂停产生信号。'}
        if market.get('lastPriceUpdateAtMs') and abs(now-int(market['lastPriceUpdateAtMs']))>15000:
            return {'signals': [], 'message': '价格本身已过期，暂停产生信号。'}
        history_written = int(history.get('writtenAtMs') or 0)
        history_fresh = bool(history_written and abs(now-history_written) <= 15000)
        histories = {x.get('stockId'): x.get('samples', []) for x in history.get('stocks', [])} if history_fresh else {}
        player = market.get('player') or {}
        positions = {x.get('stockId'): x for x in player.get('positions', [])}
        events = market.get('scheduledNews') or []
        remembered=[e for e in known_events if int(e.get('scheduledAtMs') or 0)<=now and not e.get('_missingBefore')]
        by_id = {e.get('occurrenceId'):e for e in remembered+list(events) if e.get('occurrenceId')}
        events = list(by_id.values())
        cash = max(dec(player.get('cash')), Decimal(0))
        rows = []
        for stock in market.get('stocks', []):
            sid = stock.get('stockId'); price = dec(stock.get('price'))
            if not stock.get('unlocked') or not sid or price <= 0:
                continue
            pos = positions.get(sid) or {}; owned = dec(pos.get('sharesOwned')); short = dec(pos.get('sharesShorted'))
            if owned > 0 and short > 0:
                continue  # Ambiguous mixed position: execution layer pauses for review.
            upcoming = [e for e in events if e.get('stockId') == sid and int(e.get('scheduledAtMs') or 0) > now]
            resets = sorted((e for e in upcoming if str(e.get('occurrenceId','')).startswith('reset:')), key=lambda e: int(e['scheduledAtMs']))
            reset_near = bool(resets and int(resets[0]['scheduledAtMs']) - now <= 90 * 60 * 1000)
            crash_near = any(e.get('direction') == 'crash' and int(e['scheduledAtMs'])-now <= 90*60*1000 for e in upcoming)
            event_guard = any(e.get('direction') == 'pump' and now-15*60*1000 <= int(e.get('scheduledAtMs') or 0) for e in events if e.get('stockId') == sid)
            pumps = sorted((e for e in upcoming if str(e.get('occurrenceId','')).startswith('event:') and e.get('direction') == 'pump' and dec(e.get('targetPrice')) >= price * 2), key=lambda e: int(e['scheduledAtMs']))
            recent_resets = [e for e in events if sid == '$PLAIN' and e.get('stockId') == sid and str(e.get('occurrenceId','')).startswith('reset:') and 0 <= now-int(e.get('scheduledAtMs') or 0) <= 15*60*1000 and e.get('occurrenceId') not in reset_done and dec(stock.get('basePrice')) > 0 and price <= dec(stock.get('basePrice'))*Decimal('1.25')]
            sweep = bool(recent_resets and include_short and not reset_near and not crash_near)
            reset_time = max([int(e.get('scheduledAtMs') or 0) for e in events if e.get('stockId') == sid and str(e.get('occurrenceId','')).startswith('reset:') and int(e.get('scheduledAtMs') or 0) <= now] or [0])
            samples = [x for x in histories.get(sid, []) if max(now-15*60*1000,reset_time) <= int(x.get('timeMs') or 0) <= now and dec(x.get('price')) > 0]
            span = (max(int(x['timeMs']) for x in samples) - min(int(x['timeMs']) for x in samples)) if samples else 0
            mean = sum((dec(x['price']) for x in samples), Decimal(0))/len(samples) if len(samples) >= 60 and span >= 180000 and now-max(int(x['timeMs']) for x in samples)<=15000 else None
            action = reason = ''; target = None; profit = None; size = None
            if sweep and short > 0:
                action, reason = '平空提示', 'BASE 已到重置时间且价格接近基准价，平空准备扫货'
            elif owned > 0 and (reset_near or crash_near):
                action, reason = '卖出提示', '重置在 90 分钟内，考虑提前清仓' if reset_near else '下跌事件在 90 分钟内，提前清仓'
            elif sweep and short <= 0:
                action, reason, target = '重置扫货', 'BASE 到达已记录的重置时间，现价接近基准价；资金足够时最大买入，否则分批', None
            elif short > 0 and include_short and sid not in protected_shorts:
                avg = dec(pos.get('averageShortPrice'))
                if avg > 0 and price >= avg * Decimal('1.15'):
                    action, reason = '平空提示', '现价较做空均价上涨至少 15%，止损平空'
                elif mean is not None and price <= mean and avg > 0 and price < avg:
                    action, reason = '平空提示', '价格回落至近期均价且低于做空均价'
            elif owned > 0 and (any(e.get('stockId') == sid and str(e.get('occurrenceId','')).startswith('event:') and e.get('direction') == 'pump' and int(e.get('scheduledAtMs') or 0)+60000>now for e in events) or (sid == '$PLAIN' and reset_hold)):
                continue
            elif owned > 0 and mean is not None and price >= mean * Decimal('1.05') and dec(pos.get('averageBuyPrice')) > 0 and price > dec(pos.get('averageBuyPrice')):
                action, reason = '卖出提示', '现价高于近期均价至少 5%'
            elif short > 0:
                continue
            elif pumps and pumps[0]['occurrenceId'] in event_done:
                continue
            elif owned <= 0 and not crash_near and not reset_near and pumps and (not resets or int(pumps[0]['scheduledAtMs']) < int(resets[0]['scheduledAtMs'])):
                action, reason = '买入观察', '已公布拉升目标至少为现价 2 倍；目标并非成交保证'
                target = dec(pumps[0]['targetPrice'])
            elif owned <= 0 and not crash_near and not reset_near and mean is not None and price <= mean * Decimal('0.95'):
                action, reason, target = '买入观察', '现价低于近期均价至少 5%；假设回到均价', mean
            elif include_short and owned <= 0 and not (sid == '$PLAIN' and reset_hold) and mean is not None and price >= mean * Decimal('1.05') and not event_guard:
                action, reason, target = '做空观察', '现价高于近期均价至少 5%；假设回到均价', mean
            if not action:
                continue
            if action == '平空提示':
                size = short
                basis = dec(pos.get('averageShortPrice'))
                profit = (basis-price)*short if basis > 0 else None
            elif action == '卖出提示':
                size = owned
                basis = dec(pos.get('averageBuyPrice'))
                profit = (price-basis)*owned if basis > 0 else None
            else:
                percent = 25 if action == '重置扫货' else 10 if pumps and action == '买入观察' else entry_percent
                size = min(cash * dec(percent) / 100 / price, max(dec(stock.get('availableShares')), Decimal(0)))
                profit = None if target is None else (price-target)*size if action == '做空观察' else (target-price)*size
                cooldown_key = 'nextShortAtMs' if action == '做空观察' else 'nextBuyAtMs'
                if int(player.get(cooldown_key) or 0) > now:
                    reason += '；开仓仍在冷却'
            rows.append({'stock': stock.get('name') or sid, 'id': sid, 'action': action, 'reason': reason,
                         'price': str(price), 'target': str(target) if target is not None else None,
                         'size': str(size), 'profit': str(profit) if profit is not None else None,
                         'percent': percent if action in ('买入观察','做空观察','重置扫货') else None,
                         'eventId': recent_resets[0]['occurrenceId'] if sweep and action == '重置扫货' else pumps[0]['occurrenceId'] if pumps and action == '买入观察' else None})
        return {'signals': rows, 'message': '历史数据不足 3 分钟，均价信号暂不可用。' if not any(len(v)>=60 and (max(int(x.get('timeMs') or 0) for x in v)-min(int(x.get('timeMs') or 0) for x in v))>=180000 for v in histories.values() if v) else ''}
