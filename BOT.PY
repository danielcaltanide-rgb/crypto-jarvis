import os
import time
import json
import asyncio
import requests

from pathlib import Path
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# ENVIRONMENT
# ============================================================

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
)

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError(
        "Missing TELEGRAM_BOT_TOKEN in Railway."
    )


# ============================================================
# PAPER ACCOUNT
# ============================================================

STARTING_BALANCE = 1000.00

# Standard qualifying signal
BASE_TRADE_SIZE = 20.00

# Exceptional signal
STRONG_TRADE_SIZE = 30.00

MAX_OPEN_POSITIONS = 6

ENTRY_SCORE = 58
STRONG_ENTRY_SCORE = 78

WATCH_SCORE = 50


# ============================================================
# DAILY TARGET / RISK
# ============================================================

DAILY_PROFIT_TARGET = 100.00

# Stop opening new trades after this loss.
DAILY_MAX_LOSS = 75.00

# Prevent endless overtrading.
MAX_TRADES_PER_DAY = 30


# ============================================================
# SCANNER SPEED
# ============================================================

SCAN_INTERVAL = 20
MONITOR_INTERVAL = 8

FIRST_SCAN_DELAY = 8
FIRST_MONITOR_DELAY = 12


# ============================================================
# NEW / LOW-CAP FILTERS
# ============================================================

# Very young pairs are allowed.
MIN_AGE_MINUTES = 1

# Still a new-coin sniper.
MAX_AGE_MINUTES = 240

# Market cap may be unavailable/near-zero.
MIN_MARKET_CAP = 0
MAX_MARKET_CAP = 5_000_000

# Very low liquidity allowed for PAPER experimentation.
MIN_LIQUIDITY = 2_000
MAX_LIQUIDITY = 1_500_000

# Activity requirements.
MIN_VOLUME_5M = 500
MIN_TXNS_5M = 4

# Anti-chase limits.
MAX_PUMP_5M = 70.0
MAX_PUMP_1H = 300.0


# ============================================================
# EXIT RULES
# ============================================================

TAKE_PROFIT = 7.0
STOP_LOSS = 4.0

TRAIL_ACTIVATION = 4.0
TRAIL_DISTANCE = 2.5

MAX_HOLD_MINUTES = 12


# ============================================================
# SIMULATED COSTS
# ============================================================

BASE_ENTRY_SLIPPAGE = 0.010
BASE_EXIT_SLIPPAGE = 0.010

FEE_RATE = 0.003

LOW_LIQ_EXTRA_SLIPPAGE = 0.015
ULTRA_LOW_LIQ_EXTRA_SLIPPAGE = 0.030


# ============================================================
# COOLDOWNS
# ============================================================

REENTRY_COOLDOWN = 20 * 60
WATCH_COOLDOWN = 5 * 60


# ============================================================
# HTTP
# ============================================================

HTTP_TIMEOUT = 12

session = requests.Session()

session.headers.update(
    {
        "User-Agent":
            "CaltanidePaperSniper/4.0"
    }
)


# ============================================================
# STATE
# ============================================================

STATE_FILE = Path(
    os.getenv(
        "STATE_FILE",
        "paper_sniper_state.json",
    )
)

paper_cash = STARTING_BALANCE

positions = {}
trade_history = []

recent_entries = {}
recent_watch_alerts = {}

known_addresses = {}

last_scan_time = 0.0
last_scan_candidates = 0
last_scan_best = None

ADDRESS_MEMORY_SECONDS = 8 * 60 * 60
MAX_KNOWN_ADDRESSES = 1000


# ============================================================
# DAILY STATE
# ============================================================

daily_date = (
    datetime.now()
    .date()
    .isoformat()
)

daily_realized_pnl = 0.0
daily_trade_count = 0

daily_wins = 0
daily_losses = 0


# ============================================================
# DISCOVERY ENDPOINTS
# ============================================================

DISCOVERY_URLS = [
    (
        "https://api.dexscreener.com/"
        "token-profiles/latest/v1"
    ),
    (
        "https://api.dexscreener.com/"
        "token-boosts/latest/v1"
    ),
    (
        "https://api.dexscreener.com/"
        "token-boosts/top/v1"
    ),
    (
        "https://api.dexscreener.com/"
        "community-takeovers/latest/v1"
    ),
    (
        "https://api.dexscreener.com/"
        "ads/latest/v1"
    ),
]


# ============================================================
# HELPERS
# ============================================================

