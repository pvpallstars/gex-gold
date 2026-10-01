"""ดึง option chain ของ GC → คำนวณ GEX → เขียน data.json"""
import json, math, datetime as dt
import numpy as np, pandas as pd, requests, yfinance as yf
from scipy.stats import norm
from scipy.optimize import brentq

import gc_meta as M

SYMBOL = "GCZ6"          # เปลี่ยนตาม front-month ที่ใช้
MULT   = 100             # GC = $100 ต่อ 1 point
UA     = {"User-Agent": "Mozilla/5.0"}


# ───────────────────────── Black-76 ─────────────────────────
def b76_gamma(F, K, T, r, s):
    if T <= 0 or s <= 0:
        return 0.0
    d1 = (math.log(F / K) + 0.5 * s * s * T) / (s * math.sqrt(T))
    return math.exp(-r * T) * norm.pdf(d1) / (F * s * math.sqrt(T))


def b76_price(F, K, T, r, s, cp):
    if T <= 0 or s <= 0:
        return max(0.0, (F - K) if cp == 'C' else (K - F))
    d1 = (math.log(F / K) + 0.5 * s * s * T) / (s * math.sqrt(T))
    d2 = d1 - s * math.sqrt(T)
    df = math.exp(-r * T)
    return (df * (F * norm.cdf(d1) - K * norm.cdf(d2)) if cp == 'C'
            else df * (K * norm.cdf(-d2) - F * norm.cdf(-d1)))


def implied_vol(px, F, K, T, r, cp):
    try:
        return brentq(lambda s: b76_price(F, K, T, r, s, cp) - px,
                      1e-4, 5.0, maxiter=100)
    except Exception:
        return None


# ───────────────────────── sources ─────────────────────────
def fetch_cme(product_id="192", month="Z6"):
    """CME settlement endpoint (สาธารณะ แต่ path เปลี่ยนเป็นระยะ)"""
    url = ("https://www.cmegroup.com/CmeWS/mvc/Settlements/Options/"
           f"Settlements/{product_id}/OOF?monthYear=GC{month}&tradeDate=")
    j = requests.get(url, headers=UA, timeout=20).json()
    rows = []
    for s in j["settlements"]:
        if s["strike"] in ("", "Total"):
            continue
        rows.append(dict(
            strike=float(str(s["strike"]).replace(",", "")),
            type=s["type"][0].upper(),
            price=float(s["settle"]),
            oi=int(str(s.get("openInterest", "0")).replace(",", "") or 0),
            expiry=M.expiry_from_series(s.get("monthYear", f"GC{month}"))))
    return pd.DataFrame(rows), j.get("tradeDate")


def fetch_gld_fallback():
    """CME ล่ม → ใช้ GLD สเกลเป็นระดับทอง (แม่นน้อยกว่าชัดเจน)"""
    t = yf.Ticker("GLD")
    gld  = float(t.fast_info["last_price"])
    gold = float(yf.Ticker("GC=F").fast_info["last_price"])
    k = gold / gld
    exp = t.options[0]
    ch  = t.option_chain(exp)
    exp_dt = dt.datetime.strptime(exp, "%Y-%m-%d").replace(
        hour=15, minute=0, tzinfo=M.CT)
    out = []
    for df, cp in ((ch.calls, 'C'), (ch.puts, 'P')):
        for _, o in df.iterrows():
            out.append(dict(strike=round(o.strike * k / 5) * 5, type=cp,
                            price=float(o.lastPrice) * k,
                            oi=int(o.openInterest or 0), expiry=exp_dt))
    return pd.DataFrame(out), exp


# ───────────────────────── GEX ─────────────────────────
def build(chain, F, r):
    recs = []
    for _, o in chain.iterrows():
        if o.oi <= 0:
            continue
        T = M.year_fraction(o.expiry)
        iv = implied_vol(o.price, F, o.strike, T, r, o.type)
        if not iv or not (0.02 < iv < 3.0):
            continue
        g = b76_gamma(F, o.strike, T, r, iv)
        recs.append(dict(strike=o.strike, type=o.type, oi=o.oi, iv=iv, T=T,
                         gex=(1 if o.type == 'C' else -1)
                             * g * o.oi * MULT * F * F * 0.01))
    return pd.DataFrame(recs)


def net_at(df, F, r):
    tot = 0.0
    for _, o in df.iterrows():
        g = b76_gamma(F, o.strike, o["T"], r, o.iv)
        tot += (1 if o.type == 'C' else -1) * g * o.oi * MULT * F * F * 0.01
    return tot


