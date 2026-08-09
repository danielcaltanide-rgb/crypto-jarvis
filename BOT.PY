import os
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import requests

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# 1. ENVIRONMENT
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is missing from Railway Variables."
    )


# ============================================================
# 2. BOT SETTINGS
# ============================================================

STARTING_BALANCE = 1000.00

TRADE_SIZE = 20.00

# More selective configuration.
MAX_OPEN_POSITIONS = 2

ENTRY_SCORE = 72
WATCH_SCORE = 60

SCAN_INTERVAL_SECONDS = 20
MONITOR_INTERVAL_SECONDS = 8


# ============================================================
# 3. MARKET FILTERS
# ============================================================

MIN_AGE_MINUTES = 2
MAX_AGE_MINUTES = 180

MIN_MARKET_CAP = 30_000
MAX_MARKET_CAP = 2_000_000

MIN_LIQUIDITY = 15_000
MAX_LIQUIDITY = 800_000

MIN_5M_VOLUME = 3_000
MIN_5M_TRANSACTIONS = 12

MIN_BUY_SELL_RATIO = 1.35

# Avoid entering after a huge candle.
MAX_5M_PRICE_CHANGE = 22.0
MAX_1H_PRICE_CHANGE = 100.0


# ============================================================
# 4. PAPER EXIT RULES
# ============================================================

TAKE_PROFIT_PERCENT = 6.0
STOP_LOSS_PERCENT = 3.0

TRAIL_START_PERCENT = 3.0
TRAIL_DISTANCE_PERCENT = 2.0

MAX_HOLD_MINUTES = 12


# ============================================================
# 5. PAPER COST MODEL
# ============================================================

ENTRY_SLIPPAGE = 0.01
EXIT_SLIPPAGE = 0.01

FEE_RATE = 0.003


# ============================================================
# 6. DAILY SAFETY RULES
# ============================================================

# Target only.
DAILY_TARGET = 100.00

DAILY_MAX_LOSS = 50.00

MAX_TRADES_PER_DAY = 20


# ============================================================
# 7. COOLDOWNS
# ============================================================

REENTRY_COOLDOWN_SECONDS = 30 * 60
WATCH_COOLDOWN_SECONDS = 5 * 60


# ============================================================
# 8. STATE
# ============================================================

STATE_FILE = Path("sniper_state.json")

paper_cash = STARTING_BALANCE

positions = {}
trade_history = []

recent_entries = {}
recent_alerts = {}

known_addresses = {}

last_scan_time = 0.0
last_scan_count = 0
last_scan_best = None
last_scan_error = "none"


# ============================================================
# 9. DAILY STATE
# ============================================================

daily_date = datetime.now().date().isoformat()

daily_pnl = 0.0
daily_trades = 0
daily_wins = 0
daily_losses = 0


# ============================================================
# 10. HTTP
# ============================================================

HTTP_TIMEOUT = 15

http = requests.Session()

http.headers.update(
    {
        "User-Agent": "CaltanideJarvisPaperSniper/6.0"
    }
)


# ============================================================
# 11. DEXSCREENER DISCOVERY
# ============================================================

DISCOVERY_URLS = [
    "https://api.dexscreener.com/token-profiles/latest/v1",
    "https://api.dexscreener.com/token-boosts/latest/v1",
    "https://api.dexscreener.com/token-boosts/top/v1",
    "https://api.dexscreener.com/community-takeovers/latest/v1",
    "https://api.dexscreener.com/ads/latest/v1",
]

ADDRESS_MEMORY_SECONDS = 6 * 60 * 60
MAX_KNOWN_ADDRESSES = 600


# ============================================================
# 12. SAFE CONVERSION HELPERS
# ============================================================

