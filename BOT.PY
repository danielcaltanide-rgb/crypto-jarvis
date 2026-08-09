import os
import time
import json
import asyncio
import requests
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
)

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError(
        "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_TOKEN."
    )


# =========================================================
# FILE PERSISTENCE
# =========================================================

STATE_FILE = Path("paper_sniper_state.json")


# =========================================================
# PAPER ACCOUNT SETTINGS
# =========================================================

STARTING_BALANCE = 1000.00
TRADE_SIZE = 20.00

MAX_OPEN_POSITIONS = 5

ENTRY_SCORE = 62
WATCH_SCORE = 55


# =========================================================
# SCAN SPEED
# =========================================================

SCAN_INTERVAL = 20
MONITOR_INTERVAL = 8

FIRST_SCAN_DELAY = 10


# =========================================================
# NEW COIN FILTERS
# =========================================================

MIN_AGE_MINUTES = 2
MAX_AGE_MINUTES = 180

MIN_MARKET_CAP = 25_000
MAX_MARKET_CAP = 2_500_000

MIN_LIQUIDITY = 12_000
MAX_LIQUIDITY = 800_000

MIN_5M_VOLUME = 1_500
MIN_5M_TRANSACTIONS = 8

MAX_5M_PUMP = 30.0
MAX_1H_PUMP = 150.0


# =========================================================
# EXIT RULES
# =========================================================

TAKE_PROFIT = 5.0
STOP_LOSS = 3.0

TRAIL_ACTIVATION = 3.0
TRAIL_DISTANCE = 2.0

MAX_HOLD_MINUTES = 15


# =========================================================
# SIMULATED EXECUTION COSTS
# =========================================================

ENTRY_SLIPPAGE = 0.008
EXIT_SLIPPAGE = 0.008

ENTRY_FEE_RATE = 0.003
EXIT_FEE_RATE = 0.003


# =========================================================
# COOLDOWNS
# =========================================================

REENTRY_COOLDOWN = 30 * 60
WATCH_COOLDOWN = 5 * 60


# =========================================================
# HTTP
# =========================================================

HTTP_TIMEOUT = 12

session = requests.Session()

session.headers.update(
    {
        "User-Agent": "CaltanidePaperSniper/2.0"
    }
)


# =========================================================
# RUNTIME STATE
# =========================================================

paper_cash = STARTING_BALANCE

positions = {}
history = []

recent_entries = {}
recent_watch_alerts = {}

last_scan_time = 0.0
last_scan_candidates = 0
last_scan_best = None

known_addresses = {}

ADDRESS_MEMORY_SECONDS = 6 * 60 * 60
MAX_KNOWN_ADDRESSES = 600


# =========================================================
# PERSISTENCE
# =========================================================

def save_state():

    data = {
        "paper_cash": paper_cash,
        "positions": positions,
        "history": history,
        "recent_entries": recent_entries,
    }

    try:
        STATE_FILE.write_text(
            json.dumps(
                data,
                indent=2,
            )
        )

    except Exception as error:
        print(
            "save_state error:",
            repr(error),
        )


def load_state():

    global paper_cash
    global positions
    global history
    global recent_entries

    if not STATE_FILE.exists():
        return

    try:

        data = json.loads(
            STATE_FILE.read_text()
        )

        paper_cash = float(
            data.get(
                "paper_cash",
                STARTING_BALANCE,
            )
        )

        positions = data.get(
            "positions",
            {},
        )

        history = data.get(
            "history",
            [],
        )

        recent_entries = data.get(
            "recent_entries",
            {},
        )

        print(
            "Paper sniper state loaded."
        )

    except Exception as error:

        print(
            "load_state error:",
            repr(error),
        )


# =========================================================
# HTTP HELPERS
# =========================================================

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


# =========================================================
# DEX DISCOVERY
# =========================================================

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
]


