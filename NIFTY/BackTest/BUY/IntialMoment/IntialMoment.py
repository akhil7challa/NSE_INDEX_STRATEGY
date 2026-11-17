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
SYMBOL_TOKEN = "99926000"  # NIFTY 50

INTERVAL = "ONE_MINUTE"
IST = ZoneInfo("Asia/Kolkata")

START_YEAR = 2015

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

CANDLE_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume"
]


# ============================================================
# STRATEGY RULES (USER CUSTOM GAP RULES)
# ============================================================

GAP_THRESHOLD_PCT = 0.0015
C1_MIN_RANGE = 20.0
C1_MAX_RANGE = 150.0
ENTRY_CUTOFF_TIME = time(10, 0)
EOD_EXIT_TIME = time(15, 15)
PARTIAL_EXIT_FRACTION = 0.50
TRAILING_STEP_RATIO = 0.20  # 20% of initial risk per bar trailing step


# ============================================================
# SMARTAPI LOGIN
# ============================================================

def login_to_smartapi():
    if not all([API_KEY, CLIENT_CODE, PASSWORD, TOTP_SECRET]):
        raise ValueError("Missing SmartAPI credentials.")

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

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()

    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(
            IST, ambiguous="NaT", nonexistent="shift_forward"
        )
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
                df = pd.DataFrame(data, columns=CANDLE_COLUMNS)
                return convert_dataframe_to_ist(df)
            last_error = RuntimeError(f"Historical API response: {response}")
        except Exception as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            time_module.sleep(delay)

    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts: {last_error}")


