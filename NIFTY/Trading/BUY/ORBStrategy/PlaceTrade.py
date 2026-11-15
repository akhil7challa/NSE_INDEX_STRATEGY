import os
import sys
import time
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

# "YES" = actual live market entry & management, "NO" = simulation / backtest mode
LIVE_TRADING = "NO"

ANALYSIS_FILE = "option_orb_analysis.csv"
TRADE_LOG = "trade_execution_results.csv"

OPTION_EXCHANGE = "NFO"
CANDLE_INTERVAL = "ONE_MINUTE"
POLL_SECONDS = 15
LOTS = 1

ENTRY_TIME = dt_time(15, 9)
EXIT_TIME = dt_time(15, 14)

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("option_orb_part3")


# ============================================================
# HELPERS & API
# ============================================================

def login():
    api = SmartConnect(api_key=API_KEY)
    otp = pyotp.TOTP(TOTP_SECRET).now()
    response = api.generateSession(CLIENT_CODE, PASSWORD, otp)
    if not isinstance(response, dict) or not response.get("status") or not response.get("data"):
        raise RuntimeError(f"Login failed: {response}")
    log.info("Angel One login successful.")
    return api


def fetch_candles(api, exchange, token, trading_day, from_time=dt_time(9, 15), to_time=dt_time(15, 30)):
    start = datetime.combine(trading_day, from_time, tzinfo=IST)
    end = datetime.combine(trading_day, to_time, tzinfo=IST)

    params = {
        "exchange": exchange,
        "symboltoken": str(token),
        "interval": CANDLE_INTERVAL,
        "fromdate": start.strftime("%Y-%m-%d %H:%M"),
        "todate": end.strftime("%Y-%m-%d %H:%M"),
    }

    response = api.getCandleData(params)
    rows = response.get("data") if isinstance(response, dict) else None
    if rows is None:
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


def place_market_order(api, symbol, token, transaction_type, quantity, simulated_price=None):
    if LIVE_TRADING != "YES":
        price = simulated_price if simulated_price is not None else 100.0
        log.warning("PAPER MODE: Market %s order for %s (Qty: %d) simulated at price %.2f.", transaction_type, symbol, quantity, price)
        return {"order_id": "PAPER_ID", "average_price": price, "quantity": quantity}

    params = {
        "variety": "NORMAL",
        "tradingsymbol": symbol,
        "symboltoken": str(token),
        "transactiontype": transaction_type,
        "exchange": OPTION_EXCHANGE,
        "ordertype": "MARKET",
        "producttype": "INTRADAY",
        "duration": "DAY",
        "price": "0",
        "squareoff": "0",
        "stoploss": "0",
        "quantity": str(quantity),
        "ordertag": "OPTIONORB",
    }

    response = api.placeOrderFullResponse(params)
    if not isinstance(response, dict) or not response.get("status"):
        raise RuntimeError(f"Order failed: {response}")

    data = response.get("data") or {}
    order_id = str(data.get("orderid") or data.get("orderId"))
    log.info("Market order placed successfully. Order ID: %s", order_id)
    
    time.sleep(2)
    book = api.orderBook()
    avg_price = 100.0
    for order in (book.get("data") or []):
        if str(order.get("orderid", order.get("orderId", ""))) == order_id:
            avg_price = float(order.get("averageprice") or 100.0)
            break

    return {"order_id": order_id, "average_price": avg_price, "quantity": quantity}


# ============================================================
# STRATEGY EXECUTION & TRAILING STOP-LOSS MANAGEMENT
# ============================================================