def cleanup_known_addresses():

    now = time.time()

    expired = []

    for address, timestamp in (
        known_addresses.items()
    ):

        if (
            now - timestamp
            > ADDRESS_MEMORY_SECONDS
        ):
            expired.append(
                address
            )

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
            key=lambda item: item[1],
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


def discover_solana_tokens():

    now = time.time()

    for url in DISCOVERY_URLS:

        try:
            data = get_json(url)

        except Exception as error:

            print(
                "Discovery error:",
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

            address = item.get(
                "tokenAddress"
            )

            if not address:
                continue

            known_addresses[
                address
            ] = now

    cleanup_known_addresses()

    return list(
        known_addresses.keys()
    )


# =========================================================
# PAIR LOOKUP
# =========================================================

def fetch_candidate_pairs():

    addresses = (
        discover_solana_tokens()
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
            data = get_json(url)

        except Exception as error:

            print(
                "Token batch error:",
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

    best_pairs = {}

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

        address = base.get(
            "address"
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
                    old.get("liquidity")
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


# =========================================================
# NORMALIZE PAIRS
# =========================================================

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

    transactions = (
        pair.get("txns")
        or {}
    )

    tx5 = (
        transactions.get("m5")
        or {}
    )

    tx1 = (
        transactions.get("h1")
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
        price = 0.0

    return {

        "address":
            address,

        "pair":
            pair.get("pairAddress")
            or "",

        "dex":
            pair.get("dexId")
            or "unknown",

        "name":
            base.get("name")
            or "Unknown",

        "symbol":
            base.get("symbol")
            or "?",

        "price":
            price,

        "age":
            age_minutes,

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
                        pair.get("liquidity")
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
    }


# =========================================================
# SCORE TOKENS
# =========================================================

def score_token(c):

    reasons = []
    risks = []

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
            ["age rejected"],
        )

    if not (
        MIN_MARKET_CAP
        <= c["market_cap"]
        <= MAX_MARKET_CAP
    ):
        return (
            0,
            reasons,
            ["market cap rejected"],
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
        < MIN_5M_VOLUME
    ):
        return (
            0,
            reasons,
            ["5m volume rejected"],
        )

    tx_count = (
        c["buys_5m"]
        + c["sells_5m"]
    )

    if (
        tx_count
        < MIN_5M_TRANSACTIONS
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
            ["5m pump too extended"],
        )

    if (
        c["change_1h"]
        > MAX_1H_PUMP
    ):
        return (
            0,
            reasons,
            ["1h pump too extended"],
        )

    score = 0


    # AGE

    if c["age"] <= 10:

        score += 15

        reasons.append(
            "very fresh"
        )

    elif c["age"] <= 30:

        score += 13

        reasons.append(
            "fresh launch"
        )

    elif c["age"] <= 60:

        score += 9

    else:

        score += 4


    # MARKET CAP

    if (
        50_000
        <= c["market_cap"]
        <= 500_000
    ):

        score += 14

        reasons.append(
            "micro-cap"
        )

    elif (
        c["market_cap"]
        <= 1_000_000
    ):

        score += 10

    else:

        score += 5


    # LIQUIDITY

    if (
        30_000
        <= c["liquidity"]
        <= 250_000
    ):

        score += 17

        reasons.append(
            "usable liquidity"
        )

    elif (
        c["liquidity"]
        >= 20_000
    ):

        score += 11

    else:

        score += 5


    # VOLUME

    if (
        c["volume_5m"]
        >= 50_000
    ):

        score += 16

        reasons.append(
            "explosive volume"
        )

    elif (
        c["volume_5m"]
        >= 20_000
    ):

        score += 13

        reasons.append(
            "strong volume"
        )

    elif (
        c["volume_5m"]
        >= 5_000
    ):

        score += 8

    else:

        score += 4


    # VOLUME ACCELERATION

    if (
        c["volume_1h"]
        > 0
    ):

        expected_5m = (
            c["volume_1h"]
            / 12
        )

        if expected_5m > 0:

            acceleration = (
                c["volume_5m"]
                / expected_5m
            )

            if (
                acceleration
                >= 4
            ):

                score += 14

                reasons.append(
                    "volume exploding"
                )

            elif (
                acceleration
                >= 2
            ):

                score += 10

                reasons.append(
                    "volume accelerating"
                )

            elif (
                acceleration
                >= 1.25
            ):

                score += 5

            elif (
                acceleration
                < 0.5
            ):

                score -= 6

                risks.append(
                    "volume fading"
                )


    # BUY PRESSURE

    buys = (
        c["buys_5m"]
    )

    sells = (
        c["sells_5m"]
    )

    if (
        sells == 0
        and buys >= 10
    ):

        score += 15

        reasons.append(
            "buyers dominating"
        )

    elif sells > 0:

        ratio = (
            buys / sells
        )

        if (
            ratio >= 3
        ):

            score += 16

            reasons.append(
                "extreme buy pressure"
            )

        elif (
            ratio >= 2
        ):

            score += 13

            reasons.append(
                "strong buy pressure"
            )

        elif (
            ratio >= 1.4
        ):

            score += 8

        elif (
            ratio < 0.8
        ):

            score -= 12

            risks.append(
                "sell pressure"
            )


    # MOMENTUM

    move = (
        c["change_5m"]
    )

    if (
        1
        <= move
        <= 8
    ):

        score += 12

        reasons.append(
            "clean momentum"
        )

    elif (
        8
        < move
        <= 15
    ):

        score += 8

    elif (
        15
        < move
        <= 25
    ):

        score += 2

        risks.append(
            "already moving fast"
        )

    elif move < -10:

        score -= 15

        risks.append(
            "price falling"
        )

    elif move < 0:

        score -= 5


    # LIQUIDITY RATIO

    if c["market_cap"] > 0:

        ratio = (
            c["liquidity"]
            / c["market_cap"]
        )

        if ratio >= 0.25:

            score += 9

            reasons.append(
                "strong liquidity ratio"
            )

        elif ratio >= 0.10:

            score += 5

        elif ratio < 0.03:

            score -= 15

            risks.append(
                "thin liquidity"
            )

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


# =========================================================
# RANK COINS
# =========================================================

def get_rankings():

    pairs = (
        fetch_candidate_pairs()
    )

    results = []

    now = time.time()

    for address, pair in (
        pairs.items()
    ):

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
                now
                - previous_entry
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

        if score < 40:
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

        results.append(
            candidate
        )

    results.sort(
        key=lambda coin: (
            coin["score"],
            coin["volume_5m"],
            coin["buys_5m"],
            coin["liquidity"],
        ),
        reverse=True,
    )

    return results


# =========================================================
# TOKEN PRICE
# =========================================================

def get_token_price(
    address,
):

    url = (
        "https://api.dexscreener.com/"
        "token-pairs/v1/solana/"
        f"{address}"
    )

    data = get_json(
        url
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
        key=lambda item: float(
            (
                (
                    item.get("liquidity")
                    or {}
                ).get("usd")
            )
            or 0
        ),
    )

    try:

        return float(
            pair.get("priceUsd")
            or 0
        )

    except Exception:
        return None


# =========================================================
# ESTIMATE PAPER EXIT
# =========================================================

def estimate_exit(
    position,
    quote,
):

    simulated_exit_price = (
        quote
        * (
            1
            - EXIT_SLIPPAGE
        )
    )

    gross = (
        position["quantity"]
        * simulated_exit_price
    )

    exit_fee = (
        gross
        * EXIT_FEE_RATE
    )

    proceeds = (
        gross
        - exit_fee
    )

    total_entry_cost = (
        position["trade_size"]
        + position["entry_fee"]
    )

    pnl = (
        proceeds
        - total_entry_cost
    )

    pnl_pct = (
        pnl
        / total_entry_cost
        * 100
        if total_entry_cost
        else 0
    )

    return {

        "exit_price":
            simulated_exit_price,

        "exit_fee":
            exit_fee,

        "proceeds":
            proceeds,

        "pnl":
            pnl,

        "pnl_pct":
            pnl_pct,
    }


# =========================================================
# PAPER BUY
# =========================================================

def open_trade(
    coin,
):

    global paper_cash

    if (
        len(positions)
        >= MAX_OPEN_POSITIONS
    ):
        return None

    address = (
        coin["address"]
    )

    if (
        address
        in positions
    ):
        return None

    quote = (
        coin["price"]
    )

    if quote <= 0:
        return None

    simulated_entry = (
        quote
        * (
            1
            + ENTRY_SLIPPAGE
        )
    )

    entry_fee = (
        TRADE_SIZE
        * ENTRY_FEE_RATE
    )

    total_required = (
        TRADE_SIZE
        + entry_fee
    )

    if (
        paper_cash
        < total_required
    ):
        return None

    quantity = (
        TRADE_SIZE
        / simulated_entry
    )

    position = {

        "address":
            address,

        "name":
            coin["name"],

        "symbol":
            coin["symbol"],

        "score":
            coin["score"],

        "market_cap":
            coin["market_cap"],

        "liquidity":
            coin["liquidity"],

        "entry_quote":
            quote,

        "entry_price":
            simulated_entry,

        "trade_size":
            TRADE_SIZE,

        "entry_fee":
            entry_fee,

        "quantity":
            quantity,

        "highest_quote":
            quote,

        "trailing":
            False,

        "opened_at":
            time.time(),
    }

    paper_cash -= (
        total_required
    )

    positions[
        address
    ] = position

    recent_entries[
        address
    ] = time.time()

    save_state()

    return position


# =========================================================
# PAPER SELL
# =========================================================

def close_trade(
    address,
    quote,
    reason,
):

    global paper_cash

    position = (
        positions.get(
            address
        )
    )

    if not position:
        return None

    result = (
        estimate_exit(
            position,
            quote,
        )
    )

    paper_cash += (
        result["proceeds"]
    )

    trade = {

        **position,

        **result,

        "exit_quote":
            quote,

        "reason":
            reason,

        "closed_at":
            time.time(),
    }

    history.append(
        trade
    )

    positions.pop(
        address,
        None,
    )

    save_state()

    return trade


# =========================================================
# AUTOMATIC SNIPER
# =========================================================

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

        rankings = (
            await asyncio.to_thread(
                get_rankings
            )
        )

    except Exception as error:

        print(
            "Sniper scan error:",
            repr(error),
        )

        return

    last_scan_time = (
        time.time()
    )

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

    best = (
        rankings[0]
    )


    # -----------------------------------------------------
    # WATCH ALERT
    # -----------------------------------------------------

    if (
        best["score"]
        >= WATCH_SCORE
    ):

        previous_alert = (
            recent_watch_alerts.get(
                best["address"],
                0,
            )
        )

        if (
            time.time()
            - previous_alert
            >= WATCH_COOLDOWN
        ):

            recent_watch_alerts[
                best["address"]
            ] = time.time()

            reasons = ", ".join(
                best["reasons"][:3]
            )

            risks = ", ".join(
                best["risks"][:3]
            )

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🎯 NEW COIN WATCH\n\n"

                    f"{best['name']} "
                    f"({best['symbol']})\n"

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

                    f"5m buys/sells: "
                    f"{best['buys_5m']}/"
                    f"{best['sells_5m']}\n"

                    f"5m move: "
                    f"{best['change_5m']:+.2f}%\n"

                    f"Strengths: "
                    f"{reasons or 'none'}\n"

                    f"Risks: "
                    f"{risks or 'none'}"
                ),
            )


    # -----------------------------------------------------
    # MULTIPLE PAPER ENTRIES
    # -----------------------------------------------------

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

    if not eligible:
        return

    for coin in eligible[
        :slots
    ]:

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
                "⚡🎯 PAPER SNIPE OPENED\n\n"

                f"{position['name']} "
                f"({position['symbol']})\n"

                f"Score: "
                f"{position['score']}/100\n"

                f"Paper stake: "
                f"${position['trade_size']:.2f}\n"

                f"Simulated entry: "
                f"${position['entry_price']:.10f}\n"

                f"MC: "
                f"${position['market_cap']:,.0f}\n"

                f"Liquidity: "
                f"${position['liquidity']:,.0f}\n\n"

                f"TP: "
                f"+{TAKE_PROFIT:.1f}%\n"

                f"SL: "
                f"-{STOP_LOSS:.1f}%\n"

                f"Max hold: "
                f"{MAX_HOLD_MINUTES} min"
            ),
        )