def get_json(url):

    response = session.get(
        url,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def chunks(items, size):

    for i in range(
        0,
        len(items),
        size,
    ):

        yield items[
            i:i + size
        ]


# ============================================================
# DAILY RESET
# ============================================================

def reset_daily_stats_if_needed():

    global daily_date
    global daily_realized_pnl
    global daily_trade_count
    global daily_wins
    global daily_losses

    today = (
        datetime.now()
        .date()
        .isoformat()
    )

    if today == daily_date:
        return

    daily_date = today

    daily_realized_pnl = 0.0
    daily_trade_count = 0

    daily_wins = 0
    daily_losses = 0

    save_state()


def trading_allowed():

    reset_daily_stats_if_needed()

    if (
        daily_realized_pnl
        >= DAILY_PROFIT_TARGET
    ):
        return (
            False,
            "🎯 DAILY PROFIT TARGET REACHED",
        )

    if (
        daily_realized_pnl
        <= -DAILY_MAX_LOSS
    ):
        return (
            False,
            "🛑 DAILY LOSS LIMIT REACHED",
        )

    if (
        daily_trade_count
        >= MAX_TRADES_PER_DAY
    ):
        return (
            False,
            "🛑 DAILY TRADE LIMIT REACHED",
        )

    return (
        True,
        "🟢 ACTIVE",
    )


# ============================================================
# PERSISTENCE
# ============================================================

def save_state():

    state = {
        "paper_cash":
            paper_cash,

        "positions":
            positions,

        "trade_history":
            trade_history,

        "recent_entries":
            recent_entries,

        "daily_date":
            daily_date,

        "daily_realized_pnl":
            daily_realized_pnl,

        "daily_trade_count":
            daily_trade_count,

        "daily_wins":
            daily_wins,

        "daily_losses":
            daily_losses,
    }

    try:

        temp = Path(
            str(STATE_FILE)
            + ".tmp"
        )

        temp.write_text(
            json.dumps(
                state,
                indent=2,
            )
        )

        temp.replace(
            STATE_FILE
        )

    except Exception as error:

        print(
            "save_state:",
            repr(error),
        )


def load_state():

    global paper_cash
    global positions
    global trade_history
    global recent_entries

    global daily_date
    global daily_realized_pnl
    global daily_trade_count
    global daily_wins
    global daily_losses

    if not STATE_FILE.exists():
        return

    try:

        state = json.loads(
            STATE_FILE.read_text()
        )

        paper_cash = float(
            state.get(
                "paper_cash",
                STARTING_BALANCE,
            )
        )

        positions = state.get(
            "positions",
            {},
        )

        trade_history = state.get(
            "trade_history",
            [],
        )

        recent_entries = state.get(
            "recent_entries",
            {},
        )

        daily_date = state.get(
            "daily_date",
            daily_date,
        )

        daily_realized_pnl = float(
            state.get(
                "daily_realized_pnl",
                0,
            )
        )

        daily_trade_count = int(
            state.get(
                "daily_trade_count",
                0,
            )
        )

        daily_wins = int(
            state.get(
                "daily_wins",
                0,
            )
        )

        daily_losses = int(
            state.get(
                "daily_losses",
                0,
            )
        )

        reset_daily_stats_if_needed()

        print(
            "Paper sniper state loaded."
        )

    except Exception as error:

        print(
            "load_state:",
            repr(error),
        )


# ============================================================
# ADDRESS DISCOVERY
# ============================================================

def cleanup_addresses():

    now = time.time()

    expired = [
        address
        for address, timestamp
        in known_addresses.items()
        if (
            now - timestamp
            > ADDRESS_MEMORY_SECONDS
        )
    ]

    for address in expired:

        known_addresses.pop(
            address,
            None,
        )

    if (
        len(known_addresses)
        > MAX_KNOWN_ADDRESSES
    ):

        newest = sorted(
            known_addresses.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        newest = newest[
            :MAX_KNOWN_ADDRESSES
        ]

        known_addresses.clear()

        known_addresses.update(
            dict(newest)
        )


def discover_addresses():

    now = time.time()

    for url in DISCOVERY_URLS:

        try:

            data = get_json(
                url
            )

        except Exception as error:

            print(
                "discovery:",
                repr(error),
            )

            continue

        if not isinstance(
            data,
            list,
        ):
            continue

        for item in data:

            if (
                item.get("chainId")
                != "solana"
            ):
                continue

            address = (
                item.get("tokenAddress")
            )

            if not address:
                continue

            known_addresses[
                address
            ] = now

    cleanup_addresses()

    return list(
        known_addresses.keys()
    )


# ============================================================
# FETCH BEST POOLS
# ============================================================

def fetch_best_pairs():

    addresses = (
        discover_addresses()
    )

    if not addresses:
        return {}

    all_pairs = []

    for batch in chunks(
        addresses,
        30,
    ):

        url = (
            "https://api.dexscreener.com/"
            "tokens/v1/solana/"
            + ",".join(batch)
        )

        try:

            data = get_json(
                url
            )

        except Exception as error:

            print(
                "pair fetch:",
                repr(error),
            )

            continue

        if isinstance(
            data,
            list,
        ):

            all_pairs.extend(
                data
            )

    best = {}

    for pair in all_pairs:

        if (
            pair.get("chainId")
            != "solana"
        ):
            continue

        base = (
            pair.get("baseToken")
            or {}
        )

        address = (
            base.get("address")
        )

        if not address:
            continue

        liquidity = float(
            (
                (
                    pair.get("liquidity")
                    or {}
                ).get("usd")
            )
            or 0
        )

        previous = best.get(
            address
        )

        if previous is None:

            best[
                address
            ] = pair

            continue

        previous_liquidity = float(
            (
                (
                    previous.get(
                        "liquidity"
                    )
                    or {}
                ).get("usd")
            )
            or 0
        )

        if (
            liquidity
            > previous_liquidity
        ):

            best[
                address
            ] = pair

    return best


# ============================================================
# NORMALIZE PAIR
# ============================================================

def normalize_pair(
    address,
    pair,
):

    base = (
        pair.get("baseToken")
        or {}
    )

    volume = (
        pair.get("volume")
        or {}
    )

    changes = (
        pair.get("priceChange")
        or {}
    )

    txns = (
        pair.get("txns")
        or {}
    )

    tx5 = (
        txns.get("m5")
        or {}
    )

    created = (
        pair.get("pairCreatedAt")
    )

    if not created:
        return None

    try:

        age = (
            int(time.time() * 1000)
            - int(created)
        ) / 60000

    except Exception:

        return None

    if age < 0:
        return None

    try:

        price = float(
            pair.get("priceUsd")
            or 0
        )

    except Exception:

        price = 0

    return {
        "address":
            address,

        "name":
            base.get("name")
            or "Unknown",

        "symbol":
            base.get("symbol")
            or "?",

        "price":
            price,

        "age":
            age,

        "market_cap":
            float(
                pair.get("marketCap")
                or pair.get("fdv")
                or 0
            ),

        "liquidity":
            float(
                (
                    (
                        pair.get(
                            "liquidity"
                        )
                        or {}
                    ).get("usd")
                )
                or 0
            ),

        "volume_5m":
            float(
                volume.get("m5")
                or 0
            ),

        "volume_1h":
            float(
                volume.get("h1")
                or 0
            ),

        "buys_5m":
            int(
                tx5.get("buys")
                or 0
            ),

        "sells_5m":
            int(
                tx5.get("sells")
                or 0
            ),

        "change_5m":
            float(
                changes.get("m5")
                or 0
            ),

        "change_1h":
            float(
                changes.get("h1")
                or 0
            ),
    }


# ============================================================
# RISK LABEL
# ============================================================

def risk_label(c):

    if (
        c["market_cap"]
        < 25_000
        or c["liquidity"]
        < 5_000
    ):
        return "☢️ ULTRA"

    if (
        c["market_cap"]
        < 75_000
        or c["liquidity"]
        < 15_000
    ):
        return "🔴 EXTREME"

    if (
        c["market_cap"]
        < 250_000
    ):
        return "🟠 VERY HIGH"

    return "🟡 HIGH"


# ============================================================
# SCORE TOKEN
# ============================================================

def score_token(c):

    reasons = []
    risks = []

    if c["price"] <= 0:
        return 0, reasons, risks

    if not (
        MIN_AGE_MINUTES
        <= c["age"]
        <= MAX_AGE_MINUTES
    ):
        return 0, reasons, risks

    if (
        c["market_cap"]
        > MAX_MARKET_CAP
    ):
        return 0, reasons, risks

    if not (
        MIN_LIQUIDITY
        <= c["liquidity"]
        <= MAX_LIQUIDITY
    ):
        return 0, reasons, risks

    if (
        c["volume_5m"]
        < MIN_VOLUME_5M
    ):
        return 0, reasons, risks

    tx_count = (
        c["buys_5m"]
        + c["sells_5m"]
    )

    if (
        tx_count
        < MIN_TXNS_5M
    ):
        return 0, reasons, risks

    if (
        c["change_5m"]
        > MAX_PUMP_5M
    ):
        return 0, reasons, risks

    if (
        c["change_1h"]
        > MAX_PUMP_1H
    ):
        return 0, reasons, risks

    score = 0


    # AGE

    if c["age"] <= 5:

        score += 18
        reasons.append(
            "brand new"
        )

    elif c["age"] <= 15:

        score += 15
        reasons.append(
            "very fresh"
        )

    elif c["age"] <= 45:

        score += 11

    elif c["age"] <= 120:

        score += 6

    else:

        score += 2


    # MARKET CAP

    mc = c["market_cap"]

    if mc <= 0:

        score += 2
        risks.append(
            "MC unavailable"
        )

    elif mc < 25_000:

        score += 11
        risks.append(
            "ultra micro cap"
        )

    elif mc < 75_000:

        score += 14
        reasons.append(
            "very low cap"
        )

    elif mc < 250_000:

        score += 15
        reasons.append(
            "micro cap"
        )

    elif mc < 1_000_000:

        score += 11

    else:

        score += 5


    # LIQUIDITY

    liquidity = c[
        "liquidity"
    ]

    if liquidity < 5_000:

        score += 2
        risks.append(
            "very thin liquidity"
        )

    elif liquidity < 10_000:

        score += 6
        risks.append(
            "thin liquidity"
        )

    elif liquidity < 30_000:

        score += 11

    elif liquidity <= 250_000:

        score += 16
        reasons.append(
            "good liquidity"
        )

    else:

        score += 9


    # VOLUME

    volume5 = c[
        "volume_5m"
    ]

    if volume5 >= 100_000:

        score += 18
        reasons.append(
            "explosive volume"
        )

    elif volume5 >= 30_000:

        score += 15
        reasons.append(
            "very strong volume"
        )

    elif volume5 >= 10_000:

        score += 11

    elif volume5 >= 3_000:

        score += 7

    else:

        score += 3


    # VOLUME ACCELERATION

    volume1h = c[
        "volume_1h"
    ]

    if volume1h > 0:

        baseline = (
            volume1h / 12
        )

        if baseline > 0:

            accel = (
                volume5 / baseline
            )

            if accel >= 5:

                score += 16
                reasons.append(
                    "volume exploding"
                )

            elif accel >= 3:

                score += 13
                reasons.append(
                    "volume accelerating"
                )

            elif accel >= 1.5:

                score += 8

            elif accel < 0.5:

                score -= 7
                risks.append(
                    "volume fading"
                )


    # BUY PRESSURE

    buys = c[
        "buys_5m"
    ]

    sells = c[
        "sells_5m"
    ]

    if (
        sells == 0
        and buys >= 6
    ):

        score += 16
        reasons.append(
            "buyers dominating"
        )

    elif sells > 0:

        ratio = (
            buys / sells
        )

        if ratio >= 4:

            score += 18
            reasons.append(
                "extreme buy pressure"
            )

        elif ratio >= 2.5:

            score += 15
            reasons.append(
                "strong buy pressure"
            )

        elif ratio >= 1.5:

            score += 10

        elif ratio >= 1.1:

            score += 5

        elif ratio < 0.7:

            score -= 15
            risks.append(
                "heavy selling"
            )


    # MOMENTUM

    move = c[
        "change_5m"
    ]

    if 1 <= move <= 10:

        score += 14
        reasons.append(
            "early momentum"
        )

    elif 10 < move <= 25:

        score += 10

    elif 25 < move <= 45:

        score += 4
        risks.append(
            "fast move"
        )

    elif move > 45:

        score -= 7
        risks.append(
            "late entry risk"
        )

    elif move < -5:

        score -= 15
        risks.append(
            "falling"
        )


    # LIQUIDITY / MARKET CAP

    if (
        mc > 0
        and liquidity > 0
    ):

        ratio = (
            liquidity / mc
        )

        if ratio >= 0.30:

            score += 10
            reasons.append(
                "strong liquidity ratio"
            )

        elif ratio >= 0.15:

            score += 7

        elif ratio >= 0.07:

            score += 3

        elif ratio < 0.025:

            score -= 12
            risks.append(
                "weak liquidity ratio"
            )


    # ACTIVITY

    if buys >= 100:

        score += 7

    elif buys >= 30:

        score += 4


    return (
        max(
            0,
            min(
                score,
                100,
            ),
        ),
        reasons,
        risks,
    )


# ============================================================
# RANKINGS
# ============================================================

def get_rankings():

    pairs = fetch_best_pairs()

    now = time.time()

    ranked = []

    for (
        address,
        pair,
    ) in pairs.items():

        candidate = normalize_pair(
            address,
            pair,
        )

        if not candidate:
            continue

        if address in positions:
            continue

        last_entry = (
            recent_entries.get(
                address,
                0,
            )
        )

        if (
            last_entry
            and (
                now - last_entry
            )
            < REENTRY_COOLDOWN
        ):
            continue

        (
            score,
            reasons,
            risks,
        ) = score_token(
            candidate
        )

        if score < 35:
            continue

        candidate[
            "score"
        ] = score

        candidate[
            "reasons"
        ] = reasons

        candidate[
            "risks"
        ] = risks

        candidate[
            "risk"
        ] = risk_label(
            candidate
        )

        ranked.append(
            candidate
        )

    ranked.sort(
        key=lambda x: (
            x["score"],
            x["volume_5m"],
            x["buys_5m"],
            x["liquidity"],
        ),
        reverse=True,
    )

    return ranked


# ============================================================
# CURRENT TOKEN QUOTE
# ============================================================

def current_quote(
    address,
):

    data = get_json(
        (
            "https://api.dexscreener.com/"
            "token-pairs/v1/solana/"
            f"{address}"
        )
    )

    if (
        not isinstance(
            data,
            list,
        )
        or not data
    ):
        return None

    pair = max(
        data,
        key=lambda p:
            float(
                (
                    (
                        p.get(
                            "liquidity"
                        )
                        or {}
                    ).get("usd")
                )
                or 0
            ),
    )

    try:

        price = float(
            pair.get("priceUsd")
            or 0
        )

    except Exception:

        return None

    liquidity = float(
        (
            (
                pair.get(
                    "liquidity"
                )
                or {}
            ).get("usd")
        )
        or 0
    )

    return {
        "price":
            price,

        "liquidity":
            liquidity,
    }


# ============================================================
# SLIPPAGE
# ============================================================

def entry_slippage(
    liquidity,
):

    if liquidity < 5_000:

        return (
            BASE_ENTRY_SLIPPAGE
            + ULTRA_LOW_LIQ_EXTRA_SLIPPAGE
        )

    if liquidity < 15_000:

        return (
            BASE_ENTRY_SLIPPAGE
            + LOW_LIQ_EXTRA_SLIPPAGE
        )

    if liquidity < 30_000:

        return (
            BASE_ENTRY_SLIPPAGE
            + 0.008
        )

    return (
        BASE_ENTRY_SLIPPAGE
    )


def exit_slippage(
    liquidity,
):

    if liquidity < 5_000:

        return (
            BASE_EXIT_SLIPPAGE
            + ULTRA_LOW_LIQ_EXTRA_SLIPPAGE
        )

    if liquidity < 15_000:

        return (
            BASE_EXIT_SLIPPAGE
            + LOW_LIQ_EXTRA_SLIPPAGE
        )

    if liquidity < 30_000:

        return (
            BASE_EXIT_SLIPPAGE
            + 0.008
        )

    return (
        BASE_EXIT_SLIPPAGE
    )


# ============================================================
# OPEN PAPER TRADE
# ============================================================

def open_trade(
    coin,
):

    global paper_cash

    allowed, _ = (
        trading_allowed()
    )

    if not allowed:
        return None

    if (
        len(positions)
        >= MAX_OPEN_POSITIONS
    ):
        return None

    address = coin[
        "address"
    ]

    if address in positions:
        return None

    if (
        coin["score"]
        >= STRONG_ENTRY_SCORE
    ):

        trade_size = (
            STRONG_TRADE_SIZE
        )

    else:

        trade_size = (
            BASE_TRADE_SIZE
        )

    quote = coin[
        "price"
    ]

    if quote <= 0:
        return None

    slip = entry_slippage(
        coin["liquidity"]
    )

    simulated_entry = (
        quote
        * (
            1 + slip
        )
    )

    fee = (
        trade_size
        * FEE_RATE
    )

    required = (
        trade_size
        + fee
    )

    if paper_cash < required:
        return None

    quantity = (
        trade_size
        / simulated_entry
    )

    position = {
        "address":
            address,

        "name":
            coin["name"],

        "symbol":
            coin["symbol"],

        "risk":
            coin["risk"],

        "score":
            coin["score"],

        "trade_size":
            trade_size,

        "entry_quote":
            quote,

        "entry_price":
            simulated_entry,

        "entry_fee":
            fee,

        "entry_slippage":
            slip,

        "quantity":
            quantity,

        "market_cap":
            coin["market_cap"],

        "liquidity":
            coin["liquidity"],

        "highest_quote":
            quote,

        "trailing":
            False,

        "opened_at":
            time.time(),
    }

    paper_cash -= required

    positions[
        address
    ] = position

    recent_entries[
        address
    ] = time.time()

    save_state()

    return position


# ============================================================
# EXIT CALCULATION
# ============================================================

def estimate_exit(
    position,
    quote,
    liquidity,
):

    slip = exit_slippage(
        liquidity
    )

    simulated_exit = (
        quote
        * (
            1 - slip
        )
    )

    gross = (
        position["quantity"]
        * simulated_exit
    )

    exit_fee = (
        gross
        * FEE_RATE
    )

    proceeds = (
        gross - exit_fee
    )

    original_cost = (
        position["trade_size"]
        + position["entry_fee"]
    )

    pnl = (
        proceeds
        - original_cost
    )

    pnl_pct = (
        pnl
        / original_cost
        * 100
        if original_cost
        else 0
    )

    return {
        "exit_price":
            simulated_exit,

        "exit_fee":
            exit_fee,

        "exit_slippage":
            slip,

        "proceeds":
            proceeds,

        "pnl":
            pnl,

        "pnl_pct":
            pnl_pct,
    }


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    address,
    quote,
    liquidity,
    reason,
):

    global paper_cash

    global daily_realized_pnl
    global daily_trade_count
    global daily_wins
    global daily_losses

    reset_daily_stats_if_needed()

    position = (
        positions.get(
            address
        )
    )

    if not position:
        return None

    result = estimate_exit(
        position,
        quote,
        liquidity,
    )

    paper_cash += (
        result["proceeds"]
    )

    trade = {
        **position,
        **result,

        "exit_quote":
            quote,

        "exit_liquidity":
            liquidity,

        "reason":
            reason,

        "closed_at":
            time.time(),
    }

    trade_history.append(
        trade
    )

    daily_trade_count += 1

    daily_realized_pnl += (
        result["pnl"]
    )

    if result["pnl"] > 0:

        daily_wins += 1

    else:

        daily_losses += 1

    positions.pop(
        address,
        None,
    )

    save_state()

    return trade


# ============================================================
# AUTOMATIC SCANNER
# ============================================================

async def sniper_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    global last_scan_time
    global last_scan_candidates
    global last_scan_best

    allowed, reason = (
        trading_allowed()
    )

    if not allowed:

        print(
            "New entries paused:",
            reason,
        )

        return

    chat_id = (
        context.job.chat_id
    )

    try:

        rankings = (
            await asyncio.to_thread(
                get_rankings
            )
        )

    except Exception as error:

        print(
            "sniper_job:",
            repr(error),
        )

        return

    last_scan_time = time.time()

    last_scan_candidates = (
        len(rankings)
    )

    last_scan_best = (
        rankings[0]
        if rankings
        else None
    )

    if not rankings:
        return


    # WATCH ALERT

    best = rankings[0]

    if (
        best["score"]
        >= WATCH_SCORE
    ):

        previous = (
            recent_watch_alerts.get(
                best["address"],
                0,
            )
        )

        if (
            time.time()
            - previous
            >= WATCH_COOLDOWN
        ):

            recent_watch_alerts[
                best["address"]
            ] = time.time()

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🎯 SNIPER WATCH\n\n"

                    f"{best['name']} "
                    f"({best['symbol']})\n"

                    f"{best['risk']}\n"

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

                    f"5m: "
                    f"{best['change_5m']:+.2f}%"
                ),
            )


    # MULTIPLE ENTRIES

    slots = (
        MAX_OPEN_POSITIONS
        - len(positions)
    )

    if slots <= 0:
        return

    eligible = [
        coin
        for coin
        in rankings
        if (
            coin["score"]
            >= ENTRY_SCORE
        )
    ]

    for coin in eligible[
        :slots
    ]:

        allowed, _ = (
            trading_allowed()
        )

        if not allowed:
            break

        position = (
            open_trade(
                coin
            )
        )

        if not position:
            continue

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚡ PAPER SNIPE OPENED\n\n"

                f"{position['name']} "
                f"({position['symbol']})\n"

                f"{position['risk']}\n"

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

                f"Daily target: "
                f"${DAILY_PROFIT_TARGET:.2f}\n"

                f"Today's P/L: "
                f"${daily_realized_pnl:+.2f}"
            ),
        )