def find_flip(df, F, r):
    grid = np.arange(F * 0.90, F * 1.10, F * 0.002)
    vals = [net_at(df, x, r) for x in grid]
    for i in range(1, len(vals)):
        if (vals[i-1] < 0 <= vals[i]) or (vals[i-1] > 0 >= vals[i]):
            x0, x1, y0, y1 = grid[i-1], grid[i], vals[i-1], vals[i]
            return float(x0 - y0 * (x1 - x0) / (y1 - y0))
    return None


def max_pain(chain):
    pain = {}
    for S in sorted(chain.strike.unique()):
        c = chain[(chain.type == 'C') & (chain.strike < S)]
        p = chain[(chain.type == 'P') & (chain.strike > S)]
        pain[S] = ((S - c.strike) * c.oi).sum() + ((p.strike - S) * p.oi).sum()
    return float(min(pain, key=pain.get))


# ───────────────────────── main ─────────────────────────
def main():
    r, r_src = M.risk_free()
    print(f"risk-free = {r:.3%} ({r_src})")

    stale = False
    try:
        chain, oi_date = fetch_cme()
        src = "CME GC"
    except Exception as e:
        print("CME ล้มเหลว:", e, "→ GLD fallback")
        chain, oi_date = fetch_gld_fallback()
        src, stale = "GLD (scaled)", True

    F = float(yf.Ticker("GC=F").fast_info["last_price"])
    S, basis, spot_src = M.spot_and_basis(F, SYMBOL, r)
    print(f"F={F:.2f}  S={S:.2f}  basis={basis:+.2f} ({spot_src})")

    exp = M.option_expiry(SYMBOL)
    dte = (exp.astimezone(M.UTC) - dt.datetime.now(M.UTC)).total_seconds() / 86400
    print(f"expiry {exp:%Y-%m-%d %H:%M %Z} · DTE {dte:.2f}")

    df = build(chain, F, r)
    if df.empty:
        raise SystemExit("ไม่มีแถวที่คำนวณ IV ได้ — ตรวจ chain ก่อน")

    by_strike = df.groupby("strike").gex.sum().sort_index()
    calls = df[df.type == 'C'].groupby("strike").gex.sum()
    puts  = df[df.type == 'P'].groupby("strike").gex.sum()
    cw, pw, mp = float(calls.idxmax()), float(puts.idxmin()), max_pain(chain)
    flip = find_flip(df, F, r)

    levels = [
        dict(price=cw, tag="Call Wall · Peak Call GEX", kind="call"),
        dict(price=mp, tag="Max Pain", kind="call" if mp > F else "put"),
        dict(price=pw, tag="Put Wall · Peak Put GEX", kind="put"),
    ]
    if flip:
        levels.append(dict(price=flip, tag="Gamma Flip", kind="flip"))
    for k in calls.nlargest(3).index:
        levels.append(dict(price=float(k), tag="Call GEX", kind="call"))
    for k in puts.nsmallest(3).index:
        levels.append(dict(price=float(k), tag="Put GEX", kind="put"))

    seen, uniq = set(), []
    for l in levels:
        key = round(l["price"], 1)
        if key not in seen:
            seen.add(key)
            l["spot_price"] = round(l["price"] - basis, 2)
            uniq.append(l)

    out = dict(
        symbol=SYMBOL, source=src, stale=stale,
        updated_at=dt.datetime.now(M.UTC).strftime("%Y-%m-%d %H:%M UTC"),
        oi_date=str(oi_date),
        future_price=F, spot_price=S,
        basis=round(basis, 2), basis_source=spot_src,
        risk_free=r, risk_free_source=r_src,
        expiry=exp.astimezone(M.UTC).isoformat(),
        expiry_ct=exp.strftime("%Y-%m-%d %H:%M %Z"),
        dte=round(dte, 2),
        net_gex=float(df.gex.sum()), gamma_flip=flip,
        max_pain=mp, call_wall=cw, put_wall=pw,
        total_oi=int(chain.oi.sum()),
        by_strike=[dict(strike=float(k), gex=float(v))
                   for k, v in by_strike.items()],
        levels=uniq)

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("เขียน data.json แล้ว · Net GEX =", f"{out['net_gex']/1e6:.1f} $mm")


if __name__ == "__main__":
    main()