# =========================================================
# POSITION MONITOR
# =========================================================

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

            quote = (
                await asyncio.to_thread(
                    get_token_price,
                    address,
                )
            )

        except Exception as error:

            print(
                "Monitor error:",
                repr(error),
            )

            continue

        if not quote:
            continue


        # -------------------------------------------------
        # UPDATE HIGH
        # -------------------------------------------------

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


        # -------------------------------------------------
        # CURRENT NET P/L
        # -------------------------------------------------

        result = (
            estimate_exit(
                position,
                quote,
            )
        )

        pnl_pct = (
            result[
                "pnl_pct"
            ]
        )


        # -------------------------------------------------
        # TRAILING STOP
        # -------------------------------------------------

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

        high = (
            position[
                "highest_quote"
            ]
        )

        drawdown = (
            (
                quote
                - high
            )
            / high
            * 100
            if high > 0
            else 0
        )


        # -------------------------------------------------
        # TIME
        # -------------------------------------------------

        held_minutes = (
            time.time()
            - position[
                "opened_at"
            ]
        ) / 60


        # -------------------------------------------------
        # EXIT DECISION
        # -------------------------------------------------

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
            position[
                "trailing"
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

            reason = (
                "TIME EXIT"
            )

        if not reason:
            continue


        # -------------------------------------------------
        # CLOSE
        # -------------------------------------------------

        trade = (
            close_trade(
                address,
                quote,
                reason,
            )
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

                f"Exit: "
                f"{trade['reason']}\n"

                f"Net return: "
                f"{trade['pnl_pct']:+.2f}%\n"

                f"P/L: "
                f"${trade['pnl']:+.2f}\n"

                f"Paper cash: "
                f"${paper_cash:.2f}"
            ),
        )