# ============================================================
# POSITION MONITOR
# ============================================================

async def monitor_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        context.job.chat_id
    )

    for address in list(
        positions.keys()
    ):

        position = (
            positions.get(
                address
            )
        )

        if not position:
            continue

        try:

            quote_data = (
                await asyncio.to_thread(
                    current_quote,
                    address,
                )
            )

        except Exception as error:

            print(
                "monitor:",
                repr(error),
            )

            continue

        if not quote_data:
            continue

        quote = quote_data[
            "price"
        ]

        liquidity = quote_data[
            "liquidity"
        ]

        if quote <= 0:
            continue


        # HIGH-WATER MARK

        if (
            quote
            > position[
                "highest_quote"
            ]
        ):

            position[
                "highest_quote"
            ] = quote

            save_state()


        result = estimate_exit(
            position,
            quote,
            liquidity,
        )

        pnl_pct = result[
            "pnl_pct"
        ]


        # TRAILING STOP

        high_gain = (
            (
                position[
                    "highest_quote"
                ]
                - position[
                    "entry_quote"
                ]
            )
            / position[
                "entry_quote"
            ]
            * 100
        )

        if (
            high_gain
            >= TRAIL_ACTIVATION
        ):

            position[
                "trailing"
            ] = True

        highest = position[
            "highest_quote"
        ]

        drawdown = (
            (
                quote - highest
            )
            / highest
            * 100
            if highest
            else 0
        )


        # TIME OPEN

        held_minutes = (
            time.time()
            - position[
                "opened_at"
            ]
        ) / 60


        reason = None

        if (
            pnl_pct
            >= TAKE_PROFIT
        ):

            reason = (
                "TAKE PROFIT"
            )

        elif (
            pnl_pct
            <= -STOP_LOSS
        ):

            reason = (
                "STOP LOSS"
            )

        elif (
            position["trailing"]
            and (
                drawdown
                <= -TRAIL_DISTANCE
            )
        ):

            reason = (
                "TRAILING EXIT"
            )

        elif (
            held_minutes
            >= MAX_HOLD_MINUTES
        ):

            reason = (
                "TIME EXIT"
            )

        elif (
            liquidity
            < MIN_LIQUIDITY * 0.5
        ):

            reason = (
                "LIQUIDITY COLLAPSE"
            )

        if not reason:
            continue


        trade = close_trade(
            address,
            quote,
            liquidity,
            reason,
        )

        if not trade:
            continue

        icon = (
            "🟢"
            if trade["pnl"] >= 0
            else "🔴"
        )

        allowed, status_text = (
            trading_allowed()
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
                f"{trade['pnl_pct']:+.2f}%\n"

                f"P/L: "
                f"${trade['pnl']:+.2f}\n\n"

                f"Today's P/L: "
                f"${daily_realized_pnl:+.2f}\n"

                f"Today's trades: "
                f"{daily_trade_count}/"
                f"{MAX_TRADES_PER_DAY}\n"

                f"Status: "
                f"{status_text}"
            ),
        )