def fetch_year_historical_data(smart_api, year, now_ist):
    all_data = []
    fetch_status = []
    today = now_ist.date()

    print("\n" + "=" * 90)
    print(f"FETCHING 1-MINUTE DATA FOR YEAR {year}")
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

        print(f"\n[FETCHING MONTH] {month_name} {year}")
        print(f"   Time Range -> From: {start_dt} | To: {end_dt}")

        try:
            month_df = fetch_historical_data(smart_api, start_dt, end_dt)
            if month_df.empty:
                print(f"   [NO DATA] Received 0 candles for {month_name} {year}")
                fetch_status.append({
                    "year": year, "month": month, "month_name": month_name,
                    "fetch_status": "NO DATA", "candles_received": 0
                })
                continue

            month_df = month_df[
                (month_df["timestamp"].dt.year == year)
                & (month_df["timestamp"].dt.month == month)
                & (month_df["timestamp"] <= end_dt)
            ].copy()

            if not month_df.empty:
                count = len(month_df)
                first_ts = month_df['timestamp'].min()
                last_ts = month_df['timestamp'].max()
                print(f"   [SUCCESS] Candles received: {count} | Range: {first_ts} to {last_ts}")
                
                all_data.append(month_df)
                fetch_status.append({
                    "year": year, "month": month, "month_name": month_name,
                    "fetch_status": "SUCCESS", "candles_received": count
                })
            else:
                print(f"   [NO DATA] Filtered dataframe empty for {month_name} {year}")
                fetch_status.append({
                    "year": year, "month": month, "month_name": month_name,
                    "fetch_status": "NO DATA", "candles_received": 0
                })
            time_module.sleep(0.3)
        except Exception as exc:
            error_text = str(exc)
            print(f"   [ERROR] Failed fetching {month_name} {year}: {error_text}")
            fetch_status.append({
                "year": year, "month": month, "month_name": month_name,
                "fetch_status": "FAILED", "candles_received": 0, "error": error_text
            })

    status_df = pd.DataFrame(fetch_status)
    if not all_data:
        return pd.DataFrame(columns=CANDLE_COLUMNS), status_df

    combined_df = (
        pd.concat(all_data, ignore_index=True)
        .drop_duplicates(subset=["timestamp"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    print(f"\nTotal 1-min candles successfully compiled for {year}: {len(combined_df)}")
    return convert_dataframe_to_ist(combined_df), status_df


# ============================================================
# OPTIMIZED STRATEGY LOGIC (VECTORIZED & NUMPY ACCELERATED)
# ============================================================

def classify_candle_1(c1_high, c1_low, c1_open, c1_close, gap_type):
    c_range = c1_high - c1_low
    if c_range <= 0 or c_range < C1_MIN_RANGE or c_range > C1_MAX_RANGE:
        return None, None

    body = abs(c1_close - c1_open)
    body_ratio = body / c_range
    is_green = c1_close > c1_open
    clv = (c1_close - c1_low) / c_range

    is_strong_bull = is_green and (
        body_ratio >= 0.70 or (body_ratio >= 0.45 and clv >= 0.75)
    )
    is_strong_bear = (not is_green) and (
        body_ratio >= 0.70 or (body_ratio >= 0.45 and clv <= 0.25)
    )

    if gap_type == "GAP_UP" and is_strong_bull:
        return 1, "LONG"
    elif gap_type == "GAP_DOWN" and is_strong_bear:
        return 2, "SHORT"

    return None, None


def _base_result_row(trading_date):
    return {
        "date": trading_date,
        "year": trading_date.year,
        "month": trading_date.month,
        "trade_type": "No Trade",
        "scenario": None,
        "gap_type": None,
        "gap_percent": None,
        "entry_time": None,
        "entry_price": None,
        "stop_loss": None,
        "initial_risk": None,
        "target_1": None,
        "target_2": None,
        "exit_time": None,
        "exit_price": None,
        "exit_reason": "No Qualifying Gap",
        "points": 0.0,
        "result": "No Trade",
    }


def analyze_day_fast(trading_date, day_df, prev_close):
    result = _base_result_row(trading_date)

    if day_df is None or day_df.empty or prev_close is None or prev_close <= 0:
        result["exit_reason"] = "Missing Current/Previous Session Data"
        return result

    # Convert day dataframe columns to NumPy arrays for blazing fast iteration
    opens = day_df["open"].to_numpy()
    highs = day_df["high"].to_numpy()
    lows = day_df["low"].to_numpy()
    closes = day_df["close"].to_numpy()
    times = day_df["time_obj"].to_numpy()
    time_strs = day_df["time_str"].to_numpy()

    # Filter session candles between 09:15 and 15:30 using vectorized time checks
    t_915 = time(9, 15)
    t_1530 = time(15, 30)
    session_mask = (times >= t_915) & (times < t_1530)

    if session_mask.sum() < 6:
        result["exit_reason"] = "Insufficient Session Data"
        return result

    s_opens = opens[session_mask]
    s_highs = highs[session_mask]
    s_lows = lows[session_mask]
    s_closes = closes[session_mask]
    s_times = times[session_mask]
    s_time_strs = time_strs[session_mask]

    # Aggregate 09:15 to 09:19 for C1 (5-min opening candle)
    c1_mask = (s_times >= time(9, 15)) & (s_times < time(9, 20))
    if not c1_mask.any():
        result["exit_reason"] = "Missing 09:15 Candle Data"
        return result

    c1_open = s_opens[c1_mask][0]
    c1_high = s_highs[c1_mask].max()
    c1_low = s_lows[c1_mask].min()
    c1_close = s_closes[c1_mask][-1]

    gap_pct = (c1_open - prev_close) / prev_close

    if gap_pct >= GAP_THRESHOLD_PCT:
        gap_type = "GAP_UP"
    elif gap_pct <= -GAP_THRESHOLD_PCT:
        gap_type = "GAP_DOWN"
    else:
        result["exit_reason"] = "Gap Below Threshold"
        result["gap_percent"] = round(gap_pct * 100, 4)
        return result

    scenario, direction = classify_candle_1(c1_high, c1_low, c1_open, c1_close, gap_type)
    if scenario is None:
        result["exit_reason"] = "Candle 1 Range/Classification Filter"
        result.update({
            "gap_type": gap_type,
            "gap_percent": round(gap_pct * 100, 4),
        })
        return result

    c1_range = c1_high - c1_low
    c1_mid = c1_low + (c1_range / 2.0) if direction == "LONG" else c1_high - (c1_range / 2.0)

    # Execution slice from 09:20 onwards
    exec_mask = s_times >= time(9, 20)
    e_opens = s_opens[exec_mask]
    e_highs = s_highs[exec_mask]
    e_lows = s_lows[exec_mask]
    e_closes = s_closes[exec_mask]
    e_times = s_times[exec_mask]
    e_time_strs = s_time_strs[exec_mask]

    if len(e_opens) == 0:
        result["exit_reason"] = "No Execution Candles"
        result.update({"scenario": scenario, "gap_type": gap_type, "gap_percent": round(gap_pct * 100, 4)})
        return result

    pullback_reached = False
    in_trade = False
    entry_price = None
    entry_time = None
    setup_stop = c1_low if direction == "LONG" else c1_high
    initial_risk = None
    current_sl = setup_stop
    tg1_hit = False
    realized_pnl = 0.0
    final_exit = None
    final_exit_time = None
    final_exit_reason = None

    t_1000 = "10:00"
    t_1515 = "15:15"

    for idx in range(len(e_opens)):
        bar_time = e_time_strs[idx]
        bar_open = e_opens[idx]
        bar_high = e_highs[idx]
        bar_low = e_lows[idx]
        bar_close = e_closes[idx]

        prev_close_val = e_closes[idx - 1] if idx > 0 else e_closes[0]
        prev_open_val = e_opens[idx - 1] if idx > 0 else e_opens[0]

        if in_trade:
            step_pts = initial_risk * TRAILING_STEP_RATIO
            if direction == "LONG":
                current_sl = max(current_sl, current_sl + step_pts)
            else:
                current_sl = min(current_sl, current_sl - step_pts)

            stop_hit = (bar_low <= current_sl) if direction == "LONG" else (bar_high >= current_sl)
            if stop_hit:
                remaining_weight = 1.0 - PARTIAL_EXIT_FRACTION if tg1_hit else 1.0
                remaining_points = (current_sl - entry_price) if direction == "LONG" else (entry_price - current_sl)
                realized_pnl += remaining_points * remaining_weight
                final_exit = current_sl
                final_exit_time = bar_time
                final_exit_reason = "SL_TRAILED"
                break

            target_1 = c1_high if direction == "LONG" else c1_low
            target_2 = entry_price + (2.0 * initial_risk) if direction == "LONG" else entry_price - (2.0 * initial_risk)

            target_1_hit_now = (not tg1_hit) and ((bar_high >= target_1) if direction == "LONG" else (bar_low <= target_1))
            if target_1_hit_now:
                first_leg = (target_1 - entry_price) if direction == "LONG" else (entry_price - target_1)
                realized_pnl += first_leg * PARTIAL_EXIT_FRACTION
                tg1_hit = True
                current_sl = max(current_sl, entry_price) if direction == "LONG" else min(current_sl, entry_price)

            target_2_hit_now = (bar_high >= target_2) if direction == "LONG" else (bar_low <= target_2)
            if target_2_hit_now:
                remaining_weight = 1.0 - PARTIAL_EXIT_FRACTION if tg1_hit else 1.0
                second_leg = (target_2 - entry_price) if direction == "LONG" else (entry_price - target_2)
                realized_pnl += second_leg * remaining_weight
                final_exit = target_2
                final_exit_time = bar_time
                final_exit_reason = "TG2"
                break

            if bar_time >= t_1515:
                remaining_weight = 1.0 - PARTIAL_EXIT_FRACTION if tg1_hit else 1.0
                remaining_points = (bar_close - entry_price) if direction == "LONG" else (entry_price - bar_close)
                realized_pnl += remaining_points * remaining_weight
                final_exit = bar_close
                final_exit_time = bar_time
                final_exit_reason = "EOD"
                break

            continue

        if bar_time > t_1000:
            break

        if not pullback_reached:
            if direction == "LONG" and bar_low <= c1_mid:
                pullback_reached = True
            elif direction == "SHORT" and bar_high >= c1_mid:
                pullback_reached = True

        if pullback_reached:
            is_bar_green = bar_close > bar_open
            trigger_met = False

            if direction == "LONG":
                if is_bar_green and bar_close > prev_close_val:
                    trigger_met = True
                elif (not is_bar_green) and bar_close > prev_open_val:
                    trigger_met = True
            else:
                if is_bar_green and bar_close < prev_open_val:
                    trigger_met = True
                elif (not is_bar_green) and bar_close < prev_close_val:
                    trigger_met = True

            if trigger_met:
                entry_price = bar_close
                entry_time = bar_time
                current_sl = setup_stop
                initial_risk = abs(entry_price - current_sl)
                if initial_risk <= 0:
                    result["exit_reason"] = "Invalid Initial Risk"
                    return result
                in_trade = True
                continue

    if in_trade and final_exit is None:
        eligible_mask = e_time_strs <= t_1515
        if eligible_mask.any():
            last_idx = np.where(eligible_mask)[0][-1]
            exit_px = e_closes[last_idx]
            remaining_weight = 1.0 - PARTIAL_EXIT_FRACTION if tg1_hit else 1.0
            remaining_points = (exit_px - entry_price) if direction == "LONG" else (entry_price - exit_px)
            realized_pnl += remaining_points * remaining_weight
            final_exit = exit_px
            final_exit_time = e_time_strs[last_idx]
            final_exit_reason = "EOD"

    if not in_trade or final_exit is None:
        result.update({
            "scenario": scenario,
            "gap_type": gap_type,
            "gap_percent": round(gap_pct * 100, 4),
            "exit_reason": "No Entry Trigger",
        })
        return result

    points = float(realized_pnl)
    result.update({
        "trade_type": direction,
        "scenario": scenario,
        "gap_type": gap_type,
        "gap_percent": round(gap_pct * 100, 4),
        "entry_time": entry_time,
        "entry_price": round(float(entry_price), 2),
        "stop_loss": round(float(setup_stop), 2),
        "initial_risk": round(float(initial_risk), 2),
        "exit_time": final_exit_time,
        "exit_price": round(float(final_exit), 2),
        "exit_reason": final_exit_reason,
        "points": round(points, 2),
        "result": ("Win" if points > 0 else "Loss" if points < 0 else "Breakeven"),
    })
    return result


def analyze_advanced_metrics(results_df):
    traded = results_df[results_df["result"].isin(["Win", "Loss", "Breakeven"])].copy()
    if traded.empty:
        print("No trades available for advanced metrics.")
        return

    traded = traded.sort_values("date").reset_index(drop=True)
    cumulative = traded["points"].cumsum()
    equity_curve = pd.concat([pd.Series([0.0]), cumulative.reset_index(drop=True)], ignore_index=True)
    running_peak = equity_curve.cummax()
    drawdown = equity_curve - running_peak
    max_drawdown = float(drawdown.min())

    daily_points = results_df.groupby("date")["points"].sum().sort_index()
    daily_mean = daily_points.mean()
    daily_std = daily_points.std(ddof=1)
    daily_sharpe_proxy = (daily_mean / daily_std) * np.sqrt(252) if daily_std > 0 else 0.0

    wins = int((traded["result"] == "Win").sum())
    losses = int((traded["result"] == "Loss").sum())
    breakevens = int((traded["result"] == "Breakeven").sum())
    total_trades = len(traded)

    print("\n" + "=" * 90)
    print("ADVANCED RISK-ADJUSTED PERFORMANCE METRICS")
    print("=" * 90)
    print(f"Maximum Drawdown (MDD)          : {max_drawdown:.2f} points")
    print(f"Daily Point-Based Sharpe Proxy  : {daily_sharpe_proxy:.2f}")
    print(f"Total Trades                    : {total_trades}")
    print(f"Winning Trades                  : {wins}")
    print(f"Losing Trades                   : {losses}")
    print(f"Breakeven Trades                : {breakevens}")
    print("=" * 90)


def build_monthly_summary(results_df, fetch_status_df):
    monthly_rows = []
    years = sorted(set(results_df["year"].unique()) | set(fetch_status_df["year"].unique()))

    for year in years:
        for month in range(1, 13):
            m_df = results_df[(results_df["year"] == year) & (results_df["month"] == month)]
            traded = m_df[m_df["result"].isin(["Win", "Loss", "Breakeven"])]
            wins = int((traded["result"] == "Win").sum())
            losses = int((traded["result"] == "Loss").sum())
            breakevens = int((traded["result"] == "Breakeven").sum())
            total = len(traded)

            status_row = fetch_status_df[(fetch_status_df["year"] == year) & (fetch_status_df["month"] == month)]
            fetch_status = status_row.iloc[0]["fetch_status"] if not status_row.empty else "UNKNOWN"
            candles_received = int(status_row.iloc[0]["candles_received"]) if not status_row.empty else 0

            monthly_rows.append({
                "year": year, "month": month, "month_name": calendar.month_name[month],
                "fetch_status": fetch_status, "candles_received": candles_received,
                "trading_days_analyzed": len(m_df), "trades_taken": total,
                "wins": wins, "losses": losses, "breakevens": breakevens,
                "win_rate_percent": round(wins / total * 100 if total else 0.0, 2),
                "total_points": round(float(traded["points"].sum()), 2),
                "no_trade": int((m_df["result"] == "No Trade").sum())
            })
    return pd.DataFrame(monthly_rows)


def build_yearly_summary(results_df):
    yearly_rows = []
    if results_df.empty:
        return pd.DataFrame()

    for year in sorted(results_df["year"].unique()):
        y_df = results_df[results_df["year"] == year]
        traded = y_df[y_df["result"].isin(["Win", "Loss", "Breakeven"])]
        wins = int((traded["result"] == "Win").sum())
        losses = int((traded["result"] == "Loss").sum())
        breakevens = int((traded["result"] == "Breakeven").sum())
        total = len(traded)

        gross_profit = float(traded[traded["points"] > 0]["points"].sum())
        gross_loss = float(abs(traded[traded["points"] < 0]["points"].sum()))

        yearly_rows.append({
            "year": year, "trading_days_analyzed": len(y_df), "trades_taken": total,
            "wins": wins, "losses": losses, "breakevens": breakevens,
            "win_rate_percent": round(wins / total * 100 if total else 0.0, 2),
            "gross_profit": round(gross_profit, 2), "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
            "total_points": round(float(traded["points"].sum()), 2),
            "no_trade": int((y_df["result"] == "No Trade").sum())
        })
    return pd.DataFrame(yearly_rows)


def print_overall_summary(results_df, start_year, current_year):
    traded = results_df[results_df["result"].isin(["Win", "Loss", "Breakeven"])].copy()
    wins = int((traded["result"] == "Win").sum())
    losses = int((traded["result"] == "Loss").sum())
    breakevens = int((traded["result"] == "Breakeven").sum())
    total_trades = len(traded)

    win_rate_all = wins / total_trades * 100 if total_trades else 0.0
    gross_profit = float(traded[traded["points"] > 0]["points"].sum())
    gross_loss = float(abs(traded[traded["points"] < 0]["points"].sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    overall_points = float(traded["points"].sum())

    print("\n" + "=" * 100)
    print(f"OVERALL SUMMARY REPORT: {start_year} TO {current_year}")
    print("=" * 100)
    print(f"Total Trading Days Analyzed       : {len(results_df)}")
    print(f"Total Trades Taken                : {total_trades}")
    print(f"Winning Trades                    : {wins}")
    print(f"Losing Trades                     : {losses}")
    print(f"Breakeven Trades                  : {breakevens}")
    print(f"Win Rate                          : {win_rate_all:.2f}%")
    print(f"Profit Factor                     : {profit_factor:.2f}")
    print(f"Total Points Captured (Net)       : {overall_points:.2f}")
    print("=" * 100)


def main():
    smart_api = None
    try:
        now_ist = datetime.now(IST)
        smart_api = login_to_smartapi()

        all_raw = []
        all_status = []

        for year in range(START_YEAR, now_ist.year + 1):
            raw_df, status_df = fetch_year_historical_data(smart_api, year, now_ist)
            all_status.append(status_df)
            if not raw_df.empty:
                all_raw.append(raw_df)

        if not all_raw:
            print("No historical data received.")
            return

        print("\n" + "=" * 90)
        print("CONCATENATING AND PRE-PROCESSING CANDLE DATA...")
        print("=" * 90)
        raw_df = (
            pd.concat(all_raw, ignore_index=True)
            .drop_duplicates(subset=["timestamp"])
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

        # Pre-convert numeric columns globally once (huge speedup)
        for col in ["open", "high", "low", "close", "volume"]:
            raw_df[col] = pd.to_numeric(raw_df[col], errors="coerce")

        raw_df["trading_date"] = raw_df["timestamp"].dt.date
        raw_df["time_obj"] = raw_df["timestamp"].dt.time
        raw_df["time_str"] = raw_df["timestamp"].dt.strftime("%H:%M")

        trading_dates = sorted(raw_df["trading_date"].unique())

        print("PRE-GROUPING DATA BY TRADING DATES...")
        # Group dataframe by date once into a dictionary for O(1) lookups
        grouped_days = {date: group for date, group in raw_df.groupby("trading_date")}

        # Pre-calculate previous closes
        print("RUNNING BATCH VECTORIZED BACKTEST SIMULATION...")
        start_time = time_module.time()

        daily_results = []
        for idx, trading_date in enumerate(trading_dates):
            day_df = grouped_days.get(trading_date)
            prev_close = None
            if idx > 0:
                prev_date = trading_dates[idx - 1]
                prev_df = grouped_days.get(prev_date)
                if prev_df is not None and not prev_df.empty:
                    prev_close = float(prev_df.iloc[-1]["close"])

            daily_results.append(analyze_day_fast(trading_date, day_df, prev_close))

        print(f"Backtest simulation completed in {time_module.time() - start_time:.2f} seconds.")

        results_df = pd.DataFrame(daily_results).sort_values("date").reset_index(drop=True)
        fetch_status_df = pd.concat(all_status, ignore_index=True) if all_status else pd.DataFrame()

        monthly_df = build_monthly_summary(results_df, fetch_status_df)
        yearly_df = build_yearly_summary(results_df)

        print_overall_summary(results_df, START_YEAR, now_ist.year)
        analyze_advanced_metrics(results_df)

        print("\n" + "=" * 100)
        print("YEARLY SUMMARY REPORT")
        print("=" * 100)
        print(yearly_df.to_string(index=False))
        print("=" * 100)

        results_df.to_csv("user_rules_daily_results.csv", index=False)
        monthly_df.to_csv("user_rules_monthly_summary.csv", index=False)
        yearly_df.to_csv("user_rules_yearly_summary.csv", index=False)
        print("\nCSV files saved successfully.")

    except Exception as exc:
        print(f"\nERROR: {exc}")
        raise
    finally:
        if smart_api:
            try:
                smart_api.terminateSession(CLIENT_CODE)
            except Exception:
                pass


if __name__ == "__main__":
    main()