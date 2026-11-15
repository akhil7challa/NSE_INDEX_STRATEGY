import os
import time as time_module
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
from SmartApi import SmartConnect

# ============================================================
# CONFIGURATION
# ============================================================

API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"

EXCHANGE = "NSE"
SYMBOL_TOKEN = "99926000"  # NIFTY 50
INDEX_NAME = "NIFTY 50"

INTERVAL = "ONE_MINUTE"
IST = ZoneInfo("Asia/Kolkata")

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

# ============================================================
# STRATEGY RULES (MATCHING BACKTEST)
# ============================================================

ORB_START_TIME = time(9, 15)
ORB_END_TIME = time(9, 44)
TRADE_SCAN_START_TIME = time(9, 45)
TRADE_DEADLINE_TIME = time(12, 0)
MIN_ORB_RANGE_PCT = 0.0

CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
OUTPUT_FILE = "breakout_signals.csv"


# ============================================================
# SMARTAPI LOGIN
# ============================================================

def login_to_smartapi():
    if not all([API_KEY, CLIENT_CODE, PASSWORD, TOTP_SECRET]):
        raise ValueError("Missing credentials. Set correct API keys and secrets.")

    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    session_data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)

    if not session_data or not session_data.get("status"):
        raise RuntimeError(f"SmartAPI login failed: {session_data}")

    return smart_api


# ============================================================
# TIMEZONE & DATA CONVERSION
# ============================================================

def convert_dataframe_to_ist(df):
    if df.empty or "timestamp" not in df.columns:
        return df
    
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()
    
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(IST, ambiguous="NaT", nonexistent="shift_forward")
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert(IST)
        
    return df


# ============================================================
# HISTORICAL DATA FETCHING
# ============================================================

def fetch_historical_data(smart_api, from_date, to_date):
    params = {
        "exchange": EXCHANGE,
        "symboltoken": SYMBOL_TOKEN,
        "interval": INTERVAL,
        "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
        "todate": to_date.strftime("%Y-%m-%d %H:%M"),
    }

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = smart_api.getCandleData(params)
            if response and response.get("status") and response.get("data") is not None:
                data = response["data"]
                if not data:
                    return pd.DataFrame(columns=CANDLE_COLUMNS)
                return convert_dataframe_to_ist(pd.DataFrame(data, columns=CANDLE_COLUMNS))
            last_error = RuntimeError(f"Historical API response: {response}")
        except Exception as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            print(f"   Attempt {attempt}/{MAX_RETRIES} failed: {last_error}")
            print(f"   Retrying in {delay} seconds...")
            time_module.sleep(delay)

    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts: {last_error}")


# ============================================================
# BREAKOUT SCANNER (12:00 PM CHECK)
# ============================================================

def scan_todays_breakout(smart_api, now_ist):
    trading_date = now_ist.date()
    start_dt = datetime.combine(trading_date, time(9, 15), tzinfo=IST)
    end_dt = datetime.combine(trading_date, time(12, 0), tzinfo=IST)

    print(f"Fetching intraday candles for {trading_date} from {start_dt.strftime('%H:%M')} to {end_dt.strftime('%H:%M')} IST...")
    
    raw_df = fetch_historical_data(smart_api, start_dt, end_dt)
    if raw_df.empty:
        print("No candle data received for today.")
        return None

    raw_df = raw_df.sort_values("timestamp").copy()
    for col in ["open", "high", "low", "close", "volume"]:
        raw_df[col] = pd.to_numeric(raw_df[col], errors="coerce")

    raw_df["time_obj"] = raw_df["timestamp"].dt.time

    # 1. Calculate ORB Range (09:15 to 09:44)
    orb_candles = raw_df[(raw_df["time_obj"] >= ORB_START_TIME) & (raw_df["time_obj"] <= ORB_END_TIME)]
    if len(orb_candles) < 15:
        print(f"Insufficient ORB candles found ({len(orb_candles)} candles).")
        return None

    orb_high = orb_candles["high"].max()
    orb_low = orb_candles["low"].min()
    day_open = float(raw_df.iloc[0]["open"])
    orb_range_pct = ((orb_high - orb_low) / day_open) * 100

    if orb_range_pct < MIN_ORB_RANGE_PCT:
        print("ORB range filtered out by minimum percentage rule.")
        return None

    print(f"ORB High: {orb_high:.2f} | ORB Low: {orb_low:.2f}")

    # 2. Scan for Breakout between 09:45 and 12:00
    scan_candles = raw_df[(raw_df["time_obj"] >= TRADE_SCAN_START_TIME) & (raw_df["time_obj"] <= TRADE_DEADLINE_TIME)]
    breakout_type = None
    breakout_time = None

    for _, row in scan_candles.iterrows():
        if row["high"] > orb_high and row["low"] < orb_low:
            continue
        elif row["high"] > orb_high:
            breakout_type = "BUY"
            breakout_time = row["timestamp"].strftime("%H:%M:%S")
            breakout_price = row["high"]
            breakout_candle_time = row["time_obj"]
            breakout_row = row
            breaklog_text = f"BUY Breakout detected at {breakout_time} (Price: {breakout_price:.2f} > ORB High {orb_high:.2f})"
            break
        elif row["low"] < orb_low:
            breakout_type = "SELL"
            breakout_time = row["timestamp"].strftime("%H:%M:%S")
            breakout_price = row["low"]
            breakout_candle_time = row["time_obj"]
            breakout_row = row
            breaklog_text = f"SELL Breakout detected at {breakout_time} (Price: {breakout_price:.2f} < ORB Low {orb_low:.2f})"
            break

    if not breakout_type:
        print("No breakout occurred today by 12:00 PM.")
        return {
            "date": str(trading_date),
            "breakout_time": "N/A",
            "index_name": INDEX_NAME,
            "symbol_token": SYMBOL_TOKEN,
            "signal": "No Breakout",
            "orb_high": orb_high,
            "orb_low": orb_low
        }

    print(breaklog_text)
    
    result_dict = {
        "date": str(trading_date),
        "breakout_time": breakout_time,
        "index_name": INDEX_NAME,
        "symbol_token": SYMBOL_TOKEN,
        "signal": breakout_type,
        "orb_high": orb_high,
        "orb_low": orb_low
    }

    # Save to CSV output file
    df_out = pd.DataFrame([result_dict])
    header = not os.path.exists(OUTPUT_FILE)
    df_out.to_csv(OUTPUT_FILE, mode='a', index=False, header=header)
    print(f"Breakout information successfully appended to {OUTPUT_FILE}")

    return result_dict


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    smart_api = None
    try:
        now_ist = datetime.now(IST)
        smart_api = login_to_smartapi()

        print("=" * 70)
        print(f"ANGLEONE LIVE ORB SCANNER - {INDEX_NAME} ({now_ist.date()})")
        print("=" * 70)

        signal_info = scan_todays_breakout(smart_api, now_ist)
        print("\nScan completed successfully.")

    except Exception as exc:
        print(f"\nERROR: {exc}")
    finally:
        if smart_api:
            try:
                smart_api.terminateSession(CLIENT_CODE)
                print("\nSmartAPI session terminated.")
            except:
                pass


if __name__ == "__main__":
    main()