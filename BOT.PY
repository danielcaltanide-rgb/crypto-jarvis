import os
import time
import json
import asyncio
import requests
from pathlib import Path
from collections import deque

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
        "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_TOKEN."
    )


# ============================================================
# GENERAL SETTINGS
# ============================================================

HTTP_TIMEOUT = 12

SCAN_INTERVAL = 20
MONITOR_INTERVAL = 8

FIRST_SCAN_DELAY = 8
FIRST_MONITOR_DELAY = 12


# ============================================================
# PAPER WALLET
# ============================================================

STARTING_BALANCE = 1000.00

# Small stake because this scanner is deliberately aggressive.
TRADE_SIZE = 15.00

# Allows several simulated trades at once.
MAX_OPEN_POSITIONS = 6

# Automatic buy threshold.
ENTRY_SCORE = 58

# Watch alert threshold.
WATCH_SCORE = 50


# ============================================================
# VERY LOW CAP / NEW COIN FILTERS
# ============================================================

# This deliberately allows extremely tiny caps.
MIN_MARKET_CAP = 0

# Still focused on small caps.
MAX_MARKET_CAP = 5_000_000

# Very low liquidity is allowed in PAPER MODE.
#
# Anything below this is rejected because price data at
# essentially zero liquidity is not useful even for simulation.
MIN_LIQUIDITY = 2_000
MAX_LIQUIDITY = 1_500_000

# Pair age.
MIN_AGE_MINUTES = 1
MAX_AGE_MINUTES = 240

# Minimum activity.
MIN_VOLUME_5M = 500
MIN_TXNS_5M = 4

# We still avoid extremely late vertical candles.
MAX_5M_PUMP = 70.0
MAX_1H_PUMP = 300.0


# ============================================================
# FAST EXIT RULES
# ============================================================

TAKE_PROFIT = 6.0
STOP_LOSS = 4.0

TRAIL_ACTIVATION = 4.0
TRAIL_DISTANCE = 2.5

MAX_HOLD_MINUTES = 15


# ============================================================
# SIMULATED EXECUTION
# ============================================================

# Low-cap memes usually deserve harsher simulated execution.
BASE_ENTRY_SLIPPAGE = 0.010
BASE_EXIT_SLIPPAGE = 0.010

FEE_RATE = 0.003

# Additional paper slippage for extremely thin liquidity.
ULTRA_LOW_LIQUIDITY_SLIPPAGE = 0.025
LOW_LIQUIDITY_SLIPPAGE = 0.015


# ============================================================
# COOLDOWNS
# ============================================================

REENTRY_COOLDOWN = 20 * 60
WATCH_COOLDOWN = 5 * 60


# ============================================================
# PERSISTENT PAPER STATE
# ============================================================

STATE_FILE = Path(
    os.getenv(
        "STATE_FILE",
        "paper_sniper_state.json",
    )
)


# ============================================================
# MEMORY
# ============================================================

ADDRESS_MEMORY_SECONDS = 8 * 60 * 60
MAX_KNOWN_ADDRESSES = 1000

known_addresses = {}

recent_watch_alerts = {}

last_scan_time = 0.0
last_scan_candidates = 0
last_scan_best = None
last_discovered_count = 0

scan_history = deque(
    maxlen=300
)


# ============================================================
# PAPER ACCOUNT STATE
# ============================================================

paper_cash = STARTING_BALANCE

positions = {}
trade_history = []

recent_entries = {}


# ============================================================
# HTTP
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent":
            "CaltanideUltraLowCapPaperSniper/3.0"
    }
)


def get_json(url):

    response = session.get(
        url,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def chunks(items, size):

    for index in range(
        0,
        len(items),
        size,
    ):
        yield items[
            index:index + size
        ]


# ============================================================
# SAVE / LOAD PAPER WALLET
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
    }

    try:

        temp_file = Path(
            str(STATE_FILE) + ".tmp"
        )

        temp_file.write_text(
            json.dumps(
                state,
                indent=2,
            )
        )

        temp_file.replace(
            STATE_FILE
        )

    except Exception as error:

        print(
            "save_state error:",
            repr(error),
        )


def load_state():

    global paper_cash
    global positions
    global trade_history
    global recent_entries

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

        print(
            "Paper wallet restored."
        )

    except Exception as error:

        print(
            "load_state error:",
            repr(error),
        )