# =========================================================
# JOB SCHEDULER
# =========================================================

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
        job_queue
        .get_jobs_by_name(
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
        job_queue
        .get_jobs_by_name(
            monitor_name
        )
    ):

        job_queue.run_repeating(
            monitor_job,
            interval=
                MONITOR_INTERVAL,
            first=
                MONITOR_INTERVAL,
            chat_id=
                chat_id,
            name=
                monitor_name,
        )


# =========================================================
# STARTUP
# =========================================================

async def post_init(
    application,
):

    if not CHAT_ID:

        print(
            "TELEGRAM_CHAT_ID not set."
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
                "🎯 PAPER SNIPER ONLINE\n\n"
                f"Scan every: "
                f"{SCAN_INTERVAL}s\n"
                f"Monitor every: "
                f"{MONITOR_INTERVAL}s\n"
                f"Max positions: "
                f"{MAX_OPEN_POSITIONS}\n"
                f"Stake: "
                f"${TRADE_SIZE:.2f}\n"
                f"Paper cash: "
                f"${paper_cash:.2f}"
            ),
        )

    except Exception as error:

        print(
            "Startup error:",
            repr(error),
        )


# =========================================================
# COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    schedule_jobs(
        context.job_queue,
        update.effective_chat.id,
    )

    await update.message.reply_text(
        "🎯 PAPER NEW-COIN SNIPER\n\n"

        "/status\n"
        "/snipe\n"
        "/positions\n"
        "/account\n"
        "/history\n"
        "/stats\n"
        "/chatid\n\n"

        "Automatic paper sniping is active."
    )


