import os
import sys
import time as time_module
import logging
from pathlib import Path
from datetime import datetime, date, time as dt_time
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
from SmartApi import SmartConnect

# ============================================================
# CONFIGURATION
# ============================================================

IST = ZoneInfo("Asia/Kolkata")

API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"

# Test Date Configuration
TEST_DATE = date(2026, 11, 13)  # Change this to any past trading date you want to test

INSTRUMENT_MASTER = Path(
    r"C:\Users\challa\Desktop\Challa\Personal\NSE\NIFTY"
    r"\AngelOne_Instruments\Options_Instrumentation\nifty_current_expiry.csv"
)

SIGNAL_FILE = "breakout_signals.csv"
OUTPUT_FILE = "option_orb_analysis.csv"

SPOT_EXCHANGE = "NSE"
OPTION_EXCHANGE = "NFO"
CANDLE_INTERVAL = "ONE_MINUTE"

ORB_START_TIME = dt_time(9, 15)
ORB_END_TIME = dt_time(9, 45)

MAX_RETRIES = 5
RETRY_BASE_SECONDS = 3

CSV_TOKEN = "symboltoken"
CSV_SYMBOL = "tradingsymbol"
CSV_NAME = "name"
CSV_EXPIRY = "expiry"
CSV_STRIKE = "strike"
CSV_LOTSIZE = "lotsize"
CSV_TYPE = "instrumenttype"
CSV_EXCHANGE = "exchange"

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("option_orb_part2")


# ============================================================
# HELPERS & API (WITH RETRIES & TIMEOUT RESILIENCE)
# ============================================================

def login():
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(f"Attempting SmartAPI login (Attempt {attempt}/{MAX_RETRIES})...")
            api = SmartConnect(api_key=API_KEY)
            otp = pyotp.TOTP(TOTP_SECRET).now()
            
            response = api.generateSession(CLIENT_CODE, PASSWORD, otp)
            if isinstance(response, dict) and response.get("status") and response.get("data"):
                log.info("Angel One login successful.")
                return api
            else:
                last_error = RuntimeError(f"Login response invalid: {response}")
        except Exception as exc:
            last_error = exc
            log.warning(f"Login attempt {attempt} encountered exception: {exc}")

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            log.info(f"Retrying login in {delay} seconds...")
            time_module.sleep(delay)

    raise RuntimeError(f"Failed to log in after {MAX_RETRIES} attempts: {last_error}")


def fetch_candles(api, exchange, token, trading_day, from_time=dt_time(9, 0), to_time=dt_time(15, 30)):
    start = datetime.combine(trading_day, from_time, tzinfo=IST)
    end = datetime.combine(trading_day, to_time, tzinfo=IST)

    params = {
        "exchange": exchange,
        "symboltoken": str(token),
        "interval": CANDLE_INTERVAL,
        "fromdate": start.strftime("%Y-%m-%d %H:%M"),
        "todate": end.strftime("%Y-%m-%d %H:%M"),
    }

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time_module.sleep(0.5)  # Rate limit safety buffer
            response = api.getCandleData(params)
            
            if response and isinstance(response, dict):
                message = str(response.get("message", "")).lower()
                if "access rate" in message or "exceeding" in message:
                    raise RuntimeError(f"Rate limit hit: {response.get('message')}")
                
                rows = response.get("data")
                if rows is not None:
                    if not rows:
                        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
                    
                    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
                    df = df.dropna(subset=["timestamp"]).copy()
                    
                    if df["timestamp"].dt.tz is None:
                        df["timestamp"] = df["timestamp"].dt.tz_localize(IST, ambiguous="NaT", nonexistent="shift_forward")
                    else:
                        df["timestamp"] = df["timestamp"].dt.tz_convert(IST)

                    for col in ["open", "high", "low", "close", "volume"]:
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                    return df.sort_values("timestamp").reset_index(drop=True)
            
            last_error = RuntimeError(f"Candle API response invalid: {response}")
        except Exception as exc:
            last_error = exc
            log.warning(f"Candle fetch attempt {attempt}/{MAX_RETRIES} failed for token {token}: {exc}")

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            log.info(f"Retrying candle fetch in {delay} seconds...")
            time_module.sleep(delay)

    log.error(f"Failed to fetch candles for token {token} after {MAX_RETRIES} attempts.")
    return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])


