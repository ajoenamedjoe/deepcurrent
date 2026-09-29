"""Mock Unusual Whales API (synthetic payloads in the shape of the public endpoints) so the full server pipeline can be exercised without a token."""
import json, random, sys, os
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixture import UNIVERSE

random.seed(11)
TICKERS = [r["ticker"] for r in UNIVERSE] + ["BLMP","NOVQ","KRLZ","PYXO","TUNQ","WRNX","GLYQ","MORZ","FENQ","ZORB"]

def synth(t, i):
    base = dict(UNIVERSE[i % len(UNIVERSE)])
    base["ticker"] = t
    base["full_name"] = t + " INC"
    px = round(random.uniform(20, 600), 2)
    base["close"] = str(px); base["prev_close"] = str(round(px * random.uniform(.96,1.04),2))
    sign = random.choice([1,1,-1])
    base["net_call_premium"] = str(round(random.uniform(2e6,8e7)*sign,2))
    base["net_put_premium"] = str(round(random.uniform(-2e7,2e7),2))
    base["call_open_interest"] = int(random.uniform(3e5,3e6))
    base["prev_call_oi"] = int(base["call_open_interest"]*random.uniform(.90,1.0))
    base["put_open_interest"] = int(random.uniform(2e5,2e6))
    base["prev_put_oi"] = int(base["put_open_interest"]*random.uniform(.92,1.02))
    base["stock_volume"] = int(random.uniform(5e6,8e7))
    base["marketcap"] = str(int(random.uniform(5e9, 3e12)))
    base["relative_volume"] = str(round(random.uniform(.4,2.2),3))
    base["iv_rank"] = str(round(random.uniform(5,95),2))
    base["next_earnings_date"] = str(date.today()+timedelta(days=random.choice([4,20,60,None] and [4,20,60])))
    return base

UNI = [synth(t, i) for i, t in enumerate(TICKERS)]

def candles(px):
    out=[]; c=px*0.85; d=date.today()-timedelta(days=190)
    for i in range(130):
        c *= (1+random.uniform(-0.02,0.024))
        hi=c*(1+random.uniform(0,.015)); lo=c*(1-random.uniform(0,.015))
        out.append({"date":str(d+timedelta(days=i)),"open":round(c,2),"high":round(hi,2),
                    "low":round(lo,2),"close":round(c,2),"volume":int(random.uniform(1e6,9e7))})
    return out

class H(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*a): pass
    def j(self, obj, code=200):
        b=json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p=urllib.parse.urlparse(self.path).path
        auth=self.headers.get("Authorization","")
        if not auth.startswith("Bearer "): return self.j({"error":"unauthorized"},401)
        if p=="/api/screener/stocks": return self.j({"data":UNI})
        if p=="/api/option-trades/flow-alerts":
            today=date.today()
            return self.j({"data":[{
                "ticker":random.choice(TICKERS),"type":random.choice(["call","put"]),
                "strike":str(round(random.uniform(20,600),1)),
                "expiry":str(today+timedelta(days=random.choice([5,9,12,25,35,60,120]))),
                "total_premium":str(round(random.uniform(6e4,4e6),2)),
                "volume":random.randint(500,9000),"open_interest":random.randint(200,60000),
                "volume_oi_ratio":str(round(random.uniform(.1,6),3)),
                "alert_rule":random.choice(["RepeatedHits","AscendingFill","SweepsFollowedByFloor"]),
                "has_sweep":random.choice([True,False]),"has_floor":False,
                "all_opening_trades":random.choice([True,False]),"has_multileg":False,
                "underlying_price":str(round(random.uniform(20,600),2)),
                "created_at":"2031-03-14T19:41:36Z"} for _ in range(90)]})
        if p=="/api/darkpool/recent":
            return self.j({"data":[{
                "ticker":random.choice(TICKERS),"price":str(round(random.uniform(20,600),2)),
                "size":random.randint(5000,900000),"premium":str(round(random.uniform(2e6,6e8),2)),
                "volume":random.randint(5_000_000,90_000_000),
                "nbbo_bid":"100.00","nbbo_ask":"100.10","sector":"Technology",
                "sale_cond_codes":"","executed_at":"2031-03-14T20:37:52Z"} for _ in range(120)]})
        if p=="/api/market/market-tide":
            return self.j({"data":[{"timestamp":f"2031-03-14T{13+i//12:02d}:{(i%12)*5:02d}:00Z",
                "net_call_premium":str(round(random.uniform(-4e8,9e8),2)),
                "net_put_premium":str(round(random.uniform(-3e8,3e8),2))} for i in range(70)]})
        if "/gex-levels" in p:
            s=round(random.uniform(20,600),2)
            return self.j({"data":{"call_wall":str(round(s*1.06,2)),"put_wall":str(round(s*0.94,2)),
                "gamma_flip":str(round(s*random.uniform(.97,1.03),2)),"gamma_magnet":str(s),
                "date":"2031-03-14","source":"vol"}})
        if "/ohlc/" in p:
            return self.j({"data":candles(random.uniform(20,600))})
        return self.j({"error":"unknown path","path":p},404)

if __name__=="__main__":
    ThreadingHTTPServer(("127.0.0.1",9911),H).serve_forever()