# ============================================================
# JOBS
# ============================================================

def schedule_jobs(
    job_queue,
    chat_id,
):

    chat_id = int(
        chat_id
    )

    sniper_name = (
        f"sniper_{chat_id}"
    )

    monitor_name = (
        f"monitor_{chat_id}"
    )

    if not (
        job_queue.get_jobs_by_name(
            sniper_name
        )
    ):

        job_queue.run_repeating(
            sniper_job,
            interval=
                SCAN_INTERVAL,
            first=
                FIRST_SCAN_DELAY,
            chat_id=
                chat_id,
            name=
                sniper_name,
        )

    if not (
        job_queue.get_jobs_by_name(
            monitor_name
        )
    ):

        job_queue.run_repeating(
            monitor_job,
            interval=
                MONITOR_INTERVAL,
            first=
                FIRST_MONITOR_DELAY,
            chat_id=
                chat_id,
            name=
                monitor_name,
        )


# ============================================================
# STARTUP
# ============================================================

async def post_init(
    application,
):

    if not CHAT_ID:
        return

    try:

        chat_id = int(
            CHAT_ID
        )

        schedule_jobs(
            application.job_queue,
            chat_id,
        )

        allowed, status_text = (
            trading_allowed()
        )

        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "🎯 PAPER SNIPER ONLINE\n\n"

                f"Balance: "
                f"${paper_cash:.2f}\n"

                f"Daily target: "
                f"${DAILY_PROFIT_TARGET:.2f}\n"

                f"Daily max loss: "
                f"-${DAILY_MAX_LOSS:.2f}\n"

                f"Max trades/day: "
                f"{MAX_TRADES_PER_DAY}\n"

                f"Status: "
                f"{status_text}"
            ),
        )

    except Exception as error:

        print(
            "post_init:",
            repr(error),
        )


