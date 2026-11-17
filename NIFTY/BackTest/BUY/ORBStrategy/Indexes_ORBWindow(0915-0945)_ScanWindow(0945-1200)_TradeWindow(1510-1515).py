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

# Set these as environment variables before running the script.
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
# STRATEGY RULES
# ============================================================

ORB_START_TIME = time(9, 15)
ORB_END_TIME = time(9, 44)

TRADE_SCAN_START_TIME = time(9, 45)
TRADE_DEADLINE_TIME = time(11, 59)

TRADE_ENTRY_TIME = time(14, 59)
EXIT_TIME = time(15, 14)

MIN_ORB_RANGE_PCT = 0.0

TRAILING_STEP_PERCENT = 0.20


# ============================================================
# SMARTAPI LOGIN
# ============================================================

def login_to_smartapi():

    if not all([API_KEY, CLIENT_CODE, PASSWORD, TOTP_SECRET]):
        raise ValueError(
            "Missing SmartAPI credentials. Set the following "
            "environment variables: SMARTAPI_API_KEY, "
            "SMARTAPI_CLIENT_CODE, SMARTAPI_PASSWORD, "
            "SMARTAPI_TOTP_SECRET."
        )

    smart_api = SmartConnect(api_key=API_KEY)

    totp = pyotp.TOTP(TOTP_SECRET).now()

    session_data = smart_api.generateSession(
        CLIENT_CODE,
        PASSWORD,
        totp
    )

    if not session_data or not session_data.get("status"):
        raise RuntimeError(
            f"SmartAPI login failed: {session_data}"
        )

    return smart_api


# ============================================================
# TIMEZONE & DATA CONVERSION
# ============================================================

def convert_dataframe_to_ist(df):

    if df.empty or "timestamp" not in df.columns:
        return df

    df = df.copy()

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        errors="coerce"
    )

    df = df.dropna(subset=["timestamp"]).copy()

    if df["timestamp"].dt.tz is None:

        df["timestamp"] = df["timestamp"].dt.tz_localize(
            IST,
            ambiguous="NaT",
            nonexistent="shift_forward"
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

            if (
                response
                and response.get("status")
                and response.get("data") is not None
            ):

                data = response["data"]

                if not data:
                    return pd.DataFrame(columns=CANDLE_COLUMNS)

                df = pd.DataFrame(
                    data,
                    columns=CANDLE_COLUMNS
                )

                return convert_dataframe_to_ist(df)

            last_error = RuntimeError(
                f"Historical API response: {response}"
            )

        except Exception as exc:

            last_error = exc

        if attempt < MAX_RETRIES:

            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))

            print(
                f"   Attempt {attempt}/{MAX_RETRIES} failed: "
                f"{last_error}"
            )

            print(f"   Retrying in {delay} seconds...")

            time_module.sleep(delay)

    raise RuntimeError(
        f"Failed after {MAX_RETRIES} attempts: {last_error}"
    )