def fnum(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def inum(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


# ============================================================
# 13. HTTP HELPER
# ============================================================

def get_json(url):
    response = http.get(
        url,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


# ============================================================
# 14. DAILY RESET
# ============================================================

def reset_daily_if_needed():

    global daily_date
    global daily_pnl
    global daily_trades
    global daily_wins
    global daily_losses

    today = datetime.now().date().isoformat()

    if today == daily_date:
        return

    daily_date = today

    daily_pnl = 0.0
    daily_trades = 0
    daily_wins = 0
    daily_losses = 0

    save_state()


# ============================================================
# 15. DAILY TRADING STATUS
# ============================================================

def trading_status():

    reset_daily_if_needed()

    if daily_pnl >= DAILY_TARGET:
        return False, "TARGET REACHED"

    if daily_pnl <= -DAILY_MAX_LOSS:
        return False, "LOSS LIMIT REACHED"

    if daily_trades >= MAX_TRADES_PER_DAY:
        return False, "TRADE LIMIT REACHED"

    return True, "ACTIVE"


# ============================================================
# 16. PERSISTENCE
# ============================================================

def save_state():

    data = {
        "paper_cash": paper_cash,
        "positions": positions,
        "trade_history": trade_history,
        "recent_entries": recent_entries,

        "daily_date": daily_date,
        "daily_pnl": daily_pnl,
        "daily_trades": daily_trades,
        "daily_wins": daily_wins,
        "daily_losses": daily_losses,
    }

    try:
        STATE_FILE.write_text(
            json.dumps(data, indent=2)
        )

    except Exception as error:
        print("save_state error:", repr(error))


def load_state():

    global paper_cash
    global positions
    global trade_history
    global recent_entries

    global daily_date
    global daily_pnl
    global daily_trades
    global daily_wins
    global daily_losses

    if not STATE_FILE.exists():
        return

    try:
        data = json.loads(
            STATE_FILE.read_text()
        )

        paper_cash = fnum(
            data.get("paper_cash"),
            STARTING_BALANCE,
        )

        positions = data.get(
            "positions",
            {},
        )

        trade_history = data.get(
            "trade_history",
            [],
        )

        recent_entries = data.get(
            "recent_entries",
            {},
        )

        daily_date = data.get(
            "daily_date",
            daily_date,
        )

        daily_pnl = fnum(
            data.get("daily_pnl")
        )

        daily_trades = inum(
            data.get("daily_trades")
        )

        daily_wins = inum(
            data.get("daily_wins")
        )

        daily_losses = inum(
            data.get("daily_losses")
        )

        reset_daily_if_needed()

        print("Paper state loaded.")

    except Exception as error:
        print("load_state error:", repr(error))


# ============================================================
# 17. CLEAN TOKEN MEMORY
# ============================================================

def clean_known_addresses():

    now = time.time()

    expired = []

    for address, seen_at in known_addresses.items():

        if now - seen_at > ADDRESS_MEMORY_SECONDS:
            expired.append(address)

    for address in expired:
        known_addresses.pop(address, None)

    if len(known_addresses) > MAX_KNOWN_ADDRESSES:

        newest = sorted(
            known_addresses.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:MAX_KNOWN_ADDRESSES]

        known_addresses.clear()

        known_addresses.update(
            dict(newest)
        )


# ============================================================
# 18. DISCOVER SOLANA TOKENS
# ============================================================

def discover_addresses():

    now = time.time()

    for url in DISCOVERY_URLS:

        try:
            data = get_json(url)

        except Exception as error:
            print(
                "discovery error:",
                url,
                repr(error),
            )
            continue

        if not isinstance(data, list):
            continue

        for item in data:

            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")

            if not address:
                continue

            known_addresses[address] = now

    clean_known_addresses()

    return list(
        known_addresses.keys()
    )


# ============================================================
# 19. GET DEX PAIRS
# ============================================================

def fetch_best_pairs():

    addresses = discover_addresses()

    if not addresses:
        return {}

    all_pairs = []

    # DEX Screener supports up to 30 addresses in this endpoint.
    for batch in chunks(addresses, 30):

        url = (
            "https://api.dexscreener.com/"
            "tokens/v1/solana/"
            + ",".join(batch)
        )

        try:
            result = get_json(url)

        except Exception as error:
            print(
                "pair fetch error:",
                repr(error),
            )
            continue

        if isinstance(result, list):
            all_pairs.extend(result)

    best_pairs = {}

    for pair in all_pairs:

        if pair.get("chainId") != "solana":
            continue

        base = pair.get("baseToken") or {}

        address = base.get("address")

        if not address:
            continue

        liquidity = fnum(
            (pair.get("liquidity") or {}).get("usd")
        )

        previous = best_pairs.get(address)

        if previous is None:
            best_pairs[address] = pair
            continue

        previous_liquidity = fnum(
            (previous.get("liquidity") or {}).get("usd")
        )

        if liquidity > previous_liquidity:
            best_pairs[address] = pair

    return best_pairs


# ============================================================
# 20. NORMALIZE MARKET DATA
# ============================================================

def normalize_pair(address, pair):

    base = pair.get("baseToken") or {}

    volume = pair.get("volume") or {}

    changes = pair.get("priceChange") or {}

    transactions = pair.get("txns") or {}

    five_minute_txns = transactions.get("m5") or {}

    created = pair.get("pairCreatedAt")

    if not created:
        return None

    try:
        age_minutes = (
            int(time.time() * 1000)
            - int(created)
        ) / 60_000

    except Exception:
        return None

    if age_minutes < 0:
        return None

    return {
        "address": address,

        "name": base.get("name") or "Unknown",

        "symbol": base.get("symbol") or "?",

        "price": fnum(
            pair.get("priceUsd")
        ),

        "age": age_minutes,

        "market_cap": fnum(
            pair.get("marketCap")
            or pair.get("fdv")
        ),

        "liquidity": fnum(
            (pair.get("liquidity") or {}).get("usd")
        ),

        "volume_5m": fnum(
            volume.get("m5")
        ),

        "volume_1h": fnum(
            volume.get("h1")
        ),

        "buys_5m": inum(
            five_minute_txns.get("buys")
        ),

        "sells_5m": inum(
            five_minute_txns.get("sells")
        ),

        "change_5m": fnum(
            changes.get("m5")
        ),

        "change_1h": fnum(
            changes.get("h1")
        ),
    }


# ============================================================
# 21. SCORE TOKEN
# ============================================================

def score_token(c):

    reasons = []
    risks = []

    # ---------------------------
    # HARD FILTERS
    # ---------------------------

    if c["price"] <= 0:
        return 0, reasons, ["no price"]

    if not (
        MIN_AGE_MINUTES
        <= c["age"]
        <= MAX_AGE_MINUTES
    ):
        return 0, reasons, ["age"]

    if not (
        MIN_MARKET_CAP
        <= c["market_cap"]
        <= MAX_MARKET_CAP
    ):
        return 0, reasons, ["market cap"]

    if not (
        MIN_LIQUIDITY
        <= c["liquidity"]
        <= MAX_LIQUIDITY
    ):
        return 0, reasons, ["liquidity"]

    if c["volume_5m"] < MIN_5M_VOLUME:
        return 0, reasons, ["volume"]

    tx_count = (
        c["buys_5m"]
        + c["sells_5m"]
    )

    if tx_count < MIN_5M_TRANSACTIONS:
        return 0, reasons, ["transactions"]

    if c["change_5m"] > MAX_5M_PRICE_CHANGE:
        return 0, reasons, ["5m pump too high"]

    if c["change_1h"] > MAX_1H_PRICE_CHANGE:
        return 0, reasons, ["1h pump too high"]

    buys = c["buys_5m"]
    sells = c["sells_5m"]

    if sells > 0:

        buy_sell_ratio = buys / sells

        if buy_sell_ratio < MIN_BUY_SELL_RATIO:
            return (
                0,
                reasons,
                ["buy pressure too weak"],
            )

    # ---------------------------
    # SCORE
    # ---------------------------

    score = 0

    # Freshness

    if c["age"] <= 10:
        score += 15
        reasons.append("very fresh")

    elif c["age"] <= 30:
        score += 12
        reasons.append("fresh")

    elif c["age"] <= 60:
        score += 8

    else:
        score += 4

    # Market cap

    market_cap = c["market_cap"]

    if 50_000 <= market_cap <= 350_000:
        score += 15
        reasons.append("micro-cap zone")

    elif market_cap <= 750_000:
        score += 11

    elif market_cap <= 1_500_000:
        score += 7

    else:
        score += 3

    # Liquidity

    liquidity = c["liquidity"]

    if 30_000 <= liquidity <= 200_000:
        score += 17
        reasons.append("healthy liquidity")

    elif liquidity >= 20_000:
        score += 12

    else:
        score += 5
        risks.append("lower liquidity")

    # Volume

    volume_5m = c["volume_5m"]

    if volume_5m >= 50_000:
        score += 16
        reasons.append("strong volume")

    elif volume_5m >= 20_000:
        score += 13

    elif volume_5m >= 8_000:
        score += 9

    else:
        score += 5

    # Volume acceleration

    if c["volume_1h"] > 0:

        normal_five_minute_volume = (
            c["volume_1h"] / 12
        )

        if normal_five_minute_volume > 0:

            acceleration = (
                volume_5m
                / normal_five_minute_volume
            )

            if acceleration >= 4:
                score += 15
                reasons.append(
                    "volume acceleration"
                )

            elif acceleration >= 2:
                score += 11

            elif acceleration >= 1.25:
                score += 6

            elif acceleration < 0.6:
                score -= 8
                risks.append(
                    "volume fading"
                )

    # Buy pressure

    if sells == 0 and buys >= 10:

        score += 16
        reasons.append(
            "buyers dominating"
        )

    elif sells > 0:

        ratio = buys / sells

        if ratio >= 3:
            score += 17
            reasons.append(
                "extreme buy pressure"
            )

        elif ratio >= 2:
            score += 14
            reasons.append(
                "strong buy pressure"
            )

        elif ratio >= 1.5:
            score += 9

        else:
            score += 4

    # Momentum

    move = c["change_5m"]

    if 1 <= move <= 7:
        score += 14
        reasons.append(
            "early momentum"
        )

    elif 7 < move <= 12:
        score += 10

    elif 12 < move <= 18:
        score += 5
        risks.append(
            "already moving"
        )

    elif move > 18:
        score -= 8
        risks.append(
            "late-entry risk"
        )

    elif move < 0:
        score -= 5

    # Liquidity / market cap ratio

    if market_cap > 0:

        liquidity_ratio = (
            liquidity
            / market_cap
        )

        if liquidity_ratio >= 0.25:
            score += 10
            reasons.append(
                "strong liquidity ratio"
            )

        elif liquidity_ratio >= 0.12:
            score += 6

        elif liquidity_ratio < 0.04:
            score -= 12
            risks.append(
                "weak liquidity ratio"
            )

    score = max(
        0,
        min(score, 100),
    )

    return score, reasons, risks


# ============================================================
# 22. RANK CANDIDATES
# ============================================================

def get_rankings():

    pairs = fetch_best_pairs()

    results = []

    now = time.time()

    for address, pair in pairs.items():

        candidate = normalize_pair(
            address,
            pair,
        )

        if not candidate:
            continue

        if address in positions:
            continue

        last_entry = fnum(
            recent_entries.get(address)
        )

        if (
            last_entry
            and now - last_entry
            < REENTRY_COOLDOWN_SECONDS
        ):
            continue

        score, reasons, risks = score_token(
            candidate
        )

        if score < WATCH_SCORE:
            continue

        candidate["score"] = score
        candidate["reasons"] = reasons
        candidate["risks"] = risks

        results.append(candidate)

    results.sort(
        key=lambda token: (
            token["score"],
            token["volume_5m"],
            token["buys_5m"],
            token["liquidity"],
        ),
        reverse=True,
    )

    return results


# ============================================================
# 23. GET CURRENT TOKEN PRICE
# ============================================================

def get_current_quote(address):

    url = (
        "https://api.dexscreener.com/"
        "token-pairs/v1/solana/"
        f"{address}"
    )

    result = get_json(url)

    if not isinstance(result, list):
        return None

    if not result:
        return None

    best_pair = max(
        result,
        key=lambda pair: fnum(
            (pair.get("liquidity") or {}).get("usd")
        ),
    )

    price = fnum(
        best_pair.get("priceUsd")
    )

    liquidity = fnum(
        (best_pair.get("liquidity") or {}).get("usd")
    )

    if price <= 0:
        return None

    return {
        "price": price,
        "liquidity": liquidity,
    }


# ============================================================
# 24. OPEN PAPER TRADE
# ============================================================

def open_paper_trade(candidate):

    global paper_cash

    allowed, _ = trading_status()

    if not allowed:
        return None

    if len(positions) >= MAX_OPEN_POSITIONS:
        return None

    if candidate["score"] < ENTRY_SCORE:
        return None

    address = candidate["address"]

    if address in positions:
        return None

    market_price = candidate["price"]

    if market_price <= 0:
        return None

    entry_price = (
        market_price
        * (1 + ENTRY_SLIPPAGE)
    )

    entry_fee = (
        TRADE_SIZE
        * FEE_RATE
    )

    total_cost = (
        TRADE_SIZE
        + entry_fee
    )

    if paper_cash < total_cost:
        return None

    quantity = (
        TRADE_SIZE
        / entry_price
    )

    position = {
        "address": address,

        "name": candidate["name"],
        "symbol": candidate["symbol"],

        "score": candidate["score"],

        "market_cap":
            candidate["market_cap"],

        "liquidity":
            candidate["liquidity"],

        "trade_size":
            TRADE_SIZE,

        "entry_quote":
            market_price,

        "entry_price":
            entry_price,

        "entry_fee":
            entry_fee,

        "quantity":
            quantity,

        "highest_quote":
            market_price,

        "trailing":
            False,

        "opened_at":
            time.time(),
    }

    paper_cash -= total_cost

    positions[address] = position

    recent_entries[address] = time.time()

    save_state()

    return position


# ============================================================
# 25. ESTIMATE PAPER EXIT
# ============================================================

def estimate_exit(position, market_price):

    simulated_exit = (
        market_price
        * (1 - EXIT_SLIPPAGE)
    )

    gross_proceeds = (
        position["quantity"]
        * simulated_exit
    )

    exit_fee = (
        gross_proceeds
        * FEE_RATE
    )

    proceeds = (
        gross_proceeds
        - exit_fee
    )

    original_cost = (
        position["trade_size"]
        + position["entry_fee"]
    )

    pnl = (
        proceeds
        - original_cost
    )

    if original_cost > 0:

        pnl_percent = (
            pnl
            / original_cost
            * 100
        )

    else:

        pnl_percent = 0.0

    return {
        "exit_price":
            simulated_exit,

        "exit_fee":
            exit_fee,

        "proceeds":
            proceeds,

        "pnl":
            pnl,

        "pnl_percent":
            pnl_percent,
    }


# ============================================================
# 26. CLOSE PAPER TRADE
# ============================================================

def close_paper_trade(
    address,
    market_price,
    reason,
):

    global paper_cash

    global daily_pnl
    global daily_trades
    global daily_wins
    global daily_losses

    reset_daily_if_needed()

    position = positions.get(address)

    if not position:
        return None

    result = estimate_exit(
        position,
        market_price,
    )

    paper_cash += result["proceeds"]

    trade = {
        **position,
        **result,

        "exit_quote":
            market_price,

        "reason":
            reason,

        "closed_at":
            time.time(),
    }

    trade_history.append(trade)

    positions.pop(
        address,
        None,
    )

    daily_pnl += result["pnl"]
    daily_trades += 1

    if result["pnl"] > 0:
        daily_wins += 1

    else:
        daily_losses += 1

    save_state()

    return trade


# ============================================================
# 27. AUTOMATIC SNIPER JOB
# ============================================================

async def sniper_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    global last_scan_time
    global last_scan_count
    global last_scan_best
    global last_scan_error

    chat_id = context.job.chat_id

    try:

        rankings = await asyncio.to_thread(
            get_rankings
        )

        last_scan_time = time.time()

        last_scan_count = len(
            rankings
        )

        last_scan_best = (
            rankings[0]
            if rankings
            else None
        )

        last_scan_error = "none"

    except Exception as error:

        last_scan_time = time.time()

        last_scan_count = 0

        last_scan_best = None

        last_scan_error = (
            f"{type(error).__name__}: "
            f"{error}"
        )

        print(
            "scanner error:",
            last_scan_error,
        )

        return

    if not rankings:
        return

    best = rankings[0]

    # -----------------------------------------
    # WATCH MESSAGE
    # -----------------------------------------

    previous_alert = fnum(
        recent_alerts.get(
            best["address"]
        )
    )

    if (
        best["score"] >= WATCH_SCORE
        and
        time.time() - previous_alert
        >= WATCH_COOLDOWN_SECONDS
    ):

        recent_alerts[
            best["address"]
        ] = time.time()

        strengths = ", ".join(
            best["reasons"][:3]
        )

        risks = ", ".join(
            best["risks"][:3]
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "👀 SNIPER WATCH\n\n"

                f"{best['name']} "
                f"({best['symbol']})\n"

                f"Score: "
                f"{best['score']}/100\n"

                f"Age: "
                f"{best['age']:.1f}m\n"

                f"MC: "
                f"${best['market_cap']:,.0f}\n"

                f"Liquidity: "
                f"${best['liquidity']:,.0f}\n"

                f"5m volume: "
                f"${best['volume_5m']:,.0f}\n"

                f"B/S: "
                f"{best['buys_5m']}/"
                f"{best['sells_5m']}\n"

                f"5m move: "
                f"{best['change_5m']:+.2f}%\n\n"

                f"Strengths: "
                f"{strengths or 'none'}\n"

                f"Risks: "
                f"{risks or 'none'}"
            ),
        )

    allowed, _ = trading_status()

    if not allowed:
        return

    slots = (
        MAX_OPEN_POSITIONS
        - len(positions)
    )

    if slots <= 0:
        return

    eligible = [
        token
        for token in rankings
        if token["score"] >= ENTRY_SCORE
    ]

    for token in eligible[:slots]:

        position = open_paper_trade(
            token
        )

        if not position:
            continue

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🎯 PAPER SNIPE OPENED\n\n"

                f"{position['name']} "
                f"({position['symbol']})\n"

                f"Score: "
                f"{position['score']}/100\n"

                f"Stake: "
                f"${position['trade_size']:.2f}\n"

                f"Entry: "
                f"${position['entry_price']:.12f}\n"

                f"MC: "
                f"${position['market_cap']:,.0f}\n"

                f"Liquidity: "
                f"${position['liquidity']:,.0f}\n\n"

                f"TP: "
                f"+{TAKE_PROFIT_PERCENT:.1f}%\n"

                f"SL: "
                f"-{STOP_LOSS_PERCENT:.1f}%"
            ),
        )