# ============================================================
# PAPER EQUITY
# ============================================================

async def calculate_equity():

    equity = paper_cash
    unrealized = 0.0

    for position in list(
        positions.values()
    ):

        try:

            data = (
                await asyncio.to_thread(
                    current_quote,
                    position[
                        "address"
                    ],
                )
            )

        except Exception:

            continue

        if not data:
            continue

        result = estimate_exit(
            position,
            data["price"],
            data["liquidity"],
        )

        equity += (
            result["proceeds"]
        )

        unrealized += (
            result["pnl"]
        )

    return (
        equity,
        unrealized,
    )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    schedule_jobs(
        context.job_queue,
        update.effective_chat.id,
    )

    await update.message.reply_text(
        "🎯 CALTANIDE PAPER SNIPER\n\n"

        "/status\n"
        "/today\n"
        "/snipe\n"
        "/paper\n"
        "/account\n"
        "/positions\n"
        "/history\n"
        "/stats\n"
        "/resetpaper\n"
        "/chatid"
    )


async def chatid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🆔 Telegram chat ID:\n"
        f"{update.effective_chat.id}\n\n"
        "Railway variable:\n"
        "TELEGRAM_CHAT_ID"
    )


async def today_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    reset_daily_stats_if_needed()

    allowed, status_text = (
        trading_allowed()
    )

    remaining = max(
        0,
        DAILY_PROFIT_TARGET
        - daily_realized_pnl,
    )

    win_rate = (
        daily_wins
        / daily_trade_count
        * 100
        if daily_trade_count
        else 0
    )

    await update.message.reply_text(
        "🎯 DAILY PAPER TARGET\n\n"

        f"Target: "
        f"${DAILY_PROFIT_TARGET:.2f}\n"

        f"Today's P/L: "
        f"${daily_realized_pnl:+.2f}\n"

        f"Remaining: "
        f"${remaining:.2f}\n\n"

        f"Trades: "
        f"{daily_trade_count}/"
        f"{MAX_TRADES_PER_DAY}\n"

        f"Wins: "
        f"{daily_wins}\n"

        f"Losses: "
        f"{daily_losses}\n"

        f"Win rate: "
        f"{win_rate:.1f}%\n\n"

        f"Daily loss limit: "
        f"-${DAILY_MAX_LOSS:.2f}\n"

        f"Status: "
        f"{status_text}"
    )