def find_column(df, override, candidates, description):
    if override:
        if override not in df.columns:
            raise ValueError(f"Configured {description} column '{override}' not found.")
        return override
    normalize = lambda value: "".join(ch for ch in str(value).lower() if ch.isalnum())
    columns = {normalize(col): col for col in df.columns}
    for candidate in candidates:
        key = normalize(candidate)
        if key in columns:
            return columns[key]
    raise ValueError(f"Cannot detect the {description} column.")


def load_instruments():
    if not INSTRUMENT_MASTER.exists():
        raise FileNotFoundError(f"Instrument CSV not found: {INSTRUMENT_MASTER}")

    df = pd.read_csv(INSTRUMENT_MASTER, dtype=str, sep=None, engine="python")
    df.columns = [str(col).strip() for col in df.columns]

    columns = {
        "token": find_column(df, CSV_TOKEN, ["token", "symboltoken", "symbol_token", "instrument_token"], "token"),
        "symbol": find_column(df, CSV_SYMBOL, ["symbol", "tradingsymbol", "trading_symbol"], "symbol"),
        "name": find_column(df, CSV_NAME, ["name", "underlying", "underlyingsymbol"], "underlying name"),
        "expiry": find_column(df, CSV_EXPIRY, ["expiry", "expirydate", "expiry_date"], "expiry"),
        "strike": find_column(df, CSV_STRIKE, ["strike", "strikeprice", "strike_price"], "strike"),
        "lotsize": find_column(df, CSV_LOTSIZE, ["lotsize", "lot_size", "marketlot"], "lot size"),
        "type": find_column(df, CSV_TYPE, ["instrumenttype", "instrument_type"], "instrument type"),
        "exchange": find_column(df, CSV_EXCHANGE, ["exch_seg", "exchange", "exchange_segment"], "exchange"),
    }

    data = pd.DataFrame({
        "token": df[columns["token"]].fillna("").astype(str).str.strip(),
        "symbol": df[columns["symbol"]].fillna("").astype(str).str.strip(),
        "name": df[columns["name"]].fillna("").astype(str).str.upper().str.strip(),
        "expiry_raw": df[columns["expiry"]].fillna("").astype(str).str.strip(),
        "strike_raw": df[columns["strike"]].fillna("").astype(str).str.strip(),
        "lotsize_raw": df[columns["lotsize"]].fillna("").astype(str).str.strip(),
        "type": df[columns["type"]].fillna("").astype(str).str.upper().str.strip(),
        "exchange": df[columns["exchange"]].fillna("").astype(str).str.upper().str.strip(),
    })

    data["expiry"] = pd.to_datetime(data["expiry_raw"], errors="coerce", dayfirst=True).dt.date
    data["strike"] = pd.to_numeric(data["strike_raw"], errors="coerce")

    if not data["strike"].dropna().empty and data["strike"].dropna().median() > 100000:
        data["strike"] = data["strike"] / 100.0

    data["lot_size"] = pd.to_numeric(data["lotsize_raw"], errors="coerce")

    options = data[
        (data["name"].eq("NIFTY") | data["symbol"].str.upper().str.startswith("NIFTY"))
        & (data["type"].eq("OPTIDX") | data["symbol"].str.upper().str.endswith(("CE", "PE")))
        & data["exchange"].eq("NFO")
        & data["expiry"].notna()
        & data["strike"].notna()
        & (data["strike"] > 0)
        & (data["lot_size"] > 0)
        & data["token"].ne("")
    ].copy()

    log.info("Eligible NIFTY option contracts loaded: %d", len(options))
    return options