def fetch_year_historical_data(smart_api, year, now_ist):

    all_data = []
    fetch_status = []

    today = now_ist.date()

    print("\n" + "=" * 90)
    print(
        f"FETCHING 1-MINUTE {SYMBOL_TOKEN} SYMBOL_TOKEN DATA "
        f"FOR {year}"
    )
    print("=" * 90)

    if year > today.year:

        return (
            pd.DataFrame(columns=CANDLE_COLUMNS),
            pd.DataFrame(fetch_status)
        )

    last_month = 12 if year < today.year else today.month

    for month in range(1, 13):

        month_name = calendar.month_name[month]

        if month > last_month:
            continue

        last_day = calendar.monthrange(year, month)[1]

        start_dt = datetime(
            year, month, 1, 9, 0, tzinfo=IST
        )

        end_dt = min(
            datetime(
                year, month, last_day, 15, 30, tzinfo=IST
            ),
            now_ist
        )

        if start_dt > now_ist:
            continue

        print(f"\nFetching {month_name} {year}")
        print(f"   From: {start_dt}")
        print(f"   To  : {end_dt}")

        try:

            month_df = fetch_historical_data(
                smart_api,
                start_dt,
                end_dt
            )

            if month_df.empty:

                print(
                    f"   No data received for {month_name} {year}"
                )

                fetch_status.append({
                    "year": year,
                    "month": month,
                    "month_name": month_name,
                    "fetch_status": "NO DATA",
                    "candles_received": 0
                })

                continue

            month_df = month_df[
                (month_df["timestamp"].dt.year == year)
                & (month_df["timestamp"].dt.month == month)
                & (month_df["timestamp"] <= end_dt)
            ].copy()

            if not month_df.empty:

                count = len(month_df)

                print(f"   Candles received: {count}")

                all_data.append(month_df)

                fetch_status.append({
                    "year": year,
                    "month": month,
                    "month_name": month_name,
                    "fetch_status": "SUCCESS",
                    "candles_received": count
                })

            else:

                fetch_status.append({
                    "year": year,
                    "month": month,
                    "month_name": month_name,
                    "fetch_status": "NO DATA",
                    "candles_received": 0
                })

            time_module.sleep(0.4)

        except Exception as exc:

            error_text = str(exc)

            print(f"   ERROR: {error_text}")

            fetch_status.append({
                "year": year,
                "month": month,
                "month_name": month_name,
                "fetch_status": "FAILED",
                "candles_received": 0,
                "error": error_text
            })

    status_df = pd.DataFrame(fetch_status)

    if not all_data:

        return (
            pd.DataFrame(columns=CANDLE_COLUMNS),
            status_df
        )

    combined_df = (
        pd.concat(all_data, ignore_index=True)
        .drop_duplicates(subset=["timestamp"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    print(
        f"\nTotal 1-min candles for {year}: "
        f"{len(combined_df)}"
    )

    return convert_dataframe_to_ist(combined_df), status_df


# ============================================================
# CORE TRADING LOGIC
# ============================================================

def analyze_day(trading_date, day_df):

    day_df = day_df.sort_values("timestamp").copy()

    for col in ["open", "high", "low", "close", "volume"]:

        day_df[col] = pd.to_numeric(
            day_df[col],
            errors="coerce"
        )

    day_df = day_df.dropna(
        subset=["open", "high", "low", "close"]
    ).copy()

    day_df["time_obj"] = day_df["timestamp"].dt.time

    result_row = {
        "date": trading_date,
        "year": trading_date.year,
        "month": trading_date.month,
        "trade_type": "No Trade",
        "entry_time": None,
        "entry_price": None,
        "stop_loss": None,
        "exit_price": None,
        "exit_reason": "No Breakout",
        "points": 0.0,
        "result": "No Trade"
    }

    if day_df.empty:
        return result_row

    day_open = float(day_df.iloc[0]["open"])

    # --------------------------------------------------------
    # ORB CALCULATION
    # --------------------------------------------------------

    orb_candles = day_df[
        (day_df["time_obj"] >= ORB_START_TIME)
        & (day_df["time_obj"] <= ORB_END_TIME)
    ]

    if len(orb_candles) < 15:

        result_row["exit_reason"] = "Insufficient ORB Data"

        return result_row

    orb_high = float(orb_candles["high"].max())
    orb_low = float(orb_candles["low"].min())

    orb_midpoint = (orb_high + orb_low) / 2.0

    orb_range_pct = (
        (orb_high - orb_low) / day_open
    ) * 100

    if orb_range_pct < MIN_ORB_RANGE_PCT:

        result_row["exit_reason"] = "Filtered"

        return result_row

    # --------------------------------------------------------
    # BREAKOUT DETECTION
    # --------------------------------------------------------

    scan_candles = day_df[
        (day_df["time_obj"] >= TRADE_SCAN_START_TIME)
        & (day_df["time_obj"] <= TRADE_DEADLINE_TIME)
    ]

    breakout_type = None

    for _, row in scan_candles.iterrows():

        # Ignore a candle breaking both sides of the ORB.
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

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry_candle = day_df[
        day_df["time_obj"] == TRADE_ENTRY_TIME
    ]

    if entry_candle.empty:
        return result_row

    entry_price = float(
        entry_candle.iloc[0]["close"]
    )

    # --------------------------------------------------------
    # INVALID ENTRY FILTERS
    # --------------------------------------------------------

    if breakout_type == "BUY" and entry_price < orb_low:

        result_row["exit_reason"] = (
            "BUY Entry Below ORB Low - Trade Skipped"
        )

        return result_row

    if breakout_type == "SELL" and entry_price > orb_high:

        result_row["exit_reason"] = (
            "SELL Entry Above ORB High - Trade Skipped"
        )

        return result_row

    # --------------------------------------------------------
    # INITIAL STOP-LOSS LOGIC - UNCHANGED
    # --------------------------------------------------------

    if breakout_type == "BUY":

        initial_sl = (
            orb_midpoint
            if entry_price > orb_high
            else orb_low
        )

    else:

        initial_sl = (
            orb_midpoint
            if entry_price < orb_low
            else orb_high
        )

    current_stop_loss = initial_sl

    initial_risk_distance = abs(
        entry_price - initial_sl
    )

    trailing_step = (
        initial_risk_distance * TRAILING_STEP_PERCENT
    )

    result_row.update({
        "trade_type": breakout_type,
        "entry_time": str(TRADE_ENTRY_TIME),
        "entry_price": entry_price,
        "stop_loss": initial_sl
    })

    # --------------------------------------------------------
    # HOLDING PERIOD
    # --------------------------------------------------------

    holding_candles = day_df[
        (day_df["time_obj"] > TRADE_ENTRY_TIME)
        & (day_df["time_obj"] <= EXIT_TIME)
    ]

    exit_price = None
    exit_reason = "EOD Square-off"

    for _, row in holding_candles.iterrows():

        # 1. Check current stop-loss using candle CLOSE.
        if (
            breakout_type == "BUY"
            and row["close"] <= current_stop_loss
        ):

            exit_price = float(current_stop_loss)

            exit_reason = (
                f"Trailing Stop Loss Hit at {row['time_obj']}"
            )

            break

        elif (
            breakout_type == "SELL"
            and row["close"] >= current_stop_loss
        ):

            exit_price = float(current_stop_loss)

            exit_reason = (
                f"Trailing Stop Loss Hit at {row['time_obj']}"
            )

            break

        # 2. Move stop-loss closer to entry on each 1-min close.
        # This preserves your original time-based trailing rule.

        if breakout_type == "BUY":

            new_sl = current_stop_loss + trailing_step

            if new_sl <= entry_price:
                current_stop_loss = new_sl

        else:

            new_sl = current_stop_loss - trailing_step

            if new_sl >= entry_price:
                current_stop_loss = new_sl

    # --------------------------------------------------------
    # EXIT
    # --------------------------------------------------------

    if exit_price is None:

        if not holding_candles.empty:

            exit_price = float(
                holding_candles.iloc[-1]["close"]
            )

        else:

            exit_price = entry_price

    if breakout_type == "BUY":

        points = exit_price - entry_price

    else:

        points = entry_price - exit_price

    result_row.update({
        "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason,
        "points": round(points, 2),
        "result": (
            "Win" if points > 0
            else "Loss" if points < 0
            else "Breakeven"
        )
    })

    return result_row


# ============================================================
# STREAK ANALYSIS
# ============================================================

def calculate_streaks(traded_df):

    max_win_streak = 0
    max_win_points = 0.0
    max_win_dates = (None, None)

    max_loss_streak = 0
    max_loss_points = 0.0
    max_loss_dates = (None, None)

    curr_win = 0
    curr_win_pts = 0.0
    curr_win_start = None

    curr_loss = 0
    curr_loss_pts = 0.0
    curr_loss_start = None

    for _, row in traded_df.iterrows():

        res = row["result"]
        pts = float(row["points"])
        dt_str = row["date"].strftime("%Y-%m-%d")

        if res == "Win":

            curr_loss = 0
            curr_loss_pts = 0.0
            curr_loss_start = None

            if curr_win == 0:
                curr_win_start = dt_str

            curr_win += 1
            curr_win_pts += pts

            if (
                curr_win > max_win_streak
                or (
                    curr_win == max_win_streak
                    and curr_win_pts > max_win_points
                )
            ):

                max_win_streak = curr_win
                max_win_points = curr_win_pts

                max_win_dates = (
                    curr_win_start,
                    dt_str
                )

        elif res == "Loss":

            curr_win = 0
            curr_win_pts = 0.0
            curr_win_start = None

            if curr_loss == 0:
                curr_loss_start = dt_str

            curr_loss += 1
            curr_loss_pts += pts

            if (
                curr_loss > max_loss_streak
                or (
                    curr_loss == max_loss_streak
                    and curr_loss_pts < max_loss_points
                )
            ):

                max_loss_streak = curr_loss
                max_loss_points = curr_loss_pts

                max_loss_dates = (
                    curr_loss_start,
                    dt_str
                )

        else:

            # Breakeven ends both consecutive streaks.
            curr_win = 0
            curr_win_pts = 0.0
            curr_win_start = None

            curr_loss = 0
            curr_loss_pts = 0.0
            curr_loss_start = None

    return {
        "max_win_streak": max_win_streak,
        "max_win_streak_points": round(max_win_points, 2),
        "max_win_dates": max_win_dates,

        "max_loss_streak": max_loss_streak,
        "max_loss_streak_points": round(max_loss_points, 2),
        "max_loss_dates": max_loss_dates
    }


# ============================================================
# ADVANCED ANALYTICS - CORRECTED
# ============================================================

def analyze_advanced_metrics(results_df):

    traded = results_df[
        results_df["result"].isin(
            ["Win", "Loss", "Breakeven"]
        )
    ].copy()

    if traded.empty:
        print("No trades available for advanced metrics.")
        return

    traded = traded.sort_values("date").reset_index(drop=True)

    # --------------------------------------------------------
    # 1. MAXIMUM DRAWDOWN
    # Include initial zero as the starting equity peak.
    # --------------------------------------------------------

    cumulative = traded["points"].cumsum()

    equity_curve = pd.concat(
        [
            pd.Series([0.0]),
            cumulative.reset_index(drop=True)
        ],
        ignore_index=True
    )

    running_peak = equity_curve.cummax()

    drawdown = equity_curve - running_peak

    max_drawdown = float(drawdown.min())

    # --------------------------------------------------------
    # 2. DAILY POINT-BASED SHARPE PROXY
    # Aggregate all trades by trading date.
    # Dates with no trade contribute zero P&L.
    # --------------------------------------------------------

    daily_points = (
        results_df.groupby("date")["points"]
        .sum()
        .sort_index()
    )

    daily_mean = daily_points.mean()
    daily_std = daily_points.std(ddof=1)

    if daily_std > 0:

        daily_sharpe_proxy = (
            daily_mean / daily_std
        ) * np.sqrt(252)

    else:

        daily_sharpe_proxy = 0.0

    # This is a point-based proxy, not a conventional
    # account-return Sharpe ratio.

    # --------------------------------------------------------
    # 3. WIN RATE - SHOW BREAKEVENS SEPARATELY
    # --------------------------------------------------------

    wins = int((traded["result"] == "Win").sum())
    losses = int((traded["result"] == "Loss").sum())
    breakevens = int(
        (traded["result"] == "Breakeven").sum()
    )

    total_trades = len(traded)

    win_rate_including_breakevens = (
        wins / total_trades * 100
        if total_trades else 0.0
    )

    decisive_trades = wins + losses

    win_rate_excluding_breakevens = (
        wins / decisive_trades * 100
        if decisive_trades else 0.0
    )

    mean_trade_points = traded["points"].mean()
    std_trade_points = traded["points"].std()

    # --------------------------------------------------------
    # PRINT METRICS
    # --------------------------------------------------------

    print("\n" + "=" * 90)
    print("ADVANCED RISK-ADJUSTED PERFORMANCE METRICS")
    print("=" * 90)

    print(f"Maximum Drawdown (MDD)          : {max_drawdown:.2f} points")

    print(
        f"Average Trade Return            : "
        f"{mean_trade_points:.2f} points"
    )

    print(
        f"Standard Deviation per Trade    : "
        f"{std_trade_points:.2f} points"
    )

    print(
        f"Daily Point-Based Sharpe Proxy  : "
        f"{daily_sharpe_proxy:.2f}"
    )

    print("-" * 90)

    print(f"Total Trades                    : {total_trades}")
    print(f"Winning Trades                  : {wins}")
    print(f"Losing Trades                   : {losses}")
    print(f"Breakeven Trades                : {breakevens}")

    print(
        f"Win Rate (including breakevens) : "
        f"{win_rate_including_breakevens:.2f}%"
    )

    print(
        f"Win Rate (excluding breakevens) : "
        f"{win_rate_excluding_breakevens:.2f}%"
    )

    print("=" * 90)

    print(
        "Note: Sharpe shown above is based on daily index-point "
        "P&L, not account returns."
    )


# ============================================================
# MONTHLY SUMMARY
# ============================================================

def build_monthly_summary(results_df, fetch_status_df):

    monthly_rows = []

    years = sorted(
        set(results_df["year"].unique())
        | set(fetch_status_df["year"].unique())
    )

    for year in years:

        for month in range(1, 13):

            m_df = results_df[
                (results_df["year"] == year)
                & (results_df["month"] == month)
            ]

            traded = m_df[
                m_df["result"].isin(
                    ["Win", "Loss", "Breakeven"]
                )
            ]

            wins = int(
                (traded["result"] == "Win").sum()
            )

            losses = int(
                (traded["result"] == "Loss").sum()
            )

            breakevens = int(
                (traded["result"] == "Breakeven").sum()
            )

            total = len(traded)

            status_row = fetch_status_df[
                (fetch_status_df["year"] == year)
                & (fetch_status_df["month"] == month)
            ]

            if not status_row.empty:

                fetch_status = status_row.iloc[0]["fetch_status"]

                candles_received = int(
                    status_row.iloc[0]["candles_received"]
                )

            else:

                fetch_status = "UNKNOWN"
                candles_received = 0

            monthly_rows.append({

                "year": year,
                "month": month,
                "month_name": calendar.month_name[month],

                "fetch_status": fetch_status,
                "candles_received": candles_received,

                "trading_days_analyzed": len(m_df),
                "trades_taken": total,

                "wins": wins,
                "losses": losses,
                "breakevens": breakevens,

                "win_rate_percent": round(
                    wins / total * 100 if total else 0.0,
                    2
                ),

                "win_rate_ex_breakeven": round(
                    wins / (wins + losses) * 100
                    if (wins + losses) else 0.0,
                    2
                ),

                "total_points": round(
                    float(traded["points"].sum()),
                    2
                ),

                "no_trade": int(
                    (m_df["result"] == "No Trade").sum()
                )
            })

    return pd.DataFrame(monthly_rows)


# ============================================================
# YEARLY SUMMARY
# ============================================================

def build_yearly_summary(results_df):

    yearly_rows = []

    if results_df.empty:
        return pd.DataFrame()

    for year in sorted(results_df["year"].unique()):

        y_df = results_df[
            results_df["year"] == year
        ]

        traded = y_df[
            y_df["result"].isin(
                ["Win", "Loss", "Breakeven"]
            )
        ]

        wins = int(
            (traded["result"] == "Win").sum()
        )

        losses = int(
            (traded["result"] == "Loss").sum()
        )

        breakevens = int(
            (traded["result"] == "Breakeven").sum()
        )

        total = len(traded)

        decisive_trades = wins + losses

        gross_profit = float(
            traded[traded["points"] > 0]["points"].sum()
        )

        gross_loss = float(
            abs(
                traded[traded["points"] < 0]["points"].sum()
            )
        )

        yearly_rows.append({

            "year": year,
            "trading_days_analyzed": len(y_df),
            "trades_taken": total,

            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,

            "win_rate_percent": round(
                wins / total * 100 if total else 0.0,
                2
            ),

            "win_rate_ex_breakeven": round(
                wins / decisive_trades * 100
                if decisive_trades else 0.0,
                2
            ),

            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),

            "profit_factor": round(
                gross_profit / gross_loss,
                2
            ) if gross_loss > 0 else float("inf"),

            "total_points": round(
                float(traded["points"].sum()),
                2
            ),

            "no_trade": int(
                (y_df["result"] == "No Trade").sum()
            )
        })

    return pd.DataFrame(yearly_rows)


# ============================================================
# OVERALL SUMMARY
# ============================================================

def print_overall_summary(results_df, start_year, current_year):

    traded = results_df[
        results_df["result"].isin(
            ["Win", "Loss", "Breakeven"]
        )
    ].copy()

    wins = int(
        (traded["result"] == "Win").sum()
    )

    losses = int(
        (traded["result"] == "Loss").sum()
    )

    breakevens = int(
        (traded["result"] == "Breakeven").sum()
    )

    total_trades = len(traded)

    decisive_trades = wins + losses

    win_rate_all = (
        wins / total_trades * 100
        if total_trades else 0.0
    )

    win_rate_decisive = (
        wins / decisive_trades * 100
        if decisive_trades else 0.0
    )

    gross_profit = float(
        traded[traded["points"] > 0]["points"].sum()
    )

    gross_loss = float(
        abs(
            traded[traded["points"] < 0]["points"].sum()
        )
    )

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0 else float("inf")
    )

    overall_points = float(traded["points"].sum())

    stop_loss_hits = int(
        results_df["exit_reason"]
        .str.contains("Stop Loss Hit", na=False)
        .sum()
    )

    streaks = calculate_streaks(
        traded.sort_values("date")
    )

    win_dates_str = (
        f"({streaks['max_win_dates'][0]} to "
        f"{streaks['max_win_dates'][1]})"
        if streaks["max_win_streak"] > 0 else ""
    )

    loss_dates_str = (
        f"({streaks['max_loss_dates'][0]} to "
        f"{streaks['max_loss_dates'][1]})"
        if streaks["max_loss_streak"] > 0 else ""
    )

    print("\n" + "=" * 100)
    print(
        f"OVERALL SUMMARY REPORT: "
        f"{start_year} TO {current_year}"
    )
    print("=" * 100)

    print(
        f"Total Trading Days Analyzed       : "
        f"{len(results_df)}"
    )

    print(f"Total Trades Taken                : {total_trades}")
    print(f"Winning Trades                    : {wins}")
    print(f"Losing Trades                     : {losses}")
    print(f"Breakeven Trades                  : {breakevens}")

    print(f"Stop Loss Hits                    : {stop_loss_hits}")

    print(
        f"Win Rate (including breakevens)   : "
        f"{win_rate_all:.2f}%"
    )

    print(
        f"Win Rate (excluding breakevens)   : "
        f"{win_rate_decisive:.2f}%"
    )

    print(
        f"Total Positive Points (Gross)     : "
        f"{gross_profit:.2f}"
    )

    print(
        f"Total Negative Points (Gross)     : "
        f"{gross_loss:.2f}"
    )

    print(f"Profit Factor                     : {profit_factor:.2f}")
    print(f"Total Points Captured (Net)       : {overall_points:.2f}")

    print("-" * 100)

    print(
        f"Max Consecutive Wins              : "
        f"{streaks['max_win_streak']} trades | "
        f"+{streaks['max_win_streak_points']:.2f} pts "
        f"{win_dates_str}"
    )

    print(
        f"Max Consecutive Losses            : "
        f"{streaks['max_loss_streak']} trades | "
        f"{streaks['max_loss_streak_points']:.2f} pts "
        f"{loss_dates_str}"
    )

    print("=" * 100)


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():

    smart_api = None

    try:

        now_ist = datetime.now(IST)

        smart_api = login_to_smartapi()

        all_results = []
        all_status = []

        # ----------------------------------------------------
        # FETCH AND ANALYZE EACH YEAR
        # ----------------------------------------------------

        for year in range(
            START_YEAR,
            now_ist.year + 1
        ):

            raw_df, status_df = fetch_year_historical_data(
                smart_api,
                year,
                now_ist
            )

            all_status.append(status_df)

            if not raw_df.empty:

                raw_df["trading_date"] = (
                    raw_df["timestamp"].dt.date
                )

                daily = [
                    analyze_day(d_date, d_df)
                    for d_date, d_df in raw_df.groupby(
                        "trading_date"
                    )
                ]

                if daily:

                    all_results.append(
                        pd.DataFrame(daily)
                    )

                    print(
                        f"\nAnalysis completed for {year}. "
                        f"Trading dates: {len(daily)}"
                    )

        if not all_results:

            print("No data analyzed.")
            return

        # ----------------------------------------------------
        # COMBINE RESULTS
        # ----------------------------------------------------

        results_df = (
            pd.concat(all_results, ignore_index=True)
            .sort_values("date")
            .reset_index(drop=True)
        )

        fetch_status_df = (
            pd.concat(all_status, ignore_index=True)
            if all_status
            else pd.DataFrame()
        )

        # ----------------------------------------------------
        # MONTHLY SUMMARY
        # ----------------------------------------------------

        monthly_df = build_monthly_summary(
            results_df,
            fetch_status_df
        )

        print("\n" + "=" * 130)
        print("MONTHLY PERFORMANCE SUMMARY")
        print("=" * 130)

        print(
            monthly_df[
                [
                    "year",
                    "month_name",
                    "trades_taken",
                    "wins",
                    "losses",
                    "breakevens",
                    "win_rate_percent",
                    "win_rate_ex_breakeven",
                    "total_points"
                ]
            ].to_string(index=False)
        )

        # ----------------------------------------------------
        # YEARLY SUMMARY
        # ----------------------------------------------------

        yearly_df = build_yearly_summary(results_df)

        print("\n" + "=" * 130)
        print("YEARLY PERFORMANCE SUMMARY")
        print("=" * 130)

        print(yearly_df.to_string(index=False))

        # ----------------------------------------------------
        # OVERALL SUMMARY
        # ----------------------------------------------------

        print_overall_summary(
            results_df,
            START_YEAR,
            now_ist.year
        )

        # ----------------------------------------------------
        # ADVANCED RISK METRICS
        # ----------------------------------------------------

        analyze_advanced_metrics(results_df)

        # ----------------------------------------------------
        # SAVE RESULTS
        # ----------------------------------------------------

        results_df.to_csv(
            "orb_backtest_daily_results.csv",
            index=False
        )

        monthly_df.to_csv(
            "orb_backtest_monthly_summary.csv",
            index=False
        )

        yearly_df.to_csv(
            "orb_backtest_yearly_summary.csv",
            index=False
        )

        fetch_status_df.to_csv(
            "orb_backtest_fetch_status.csv",
            index=False
        )

        print("\nCSV files saved successfully:")
        print("1. orb_backtest_daily_results.csv")
        print("2. orb_backtest_monthly_summary.csv")
        print("3. orb_backtest_yearly_summary.csv")
        print("4. orb_backtest_fetch_status.csv")

    except Exception as exc:

        print(f"\nERROR: {exc}")

    finally:

        if smart_api:

            try:

                smart_api.terminateSession(CLIENT_CODE)

                print("\nSmartAPI session terminated.")

            except Exception:
                pass


if __name__ == "__main__":
    main()