# ============================================================
# 28. POSITION MONITOR JOB
# ============================================================

async def monitor_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = context.job.chat_id

    for address in list(
        positions.keys()
    ):

        position = positions.get(
            address
        )

        if not position:
            continue

        try:

            quote_data = (
                await asyncio.to_thread(
                    get_current_quote,
                    address,
                )
            )

        except Exception as error:

            print(
                "monitor quote error:",
                repr(error),
            )

            continue

        if not quote_data:
            continue

        market_price = (
            quote_data["price"]
        )

        # -----------------------------------------
        # HIGH-WATER MARK
        # -----------------------------------------

        if (
            market_price
            > position["highest_quote"]
        ):

            position[
                "highest_quote"
            ] = market_price

            save_state()

        result = estimate_exit(
            position,
            market_price,
        )

        pnl_percent = (
            result["pnl_percent"]
        )

        # -----------------------------------------
        # TRAILING ACTIVATION
        # -----------------------------------------

        gain_from_entry = (
            (
                position["highest_quote"]
                - position["entry_quote"]
            )
            / position["entry_quote"]
            * 100
        )

        if (
            gain_from_entry
            >= TRAIL_START_PERCENT
        ):

            position["trailing"] = True

        highest = (
            position["highest_quote"]
        )

        drawdown = (
            (
                market_price
                - highest
            )
            / highest
            * 100
            if highest > 0
            else 0
        )

        held_minutes = (
            time.time()
            - position["opened_at"]
        ) / 60

        reason = None

        # -----------------------------------------
        # EXIT CONDITIONS
        # -----------------------------------------

        if (
            pnl_percent
            >= TAKE_PROFIT_PERCENT
        ):

            reason = "TAKE PROFIT"

        elif (
            pnl_percent
            <= -STOP_LOSS_PERCENT
        ):

            reason = "STOP LOSS"

        elif (
            position["trailing"]
            and drawdown
            <= -TRAIL_DISTANCE_PERCENT
        ):

            reason = "TRAILING EXIT"

        elif (
            held_minutes
            >= MAX_HOLD_MINUTES
        ):

            reason = "TIME EXIT"

        if not reason:
            continue

        trade = close_paper_trade(
            address,
            market_price,
            reason,
        )

        if not trade:
            continue

        icon = (
            "🟢"
            if trade["pnl"] > 0
            else "🔴"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"{icon} PAPER SNIPE CLOSED\n\n"

                f"{trade['name']} "
                f"({trade['symbol']})\n"

                f"Reason: "
                f"{trade['reason']}\n"

                f"Return: "
                f"{trade['pnl_percent']:+.2f}%\n"

                f"P/L: "
                f"${trade['pnl']:+.2f}\n"

                f"Today's P/L: "
                f"${daily_pnl:+.2f}"
            ),
        )