def execute_strategy():
    log.info("Starting Part 3: Market Entry at 15:09 and Exit at 15:14 candle close...")

    if not os.path.exists(ANALYSIS_FILE):
        log.error("Analysis file '%s' not found. Run Part 2 first.", ANALYSIS_FILE)
        return

    analysis_df = pd.read_csv(ANALYSIS_FILE)
    if analysis_df.empty:
        log.error("Analysis file '%s' is empty.", ANALYSIS_FILE)
        return

    row = analysis_df.iloc[-1]
    trading_date = datetime.strptime(str(row["date"]), "%Y-%m-%d").date()
    signal = str(row["signal"]).upper().strip()
    option_symbol = str(row["option_symbol"])
    option_token = str(row["option_token"])
    opt_orb_high = float(row["option_orb_high"])
    opt_orb_low = float(row["option_orb_low"])
    opt_orb_midpoint = float(row["option_orb_midpoint"])
    
    log.info("Loaded Contract: %s | Signal: %s | ORB High: %.2f | ORB Low: %.2f | Midpoint: %.2f",
             option_symbol, signal, opt_orb_high, opt_orb_low, opt_orb_midpoint)

    api = login()

    try:
        lot_size = 65
        quantity = lot_size * LOTS

        # Fetch candles around the session to extract exact 15:09 and 15:14 candle prices
        candles = fetch_candles(api, OPTION_EXCHANGE, option_token, trading_date, from_time=dt_time(15, 5), to_time=dt_time(15, 16))
        
        # Determine 15:09 entry close price
        entry_price = 120.0
        entry_candle = candles[candles["timestamp"].dt.time == ENTRY_TIME]
        if not entry_candle.empty:
            entry_price = float(entry_candle.iloc[0]["close"])
        elif not candles.empty:
            entry_price = float(candles.iloc[0]["close"])

        # 1. Entry Order
        if LIVE_TRADING == "YES":
            target_entry_dt = datetime.combine(trading_date, ENTRY_TIME, tzinfo=IST)
            while datetime.now(IST) < target_entry_dt:
                time.sleep(POLL_SECONDS)

        log.info("Executing MARKET BUY entry for %s at 15:09 candle close...", option_symbol)
        entry_order = place_market_order(api, option_symbol, option_token, "BUY", quantity, simulated_price=entry_price)
        entry_price = float(entry_order["average_price"])
        log.info("Entry Executed at Average Price: %.2f", entry_price)

        # ========================================================
        # 2. INITIAL STOP-LOSS (Matching Backtest Strategy Logic)
        # ========================================================
        if signal == "BUY":
            initial_sl = opt_orb_midpoint if entry_price > opt_orb_high else opt_orb_low
        else:
            initial_sl = opt_orb_midpoint if entry_price < opt_orb_low else opt_orb_high

        current_stop_loss = initial_sl
        initial_risk_distance = abs(entry_price - initial_sl)
        trailing_step = initial_risk_distance * 0.20

        log.info("Initial Stop-Loss set to: %.2f (Risk Distance: %.2f, Step: %.2f)", 
                 current_stop_loss, initial_risk_distance, trailing_step)

        exit_reason = "15:14 Candle Close Square-off"
        exit_price = entry_price

        # ========================================================
        # 3. TRAILING STOP-LOSS RATIO / CANDLE MONITORING
        # ========================================================
        if LIVE_TRADING != "YES":
            active_candles = candles[(candles["timestamp"].dt.time >= ENTRY_TIME) & (candles["timestamp"].dt.time <= EXIT_TIME)]
            if active_candles.empty:
                active_candles = candles.tail(1)

            for _, c_row in active_candles.iterrows():
                c_close = float(c_row["close"])
                t_obj = c_row["timestamp"].time()

                # A. Check Stop-Loss Hit on candle close
                if signal == "BUY" and c_close <= current_stop_loss:
                    exit_price = float(current_stop_loss)
                    exit_reason = f"Trailing Stop Loss Hit at {t_obj}"
                    break
                elif signal == "SELL" and c_close >= current_stop_loss:
                    exit_price = float(current_stop_loss)
                    exit_reason = f"Trailing Stop Loss Hit at {t_obj}"
                    break

                # B. Ratchet stop-loss 20% closer toward entry on each 1-min close
                if signal == "BUY":
                    new_sl = current_stop_loss + trailing_step
                    if new_sl <= entry_price:
                        current_stop_loss = new_sl
                else:
                    new_sl = current_stop_loss - trailing_step
                    if new_sl >= entry_price:
                        current_stop_loss = new_sl

                exit_price = c_close  # Default to latest close if no stop hit

        # 4. Exit Order Execution
        log.info("Executing MARKET SELL exit for %s due to: %s", option_symbol, exit_reason)
        exit_order = place_market_order(api, option_symbol, option_token, "SELL", quantity, simulated_price=exit_price)
        if LIVE_TRADING == "YES":
            exit_price = float(exit_order["average_price"])

        pnl = (exit_price - entry_price) * quantity if signal == "BUY" else (entry_price - exit_price) * quantity

        # 5. Save Trade Log Results
        trade_result = {
            "date": str(trading_date),
            "symbol": option_symbol,
            "signal": signal,
            "entry_price": entry_price,
            "stop_loss": initial_sl,
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl": round(pnl, 2),
            "exit_reason": exit_reason,
        }
        df_trade = pd.DataFrame([trade_result])
        header = not os.path.exists(TRADE_LOG)
        df_trade.to_csv(TRADE_LOG, mode='a', index=False, header=header)

        print("\n" + "=" * 70)
        print(f"TRADE EXECUTION & P&L REPORT - {option_symbol}")
        print("=" * 70)
        print(f"Trading Date     : {trading_date}")
        print(f"Entry Time / Px  : 15:09 (Close ~ {entry_price:.2f})")
        print(f"Initial Stop-Loss: {initial_sl:.2f}")
        print(f"Exit Time / Px   : 15:14 (Close ~ {exit_price:.2f})")
        print(f"Quantity         : {quantity}")
        print(f"Net P&L (INR)    : {pnl:.2f}")
        print(f"Exit Reason      : {exit_reason}")
        print("=" * 70)
        log.info("Trade results successfully logged to %s", TRADE_LOG)

    finally:
        if api is not None:
            try:
                api.terminateSession(CLIENT_CODE)
            except Exception:
                pass


if __name__ == "__main__":
    execute_strategy()