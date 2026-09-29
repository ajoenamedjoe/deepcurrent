import sys, os, math, json, tempfile
os.environ.setdefault("SWING_DB", os.path.join(tempfile.mkdtemp(), "swing_test.db"))   # never the real history
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server as S
from fixture import UNIVERSE, GEX_ACME

fails = []
def check(name, cond, got=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{got}]" if got else ""))
    if not cond: fails.append(name)

print("\n== num() coercion ==")
check("string float", S.num("41278390.00") == 41278390.0)
check("negative string", S.num("-6613045.00") == -6613045.0)
check("None -> default", S.num(None, 7.0) == 7.0)
check("garbage -> default", S.num("n/a", 0.0) == 0.0)
check("int passthrough", S.num(28416530) == 28416530.0)

print("\n== flow: direction sign ==")
for r in UNIVERSE:
    v, d = S.score_flow(r)
    print(f"    {r['ticker']:5} flow={v:+.3f}  net_dir={d.get('directional',0)/1e6:+.1f}M  skew={d.get('skew',0):+.2f}")
acme_f, _ = S.score_flow(UNIVERSE[0])
volt_f, volt_d = S.score_flow(UNIVERSE[3])
check("ACME bullish flow (calls bought, puts sold)", acme_f > 0.2, f"{acme_f:+.3f}")
check("VOLT bearish flow (net put premium positive = puts bought)", volt_f < 0, f"{volt_f:+.3f}")
check("flow bounded to [-1,1]", all(-1 <= S.score_flow(r)[0] <= 1 for r in UNIVERSE))

print("\n== oi: build direction ==")
for r in UNIVERSE:
    v, d = S.score_oi(r)
    print(f"    {r['ticker']:5} oi={v:+.3f}  callOI {d['call_oi_chg']*100:+.2f}%  putOI {d['put_oi_chg']*100:+.2f}%")
check("ACME call OI grew faster than put OI -> positive", S.score_oi(UNIVERSE[0])[0] > 0)
check("VOLT both grew, calls faster -> positive", S.score_oi(UNIVERSE[3])[0] > 0)
check("oi bounded", all(-1 <= S.score_oi(r)[0] <= 1 for r in UNIVERSE))

print("\n== gamma: spot vs walls ==")
r = dict(UNIVERSE[0])
g_above, gd = S.score_gamma(r, {k: S.num(v) for k, v in GEX_ACME.items()})
print(f"    ACME spot 187.42 flip 183.5 walls 180/200 -> gamma={g_above:+.3f} {gd}")
check("above flip with room up -> positive", g_above > 0, f"{g_above:+.3f}")
g_below, _ = S.score_gamma(r, {"call_wall":195.0,"put_wall":170.0,"gamma_flip":192.0})
check("below flip -> negative", g_below < 0, f"{g_below:+.3f}")
check("no gex falls back to screener tilt", abs(S.score_gamma(r, None)[0]) <= 0.4)

print("\n== technicals ==")
closes = [100 + i*0.5 for i in range(60)]              # clean uptrend
t_up = {"close":closes[-1],"sma20":S.sma(closes,20),"sma50":S.sma(closes,50),
        "rsi14":S.rsi(closes,14),"atr14":1.0,"high20":closes[-1],"low20":closes[-20],"spark":closes[-40:]}
v_up, d_up = S.score_tech(r, t_up)
print(f"    uptrend  rsi={d_up['rsi14']:.1f} -> tech={v_up:+.3f}")
check("uptrend but RSI pegged at 100 -> penalised, not max", v_up < 0.6, f"{v_up:+.3f}")

closes_dn = [130 - i*0.5 for i in range(60)]
t_dn = {"close":closes_dn[-1],"sma20":S.sma(closes_dn,20),"sma50":S.sma(closes_dn,50),
        "rsi14":S.rsi(closes_dn,14),"atr14":1.0,"high20":closes_dn[-20],"low20":closes_dn[-1],"spark":[]}
v_dn, d_dn = S.score_tech(r, t_dn)
print(f"    downtrend rsi={d_dn['rsi14']:.1f} -> tech={v_dn:+.3f}")
check("downtrend -> negative", v_dn < -0.3, f"{v_dn:+.3f}")