# ============================================================
# 29. SCHEDULE JOBS
# ============================================================

def schedule_jobs(
    job_queue,
    chat_id,
):

    chat_id = int(chat_id)

    scanner_name = (
        f"sniper_{chat_id}"
    )

    monitor_name = (
        f"monitor_{chat_id}"
    )

    if not job_queue.get_jobs_by_name(
        scanner_name
    ):

        job_queue.run_repeating(
            sniper_job,
            interval=
                SCAN_INTERVAL_SECONDS,
            first=5,
            chat_id=chat_id,
            name=scanner_name,
        )

    if not job_queue.get_jobs_by_name(
        monitor_name
    ):

        job_queue.run_repeating(
            monitor_job,
            interval=
                MONITOR_INTERVAL_SECONDS,
            first=8,
            chat_id=chat_id,
            name=monitor_name,
        )


# ============================================================
# 30. PAPER EQUITY
# ============================================================

async def calculate_paper_equity():

    equity = paper_cash
    unrealized = 0.0

    for position in list(
        positions.values()
    ):

        try:

            quote = (
                await asyncio.to_thread(
                    get_current_quote,
                    position["address"],
                )
            )

        except Exception:
            continue

        if not quote:
            continue

        result = estimate_exit(
            position,
            quote["price"],
        )

        equity += result["proceeds"]

        unrealized += result["pnl"]

    return equity, unrealized