async def chatid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        f"Chat ID:\n"
        f"{update.effective_chat.id}"
    )


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

    best = (
        "none"
    )

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

        f"Paper cash: "
        f"${paper_cash:.2f}\n"

        f"Open: "
        f"{len(positions)}/"
        f"{MAX_OPEN_POSITIONS}\n"

        f"Closed: "
        f"{len(history)}\n\n"

        f"Entry score: "
        f"{ENTRY_SCORE}/100"
    )


async def snipe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🎯 Running paper sniper scan..."
    )

    try:

        rankings = (
            await asyncio.to_thread(
                get_rankings
            )
        )

    except Exception as error:

        print(
            "snipe error:",
            repr(error),
        )

        await update.message.reply_text(
            "Scanner temporarily unavailable."
        )

        return

    if not rankings:

        await update.message.reply_text(
            "No new coins currently pass the filters."
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
        rankings[:8],
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
                    f"Score: "
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

    for position in (
        positions.values()
    ):

        try:

            quote = (
                await asyncio.to_thread(
                    get_token_price,
                    position["address"],
                )
            )

        except Exception:

            quote = None

        pnl_text = (
            "price unavailable"
        )

        if quote:

            result = (
                estimate_exit(
                    position,
                    quote,
                )
            )

            pnl_text = (
                f"{result['pnl_pct']:+.2f}% "
                f"(${result['pnl']:+.2f})"
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
                    f"Score: "
                    f"{position['score']}/100"
                ),

                (
                    f"P/L: "
                    f"{pnl_text}"
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


async def account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    wins = sum(
        1
        for trade
        in history
        if trade["pnl"] > 0
    )

    losses = (
        len(history)
        - wins
    )

    realized = sum(
        trade["pnl"]
        for trade
        in history
    )

    win_rate = (
        wins
        / len(history)
        * 100
        if history
        else 0
    )

    await update.message.reply_text(
        "💵 PAPER SNIPER ACCOUNT\n\n"

        f"Starting: "
        f"${STARTING_BALANCE:.2f}\n"

        f"Cash: "
        f"${paper_cash:.2f}\n"

        f"Open: "
        f"{len(positions)}\n"

        f"Closed: "
        f"{len(history)}\n"

        f"Wins: "
        f"{wins}\n"

        f"Losses: "
        f"{losses}\n"

        f"Win rate: "
        f"{win_rate:.1f}%\n"

        f"Realized P/L: "
        f"${realized:+.2f}"
    )


async def history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not history:

        await update.message.reply_text(
            "No completed paper trades yet."
        )

        return

    lines = [
        "📚 RECENT PAPER SNIPES",
        "",
    ]

    for trade in (
        history[-15:]
    ):

        icon = (
            "🟢"
            if trade["pnl"] >= 0
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
        "\n".join(lines)
    )


async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not history:

        await update.message.reply_text(
            "No completed trades yet."
        )

        return

    returns = [
        trade["pnl_pct"]
        for trade
        in history
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

    await update.message.reply_text(
        "📊 SNIPER STATS\n\n"

        f"Trades: "
        f"{len(history)}\n"

        f"Average return: "
        f"{average:+.2f}%\n"

        f"Average winner: "
        f"{avg_win:+.2f}%\n"

        f"Average loser: "
        f"{avg_loss:+.2f}%\n"

        f"Best: "
        f"{max(returns):+.2f}%\n"

        f"Worst: "
        f"{min(returns):+.2f}%"
    )


# =========================================================
# MAIN
# =========================================================

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

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status,
        )
    )

    application.add_handler(
        CommandHandler(
            "snipe",
            snipe,
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
            "account",
            account,
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
            stats,
        )
    )

    application.add_handler(
        CommandHandler(
            "chatid",
            chatid,
        )
    )

    print(
        "PAPER NEW-COIN SNIPER ONLINE"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