# ============================================================
# DEX SCREENER DISCOVERY SOURCES
# ============================================================

DISCOVERY_URLS = [

    "https://api.dexscreener.com/"
    "token-profiles/latest/v1",

    "https://api.dexscreener.com/"
    "token-boosts/latest/v1",

    "https://api.dexscreener.com/"
    "token-boosts/top/v1",

    "https://api.dexscreener.com/"
    "community-takeovers/latest/v1",

    "https://api.dexscreener.com/"
    "ads/latest/v1",
]


# ============================================================
# DISCOVERY MEMORY
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

        ordered = sorted(
            known_addresses.items(),
            key=lambda item:
                item[1],
            reverse=True,
        )

        keep = dict(
            ordered[
                :MAX_KNOWN_ADDRESSES
            ]
        )

        known_addresses.clear()

        known_addresses.update(
            keep
        )


def discover_solana_addresses():

    global last_discovered_count

    now = time.time()

    before = len(
        known_addresses
    )

    for url in DISCOVERY_URLS:

        try:

            data = get_json(
                url
            )

        except Exception as error:

            print(
                "discovery error:",
                url,
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

    after = len(
        known_addresses
    )

    last_discovered_count = max(
        0,
        after - before,
    )

    return list(
        known_addresses.keys()
    )


# ============================================================
# FETCH BEST SOLANA POOL
# ============================================================

def fetch_best_pairs():

    addresses = (
        discover_solana_addresses()
    )

    if not addresses:
        return {}

    pairs = []

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
                "token lookup error:",
                repr(error),
            )

            continue

        if isinstance(
            data,
            list,
        ):

            pairs.extend(
                data
            )

    best_pairs = {}

    for pair in pairs:

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
                    pair.get(
                        "liquidity"
                    )
                    or {}
                ).get("usd")
            )
            or 0
        )

        old = best_pairs.get(
            address
        )

        if old is None:

            best_pairs[
                address
            ] = pair

            continue

        old_liquidity = float(
            (
                (
                    old.get(
                        "liquidity"
                    )
                    or {}
                ).get("usd")
            )
            or 0
        )

        if (
            liquidity
            > old_liquidity
        ):

            best_pairs[
                address
            ] = pair

    return best_pairs


# ============================================================
# NORMALIZE DEX DATA
# ============================================================

