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
UNDERLYING_TOKEN = "99926000"  # NIFTY 50 Index Token
INDEX_NAME = "NIFTY 50"

INTERVAL = "ONE_MINUTE"
IST = ZoneInfo("Asia/Kolkata")

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

SIGNAL_FILE = "breakout_signals.csv"
OUTPUT_FILE = "option_orb_analysis.csv"

# Path to your local options instrument master CSV file
INSTRUMENTS_CSV_PATH = r"C:\Users\challa\Desktop\Challa\Personal\NSE\NIFTY\AngelOne_Instruments\Options_Instrumentation\nifty_current_expiry.csv"

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

def fetch_historical_candles(smart_api, exchange, symbol_token, from_date, to_date):
    params = {
        "exchange": exchange,
        "symboltoken": str(symbol_token),
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
                    return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
                return convert_dataframe_to_ist(pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"]))
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
# LOCAL INSTRUMENTS CSV LOOKUP HELPER
# ============================================================

def find_option_token_from_csv(target_strike, option_type):
    """
    Reads local nifty_current_expiry.csv file and matches the target strike and option type (CE/PE).
    """
    if not os.path.exists(INSTRUMENTS_CSV_PATH):
        raise FileNotFoundError(f"Instruments file not found at: {INSTRUMENTS_CSV_PATH}")

    df_inst = pd.read_csv(INSTRUMENTS_CSV_PATH)
    
    # Standardize column names to lower case for reliable matching
    df_inst.columns = [c.lower().strip() for c in df_inst.columns]
    
    # Common column name variations in Angel One master files:
    # 'strike', 'symbol', 'symboltoken', 'token', 'instrumenttype', etc.
    strike_col = next((c for c in df_inst.columns if 'strike' in c), 'strike')
    token_col = next((c for c in df_inst.columns if 'token' in c or c == 'token'), 'symboltoken')
    symbol_col = next((c for c in df_inst.columns if 'symbol' in c or 'tradingsymbol' in c), 'symbol')

    # Angel One strike prices are often stored multiplied by 100 (e.g., 23500.00 or 2350000.00)
    # We inspect the strike value format or cast safely.
    for _, row in df_inst.iterrows():
        raw_strike = str(row[strike_col])
        try:
            # Clean up trailing decimals if stored as float strings
            clean_strike = float(raw_strike)
            if clean_strike > 100000:  # e.g., 2350000.00 format
                clean_strike = clean_strike / 100.0
                
            if int(clean_strike) == int(target_strike):
                sym_str = str(row[symbol_col]).upper()
                if sym_str.endswith(option_type):
                    token_val = str(row[token_col]).split('.')[0] # Remove decimal if present
                    return token_val, sym_str
        except ValueError:
            continue

    raise RuntimeError(f"Could not locate matching option contract for Strike: {target_strike} and Type: {option_type} in CSV.")


# ============================================================
# MAIN OPTION ORB PROCESSOR
# ============================================================

def process_option_orb(smart_api, now_ist):
    trading_date_str = now_ist.date().strftime("%Y-%m-%d")

    if not os.path.exists(SIGNAL_FILE):
        print(f"Signal file '{SIGNAL_FILE}' not found. Run Part 1 scanner first.")
        return

    sig_df = pd.read_csv(SIGNAL_FILE)
    today_sig = sig_df[sig_df["date"] == trading_date_str]

    if today_sig.empty:
        print(f"No breakout record found for today ({trading_date_str}) in {SIGNAL_FILE}.")
        return

    row = today_sig.iloc[0]
    signal = row["signal"]
    index_name = row["index_name"]
    symbol_token = str(row["symbol_token"])

    print(f"Loaded Signal from CSV -> Date: {trading_date_str} | Index: {index_name} | Signal: {signal}")

    if signal not in ["BUY", "SELL"]:
        print(f"Signal is '{signal}'. No option trade to scan.")
        return

    # 1. Fetch current/latest spot price of the underlying index for today
    start_dt = datetime.combine(now_ist.date(), time(9, 15), tzinfo=IST)
    end_dt = datetime.combine(now_ist.date(), time(12, 0), tzinfo=IST)

    spot_df = fetch_historical_candles(smart_api, EXCHANGE, symbol_token, start_dt, end_dt)
    if spot_df.empty:
        print("ERROR: Failed to fetch underlying spot candles.")
        return

    current_spot_price = float(spot_df.iloc[-1]["close"])
    print(f"Current Underlying Spot Price: {current_spot_price:.2f}")

    # 2. Round to nearest multiple of 50
    rounded_spot = round(current_spot_price / 50) * 50
    print(f"Rounded Spot (Nearest 50): {rounded_spot}")

    # 3. Calculate Option Strike based on user rules:
    # SELL signal -> +100 | BUY signal -> -100
    if signal == "SELL":
        target_strike = rounded_spot + 100
        option_type = "PE"  # Bearish breakout -> Put option
    else:
        target_strike = rounded_spot - 100
        option_type = "CE"  # Bullish breakout -> Call option

    print(f"Target Strike Calculated: {target_strike} {option_type}")

    # 4. Find Option Token from local CSV file
    opt_token, opt_symbol = find_option_token_from_csv(target_strike, option_type)
    print(f"Found Option Symbol from CSV: {opt_symbol} | Token: {opt_token}")

    # 5. Fetch 1-minute candles for the Option from 09:15 to 12:00
    print(f"Fetching 1-minute candles for option {opt_symbol}...")
    opt_candles = fetch_historical_candles(smart_api, "NFO", opt_token, start_dt, end_dt)

    if opt_candles.empty:
        print("ERROR: No candle data received for the option contract token.")
        return

    opt_candles = opt_candles.sort_values("timestamp").copy()
    for col in ["open", "high", "low", "close", "volume"]:
        opt_candles[col] = pd.to_numeric(opt_candles[col], errors="coerce")
    opt_candles["time_obj"] = opt_candles["timestamp"].dt.time

    # 6. Calculate Option ORB Range (09:15 to 09:44)
    orb_candles = opt_candles[(opt_candles["time_obj"] >= time(9, 15)) & (opt_candles["time_obj"] <= time(9, 44))]
    if len(orb_candles) < 15:
        print(f"Insufficient ORB candles for option ({len(orb_candles)} candles found).")
        return

    opt_orb_high = float(orb_candles["high"].max())
    opt_orb_low = float(orb_candles["low"].min())
    opt_orb_midpoint = (opt_orb_high + opt_orb_low) / 2.0

    # 7. Print to Terminal
    print("\n" + "=" * 70)
    print(f"OPTION ORB ANALYSIS REPORT (LOCAL CSV) - {opt_symbol}")
    print("=" * 70)
    print(f"Trading Date      : {trading_date_str}")
    print(f"Underlying Signal : {signal}")
    print(f"Option Contract   : {opt_symbol} (Token: {opt_token})")
    print(f"Option ORB High   : {opt_orb_high:.2f}")
    print(f"Option ORB Low    : {opt_orb_low:.2f}")
    print(f"Option Midpoint   : {opt_orb_midpoint:.2f}")
    print("=" * 70)

    # 8. Save to Output CSV File
    result_dict = {
        "date": trading_date_str,
        "signal": signal,
        "underlying_spot": current_spot_price,
        "option_symbol": opt_symbol,
        "option_token": opt_token,
        "strike": target_strike,
        "option_type": option_type,
        "option_orb_high": round(opt_orb_high, 2),
        "option_orb_low": round(opt_orb_low, 2),
        "option_orb_midpoint": round(opt_orb_midpoint, 2)
    }

    df_out = pd.DataFrame([result_dict])
    header = not os.path.exists(OUTPUT_FILE)
    df_out.to_csv(OUTPUT_FILE, mode='a', index=False, header=header)
    print(f"Results successfully saved to {OUTPUT_FILE}")


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    smart_api = None
    try:
        now_ist = datetime.now(IST)
        smart_api = login_to_smartapi()

        print("=" * 70)
        print(f"ANGLEONE OPTION ORB SCANNER - LOCAL CSV ({now_ist.date()})")
        print("=" * 70)

        process_option_orb(smart_api, now_ist)
        print("\nOption scan completed successfully.")

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