"""ปฏิทิน CME + วันหมดอายุ GC + spot/basis + risk-free (คำนวณอัตโนมัติ)"""
import csv, io, math, datetime as dt
import numpy as np, requests
from zoneinfo import ZoneInfo
from pandas.tseries.holiday import (
    AbstractHolidayCalendar, Holiday, nearest_workday, GoodFriday,
    USMartinLutherKingJr, USPresidentsDay, USMemorialDay,
    USLaborDay, USThanksgivingDay,
)

CT  = ZoneInfo("America/Chicago")
UTC = dt.timezone.utc
UA  = {"User-Agent": "Mozilla/5.0"}


# ───────────────────────── 1. ปฏิทินวันหยุด COMEX ─────────────────────────
class CMEMetals(AbstractHolidayCalendar):
    """ต่างจาก US federal: มี Good Friday / ไม่มี Columbus, Veterans Day"""
    rules = [
        Holiday("NewYear", month=1, day=1, observance=nearest_workday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19,
                start_date="2022-01-01", observance=nearest_workday),
        Holiday("July4", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


_HOL_IDX = CMEMetals().holidays(start="2015-01-01", end="2040-12-31")
_HOL_D64 = _HOL_IDX.values.astype("datetime64[D]")
_HOL_SET = {d.date() for d in _HOL_IDX}
BDC      = np.busdaycalendar(holidays=_HOL_D64)

MONTH_CODE = dict(F=1, G=2, H=3, J=4, K=5, M=6, N=7, Q=8, U=9, V=10, X=11, Z=12)


def _bdays_of_month(y, m):
    start = np.datetime64(dt.date(y, m, 1))
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = np.datetime64(dt.date(ny, nm, 1))
    all_d = np.arange(start, end)
    return all_d[np.is_busday(all_d, busdaycal=BDC)]


def _prev_bday(d):
    return np.busday_offset(np.datetime64(d), -1,
                            roll="backward", busdaycal=BDC).astype(dt.date)


def _adjust(d):
    """กฎ CME: ถ้าตรงวันศุกร์ หรือเป็นวันก่อนวันหยุด → ถอย 1 วันทำการ"""
    for _ in range(6):
        if d.weekday() == 4 or (d + dt.timedelta(days=1)) in _HOL_SET:
            d = _prev_bday(d)
        else:
            return d
    return d


def parse_contract(symbol, today=None):
    """'GCZ6' / 'GCZ26' → (2026, 12)"""
    today = today or dt.date.today()
    m = MONTH_CODE[symbol[2].upper()]
    digits = symbol[3:]
    if len(digits) >= 2:
        y = 2000 + int(digits[:2])
    else:
        y = today.year - today.year % 10 + int(digits)
        while y < today.year - 1:
            y += 10
    return y, m


def option_expiry(symbol, today=None):
    """GC monthly (OG): วันทำการที่ 4 นับถอยหลังจากสิ้นเดือน *ก่อน* เดือนสัญญา, 13:30 CT"""
    y, m = parse_contract(symbol, today)
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    d = _adjust(_bdays_of_month(py, pm)[-4].astype(dt.date))
    return dt.datetime(d.year, d.month, d.day, 13, 30, tzinfo=CT)


def futures_last_trade(symbol, today=None):
    """GC futures: วันทำการที่ 3 นับถอยหลังจากสิ้นเดือนสัญญา, 13:30 CT"""
    y, m = parse_contract(symbol, today)
    d = _bdays_of_month(y, m)[-3].astype(dt.date)
    return dt.datetime(d.year, d.month, d.day, 13, 30, tzinfo=CT)


def year_fraction(expiry, now=None):
    """ACT/365 ระดับวินาที — floor 30 นาที กัน gamma ระเบิดช่วง 0DTE"""
    now = now or dt.datetime.now(UTC)
    secs = (expiry.astimezone(UTC) - now).total_seconds()
    return max(secs, 1800.0) / (365.0 * 86400.0)


def expiry_from_series(month_year, today=None):
    """'GCZ6' / 'OGZ6' ที่ CME ส่งกลับมา → วันหมดอายุ"""
    s = month_year.strip().upper()
    code = next((c for c in reversed(s) if c in MONTH_CODE), "Z")
    i = s.rindex(code)
    return option_expiry("GC" + s[i:], today)


# ───────────────────────── 2. risk-free rate ─────────────────────────
def risk_free(default=0.042):
    """US 3-month T-bill จาก FRED (ฟรี ไม่ต้องมี API key)"""
    try:
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO"
        txt = requests.get(url, headers=UA, timeout=12).text
        rows = list(csv.DictReader(io.StringIO(txt)))
        key = [k for k in rows[-1] if k.upper() == "DGS3MO"][0]
        for r in reversed(rows):
            if r[key] not in (".", "", None):
                return float(r[key]) / 100.0, "FRED DGS3MO " + r[list(r)[0]]
    except Exception as e:
        print("  FRED ล้มเหลว:", e)
    return default, "default"


# ───────────────────────── 3. spot gold + basis ─────────────────────────
def _src_stooq():
    url = "https://stooq.com/q/l/?s=xauusd&f=sd2t2ohlcv&h&e=csv"
    row = next(csv.DictReader(io.StringIO(
        requests.get(url, headers=UA, timeout=12).text)))
    return float(row["Close"])


def _src_yahoo_fx():
    import yfinance as yf
    return float(yf.Ticker("XAUUSD=X").fast_info["last_price"])


def _src_frankfurter():
    j = requests.get("https://api.frankfurter.app/latest?from=XAU&to=USD",
                     headers=UA, timeout=12).json()
    return float(j["rates"]["USD"])


SPOT_SOURCES = [("stooq", _src_stooq),
                ("yahoo XAUUSD=X", _src_yahoo_fx),
                ("frankfurter", _src_frankfurter)]


def spot_and_basis(F, symbol, r):
    """คืน (spot, basis, source) — ถ้า feed ล่มหมด ใช้ cost-of-carry ย้อนกลับ"""
    T_fut = year_fraction(futures_last_trade(symbol))
    for name, fn in SPOT_SOURCES:
        try:
            s = fn()
            if not (500 < s < 20000):
                continue
            b = F - s
            if -0.003 * F <= b <= 0.04 * F:      # sanity band
                return s, b, name
            print(f"  {name} ให้ basis ผิดปกติ ({b:+.2f}) → ข้าม")
        except Exception as e:
            print(f"  {name} ล้มเหลว: {e}")
    s = F * math.exp(-r * T_fut)                 # F = S·e^(rT)
    return s, F - s, f"implied-carry r={r:.2%}"