def select_itm_contract(instruments, spot, breakout_type, today):
    option_type = "CE" if breakout_type == "BUY" else "PE"

    eligible = instruments[
        (instruments["expiry"] >= today)
        & instruments["symbol"].str.upper().str.endswith(option_type)
    ].copy()

    if eligible.empty:
        raise RuntimeError(f"No unexpired NIFTY {option_type} contracts found.")

    expiry = min(eligible["expiry"])
    eligible = eligible[eligible["expiry"] == expiry]

    strikes = sorted(eligible["strike"].dropna().unique().tolist())
    atm_strike = min(strikes, key=lambda val: abs(float(val) - float(spot)))
    atm_strike = float(atm_strike)

    target_strike = (atm_strike - 100) if option_type == "CE" else (atm_strike + 100)

    selected = eligible[eligible["strike"].astype(float) == target_strike]
    if selected.empty:
        selected_record = min(eligible.to_dict('records'), key=lambda r: abs(float(r["strike"]) - target_strike))
        row = pd.Series(selected_record)
        target_strike = float(row["strike"])
    else:
        row = selected.iloc[0]

    return {
        "symbol": row["symbol"],
        "token": row["token"],
        "exchange": OPTION_EXCHANGE,
        "expiry": expiry.isoformat(),
        "strike": target_strike,
        "option_type": option_type,
        "lot_size": int(row["lot_size"]),
    }


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    log.info("Starting Part 2: Option ORB Scanner from Breakout Signal File...")

    if not os.path.exists(SIGNAL_FILE):
        log.error("Signal file '%s' not found. Run Part 1 first.", SIGNAL_FILE)
        return

    sig_df = pd.read_csv(SIGNAL_FILE)
    if sig_df.empty:
        log.error("Signal file '%s' is empty.", SIGNAL_FILE)
        return

    first_row = sig_df.iloc[0]
    signal = str(first_row["signal"]).upper().strip()
    index_name = str(first_row["index_name"])
    symbol_token = str(first_row["symbol_token"])

    log.info("Loaded from %s -> Date: %s | Index: %s | Token: %s | Signal: %s", 
             SIGNAL_FILE, first_row.get("date", TEST_DATE), index_name, symbol_token, signal)

    instruments = load_instruments()
    api = login()

    try:
        # 1. Fetch candles for underlying index to get current price
        spot_df = fetch_candles(api, SPOT_EXCHANGE, symbol_token, TEST_DATE, from_time=dt_time(9, 15), to_time=dt_time(12, 0))
        if spot_df.empty:
            log.error("No candle data received for underlying index token %s", symbol_token)
            return

        current_price = float(spot_df.iloc[-1]["close"])
        log.info("Current Underlying Price: %.2f", current_price)

        # 2. Select option contract using working reference logic
        contract = select_itm_contract(instruments, current_price, signal, TEST_DATE)
        log.info("Selected Option Contract Symbol: %s | Token: %s | Strike: %s", contract["symbol"], contract["token"], contract["strike"])

        # 3. Fetch 1-minute candles for the option contract specifically for the 09:15 to 09:45 window
        option_df = fetch_candles(api, OPTION_EXCHANGE, contract["token"], TEST_DATE, from_time=ORB_START_TIME, to_time=ORB_END_TIME)
        if option_df.empty:
            log.error("No candle data received for option contract %s during window 09:15 to 09:45", contract["symbol"])
            return

        option_df["time_obj"] = option_df["timestamp"].dt.time
        opt_orb_candles = option_df[(option_df["time_obj"] >= ORB_START_TIME) & (option_df["time_obj"] <= ORB_END_TIME)]

        if opt_orb_candles.empty:
            log.error("Insufficient option ORB candles found for time window 09:15 to 09:45.")
            return

        # 4. Compute Option ORB High, Low, and Midpoint
        opt_orb_high = float(opt_orb_candles["high"].max())
        opt_orb_low = float(opt_orb_candles["low"].min())
        opt_orb_midpoint = (opt_orb_high + opt_orb_low) / 2.0

        # 5. Print Report to Terminal
        print("\n" + "=" * 70)
        print(f"OPTION ORB ANALYSIS REPORT - {contract['symbol']}")
        print("=" * 70)
        print(f"Trading Date        : {TEST_DATE}")
        print(f"Index Signal        : {signal} ({index_name})")
        print(f"Underlying Price    : {current_price:.2f}")
        print(f"Option Contract     : {contract['symbol']} (Token: {contract['token']})")
        print(f"Option ORB High     : {opt_orb_high:.2f}")
        print(f"Option ORB Low      : {opt_orb_low:.2f}")
        print(f"Option ORB Midpoint : {opt_orb_midpoint:.2f}")
        print("=" * 70)

        # 6. Save to Output CSV File
        result_dict = {
            "date": str(TEST_DATE),
            "signal": signal,
            "underlying_price": current_price,
            "option_symbol": contract["symbol"],
            "option_token": contract["token"],
            "strike": contract["strike"],
            "option_type": contract["option_type"],
            "option_orb_high": round(opt_orb_high, 2),
            "option_orb_low": round(opt_orb_low, 2),
            "option_orb_midpoint": round(opt_orb_midpoint, 2),
        }
        df_out = pd.DataFrame([result_dict])
        header = not os.path.exists(OUTPUT_FILE)
        df_out.to_csv(OUTPUT_FILE, mode='a', index=False, header=header)
        log.info("Results successfully saved to %s", OUTPUT_FILE)

    finally:
        if api is not None:
            try:
                api.terminateSession(CLIENT_CODE)
            except Exception:
                pass


if __name__ == "__main__":
    main()