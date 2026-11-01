"""Persistent paper account. Only reads game exports; all writes stay here."""
from decimal import Decimal, localcontext
import json
import os
from pathlib import Path
import threading
import time
from strategy import calculate, dec

class Paper:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else None
        self.latest = {'message': '等待新鲜行情初始化模拟账户。', 'signals': []}

    def save(self):
        tmp = self.path.with_suffix('.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def update(self, market, history, now=None):
        now = int(time.time()*1000) if now is None else now
        with self.lock, localcontext() as ctx:
            ctx.prec = 50
            written = int(market.get('writtenAtMs') or 0)
            if not market.get('marketReady') or not written or abs(now-written)>15000:
                self.latest = {'message': '行情未就绪或已过期，暂停模拟。', 'signals': []}
                return
            if self.state is None:
                initial = max(Decimal(0), dec((market.get('player') or {}).get('cash')))
                self.state = {'version': 1, 'startedAt': now, 'initialCash': str(initial), 'cash': str(initial),
                              'positions': {}, 'realized': '0', 'nextBuyAt': 0, 'active': [], 'log': []}
            state = self.state
            positions = [{'stockId': sid, 'sharesOwned': p['shares'], 'averageBuyPrice': p['average'], 'sharesShorted': '0'} for sid,p in state['positions'].items()]
            snapshot = dict(market)
            snapshot['player'] = dict(market.get('player') or {}, cash=state['cash'], positions=positions, nextBuyAtMs=0)
            result = calculate(snapshot, history, now)
            active = set(state['active'])
            current = set()
            # Process exits before entries, then deterministic stock order.
            for sig in sorted(result['signals'], key=lambda x: (x['action'] != '卖出提示', x['id'])):
                key = sig['id'] + '|' + sig['action']
                current.add(key)
                if key in active:
                    continue
                entry = {'time': now, 'stock': sig['stock'], 'id': sig['id'], 'action': sig['action'],
                         'price': sig['price'], 'reason': sig['reason'], 'status': '', 'shares': '0', 'realized': None}
                price = dec(sig['price']); sid = sig['id']
                if sig['action'] == '卖出提示' and sid in state['positions']:
                    pos = state['positions'].pop(sid); shares = dec(pos['shares'])
                    pnl = (price-dec(pos['average']))*shares
                    state['cash'] = str(dec(state['cash'])+shares*price)
                    state['realized'] = str(dec(state['realized'])+pnl)
                    entry.update(status='模拟卖出', shares=str(shares), realized=str(pnl))
                elif sig['action'] == '买入观察':
                    if now < state['nextBuyAt']:
                        entry['status'] = '未模拟成交：买入冷却中'
                    else:
                        stock = next((s for s in market.get('stocks', []) if s.get('stockId') == sid), {})
                        shares = min(dec(state['cash'])*Decimal('0.10')/price, max(Decimal(0),dec(stock.get('availableShares'))))
                        if shares <= 0:
                            entry['status'] = '未模拟成交：资金或可买股数不足'
                        else:
                            old = state['positions'].get(sid, {'shares':'0','average':'0'})
                            oldshares = dec(old['shares']); total = oldshares+shares
                            avg = (oldshares*dec(old['average'])+shares*price)/total
                            state['positions'][sid] = {'shares':str(total), 'average':str(avg), 'name':sig['stock']}
                            state['cash'] = str(dec(state['cash'])-shares*price)
                            cooldown = max(Decimal(0), dec((market.get('player') or {}).get('buyCooldownSeconds')))
                            state['nextBuyAt'] = now+int(cooldown*1000)
                            entry.update(status='模拟买入', shares=str(shares))
                else:
                    entry['status'] = '未模拟成交：没有模拟多头'
                state['log'].append(entry)
            state['active'] = sorted(current)
            self.save()
            self.latest = result

    def view(self, market):
        with self.lock, localcontext() as ctx:
            ctx.prec = 50
            if self.state is None:
                return {'message': self.latest.get('message'), 'signals': [], 'account': None, 'log': []}
            st = self.state
            prices = {s.get('stockId'): dec(s.get('price')) for s in market.get('stocks', [])}
            positions = []; value = Decimal(0); unpriced = False
            for sid, pos in st['positions'].items():
                price = prices.get(sid, Decimal(0)); shares = dec(pos['shares'])
                if price <= 0:
                    unpriced = True
                value += shares*price
                positions.append({'id':sid, 'name':pos['name'], 'shares':pos['shares'], 'average':pos['average'],
                                  'price':str(price) if price>0 else None, 'pnl':str((price-dec(pos['average']))*shares) if price>0 else None})
            equity = dec(st['cash'])+value
            account = {'startedAt':st['startedAt'], 'initialCash':st['initialCash'], 'cash':st['cash'],
                       'equity':str(equity) if not unpriced else None, 'totalPnl':str(equity-dec(st['initialCash'])) if not unpriced else None,
                       'realized':st['realized'], 'positions':positions, 'nextBuyAt':st['nextBuyAt']}
            return {'message': self.latest.get('message',''), 'signals': self.latest.get('signals',[]), 'account':account, 'log':list(reversed(st['log']))}