# ============================================================
# 31. /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    schedule_jobs(
        context.job_queue,
        update.effective_chat.id,
    )

    await update.message.reply_text(
        "🎯 JARVIS PAPER SNIPER\n\n"

        "/status\n"
        "/snipe\n"
        "/paper\n"
        "/positions\n"
        "/history\n"
        "/stats\n"
        "/today\n"
        "/chatid\n"
        "/resetpaper"
    )


# ============================================================
# 32. /STATUS
# ============================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    jobs = list(
        context.job_queue.jobs()
    )

    _, status = trading_status()

    if last_scan_time:

        scan_age = int(
            time.time()
            - last_scan_time
        )

        scan_text = (
            f"{scan_age}s ago"
        )

    else:

        scan_text = "waiting"

    best_text = "none"

    if last_scan_best:

        best_text = (
            f"{last_scan_best['symbol']} "
            f"{last_scan_best['score']}/100"
        )

    await update.message.reply_text(
        "🟢 SNIPER STATUS\n\n"

        f"Jobs: {len(jobs)}\n"
        f"Last scan: {scan_text}\n"
        f"Candidates: {last_scan_count}\n"
        f"Best: {best_text}\n"
        f"Last error: {last_scan_error}\n\n"

        f"Cash: ${paper_cash:.2f}\n"
        f"Open: {len(positions)}/"
        f"{MAX_OPEN_POSITIONS}\n"

        f"Closed: {len(trade_history)}\n\n"

        f"Entry score: "
        f"{ENTRY_SCORE}/100\n"

        f"Status: {status}"
    )


