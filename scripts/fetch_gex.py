import json
from datetime import datetime
import pandas as pd
import yfinance as yf

def find_swings(df, window=3):
    highs = df['High']
    lows = df['Low']
    swing_highs = []
    swing_lows = []
    
    for i in range(window, len(df) - window):
        if highs.iloc[i] == highs.iloc[i-window:i+window+1].max():
            swing_highs.append((df.index[i], float(highs.iloc[i])))
        if lows.iloc[i] == lows.iloc[i-window:i+window+1].min():
            swing_lows.append((df.index[i], float(lows.iloc[i])))
            
    return swing_highs, swing_lows

def detect_flip_zones(df, swing_highs, swing_lows):
    """
    ตรวจจับ Flip Zone (Swap Zone / SRF): 
    โซนที่เคยเป็นแนวต้าน/ซัพพลาย แล้วถูกเบรกทะลุกลายเป็นแนวรับ (Support/Demand Flip) 
    หรือโซนที่เคยเป็นแนวรับแล้วหลุดกลายเป็นแนวต้าน (Resistance Flip)
    """
    closes = df['Close']
    flip_zones = []
    
    # ตรวจสอบ Swing High ที่เคยถูกเบรกผ่านไปแล้ว (กลายเป็น Flip Demand / Support)
    for t, sh_price in swing_highs[:-1]: # ไม่เอาอันล่าสุดสดๆ
        # เช็คว่ามีแท่งถัดไปที่ราคาปิดทะลุ High นี้ขึ้นไปหรือไม่
        sub_df = df.loc[t:]
        if len(sub_df) > 3:
            broken = (sub_df['Close'] > sh_price).any()
            if broken:
                flip_zones.append({
                    "type": "Demand Flip (Support)",
                    "price_range": f"{sh_price - 8:,.2f} - {sh_price:,.2f}",
                    "desc": f"อดีต Supply/Resistance ที่ถูกเบรกและพลิกมาเป็นแนวรับ @ {sh_price:,.2f}"
                })

    # ตรวจสอบ Swing Low ที่เคยหลุด (Breakdown) กลายเป็น Flip Supply / Resistance
    for t, sl_price in swing_lows[:-1]:
        sub_df = df.loc[t:]
        if len(sub_df) > 3:
            broken = (sub_df['Close'] < sl_price).any()
            if broken:
                flip_zones.append({
                    "type": "Supply Flip (Resistance)",
                    "price_range": f"{sl_price:,.2f} - {sl_price + 8:,.2f}",
                    "desc": f"อดีต Demand/Support ที่หลุดและพลิกมาเป็นแนวต้าน @ {sl_price:,.2f}"
                })

    return flip_zones

def calculate_smc_with_flip(df, current_price, call_wall, put_wall):
    highs = df['High']
    lows = df['Low']
    
    swing_highs, swing_lows = find_swings(df, window=3)
    sh_sorted = sorted([price for _, price in swing_highs], reverse=True)
    sl_sorted = sorted([price for _, price in swing_lows])
    
    # คำนวณ Demand / Supply ปกติ
    sup1_high = sh_sorted[0] if len(sh_sorted) > 0 else float(highs.max())
    sup1_low = sup1_high - 10.0
    dem1_low = sl_sorted[0] if len(sl_sorted) > 0 else float(lows.min())
    dem1_high = dem1_low + 10.0
    
    # ค้นหา Flip Zones
    flips = detect_flip_zones(df, swing_highs, swing_lows)
    flip_text = flips[0]["desc"] if flips else "กำลังรอการเบรกและพลิกโซน (Zone Swap)"
    flip_range = flips[0]["price_range"] if flips else "N/A"

    poi_price = dem1_high + 3.0
    is_in_dem1 = dem1_low <= current_price <= dem1_high
    is_in_sup1 = sup1_low <= current_price <= sup1_high
    
    alert_button = None
    if is_in_dem1:
        alert_button = {"type": "buy", "text": "🟢 PRICE AT DEMAND ZONE!", "class": "btn-buy"}
    elif is_in_sup1:
        alert_button = {"type": "sell", "text": "🔴 PRICE AT SUPPLY ZONE!", "class": "btn-sell"}
    else:
        alert_button = {"type": "wait", "text": "⏳ MONITORING FLIP & SWAP ZONES", "class": "btn-wait"}

    last_close = float(df['Close'].iloc[-1])
    prev_high = float(highs.iloc[-2])
    
    mss_status = "Bullish MSS Confirmed" if last_close > prev_high else "Bearish MSS / Rejection"
    mid_range = (sup1_high + dem1_low) / 2
    
    htf_bias = "Bullish (Discount / Flip Support Active)" if current_price < mid_range else "Bearish (Premium / Flip Resistance Active)"

    return {
        "htf_bias": htf_bias,
        "supply_1": f"{sup1_low:,.2f} - {sup1_high:,.2f}",
        "demand_1": f"{dem1_low:,.2f} - {dem1_high:,.2f}",
        "flip_zone": flip_range,
        "flip_desc": flip_text,
        "current_phase": "Zone Swap / Retest Phase",
        "poi": f"Order Block @ {poi_price:,.2f}",
        "mss_status": mss_status,
        "status_desc": "📍 วิเคราะห์ตามหลักการ Flip Zone (SRF / Swap Zone สำเร็จ)",
        "alert_btn": alert_button
    }

def main():
    ticker = "GC=F"
    df = yf.download(ticker, period="5d", interval="1h", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        
    current_price = float(df['Close'].iloc[-1])
    call_wall = 4230.00
    put_wall = 4210.00
    
    smc_data = calculate_smc_with_flip(df, current_price, call_wall, put_wall)

    data = {
        "symbol": "GCZ6",
        "future_price": current_price,
        "spot_price": current_price - 42.87,
        "basis": 42.87,
        "basis_source": "implied-carry r=4.20%",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
        "expiry_ct": "2026-11-24 13:30 CST",
        "dte": 54,
        "source": "GLD (scaled)",
        "oi_date": "2026-10-01",
        "net_gex": -181300000,
        "gamma_flip": 4209.95,
        "max_pain": 4230.00,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "levels": [
            {"price": 4340.00, "tag": "Call GEX - Spot 4,297.13", "kind": "call"},
            {"price": 4265.00, "tag": "Call GEX - Spot 4,222.13", "kind": "call"},
            {"price": 4230.00, "tag": "Call Wall - Peak Call GEX - Spot 4,187.13", "kind": "call"},
            {"price": 4210.00, "tag": "Put Wall - Peak Put GEX - Spot 4,167.13", "kind": "put"}
        ],
        "by_strike": [
            {"strike": 4150, "gex": -80000000},
            {"strike": 4175, "gex": -50000000},
            {"strike": 4200, "gex": -30000000},
            {"strike": 4210, "gex": 120000000},
            {"strike": 4230, "gex": 50000000},
            {"strike": 4250, "gex": 15000000}
        ],
        "smc": smc_data
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("Successfully generated data.json with Flip Zone detection.")

if __name__ == "__main__":
    main()