import random
random.seed(7)
ch = [100.0]
for _ in range(59): ch.append(ch[-1] * (1 + random.uniform(-0.012, 0.012)))
t_ch = {"close":ch[-1],"sma20":S.sma(ch,20),"sma50":S.sma(ch,50),"rsi14":S.rsi(ch,14),
        "atr14":1.2,"high20":max(ch[-20:]),"low20":min(ch[-20:]),"spark":[]}
v_ch, _ = S.score_tech(r, t_ch)
check("choppy tape -> weak signal", abs(v_ch) < 0.75, f"{v_ch:+.3f}")
check("no technicals -> 0", S.score_tech(r, None)[0] == 0.0)

print("\n== RSI / ATR / SMA correctness ==")
# RSI on a monotonic rise must be 100; on a monotonic fall, 0.
check("RSI all-up == 100", abs(S.rsi([1+i for i in range(30)],14) - 100.0) < 1e-6)
check("RSI all-down == 0", abs(S.rsi([100-i for i in range(30)],14) - 0.0) < 1e-6)
check("RSI too-short -> None", S.rsi([1,2,3],14) is None)
h=[10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25]
l=[9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24]
c=[9.5,10.5,11.5,12.5,13.5,14.5,15.5,16.5,17.5,18.5,19.5,20.5,21.5,22.5,23.5,24.5]
a = S.atr(h,l,c,14)
check("ATR positive and near true range", a is not None and 0.5 < a < 3.0, f"{a:.3f}")
check("SMA20 of 1..20 == 10.5", abs(S.sma(list(range(1,21)),20) - 10.5) < 1e-9)
check("SMA short -> None", S.sma([1,2],20) is None)

print("\n== rows() unwrapping ==")
check("data key", S.rows({"data":[1,2]}) == [1,2])
check("result key", S.rows({"result":[3]}) == [3])
check("bare list", S.rows([9]) == [9])
check("None -> []", S.rows(None) == [])
check("dict with neither -> []", S.rows({"x":1}) == [])

print("\n== horizon tagging ==")
abt = {"ACME":[{"dte":7},{"dte":9},{"dte":11}]}
h1 = S.horizon_for("ACME", abt, 187, 200, 5)
check("median DTE 9 -> SHORT", h1["label"]=="SHORT" and h1["src"]=="flow", json.dumps(h1))
abt2 = {"ACME":[{"dte":30},{"dte":35},{"dte":40}]}
check("median DTE 35 -> SWING", S.horizon_for("ACME",abt2,187,200,5)["label"]=="SWING")
abt3 = {"ACME":[{"dte":90},{"dte":120}]}
check("median DTE 105 -> POSITION", S.horizon_for("ACME",abt3,187,200,5)["label"]=="POSITION")
h4 = S.horizon_for("ZZZ", {}, 100, 110, 2.0)   # 10/2*1.6 = 8 days
check("no flow -> ATR-derived SHORT", h4["label"]=="SHORT" and h4["src"]=="atr", json.dumps(h4))
h5 = S.horizon_for("ZZZ", {}, 100, 130, 1.0)   # 30/1*1.6 = 48 days
check("far target -> POSITION via ATR", h5["label"]=="POSITION", json.dumps(h5))

print("\n== days_to ==")
check("bad date -> None", S.days_to("not-a-date") is None)
check("None -> None", S.days_to(None) is None)
check("past date negative", S.days_to("2020-01-01") < 0)

print("\n== safe_div / clamp guards ==")
check("div by zero", S.safe_div(1,0) == 0.0)
check("clamp hi", S.clamp(5.0) == 1.0)
check("clamp lo", S.clamp(-5.0) == -1.0)
check("tanh overflow-safe", S.tanh(1e9) == 1.0)

print("\n== weights sum to 100 ==")
tot = S.CFG["w_flow"]+S.CFG["w_oi"]+S.CFG["w_dark"]+S.CFG["w_gamma"]+S.CFG["w_tech"]
check("weights == 100", abs(tot-100.0) < 1e-9, str(tot))
check("max achievable |score| == 100", abs(tot) == 100)

print("\n" + ("="*54))
if fails:
    print(f"  {len(fails)} FAILURES: " + ", ".join(fails))
    sys.exit(1)
print("  ALL MODEL CHECKS PASSED")