async def paper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    (
        equity,
        unrealized,
    ) = await calculate_equity()

    realized = sum(
        trade["pnl"]
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

        f"Total realized: "
        f"${realized:+.2f}\n"

        f"Open P/L: "
        f"${unrealized:+.2f}\n\n"

        f"Today's P/L: "
        f"${daily_realized_pnl:+.2f}\n"

        "Mode: PAPER ONLY"
    )


async def account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await paper(
        update,
        context,
    )


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    jobs = list(
        context.job_queue.jobs()
    )

    allowed, status_text = (
        trading_allowed()
    )

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

    best = "none"

    if last_scan_best:

        best = (
            f"{last_scan_best['symbol']} "
            f"{last_scan_best['score']}/100"
        )

    await update.message.reply_text(
        "🟢 SNIPER STATUS\n\n"

        f"Jobs: "
        f"{len(jobs)}\n"

        f"Last scan: "
        f"{scan_text}\n"

        f"Candidates: "
        f"{last_scan_candidates}\n"

        f"Best: "
        f"{best}\n\n"

        f"Cash: "
        f"${paper_cash:.2f}\n"

        f"Open: "
        f"{len(positions)}/"
        f"{MAX_OPEN_POSITIONS}\n"

        f"Closed: "
        f"{len(trade_history)}\n\n"

        f"Today's P/L: "
        f"${daily_realized_pnl:+.2f}\n"

        f"Daily target: "
        f"${DAILY_PROFIT_TARGET:.2f}\n"

        f"Status: "
        f"{status_text}"
    )