# ============================================================
# 33. /SNIPE
# ============================================================

async def snipe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🎯 Scanning..."
    )

    try:

        results = await asyncio.to_thread(
            get_rankings
        )

    except Exception as error:

        await update.message.reply_text(
            "Scanner error:\n"
            f"{type(error).__name__}: "
            f"{error}"
        )

        return

    if not results:

        await update.message.reply_text(
            "No qualifying candidates right now."
        )

        return

    lines = [
        "🎯 TOP CANDIDATES",
        "",
    ]

    for index, token in enumerate(
        results[:8],
        start=1,
    ):

        lines.extend(
            [
                (
                    f"{index}. "
                    f"{token['name']} "
                    f"({token['symbol']})"
                ),

                (
                    f"Score: "
                    f"{token['score']}/100"
                ),

                (
                    f"Age: "
                    f"{token['age']:.1f}m"
                ),

                (
                    f"MC: "
                    f"${token['market_cap']:,.0f}"
                ),

                (
                    f"Liq: "
                    f"${token['liquidity']:,.0f}"
                ),

                (
                    f"5m vol: "
                    f"${token['volume_5m']:,.0f}"
                ),

                (
                    f"B/S: "
                    f"{token['buys_5m']}/"
                    f"{token['sells_5m']}"
                ),

                (
                    f"5m: "
                    f"{token['change_5m']:+.2f}%"
                ),

                (
                    f"Token:\n"
                    f"{token['address']}"
                ),

                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)[:4000]
    )