def normalize_pair(
    address,
    pair,
):

    base = (
        pair.get("baseToken")
        or {}
    )

    quote = (
        pair.get("quoteToken")
        or {}
    )

    volume = (
        pair.get("volume")
        or {}
    )

    txns = (
        pair.get("txns")
        or {}
    )

    changes = (
        pair.get("priceChange")
        or {}
    )

    tx5 = (
        txns.get("m5")
        or {}
    )

    tx1 = (
        txns.get("h1")
        or {}
    )

    created = pair.get(
        "pairCreatedAt"
    )

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

    try:

        price = float(
            pair.get("priceUsd")
            or 0
        )

    except Exception:

        price = 0

    market_cap = float(
        pair.get("marketCap")
        or pair.get("fdv")
        or 0
    )

    liquidity = float(
        (
            (
                pair.get("liquidity")
                or {}
            ).get("usd")
        )
        or 0
    )

    return {

        "address":
            address,

        "pair_address":
            pair.get("pairAddress")
            or "",

        "dex":
            pair.get("dexId")
            or "unknown",

        "url":
            pair.get("url")
            or "",

        "name":
            base.get("name")
            or "Unknown",

        "symbol":
            base.get("symbol")
            or "?",

        "quote_symbol":
            quote.get("symbol")
            or "?",

        "price":
            price,

        "age":
            age_minutes,

        "market_cap":
            market_cap,

        "liquidity":
            liquidity,

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

        "volume_24h":
            float(
                volume.get("h24")
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

        "buys_1h":
            int(
                tx1.get("buys")
                or 0
            ),

        "sells_1h":
            int(
                tx1.get("sells")
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

        "change_24h":
            float(
                changes.get("h24")
                or 0
            ),
    }


# ============================================================
# LOW CAP RISK TIER
# ============================================================

def risk_tier(candidate):

    mc = candidate[
        "market_cap"
    ]

    liquidity = candidate[
        "liquidity"
    ]

    if (
        mc < 25_000
        or liquidity < 5_000
    ):

        return "☢️ ULTRA"

    if (
        mc < 75_000
        or liquidity < 15_000
    ):

        return "🔴 EXTREME"

    if mc < 250_000:

        return "🟠 VERY HIGH"

    return "🟡 HIGH"


# ============================================================
# PAPER SLIPPAGE MODEL
# ============================================================

def simulated_slippage(
    liquidity,
):

    if liquidity < 5_000:

        return (
            BASE_ENTRY_SLIPPAGE
            + ULTRA_LOW_LIQUIDITY_SLIPPAGE
        )

    if liquidity < 15_000:

        return (
            BASE_ENTRY_SLIPPAGE
            + LOW_LIQUIDITY_SLIPPAGE
        )

    if liquidity < 30_000:

        return (
            BASE_ENTRY_SLIPPAGE
            + 0.0075
        )

    return BASE_ENTRY_SLIPPAGE


# ============================================================
# SCORE NEW COIN
# ============================================================

def score_token(c):

    reasons = []
    risks = []

    # --------------------------------------------------------
    # HARD REJECTION
    # --------------------------------------------------------

    if c["price"] <= 0:

        return (
            0,
            reasons,
            ["invalid price"],
        )

    if not (
        MIN_AGE_MINUTES
        <= c["age"]
        <= MAX_AGE_MINUTES
    ):

        return (
            0,
            reasons,
            ["age outside range"],
        )

    if (
        c["market_cap"]
        > MAX_MARKET_CAP
    ):

        return (
            0,
            reasons,
            ["market cap too large"],
        )

    if not (
        MIN_LIQUIDITY
        <= c["liquidity"]
        <= MAX_LIQUIDITY
    ):

        return (
            0,
            reasons,
            ["liquidity rejected"],
        )

    if (
        c["volume_5m"]
        < MIN_VOLUME_5M
    ):

        return (
            0,
            reasons,
            ["5m volume too low"],
        )

    transactions = (
        c["buys_5m"]
        + c["sells_5m"]
    )

    if (
        transactions
        < MIN_TXNS_5M
    ):

        return (
            0,
            reasons,
            ["too few transactions"],
        )

    if (
        c["change_5m"]
        > MAX_5M_PUMP
    ):

        return (
            0,
            reasons,
            ["already exploded in 5m"],
        )

    if (
        c["change_1h"]
        > MAX_1H_PUMP
    ):

        return (
            0,
            reasons,
            ["already exploded in 1h"],
        )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

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

        reasons.append(
            "fresh"
        )

    elif c["age"] <= 120:

        score += 6

    else:

        score += 2


    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    mc = c[
        "market_cap"
    ]

    if mc <= 0:

        score += 2

        risks.append(
            "market cap unavailable"
        )

    elif mc < 25_000:

        score += 11

        reasons.append(
            "ultra micro-cap"
        )

        risks.append(
            "extreme market-cap risk"
        )

    elif mc < 75_000:

        score += 14

        reasons.append(
            "very low cap"
        )

    elif mc < 250_000:

        score += 15

        reasons.append(
            "micro-cap"
        )

    elif mc < 1_000_000:

        score += 11

    else:

        score += 5


    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    liquidity = c[
        "liquidity"
    ]

    if liquidity < 5_000:

        score += 2

        risks.append(
            "extremely thin liquidity"
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
            "strong liquidity"
        )

    else:

        score += 9


    # --------------------------------------------------------
    # 5 MINUTE VOLUME
    # --------------------------------------------------------

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

        reasons.append(
            "active volume"
        )

    elif volume5 >= 3_000:

        score += 7

    else:

        score += 3


    # --------------------------------------------------------
    # VOLUME ACCELERATION
    # --------------------------------------------------------

    volume1h = c[
        "volume_1h"
    ]

    if volume1h > 0:

        expected_5m = (
            volume1h / 12
        )

        if expected_5m > 0:

            acceleration = (
                volume5
                / expected_5m
            )

            if acceleration >= 5:

                score += 16

                reasons.append(
                    "volume exploding"
                )

            elif acceleration >= 3:

                score += 13

                reasons.append(
                    "volume acceleration"
                )

            elif acceleration >= 1.5:

                score += 8

            elif acceleration < 0.5:

                score -= 7

                risks.append(
                    "volume fading"
                )


    # --------------------------------------------------------
    # BUY PRESSURE
    # --------------------------------------------------------

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

        buy_ratio = (
            buys / sells
        )

        if buy_ratio >= 4:

            score += 18

            reasons.append(
                "extreme buy pressure"
            )

        elif buy_ratio >= 2.5:

            score += 15

            reasons.append(
                "strong buy pressure"
            )

        elif buy_ratio >= 1.5:

            score += 10

        elif buy_ratio >= 1.1:

            score += 5

        elif buy_ratio < 0.7:

            score -= 15

            risks.append(
                "heavy selling"
            )


    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

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

        reasons.append(
            "strong momentum"
        )

    elif 25 < move <= 45:

        score += 4

        risks.append(
            "already moving fast"
        )

    elif move > 45:

        score -= 7

        risks.append(
            "possible late entry"
        )

    elif -5 <= move < 0:

        score -= 4

    elif move < -5:

        score -= 15

        risks.append(
            "falling price"
        )


    # --------------------------------------------------------
    # LIQUIDITY / CAP RATIO
    # --------------------------------------------------------

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
                "excellent liquidity ratio"
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


    # --------------------------------------------------------
    # BUY COUNT ACTIVITY
    # --------------------------------------------------------

    if buys >= 100:

        score += 7

        reasons.append(
            "high transaction activity"
        )

    elif buys >= 30:

        score += 4


    score = max(
        0,
        min(
            score,
            100,
        ),
    )

    return (
        score,
        reasons,
        risks,
    )


# ============================================================
# RANK CANDIDATES
# ============================================================

def get_rankings():

    pairs = (
        fetch_best_pairs()
    )

    now = time.time()

    ranked = []

    for (
        address,
        pair,
    ) in pairs.items():

        candidate = (
            normalize_pair(
                address,
                pair,
            )
        )

        if not candidate:
            continue

        if (
            address
            in positions
        ):
            continue

        previous_entry = (
            recent_entries.get(
                address,
                0,
            )
        )

        if (
            previous_entry
            and (
                now - previous_entry
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
        ] = risk_tier(
            candidate
        )

        ranked.append(
            candidate
        )

    ranked.sort(
        key=lambda coin: (
            coin["score"],
            coin["volume_5m"],
            coin["buys_5m"],
            coin["liquidity"],
        ),
        reverse=True,
    )

    return ranked


# ============================================================
# LIVE QUOTE FROM DEXSCREENER
# ============================================================

def get_current_quote(
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
        key=lambda item:
            float(
                (
                    (
                        item.get(
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
                pair.get("liquidity")
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
# SIMULATED PAPER ENTRY
# ============================================================

def open_trade(
    coin,
):

    global paper_cash

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

    quote = coin[
        "price"
    ]

    if quote <= 0:
        return None

    entry_slippage = (
        simulated_slippage(
            coin["liquidity"]
        )
    )

    simulated_entry = (
        quote
        * (
            1
            + entry_slippage
        )
    )

    entry_fee = (
        TRADE_SIZE
        * FEE_RATE
    )

    required = (
        TRADE_SIZE
        + entry_fee
    )

    if paper_cash < required:
        return None

    quantity = (
        TRADE_SIZE
        / simulated_entry
    )

    position = {
        "address":
            address,

        "pair_address":
            coin[
                "pair_address"
            ],

        "name":
            coin["name"],

        "symbol":
            coin["symbol"],

        "risk":
            coin["risk"],

        "score":
            coin["score"],

        "entry_quote":
            quote,

        "entry_price":
            simulated_entry,

        "entry_slippage":
            entry_slippage,

        "entry_fee":
            entry_fee,

        "trade_size":
            TRADE_SIZE,

        "quantity":
            quantity,

        "market_cap":
            coin[
                "market_cap"
            ],

        "liquidity":
            coin[
                "liquidity"
            ],

        "highest_quote":
            quote,

        "trailing_active":
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
# ESTIMATE PAPER EXIT
# ============================================================

def estimate_exit(
    position,
    quote_price,
    liquidity,
):

    exit_slippage = (
        simulated_slippage(
            liquidity
        )
    )

    # Same base formula works as conservative paper exit.
    simulated_exit = (
        quote_price
        * (
            1
            - exit_slippage
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
        gross
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

        "exit_slippage":
            exit_slippage,

        "exit_fee":
            exit_fee,

        "proceeds":
            proceeds,

        "pnl":
            pnl,

        "pnl_pct":
            pnl_pct,
    }


# ============================================================
# PAPER CLOSE
# ============================================================

def close_trade(
    address,
    quote_price,
    liquidity,
    reason,
):

    global paper_cash

    position = positions.get(
        address
    )

    if not position:
        return None

    result = estimate_exit(
        position,
        quote_price,
        liquidity,
    )

    paper_cash += (
        result["proceeds"]
    )

    trade = {
        **position,
        **result,

        "exit_quote":
            quote_price,

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

    positions.pop(
        address,
        None,
    )

    save_state()

    return trade


# ============================================================
# AUTOMATIC SNIPER
# ============================================================

async def sniper_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    global last_scan_time
    global last_scan_candidates
    global last_scan_best

    chat_id = (
        context.job.chat_id
    )

    try:

        ranked = await asyncio.to_thread(
            get_rankings
        )

    except Exception as error:

        print(
            "sniper_job error:",
            repr(error),
        )

        return

    last_scan_time = time.time()

    last_scan_candidates = len(
        ranked
    )

    last_scan_best = (
        ranked[0]
        if ranked
        else None
    )

    scan_history.append(
        {
            "timestamp":
                last_scan_time,

            "candidates":
                len(ranked),

            "best_score":
                (
                    ranked[0]["score"]
                    if ranked
                    else 0
                ),
        }
    )

    if not ranked:
        return

    best = ranked[0]

    # --------------------------------------------------------
    # WATCH ALERT
    # --------------------------------------------------------

    if best["score"] >= WATCH_SCORE:

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

            strengths = ", ".join(
                best["reasons"][:4]
            )

            risks = ", ".join(
                best["risks"][:4]
            )

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🎯 SNIPER WATCH\n\n"

                    f"{best['name']} "
                    f"({best['symbol']})\n"

                    f"Risk: "
                    f"{best['risk']}\n"

                    f"Score: "
                    f"{best['score']}/100\n"

                    f"Age: "
                    f"{best['age']:.1f} min\n"

                    f"MC: "
                    f"${best['market_cap']:,.0f}\n"

                    f"Liquidity: "
                    f"${best['liquidity']:,.0f}\n"

                    f"5m volume: "
                    f"${best['volume_5m']:,.0f}\n"

                    f"5m B/S: "
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

    # --------------------------------------------------------
    # MULTIPLE PAPER SNIPES
    # --------------------------------------------------------

    available_slots = (
        MAX_OPEN_POSITIONS
        - len(positions)
    )

    if available_slots <= 0:
        return

    eligible = [
        coin
        for coin in ranked
        if (
            coin["score"]
            >= ENTRY_SCORE
        )
    ]

    if not eligible:
        return

    for coin in eligible[
        :available_slots
    ]:

        position = open_trade(
            coin
        )

        if not position:
            continue

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚡🎯 PAPER SNIPE OPENED\n\n"

                f"{position['name']} "
                f"({position['symbol']})\n"

                f"Risk: "
                f"{position['risk']}\n"

                f"Score: "
                f"{position['score']}/100\n"

                f"Paper stake: "
                f"${position['trade_size']:.2f}\n"

                f"Market cap: "
                f"${position['market_cap']:,.0f}\n"

                f"Liquidity: "
                f"${position['liquidity']:,.0f}\n"

                f"Market quote: "
                f"${position['entry_quote']:.12f}\n"

                f"Simulated entry: "
                f"${position['entry_price']:.12f}\n"

                f"Simulated slippage: "
                f"{position['entry_slippage'] * 100:.2f}%\n\n"

                f"TP: +{TAKE_PROFIT:.1f}%\n"

                f"SL: -{STOP_LOSS:.1f}%\n"

                f"Max hold: "
                f"{MAX_HOLD_MINUTES} min"
            ),
        )


# ============================================================
# FAST POSITION MONITOR
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

        quote = quote_data[
            "price"
        ]

        liquidity = quote_data[
            "liquidity"
        ]

        if quote <= 0:
            continue

        # ----------------------------------------------------
        # HIGH WATER MARK
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # ESTIMATED REALISTIC EXIT
        # ----------------------------------------------------

        result = estimate_exit(
            position,
            quote,
            liquidity,
        )

        pnl_pct = result[
            "pnl_pct"
        ]

        # ----------------------------------------------------
        # TRAILING ACTIVATION
        # ----------------------------------------------------

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
                "trailing_active"
            ] = True

        # ----------------------------------------------------
        # DRAWDOWN
        # ----------------------------------------------------

        high = position[
            "highest_quote"
        ]

        drawdown = (
            (
                quote
                - high
            )
            / high
            * 100
            if high
            else 0
        )

        # ----------------------------------------------------
        # TIME
        # ----------------------------------------------------

        held_minutes = (
            time.time()
            - position[
                "opened_at"
            ]
        ) / 60

        # ----------------------------------------------------
        # EXIT DECISION
        # ----------------------------------------------------

        reason = None

        if (
            pnl_pct
            >= TAKE_PROFIT
        ):

            reason = "TAKE PROFIT"

        elif (
            pnl_pct
            <= -STOP_LOSS
        ):

            reason = "STOP LOSS"

        elif (
            position[
                "trailing_active"
            ]
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

            reason = "TIME EXIT"

        elif (
            liquidity
            < MIN_LIQUIDITY
            * 0.5
        ):

            reason = (
                "LIQUIDITY COLLAPSE"
            )

        if not reason:
            continue

        # ----------------------------------------------------
        # CLOSE PAPER POSITION
        # ----------------------------------------------------

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

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"{icon} PAPER SNIPE CLOSED\n\n"

                f"{trade['name']} "
                f"({trade['symbol']})\n"

                f"Reason: "
                f"{trade['reason']}\n"

                f"Risk: "
                f"{trade['risk']}\n"

                f"Entry: "
                f"${trade['entry_price']:.12f}\n"

                f"Exit: "
                f"${trade['exit_price']:.12f}\n"

                f"Net return: "
                f"{trade['pnl_pct']:+.2f}%\n"

                f"P/L: "
                f"${trade['pnl']:+.2f}\n\n"

                f"Paper cash: "
                f"${paper_cash:.2f}"
            ),
        )


# ============================================================
# JOB SCHEDULER
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

        print(
            "TELEGRAM_CHAT_ID not configured."
        )

        return

    try:

        chat_id = int(
            CHAT_ID
        )

        schedule_jobs(
            application.job_queue,
            chat_id,
        )

        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "🎯 ULTRA LOW-CAP PAPER SNIPER ONLINE\n\n"

                f"Paper wallet: "
                f"${paper_cash:.2f}\n"

                f"Stake: "
                f"${TRADE_SIZE:.2f}\n"

                f"Max simultaneous trades: "
                f"{MAX_OPEN_POSITIONS}\n"

                f"Entry score: "
                f"{ENTRY_SCORE}/100\n"

                f"Scan: every "
                f"{SCAN_INTERVAL}s\n"

                "Mode: PAPER ONLY"
            ),
        )

    except Exception as error:

        print(
            "post_init error:",
            repr(error),
        )


# ============================================================
# /START
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

        "Automatic Solana new-coin "
        "paper sniping is active.\n\n"

        "/status\n"
        "/snipe\n"
        "/paper\n"
        "/account\n"
        "/positions\n"
        "/history\n"
        "/stats\n"
        "/resetpaper\n"
        "/chatid"
    )


# ============================================================
# /CHATID
# ============================================================

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

            quote_data = (
                await asyncio.to_thread(
                    get_current_quote,
                    position[
                        "address"
                    ],
                )
            )

        except Exception:

            continue

        if not quote_data:
            continue

        result = estimate_exit(
            position,
            quote_data["price"],
            quote_data["liquidity"],
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
# /PAPER
# ============================================================

async def paper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    realized = sum(
        trade["pnl"]
        for trade
        in trade_history
    )

    (
        equity,
        unrealized,
    ) = await calculate_equity()

    wins = sum(
        1
        for trade
        in trade_history
        if trade["pnl"] > 0
    )

    losses = (
        len(trade_history)
        - wins
    )

    await update.message.reply_text(
        "💵 PAPER WALLET\n\n"

        f"Starting balance: "
        f"${STARTING_BALANCE:.2f}\n"

        f"Available cash: "
        f"${paper_cash:.2f}\n"

        f"Estimated equity: "
        f"${equity:.2f}\n\n"

        f"Open trades: "
        f"{len(positions)}/"
        f"{MAX_OPEN_POSITIONS}\n"

        f"Closed trades: "
        f"{len(trade_history)}\n"

        f"Wins: "
        f"{wins}\n"

        f"Losses: "
        f"{losses}\n\n"

        f"Realized P/L: "
        f"${realized:+.2f}\n"

        f"Open P/L: "
        f"${unrealized:+.2f}\n\n"

        "Mode: PAPER MONEY"
    )


# ============================================================
# /ACCOUNT
# ============================================================

async def account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await paper(
        update,
        context,
    )


# ============================================================
# /STATUS
# ============================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    jobs = list(
        context.job_queue.jobs()
    )

    if last_scan_time:

        age = int(
            time.time()
            - last_scan_time
        )

        scan_text = (
            f"{age}s ago"
        )

    else:

        scan_text = (
            "waiting"
        )

    best = "none"

    if last_scan_best:

        best = (
            f"{last_scan_best['symbol']} "
            f"{last_scan_best['score']}/100 "
            f"{last_scan_best['risk']}"
        )

    await update.message.reply_text(
        "🟢 SNIPER STATUS\n\n"

        f"Jobs: "
        f"{len(jobs)}\n"

        f"Scan interval: "
        f"{SCAN_INTERVAL}s\n"

        f"Monitor interval: "
        f"{MONITOR_INTERVAL}s\n"

        f"Last scan: "
        f"{scan_text}\n"

        f"New addresses found: "
        f"{last_discovered_count}\n"

        f"Candidates: "
        f"{last_scan_candidates}\n"

        f"Best: "
        f"{best}\n\n"

        f"Paper cash: "
        f"${paper_cash:.2f}\n"

        f"Open: "
        f"{len(positions)}/"
        f"{MAX_OPEN_POSITIONS}\n"

        f"Closed: "
        f"{len(trade_history)}\n\n"

        f"Entry threshold: "
        f"{ENTRY_SCORE}/100"
    )


# ============================================================
# /SNIPE
# ============================================================

async def snipe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🎯 Scanning new and "
        "ultra-low-cap Solana coins..."
    )

    try:

        ranked = (
            await asyncio.to_thread(
                get_rankings
            )
        )

    except Exception as error:

        print(
            "/snipe error:",
            repr(error),
        )

        await update.message.reply_text(
            "Scanner temporarily unavailable."
        )

        return

    if not ranked:

        await update.message.reply_text(
            "No current candidates "
            "pass the minimum activity filters."
        )

        return

    lines = [
        "🎯 TOP SNIPER CANDIDATES",
        "",
    ]

    for (
        index,
        coin,
    ) in enumerate(
        ranked[:10],
        1,
    ):

        lines.extend(
            [
                (
                    f"{index}. "
                    f"{coin['name']} "
                    f"({coin['symbol']})"
                ),

                (
                    f"{coin['risk']} | "
                    f"Score "
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
                    f"Liquidity: "
                    f"${coin['liquidity']:,.0f}"
                ),

                (
                    f"5m volume: "
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

                (
                    f"Token:\n"
                    f"{coin['address']}"
                ),

                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)[:4000]
    )


# ============================================================
# /POSITIONS
# ============================================================

async def positions_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not positions:

        await update.message.reply_text(
            "No paper sniper positions open."
        )

        return

    lines = [
        "🎯 OPEN PAPER SNIPES",
        "",
    ]

    for position in list(
        positions.values()
    ):

        quote_data = None

        try:

            quote_data = (
                await asyncio.to_thread(
                    get_current_quote,
                    position[
                        "address"
                    ],
                )
            )

        except Exception:
            pass

        if quote_data:

            result = estimate_exit(
                position,
                quote_data["price"],
                quote_data["liquidity"],
            )

            pnl_text = (
                f"{result['pnl_pct']:+.2f}% "
                f"(${result['pnl']:+.2f})"
            )

            price_text = (
                f"${quote_data['price']:.12f}"
            )

        else:

            pnl_text = (
                "unavailable"
            )

            price_text = (
                "unavailable"
            )

        held = (
            time.time()
            - position[
                "opened_at"
            ]
        ) / 60

        lines.extend(
            [
                (
                    f"{position['name']} "
                    f"({position['symbol']})"
                ),

                (
                    f"Risk: "
                    f"{position['risk']}"
                ),

                (
                    f"Score: "
                    f"{position['score']}/100"
                ),

                (
                    f"Entry: "
                    f"${position['entry_price']:.12f}"
                ),

                (
                    f"Current: "
                    f"{price_text}"
                ),

                (
                    f"Estimated P/L: "
                    f"{pnl_text}"
                ),

                (
                    f"Held: "
                    f"{held:.1f} min"
                ),

                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)[:4000]
    )


# ============================================================
# /HISTORY
# ============================================================

async def history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not trade_history:

        await update.message.reply_text(
            "No completed paper snipes yet."
        )

        return

    lines = [
        "📚 PAPER SNIPE HISTORY",
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
                    f"{trade['name']} "
                    f"({trade['symbol']})"
                ),

                (
                    f"{trade['risk']}"
                ),

                (
                    f"Exit: "
                    f"{trade['reason']}"
                ),

                (
                    f"P/L: "
                    f"{trade['pnl_pct']:+.2f}% "
                    f"(${trade['pnl']:+.2f})"
                ),

                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)[:4000]
    )


# ============================================================
# /STATS
# ============================================================

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not trade_history:

        await update.message.reply_text(
            "📊 No completed trades yet.\n"
            "Let the sniper collect "
            "paper trades first."
        )

        return

    returns = [
        trade["pnl_pct"]
        for trade
        in trade_history
    ]

    winners = [
        value
        for value
        in returns
        if value > 0
    ]

    losers = [
        value
        for value
        in returns
        if value <= 0
    ]

    wins = len(
        winners
    )

    trades = len(
        returns
    )

    win_rate = (
        wins / trades * 100
    )

    average = (
        sum(returns)
        / trades
    )

    average_win = (
        sum(winners)
        / len(winners)
        if winners
        else 0
    )

    average_loss = (
        sum(losers)
        / len(losers)
        if losers
        else 0
    )

    realized = sum(
        trade["pnl"]
        for trade
        in trade_history
    )

    await update.message.reply_text(
        "📊 SNIPER PERFORMANCE\n\n"

        f"Trades: "
        f"{trades}\n"

        f"Win rate: "
        f"{win_rate:.1f}%\n"

        f"Average trade: "
        f"{average:+.2f}%\n"

        f"Average winner: "
        f"{average_win:+.2f}%\n"

        f"Average loser: "
        f"{average_loss:+.2f}%\n"

        f"Best: "
        f"{max(returns):+.2f}%\n"

        f"Worst: "
        f"{min(returns):+.2f}%\n\n"

        f"Realized P/L: "
        f"${realized:+.2f}"
    )


# ============================================================
# /RESETPAPER
# ============================================================

async def resetpaper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global paper_cash
    global positions
    global trade_history
    global recent_entries

    # Require confirmation.
    if (
        not context.args
        or context.args[0].lower()
        != "confirm"
    ):

        await update.message.reply_text(
            "⚠️ This resets the entire "
            "paper wallet and history.\n\n"

            "To confirm, send:\n"
            "/resetpaper confirm"
        )

        return

    paper_cash = (
        STARTING_BALANCE
    )

    positions = {}
    trade_history = []
    recent_entries = {}

    save_state()

    await update.message.reply_text(
        "♻️ PAPER WALLET RESET\n\n"
        f"Balance: "
        f"${STARTING_BALANCE:.2f}\n"
        "Positions: 0\n"
        "History: cleared"
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

    handlers = [
        (
            "start",
            start,
        ),
        (
            "status",
            status,
        ),
        (
            "snipe",
            snipe,
        ),
        (
            "paper",
            paper,
        ),
        (
            "account",
            account,
        ),
        (
            "positions",
            positions_command,
        ),
        (
            "history",
            history_command,
        ),
        (
            "stats",
            stats,
        ),
        (
            "resetpaper",
            resetpaper,
        ),
        (
            "chatid",
            chatid,
        ),
    ]

    for (
        command,
        callback,
    ) in handlers:

        application.add_handler(
            CommandHandler(
                command,
                callback,
            )
        )

    print(
        "ULTRA LOW-CAP "
        "PAPER SNIPER ONLINE"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