async def snipe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🎯 Scanning Solana low caps..."
    )

    try:

        rankings = (
            await asyncio.to_thread(
                get_rankings
            )
        )

    except Exception:

        await update.message.reply_text(
            "Scanner temporarily unavailable."
        )

        return

    if not rankings:

        await update.message.reply_text(
            "No candidates currently pass "
            "the activity filters."
        )

        return

    lines = [
        "🎯 TOP SNIPER CANDIDATES",
        "",
    ]

    for i, coin in enumerate(
        rankings[:10],
        1,
    ):

        lines.extend(
            [
                (
                    f"{i}. "
                    f"{coin['name']} "
                    f"({coin['symbol']})"
                ),

                (
                    f"{coin['risk']} | "
                    f"{coin['score']}/100"
                ),

                (
                    f"Age: "
                    f"{coin['age']:.1f}m"
                ),

                (
                    f"MC: "
                    f"${coin['market_cap']:,.0f}"
                ),

                (
                    f"Liq: "
                    f"${coin['liquidity']:,.0f}"
                ),

                (
                    f"5m vol: "
                    f"${coin['volume_5m']:,.0f}"
                ),

                (
                    f"B/S: "
                    f"{coin['buys_5m']}/"
                    f"{coin['sells_5m']}"
                ),

                (
                    f"5m: "
                    f"{coin['change_5m']:+.2f}%"
                ),

                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)[:4000]
    )


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
        "🎯 OPEN PAPER SNIPES",
        "",
    ]

    for position in list(
        positions.values()
    ):

        try:

            data = (
                await asyncio.to_thread(
                    current_quote,
                    position["address"],
                )
            )

        except Exception:

            data = None

        if data:

            result = estimate_exit(
                position,
                data["price"],
                data["liquidity"],
            )

            pnl = (
                f"{result['pnl_pct']:+.2f}% "
                f"(${result['pnl']:+.2f})"
            )

        else:

            pnl = "unavailable"

        held = (
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
                    f"{position['risk']}"
                ),

                (
                    f"Score: "
                    f"{position['score']}/100"
                ),

                (
                    f"Stake: "
                    f"${position['trade_size']:.2f}"
                ),

                (
                    f"P/L: "
                    f"{pnl}"
                ),

                (
                    f"Held: "
                    f"{held:.1f}m"
                ),

                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)[:4000]
    )


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
        "📚 RECENT PAPER SNIPES",
        "",
    ]

    for trade in (
        trade_history[-20:]
    ):

        icon = (
            "🟢"
            if trade["pnl"] > 0
            else "🔴"
        )

        lines.extend(
            [
                (
                    f"{icon} "
                    f"{trade['symbol']}"
                ),

                (
                    f"{trade['reason']}"
                ),

                (
                    f"{trade['pnl_pct']:+.2f}% "
                    f"(${trade['pnl']:+.2f})"
                ),

                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)[:4000]
    )