# ============================================================
# 34. /PAPER
# ============================================================

async def paper_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    equity, unrealized = (
        await calculate_paper_equity()
    )

    realized = sum(
        fnum(
            trade.get("pnl")
        )
        for trade
        in trade_history
    )

    await update.message.reply_text(
        "💵 PAPER WALLET\n\n"

        f"Starting: "
        f"${STARTING_BALANCE:.2f}\n"

        f"Cash: "
        f"${paper_cash:.2f}\n"

        f"Estimated equity: "
        f"${equity:.2f}\n\n"

        f"Open positions: "
        f"{len(positions)}/"
        f"{MAX_OPEN_POSITIONS}\n"

        f"Closed trades: "
        f"{len(trade_history)}\n\n"

        f"Realized P/L: "
        f"${realized:+.2f}\n"

        f"Open P/L: "
        f"${unrealized:+.2f}\n"

        f"Today's P/L: "
        f"${daily_pnl:+.2f}\n\n"

        "Mode: PAPER ONLY"
    )


# ============================================================
# 35. /POSITIONS
# ============================================================

async def positions_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not positions:

        await update.message.reply_text(
            "No paper positions open."
        )

        return

    lines = [
        "🎯 OPEN POSITIONS",
        "",
    ]

    for position in list(
        positions.values()
    ):

        try:

            quote = await asyncio.to_thread(
                get_current_quote,
                position["address"],
            )

        except Exception:

            quote = None

        if quote:

            result = estimate_exit(
                position,
                quote["price"],
            )

            pnl_text = (
                f"{result['pnl_percent']:+.2f}% "
                f"(${result['pnl']:+.2f})"
            )

        else:

            pnl_text = "unavailable"

        held_minutes = (
            time.time()
            - position["opened_at"]
        ) / 60

        lines.extend(
            [
                (
                    f"{position['name']} "
                    f"({position['symbol']})"
                ),

                (
                    f"Score: "
                    f"{position['score']}/100"
                ),

                (
                    f"P/L: "
                    f"{pnl_text}"
                ),

                (
                    f"Held: "
                    f"{held_minutes:.1f}m"
                ),

                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)[:4000]
    )


# ============================================================
# 36. /HISTORY
# ============================================================

async def history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not trade_history:

        await update.message.reply_text(
            "No completed paper trades yet."
        )

        return

    lines = [
        "📚 PAPER HISTORY",
        "",
    ]

    for trade in trade_history[-20:]:

        pnl = fnum(
            trade.get("pnl")
        )

        pnl_percent = fnum(
            trade.get(
                "pnl_percent"
            )
        )

        icon = (
            "🟢"
            if pnl > 0
            else "🔴"
        )

        lines.extend(
            [
                (
                    f"{icon} "
                    f"{trade.get('symbol', '?')}"
                ),

                (
                    f"{trade.get('reason', '?')}"
                ),

                (
                    f"{pnl_percent:+.2f}% "
                    f"(${pnl:+.2f})"
                ),

                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)[:4000]
    )


