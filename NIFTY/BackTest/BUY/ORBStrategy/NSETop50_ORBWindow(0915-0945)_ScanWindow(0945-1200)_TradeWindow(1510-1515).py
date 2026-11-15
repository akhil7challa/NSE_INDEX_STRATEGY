import os
import time as time_module
import calendar
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
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
TOKENS_CSV_PATH = r"C:\Users\challa\Desktop\Challa\Personal\NSE\SymbolTokens\nifty50_tokens.csv"
OUTPUT_CSV_PATH = r"C:\Users\challa\Desktop\Challa\Personal\NSE\SymbolTokens\nifty50_orb_summary_results.csv"

INTERVAL = "ONE_MINUTE"
IST = ZoneInfo("Asia/Kolkata")
START_YEAR = 2015

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2


# ============================================================
# STRATEGY RULES
# ============================================================

ORB_START_TIME = time(9, 15)
ORB_END_TIME = time(9, 44)
TRADE_SCAN_START_TIME = time(9, 45)
TRADE_DEADLINE_TIME = time(12, 0)
TRADE_ENTRY_TIME = time(15, 9)
EXIT_TIME = time(15, 14)
MIN_ORB_RANGE_PCT = 0.0

CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


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

def fetch_historical_data(smart_api, symbol_token, from_date, to_date):
    params = {
        "exchange": EXCHANGE,
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
                    return pd.DataFrame(columns=CANDLE_COLUMNS)
                return convert_dataframe_to_ist(pd.DataFrame(data, columns=CANDLE_COLUMNS))
            last_error = RuntimeError(f"Historical API response: {response}")
        except Exception as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            print(f"    Attempt {attempt}/{MAX_RETRIES} failed: {last_error}")
            print(f"    Retrying in {delay} seconds...")
            time_module.sleep(delay)

    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts: {last_error}")


def fetch_year_historical_data(smart_api, symbol_token, symbol_name, year, now_ist):
    all_data, fetch_status = [], []
    today = now_ist.date()

    print("\n" + "=" * 90)
    print(f"FETCHING 1-MINUTE DATA FOR {symbol_name} (Token: {symbol_token}) FOR {year}")
    print("=" * 90)

    if year > today.year:
        return pd.DataFrame(columns=CANDLE_COLUMNS), pd.DataFrame(fetch_status)

    last_month = 12 if year < today.year else today.month

    for month in range(1, 13):
        month_name = calendar.month_name[month]
        if month > last_month:
            continue

        last_day = calendar.monthrange(year, month)[1]
        start_dt = datetime(year, month, 1, 9, 0, tzinfo=IST)
        end_dt = min(datetime(year, month, last_day, 15, 30, tzinfo=IST), now_ist)

        if start_dt > now_ist:
            continue

        print(f"\nFetching {month_name} {year}")
        print(f"    From: {start_dt}")
        print(f"    To  : {end_dt}")

        try:
            month_df = fetch_historical_data(smart_api, symbol_token, start_dt, end_dt)
            if month_df.empty:
                print(f"    No data received for {month_name} {year}")
                fetch_status.append({"year": year, "month": month, "month_name": month_name, "fetch_status": "NO DATA", "candles_received": 0})
                continue

            month_df = month_df[
                (month_df["timestamp"].dt.year == year) &
                (month_df["timestamp"].dt.month == month) &
                (month_df["timestamp"] <= end_dt)
            ].copy()

            if not month_df.empty:
                count = len(month_df)
                print(f"    Candles received: {count}")
                all_data.append(month_df)
                fetch_status.append({"year": year, "month": month, "month_name": month_name, "fetch_status": "SUCCESS", "candles_received": count})
            else:
                fetch_status.append({"year": year, "month": month, "month_name": month_name, "fetch_status": "NO DATA", "candles_received": 0})
            
            time_module.sleep(0.4)
        except Exception as exc:
            error_text = str(exc)
            print(f"    ERROR: {error_text}")
            fetch_status.append({"year": year, "month": month, "month_name": month_name, "fetch_status": "FAILED", "candles_received": 0, "error": error_text})

    status_df = pd.DataFrame(fetch_status)
    if not all_data:
        return pd.DataFrame(columns=CANDLE_COLUMNS), status_df

    combined_df = pd.concat(all_data, ignore_index=True).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    print(f"\nTotal 1-min candles for {symbol_name} in {year}: {len(combined_df)}")
    return convert_dataframe_to_ist(combined_df), status_df


# ============================================================
# CORE TRADING LOGIC (TRAILING STOP-LOSS)
# ============================================================

def analyze_day(trading_date, day_df):
    day_df = day_df.sort_values("timestamp").copy()
    for col in ["open", "high", "low", "close", "volume"]:
        day_df[col] = pd.to_numeric(day_df[col], errors="coerce")

    day_df["time_obj"] = day_df["timestamp"].dt.time
    result_row = {
        "date": trading_date, "year": trading_date.year, "month": trading_date.month,
        "trade_type": "No Trade", "entry_price": None, "stop_loss": None,
        "exit_price": None, "exit_reason": "No Breakout", "points": 0.0, "result": "No Trade"
    }

    if day_df.empty:
        return result_row

    day_open = float(day_df.iloc[0]["open"])
    orb_candles = day_df[(day_df["time_obj"] >= ORB_START_TIME) & (day_df["time_obj"] <= ORB_END_TIME)]

    if len(orb_candles) < 15:
        result_row["exit_reason"] = "Insufficient ORB Data"
        return result_row

    orb_high = orb_candles["high"].max()
    orb_low = orb_candles["low"].min()
    orb_midpoint = (orb_high + orb_low) / 2.0
    orb_range_pct = ((orb_high - orb_low) / day_open) * 100

    if orb_range_pct < MIN_ORB_RANGE_PCT:
        result_row["exit_reason"] = "Filtered"
        return result_row

    scan_candles = day_df[(day_df["time_obj"] >= TRADE_SCAN_START_TIME) & (day_df["time_obj"] <= TRADE_DEADLINE_TIME)]
    breakout_type = None

    for _, row in scan_candles.iterrows():
        if row["high"] > orb_high and row["low"] < orb_low:
            continue
        elif row["high"] > orb_high:
            breakout_type = "BUY"
            break
        elif row["low"] < orb_low:
            breakout_type = "SELL"
            break

    if not breakout_type:
        return result_row

    entry_candle = day_df[day_df["time_obj"] == TRADE_ENTRY_TIME]
    if entry_candle.empty:
        return result_row

    entry_price = float(entry_candle.iloc[0]["close"])
    
    if breakout_type == "BUY":
        initial_sl = orb_midpoint if entry_price > orb_high else orb_low
    else:
        initial_sl = orb_midpoint if entry_price < orb_low else orb_high

    current_stop_loss = initial_sl
    initial_risk_distance = abs(entry_price - initial_sl)
    trailing_step = initial_risk_distance * 0.20

    result_row.update({
        "trade_type": breakout_type, 
        "entry_time": str(TRADE_ENTRY_TIME), 
        "entry_price": entry_price, 
        "stop_loss": initial_sl
    })

    holding_candles = day_df[(day_df["time_obj"] > TRADE_ENTRY_TIME) & (day_df["time_obj"] <= EXIT_TIME)]
    exit_price, exit_reason = None, "EOD Square-off"

    for _, row in holding_candles.iterrows():
        if breakout_type == "BUY" and row["close"] <= current_stop_loss:
            exit_price, exit_reason = float(current_stop_loss), f"Trailing Stop Loss Hit at {row['time_obj']}"
            break
        elif breakout_type == "SELL" and row["close"] >= current_stop_loss:
            exit_price, exit_reason = float(current_stop_loss), f"Trailing Stop Loss Hit at {row['time_obj']}"
            break

        if breakout_type == "BUY":
            new_sl = current_stop_loss + trailing_step
            if new_sl <= entry_price:
                current_stop_loss = new_sl
        else:
            new_sl = current_stop_loss - trailing_step
            if new_sl >= entry_price:
                current_stop_loss = new_sl

    if exit_price is None:
        exit_price = float(holding_candles.iloc[-1]["close"]) if not holding_candles.empty else entry_price

    points = (exit_price - entry_price) if breakout_type == "BUY" else (entry_price - exit_price)
    result_row.update({
        "exit_price": round(exit_price, 2), 
        "exit_reason": exit_reason, 
        "points": round(points, 2), 
        "result": "Win" if points > 0 else ("Loss" if points < 0 else "Breakeven")
    })

    return result_row


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    smart_api = None
    try:
        # Load Nifty 50 stocks list
        if not os.path.exists(TOKENS_CSV_PATH):
            raise FileNotFoundError(f"Tokens file not found at: {TOKENS_CSV_PATH}")

        tokens_df = pd.read_csv(TOKENS_CSV_PATH)
        # Clean column names
        tokens_df.columns = tokens_df.columns.str.strip()
        
        # Verify columns exist
        if "Symbol_Token" not in tokens_df.columns or "Trading_Symbol" not in tokens_df.columns:
            raise ValueError("CSV must contain 'Symbol_Token' and 'Trading_Symbol' columns.")

        now_ist = datetime.now(IST)
        smart_api = login_to_smartapi()

        all_stock_summaries = []
        master_trade_results = []

        for _, stock_row in tokens_df.iterrows():
            symbol_token = str(stock_row["Symbol_Token"]).strip()
            trading_symbol = str(stock_row["Trading_Symbol"]).strip()
            company_name = str(stock_row.get("Company", trading_symbol)).strip()

            print(lambda: None) # placeholder spacing
            print("*" * 100)
            print(f"PROCESSING STOCK: {company_name} ({trading_symbol}) | Token: {symbol_token}")
            print("*" * 100)

            all_results, all_status = [], []
            for year in range(START_YEAR, now_ist.year + 1):
                raw_df, status_df = fetch_year_historical_data(smart_api, symbol_token, trading_symbol, year, now_ist)
                all_status.append(status_df)

                if not raw_df.empty:
                    raw_df["trading_date"] = raw_df["timestamp"].dt.date
                    daily = [analyze_day(d_date, d_df) for d_date, d_df in raw_df.groupby("trading_date")]
                    if daily:
                        df_daily = pd.DataFrame(daily)
                        df_daily["symbol"] = trading_symbol
                        df_daily["company"] = company_name
                        all_results.append(df_daily)

            if not all_results:
                print(f"No data analyzed for {trading_symbol}.")
                continue

            results_df = pd.concat(all_results, ignore_index=True).sort_values("date").reset_index(drop=True)
            master_trade_results.append(results_df)

            # Stock-level aggregation
            traded = results_df[results_df["result"].isin(["Win", "Loss", "Breakeven"])].copy()
            wins = int((traded["result"] == "Win").sum())
            losses = int((traded["result"] == "Loss").sum())
            total_trades = len(traded)
            win_rate = (wins / total_trades * 100) if total_trades else 0.0
            net_points = float(traded["points"].sum()) if not traded.empty else 0.0

            all_stock_summaries.append({
                "Company": company_name,
                "Trading_Symbol": trading_symbol,
                "Symbol_Token": symbol_token,
                "Total_Days_Analyzed": len(results_df),
                "Trades_Taken": total_trades,
                "Wins": wins,
                "Losses": losses,
                "Win_Rate_Pct": round(win_rate, 2),
                "Net_Points": round(net_points, 2)
            })

        # Final Master Summary across all stocks
        if all_stock_summaries:
            summary_df = pd.DataFrame(all_stock_summaries).sort_values(by="Net_Points", ascending=False).reset_index(drop=True)
            
            print("\n" + "=" * 120)
            print("OVERALL NIFTY 50 STOCKS PERFORMANCE SUMMARY (SORTED BY NET POINTS)")
            print("=" * 120)
            print(summary_df.to_string(index=False))
            print("=" * 120)

            # Save to CSV
            summary_df.to_csv(OUTPUT_CSV_PATH, index=False)
            print(f"\nSuccessfully saved summary results to: {OUTPUT_CSV_PATH}")

            if master_trade_results:
                master_df = pd.concat(master_trade_results, ignore_index=True)
                trades_csv_path = OUTPUT_CSV_PATH.replace("_summary_results.csv", "_all_trades_detailed.csv")
                master_df.to_csv(trades_csv_path, index=False)
                print(f"Successfully saved detailed trade log to: {trades_csv_path}")

        else:
            print("No stock data successfully processed.")

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