async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not trade_history:

        await update.message.reply_text(
            "No completed trades yet."
        )

        return

    returns = [
        trade["pnl_pct"]
        for trade
        in trade_history
    ]

    winners = [
        r
        for r in returns
        if r > 0
    ]

    losers = [
        r
        for r in returns
        if r <= 0
    ]

    win_rate = (
        len(winners)
        / len(returns)
        * 100
    )

    average = (
        sum(returns)
        / len(returns)
    )

    avg_win = (
        sum(winners)
        / len(winners)
        if winners
        else 0
    )

    avg_loss = (
        sum(losers)
        / len(losers)
        if losers
        else 0
    )

    total = sum(
        trade["pnl"]
        for trade
        in trade_history
    )

    await update.message.reply_text(
        "📊 SNIPER PERFORMANCE\n\n"

        f"Trades: "
        f"{len(returns)}\n"

        f"Win rate: "
        f"{win_rate:.1f}%\n"

        f"Average trade: "
        f"{average:+.2f}%\n"

        f"Average winner: "
        f"{avg_win:+.2f}%\n"

        f"Average loser: "
        f"{avg_loss:+.2f}%\n"

        f"Best: "
        f"{max(returns):+.2f}%\n"

        f"Worst: "
        f"{min(returns):+.2f}%\n\n"

        f"Total realized: "
        f"${total:+.2f}"
    )


async def resetpaper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global paper_cash
    global positions
    global trade_history
    global recent_entries

    global daily_realized_pnl
    global daily_trade_count
    global daily_wins
    global daily_losses

    if (
        not context.args
        or context.args[0].lower()
        != "confirm"
    ):

        await update.message.reply_text(
            "⚠️ This erases the paper account.\n\n"
            "Send:\n"
            "/resetpaper confirm"
        )

        return

    paper_cash = (
        STARTING_BALANCE
    )

    positions = {}
    trade_history = []
    recent_entries = {}

    daily_realized_pnl = 0
    daily_trade_count = 0
    daily_wins = 0
    daily_losses = 0

    save_state()

    await update.message.reply_text(
        "♻️ PAPER ACCOUNT RESET\n\n"
        f"Balance: "
        f"${STARTING_BALANCE:.2f}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    load_state()

    application = (
        Application.builder()
        .token(
            BOT_TOKEN
        )
        .post_init(
            post_init
        )
        .build()
    )

    commands = [
        ("start", start),
        ("status", status),
        ("today", today_command),
        ("snipe", snipe),
        ("paper", paper),
        ("account", account),
        ("positions", positions_command),
        ("history", history_command),
        ("stats", stats),
        ("resetpaper", resetpaper),
        ("chatid", chatid),
    ]

    for (
        command,
        callback,
    ) in commands:

        application.add_handler(
            CommandHandler(
                command,
                callback,
            )
        )

    print(
        "CALTANIDE PAPER SNIPER ONLINE"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