# ============================================================
# 37. /STATS
# ============================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not trade_history:

        await update.message.reply_text(
            "No completed trades yet."
        )

        return

    returns = [
        fnum(
            trade.get(
                "pnl_percent"
            )
        )
        for trade
        in trade_history
    ]

    winners = [
        result
        for result in returns
        if result > 0
    ]

    losers = [
        result
        for result in returns
        if result <= 0
    ]

    win_rate = (
        len(winners)
        / len(returns)
        * 100
    )

    average_trade = (
        sum(returns)
        / len(returns)
    )

    average_winner = (
        sum(winners)
        / len(winners)
        if winners
        else 0
    )

    average_loser = (
        sum(losers)
        / len(losers)
        if losers
        else 0
    )

    total_pnl = sum(
        fnum(
            trade.get("pnl")
        )
        for trade
        in trade_history
    )

    await update.message.reply_text(
        "📊 SNIPER STATS\n\n"

        f"Trades: {len(returns)}\n"

        f"Win rate: "
        f"{win_rate:.1f}%\n"

        f"Average trade: "
        f"{average_trade:+.2f}%\n"

        f"Average winner: "
        f"{average_winner:+.2f}%\n"

        f"Average loser: "
        f"{average_loser:+.2f}%\n\n"

        f"Best: "
        f"{max(returns):+.2f}%\n"

        f"Worst: "
        f"{min(returns):+.2f}%\n"

        f"Total P/L: "
        f"${total_pnl:+.2f}"
    )


# ============================================================
# 38. /TODAY
# ============================================================

async def today_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    reset_daily_if_needed()

    _, status = trading_status()

    remaining = max(
        0.0,
        DAILY_TARGET - daily_pnl,
    )

    if daily_trades:

        win_rate = (
            daily_wins
            / daily_trades
            * 100
        )

    else:

        win_rate = 0.0

    await update.message.reply_text(
        "🎯 TODAY\n\n"

        f"Target: "
        f"${DAILY_TARGET:.2f}\n"

        f"P/L: "
        f"${daily_pnl:+.2f}\n"

        f"Remaining: "
        f"${remaining:.2f}\n\n"

        f"Trades: "
        f"{daily_trades}/"
        f"{MAX_TRADES_PER_DAY}\n"

        f"Wins: "
        f"{daily_wins}\n"

        f"Losses: "
        f"{daily_losses}\n"

        f"Win rate: "
        f"{win_rate:.1f}%\n\n"

        f"Loss limit: "
        f"-${DAILY_MAX_LOSS:.2f}\n"

        f"Status: "
        f"{status}"
    )


# ============================================================
# 39. /CHATID
# ============================================================

async def chatid_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "Telegram Chat ID:\n"
        f"{update.effective_chat.id}"
    )


# ============================================================
# 40. /RESETPAPER
# ============================================================

async def resetpaper_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global paper_cash
    global positions
    global trade_history
    global recent_entries

    global daily_pnl
    global daily_trades
    global daily_wins
    global daily_losses

    if (
        not context.args
        or context.args[0].lower()
        != "confirm"
    ):

        await update.message.reply_text(
            "⚠️ This clears the entire "
            "paper account.\n\n"

            "Send:\n"
            "/resetpaper confirm"
        )

        return

    paper_cash = STARTING_BALANCE

    positions = {}
    trade_history = []
    recent_entries = {}

    daily_pnl = 0.0
    daily_trades = 0
    daily_wins = 0
    daily_losses = 0

    save_state()

    await update.message.reply_text(
        "♻️ PAPER ACCOUNT RESET\n\n"
        f"Balance: "
        f"${STARTING_BALANCE:.2f}"
    )


# ============================================================
# 41. POST INIT
# ============================================================

async def post_init(application):

    if not TELEGRAM_CHAT_ID:

        print(
            "TELEGRAM_CHAT_ID not set."
        )

        return

    try:

        chat_id = int(
            TELEGRAM_CHAT_ID
        )

        schedule_jobs(
            application.job_queue,
            chat_id,
        )

        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "✅ JARVIS SNIPER ONLINE\n\n"

                "Mode: PAPER ONLY\n"

                f"Entry score: "
                f"{ENTRY_SCORE}/100\n"

                f"Trade size: "
                f"${TRADE_SIZE:.2f}\n"

                f"Max positions: "
                f"{MAX_OPEN_POSITIONS}\n"

                f"TP: "
                f"+{TAKE_PROFIT_PERCENT:.1f}%\n"

                f"SL: "
                f"-{STOP_LOSS_PERCENT:.1f}%"
            ),
        )

    except Exception as error:

        print(
            "post_init error:",
            repr(error),
        )


# ============================================================
# 42. MAIN
# ============================================================

def main():

    print("Starting Jarvis...")

    print(
        "TELEGRAM_BOT_TOKEN:",
        "SET"
        if TELEGRAM_BOT_TOKEN
        else "MISSING",
    )

    print(
        "TELEGRAM_CHAT_ID:",
        "SET"
        if TELEGRAM_CHAT_ID
        else "MISSING",
    )

    load_state()

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "snipe",
            snipe_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "paper",
            paper_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "account",
            paper_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "positions",
            positions_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "history",
            history_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            stats_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "today",
            today_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "chatid",
            chatid_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "resetpaper",
            resetpaper_command,
        )
    )

    print(
        "Jarvis started successfully."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
