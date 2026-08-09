import os
import time
import asyncio
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from openai import OpenAI


# =========================================================
# ENVIRONMENT
# =========================================================

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "Set TELEGRAM_TOKEN or TELEGRAM_BOT_TOKEN in Railway."
    )

if not OPENAI_API_KEY:
    raise RuntimeError(
        "Set OPENAI_API_KEY in Railway."
    )

client = OpenAI(
    api_key=OPENAI_API_KEY
)


# =========================================================
# GENERAL SETTINGS
# =========================================================

REQUEST_TIMEOUT = 15

AUTO_SCAN_INTERVAL = 300
FIRST_MEME_SCAN = 60
FIRST_PERP_SCAN = 90

POSITION_MONITOR_INTERVAL = 30

SEND_SCAN_SUMMARIES = True


# =========================================================
# MEME PAPER ACCOUNT
# =========================================================

MEME_STARTING_BALANCE = 1000.00
MEME_TRADE_SIZE = 50.00

MEME_ENTRY_SCORE = 45

MEME_TAKE_PROFIT = 5.0
MEME_STOP_LOSS = 2.0

MEME_MAX_HOLD_MINUTES = 60
MEME_MAX_OPEN_POSITIONS = 1


# =========================================================
# SOLANA FILTERS
# =========================================================

MIN_MARKET_CAP = 100_000
MAX_MARKET_CAP = 50_000_000

MIN_LIQUIDITY = 25_000

MIN_VOLUME_24H = 100_000
MIN_VOLUME_1H = 2_500

MIN_SCAN_SCORE = 35
MAX_TOKEN_AGE_DAYS = 30


# =========================================================
# BTC / ETH / SOL / ICP PAPER ACCOUNT
# =========================================================

PERP_STARTING_BALANCE = 1000.00

PERP_COLLATERAL = 50.00
PERP_LEVERAGE = 1.0

PERP_ENTRY_SCORE = 60

PERP_TAKE_PROFIT = 2.0
PERP_STOP_LOSS = 1.0

PERP_MAX_HOLD_MINUTES = 120
PERP_MAX_OPEN_POSITIONS = 1

PERP_FEE_RATE = 0.0005
PERP_SLIPPAGE_RATE = 0.0005


PERP_ASSETS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "ICP": "internet-computer",
}


# =========================================================
# ICP ALERTS
# =========================================================

ICP_ALERT_1H_PERCENT = 1.5
ICP_ALERT_24H_PERCENT = 5.0

ICP_ALERT_COOLDOWN = 1800


# =========================================================
# PAPER STATE
# =========================================================

meme_balance = MEME_STARTING_BALANCE
meme_positions = {}
meme_history = []

perp_balance = PERP_STARTING_BALANCE
perp_positions = {}
perp_history = []

last_icp_alert_time = 0
last_icp_alert_direction = None


# =========================================================
# HTTP
# =========================================================

def get_json(
    url,
    params=None,
):

    response = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# =========================================================
# COINGECKO
# =========================================================

def get_coin(
    coin_id,
):

    data = get_json(
        "https://api.coingecko.com/api/v3/coins/markets",
        {
            "vs_currency": "usd",
            "ids": coin_id,
            "price_change_percentage": "1h,24h",
        },
    )

    if not data:
        return None

    return data[0]


def get_major_markets():

    data = get_json(
        "https://api.coingecko.com/api/v3/coins/markets",
        {
            "vs_currency": "usd",
            "ids": ",".join(
                PERP_ASSETS.values()
            ),
            "price_change_percentage": "1h,24h",
        },
    )

    id_to_symbol = {
        coin_id: symbol
        for symbol, coin_id
        in PERP_ASSETS.items()
    }

    markets = {}

    for item in data:

        symbol = id_to_symbol.get(
            item.get("id")
        )

        if not symbol:
            continue

        markets[symbol] = {

            "id": item.get("id"),

            "name": (
                item.get("name")
                or symbol
            ),

            "symbol": symbol,

            "price": float(
                item.get("current_price")
                or 0
            ),

            "market_cap": float(
                item.get("market_cap")
                or 0
            ),

            "volume_24h": float(
                item.get("total_volume")
                or 0
            ),

            "change_1h": float(
                item.get(
                    "price_change_percentage_1h_in_currency"
                )
                or 0
            ),

            "change_24h": float(
                item.get(
                    "price_change_percentage_24h_in_currency"
                )
                or item.get(
                    "price_change_percentage_24h"
                )
                or 0
            ),
        }

    return markets


def get_major_price(
    symbol,
):

    coin_id = PERP_ASSETS.get(
        symbol
    )

    if not coin_id:
        return None

    data = get_coin(
        coin_id
    )

    if not data:
        return None

    price = data.get(
        "current_price"
    )

    if price is None:
        return None

    return float(price)


# =========================================================
# DEXSCREENER SOLANA DISCOVERY
# =========================================================

def collect_solana_addresses():

    endpoints = [

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
    ]

    seen = set()
    addresses = []

    for url in endpoints:

        try:

            items = get_json(
                url
            )

        except Exception as error:

            print(
                "Dex discovery error:",
                repr(error),
            )

            continue

        if not isinstance(
            items,
            list,
        ):

            continue

        for item in items:

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

            if address in seen:
                continue

            seen.add(
                address
            )

            addresses.append(
                address
            )

    return addresses


def chunked(
    items,
    size,
):

    for i in range(
        0,
        len(items),
        size,
    ):

        yield items[
            i:i + size
        ]


def get_solana_candidates():

    addresses = (
        collect_solana_addresses()
    )

    if not addresses:
        return []

    all_pairs = []

    for batch in chunked(
        addresses,
        30,
    ):

        url = (
            "https://api.dexscreener.com/"
            "tokens/v1/solana/"
            + ",".join(batch)
        )

        try:

            pairs = get_json(
                url
            )

        except Exception as error:

            print(
                "Dex pair lookup error:",
                repr(error),
            )

            continue

        if isinstance(
            pairs,
            list,
        ):

            all_pairs.extend(
                pairs
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
                pair.get("liquidity")
                or {}
            ).get("usd")

            or 0
        )

        old_pair = (
            best_pairs.get(
                address
            )
        )

        if old_pair is None:

            best_pairs[
                address
            ] = pair

            continue

        old_liquidity = float(

            (
                old_pair.get("liquidity")
                or {}
            ).get("usd")

            or 0
        )

        if (
            liquidity
            > old_liquidity
        ):

            best_pairs[
                address
            ] = pair

    now_ms = int(
        time.time() * 1000
    )

    candidates = []

    for (
        address,
        pair,
    ) in best_pairs.items():

        base = (
            pair.get("baseToken")
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

        tx_1h = (
            txns.get("h1")
            or {}
        )

        changes = (
            pair.get("priceChange")
            or {}
        )

        created = pair.get(
            "pairCreatedAt"
        )

        age_hours = None

        if created:

            try:

                age_hours = max(
                    0,
                    now_ms
                    - int(created),
                ) / 3_600_000

            except (
                TypeError,
                ValueError,
            ):

                age_hours = None

        try:

            price = float(
                pair.get("priceUsd")
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            price = 0.0

        candidates.append({

            "address": address,

            "name": (
                base.get("name")
                or "Unknown"
            ),

            "symbol": (
                base.get("symbol")
                or "?"
            ),

            "price": price,

            "market_cap": float(
                pair.get("marketCap")
                or pair.get("fdv")
                or 0
            ),

            "liquidity": float(

                (
                    pair.get("liquidity")
                    or {}
                ).get("usd")

                or 0
            ),

            "volume_24h": float(
                volume.get("h24")
                or 0
            ),

            "volume_1h": float(
                volume.get("h1")
                or 0
            ),

            "buys_1h": int(
                tx_1h.get("buys")
                or 0
            ),

            "sells_1h": int(
                tx_1h.get("sells")
                or 0
            ),

            "change_1h": float(
                changes.get("h1")
                or 0
            ),

            "change_24h": float(
                changes.get("h24")
                or 0
            ),

            "age_hours": (
                age_hours
            ),
        })

    return candidates


def get_solana_token_price(
    address,
):

    pairs = get_json(

        (
            "https://api.dexscreener.com/"
            "token-pairs/v1/solana/"
            f"{address}"
        )
    )

    if not isinstance(
        pairs,
        list,
    ):

        return None

    if not pairs:
        return None

    pair = max(

        pairs,

        key=lambda p: float(

            (
                p.get("liquidity")
                or {}
            ).get("usd")

            or 0
        ),
    )

    try:

        return float(
            pair.get("priceUsd")
            or 0
        )

    except (
        TypeError,
        ValueError,
    ):

        return None


# =========================================================
# MEME SCORING
# =========================================================

def score_meme(
    candidate,
):

    score = 0
    strengths = []
    risks = []

    mc = candidate[
        "market_cap"
    ]

    liquidity = candidate[
        "liquidity"
    ]

    volume_24h = candidate[
        "volume_24h"
    ]

    volume_1h = candidate[
        "volume_1h"
    ]

    buys = candidate[
        "buys_1h"
    ]

    sells = candidate[
        "sells_1h"
    ]

    change_1h = candidate[
        "change_1h"
    ]

    change_24h = candidate[
        "change_24h"
    ]

    age = candidate[
        "age_hours"
    ]

    if age is not None:

        if age < 1:

            score -= 8

            risks.append(
                "extremely new"
            )

        elif age <= 72:

            score += 8

            strengths.append(
                "early-stage"
            )

        elif (
            age
            <= 14 * 24
        ):

            score += 4

    if (
        500_000
        <= mc
        <= 10_000_000
    ):

        score += 20

        strengths.append(
            "target market-cap zone"
        )

    elif (
        100_000
        <= mc
        <= 50_000_000
    ):

        score += 10

    if (
        liquidity
        >= 1_000_000
    ):

        score += 20

        strengths.append(
            "strong liquidity"
        )

    elif (
        liquidity
        >= 500_000
    ):

        score += 16

    elif (
        liquidity
        >= 250_000
    ):

        score += 12

    elif (
        liquidity
        >= 100_000
    ):

        score += 8

    elif (
        liquidity
        >= 25_000
    ):

        score += 4

    if (
        volume_24h
        >= 5_000_000
    ):

        score += 12

    elif (
        volume_24h
        >= 1_000_000
    ):

        score += 9

    elif (
        volume_24h
        >= 100_000
    ):

        score += 5

    expected_hourly = (

        volume_24h / 24

        if volume_24h

        else 0
    )

    if expected_hourly:

        acceleration = (
            volume_1h
            / expected_hourly
        )

        if (
            acceleration
            >= 4
        ):

            score += 18

            strengths.append(
                "strong volume acceleration"
            )

        elif (
            acceleration
            >= 2
        ):

            score += 13

            strengths.append(
                "volume accelerating"
            )

        elif (
            acceleration
            >= 1.2
        ):

            score += 7

        elif (
            acceleration
            < 0.35
        ):

            score -= 5

            risks.append(
                "volume fading"
            )

    if (
        buys + sells
        >= 10
    ):

        if sells == 0:

            score += 12

            strengths.append(
                "strong buy pressure"
            )

        else:

            ratio = (
                buys / sells
            )

            if ratio >= 2:

                score += 14

                strengths.append(
                    "strong buy pressure"
                )

            elif (
                ratio
                >= 1.4
            ):

                score += 10

            elif (
                ratio
                >= 1.1
            ):

                score += 5

            elif (
                ratio
                < 0.7
            ):

                score -= 8

                risks.append(
                    "selling pressure"
                )

    if (
        1
        <= change_1h
        <= 15
    ):

        score += 10

        strengths.append(
            "healthy momentum"
        )

    elif (
        15
        < change_1h
        <= 30
    ):

        score += 5

    elif (
        change_1h
        > 60
    ):

        score -= 20

        risks.append(
            "extreme 1h pump"
        )

    elif (
        change_1h
        > 35
    ):

        score -= 10

        risks.append(
            "price extended"
        )

    elif (
        change_1h
        < -20
    ):

        score -= 15

        risks.append(
            "sharp decline"
        )

    elif (
        change_1h
        < -10
    ):

        score -= 6

        risks.append(
            "weak momentum"
        )

    if (
        change_24h
        > 300
    ):

        score -= 15

        risks.append(
            "extreme 24h pump"
        )

    if mc and liquidity:

        liquidity_ratio = (
            liquidity / mc
        )

        if (
            liquidity_ratio
            >= 0.20
        ):

            score += 8

            strengths.append(
                "healthy liquidity ratio"
            )

        elif (
            liquidity_ratio
            >= 0.08
        ):

            score += 4

        elif (
            liquidity_ratio
            < 0.02
        ):

            score -= 12

            risks.append(
                "thin liquidity"
            )

    score = max(
        0,
        min(
            100,
            score,
        ),
    )

    if score >= 80:

        label = (
            "🔥 STRONG WATCH"
        )

    elif score >= 65:

        label = (
            "👀 WATCH"
        )

    elif score >= 35:

        label = (
            "⚠️ SPECULATIVE"
        )

    else:

        label = "WEAK"

    return (
        score,
        label,
        strengths,
        risks,
    )


def get_ranked_memes():

    results = []

    for candidate in (
        get_solana_candidates()
    ):

        if (
            candidate["price"]
            <= 0
        ):

            continue

        if not (
            MIN_MARKET_CAP
            <= candidate["market_cap"]
            <= MAX_MARKET_CAP
        ):

            continue

        if (
            candidate["liquidity"]
            < MIN_LIQUIDITY
        ):

            continue

        if (
            candidate["volume_24h"]
            < MIN_VOLUME_24H
        ):

            continue

        if (
            candidate["volume_1h"]
            < MIN_VOLUME_1H
        ):

            continue

        if (
            candidate["age_hours"]
            is not None
        ):

            if (
                candidate["age_hours"]
                > MAX_TOKEN_AGE_DAYS
                * 24
            ):

                continue

        (
            score,
            label,
            strengths,
            risks,
        ) = score_meme(
            candidate
        )

        if (
            score
            < MIN_SCAN_SCORE
        ):

            continue

        candidate[
            "score"
        ] = score

        candidate[
            "label"
        ] = label

        candidate[
            "strengths"
        ] = strengths

        candidate[
            "risks"
        ] = risks

        results.append(
            candidate
        )

    results.sort(

        key=lambda item: (

            item["score"],
            item["volume_1h"],
            item["liquidity"],
        ),

        reverse=True,
    )

    return results


# =========================================================
# MEME PAPER BUY / SELL
# =========================================================

def open_meme_trade(
    candidate,
):

    global meme_balance

    if (
        len(meme_positions)
        >= MEME_MAX_OPEN_POSITIONS
    ):

        return None

    if (
        meme_balance
        < MEME_TRADE_SIZE
    ):

        return None

    address = candidate[
        "address"
    ]

    if (
        address
        in meme_positions
    ):

        return None

    if (
        candidate["price"]
        <= 0
    ):

        return None

    position = {

        "address": address,

        "name": candidate[
            "name"
        ],

        "symbol": candidate[
            "symbol"
        ],

        "entry_price": candidate[
            "price"
        ],

        "quantity": (
            MEME_TRADE_SIZE
            / candidate["price"]
        ),

        "cost": MEME_TRADE_SIZE,

        "score": candidate[
            "score"
        ],

        "opened_at": (
            time.time()
        ),
    }

    meme_positions[
        address
    ] = position

    meme_balance -= (
        MEME_TRADE_SIZE
    )

    return position


def close_meme_trade(
    address,
    current_price,
    reason,
):

    global meme_balance

    position = (
        meme_positions.get(
            address
        )
    )

    if not position:
        return None

    proceeds = (
        position["quantity"]
        * current_price
    )

    pnl = (
        proceeds
        - position["cost"]
    )

    pnl_pct = (
        pnl
        / position["cost"]
        * 100
    )

    meme_balance += (
        proceeds
    )

    trade = {

        **position,

        "exit_price": (
            current_price
        ),

        "pnl": pnl,

        "pnl_pct": (
            pnl_pct
        ),

        "reason": reason,

        "closed_at": (
            time.time()
        ),
    }

    meme_history.append(
        trade
    )

    del meme_positions[
        address
    ]

    return trade


# =========================================================
# MAJOR COIN SIGNAL ENGINE
# =========================================================

def score_major_market(
    market,
):

    long_score = 0
    short_score = 0

    change_1h = market[
        "change_1h"
    ]

    change_24h = market[
        "change_24h"
    ]

    activity_ratio = (

        market["volume_24h"]
        / market["market_cap"]

        if market[
            "market_cap"
        ]

        else 0
    )

    if (
        activity_ratio
        >= 0.03
    ):

        long_score += 10
        short_score += 10

    # LONG

    if (
        0.4
        <= change_1h
        <= 4
    ):

        long_score += 35

    elif (
        change_1h
        > 4
    ):

        long_score += 15

    if (
        0.5
        <= change_24h
        <= 12
    ):

        long_score += 30

    elif (
        change_24h
        > 12
    ):

        long_score += 10

    if (
        change_1h > 0
        and change_24h > 0
    ):

        long_score += 20

    # SHORT

    if (
        -4
        <= change_1h
        <= -0.4
    ):

        short_score += 35

    elif (
        change_1h
        < -4
    ):

        short_score += 15

    if (
        -12
        <= change_24h
        <= -0.5
    ):

        short_score += 30

    elif (
        change_24h
        < -12
    ):

        short_score += 10

    if (
        change_1h < 0
        and change_24h < 0
    ):

        short_score += 20

    if (
        abs(change_1h)
        > 8
    ):

        long_score -= 20
        short_score -= 20

    if (
        abs(change_24h)
        > 20
    ):

        long_score -= 15
        short_score -= 15

    long_score = max(
        0,
        min(
            100,
            long_score,
        ),
    )

    short_score = max(
        0,
        min(
            100,
            short_score,
        ),
    )

    if (
        long_score
        > short_score
    ):

        return (
            "LONG",
            long_score,
        )

    if (
        short_score
        > long_score
    ):

        return (
            "SHORT",
            short_score,
        )

    return (
        "NONE",
        0,
    )


def get_major_signals():

    signals = []

    markets = (
        get_major_markets()
    )

    for (
        symbol,
        market,
    ) in markets.items():

        (
            side,
            score,
        ) = score_major_market(
            market
        )

        signals.append({

            **market,

            "side": side,

            "score": score,
        })

    signals.sort(

        key=lambda item: (
            item["score"]
        ),

        reverse=True,
    )

    return signals


# =========================================================
# PAPER LONG / SHORT
# =========================================================

def open_perp_trade(
    signal,
):

    global perp_balance

    if (
        len(perp_positions)
        >= PERP_MAX_OPEN_POSITIONS
    ):

        return None

    symbol = signal[
        "symbol"
    ]

    if (
        symbol
        in perp_positions
    ):

        return None

    if (
        signal["side"]
        not in (
            "LONG",
            "SHORT",
        )
    ):

        return None

    market_price = signal[
        "price"
    ]

    if market_price <= 0:
        return None

    notional = (
        PERP_COLLATERAL
        * PERP_LEVERAGE
    )

    entry_fee = (
        notional
        * PERP_FEE_RATE
    )

    total_required = (
        PERP_COLLATERAL
        + entry_fee
    )

    if (
        perp_balance
        < total_required
    ):

        return None

    if (
        signal["side"]
        == "LONG"
    ):

        entry_price = (

            market_price

            * (
                1
                + PERP_SLIPPAGE_RATE
            )
        )

    else:

        entry_price = (

            market_price

            * (
                1
                - PERP_SLIPPAGE_RATE
            )
        )

    perp_balance -= (
        total_required
    )

    position = {

        "symbol": symbol,

        "name": signal[
            "name"
        ],

        "side": signal[
            "side"
        ],

        "score": signal[
            "score"
        ],

        "entry_price": (
            entry_price
        ),

        "collateral": (
            PERP_COLLATERAL
        ),

        "notional": notional,

        "entry_fee": (
            entry_fee
        ),

        "opened_at": (
            time.time()
        ),
    }

    perp_positions[
        symbol
    ] = position

    return position


def close_perp_trade(
    symbol,
    market_price,
    reason,
):

    global perp_balance

    position = (
        perp_positions.get(
            symbol
        )
    )

    if not position:
        return None

    if (
        position["side"]
        == "LONG"
    ):

        exit_price = (

            market_price

            * (
                1
                - PERP_SLIPPAGE_RATE
            )
        )

        return_pct = (

            exit_price
            - position[
                "entry_price"
            ]

        ) / position[
            "entry_price"
        ]

    else:

        exit_price = (

            market_price

            * (
                1
                + PERP_SLIPPAGE_RATE
            )
        )

        return_pct = (

            position[
                "entry_price"
            ]
            - exit_price

        ) / position[
            "entry_price"
        ]

    gross_pnl = (
        return_pct
        * position["notional"]
    )

    exit_fee = (
        position["notional"]
        * PERP_FEE_RATE
    )

    net_pnl = (

        gross_pnl

        - position[
            "entry_fee"
        ]

        - exit_fee
    )

    perp_balance += (

        position[
            "collateral"
        ]

        + net_pnl
    )

    trade = {

        **position,

        "exit_price": (
            exit_price
        ),

        "net_pnl": (
            net_pnl
        ),

        "pnl_pct": (

            net_pnl

            / position[
                "collateral"
            ]

            * 100
        ),

        "reason": reason,

        "closed_at": (
            time.time()
        ),
    }

    perp_history.append(
        trade
    )

    del perp_positions[
        symbol
    ]

    return trade


# =========================================================
# AUTOMATIC SOLANA SCAN
# =========================================================

async def auto_meme_scan(
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        context.job.chat_id
    )

    try:

        results = await asyncio.to_thread(
            get_ranked_memes
        )

        if SEND_SCAN_SUMMARIES:

            if results:

                best = results[0]

                await context.bot.send_message(

                    chat_id=chat_id,

                    text=(
                        "🔎 AUTO SOLANA SCAN\n\n"

                        f"Candidates: "
                        f"{len(results)}\n"

                        f"Best: "
                        f"{best['name']} "
                        f"({best['symbol']})\n"

                        f"Score: "
                        f"{best['score']}/100\n"

                        f"Signal: "
                        f"{best['label']}\n"

                        f"Market cap: "
                        f"${best['market_cap']:,.0f}\n"

                        f"Liquidity: "
                        f"${best['liquidity']:,.0f}\n"

                        f"1h volume: "
                        f"${best['volume_1h']:,.0f}"
                    ),
                )

            else:

                await context.bot.send_message(

                    chat_id=chat_id,

                    text=(
                        "🔎 AUTO SOLANA SCAN\n\n"
                        "No candidates currently "
                        "pass the filters."
                    ),
                )

        if meme_positions:
            return

        eligible = [

            candidate

            for candidate
            in results

            if (
                candidate[
                    "score"
                ]
                >= MEME_ENTRY_SCORE
            )
        ]

        if not eligible:
            return

        position = (
            open_meme_trade(
                eligible[0]
            )
        )

        if not position:
            return

        await context.bot.send_message(

            chat_id=chat_id,

            text=(
                "📥 MEME PAPER BUY\n\n"

                f"{position['name']} "
                f"({position['symbol']})\n"

                f"Score: "
                f"{position['score']}/100\n"

                f"Size: "
                f"${position['cost']:.2f}\n"

                f"Entry: "
                f"${position['entry_price']:.10f}\n"

                f"TP: "
                f"+{MEME_TAKE_PROFIT:.1f}%\n"

                f"SL: "
                f"-{MEME_STOP_LOSS:.1f}%"
            ),
        )

    except Exception as error:

        print(
            "auto_meme_scan error:",
            repr(error),
        )


# =========================================================
# AUTOMATIC MAJOR COIN SCAN
# =========================================================

async def auto_perp_scan(
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        context.job.chat_id
    )

    try:

        signals = await asyncio.to_thread(
            get_major_signals
        )

        if SEND_SCAN_SUMMARIES:

            lines = [
                "📈 AUTO LONG/SHORT SCAN",
                "",
            ]

            for signal in signals:

                lines.append(
                    (
                        f"{signal['symbol']}: "
                        f"{signal['side']} "
                        f"{signal['score']}/100 | "
                        f"1h "
                        f"{signal['change_1h']:+.2f}% | "
                        f"24h "
                        f"{signal['change_24h']:+.2f}%"
                    )
                )

            await context.bot.send_message(

                chat_id=chat_id,

                text="\n".join(
                    lines
                ),
            )

        if perp_positions:
            return

        eligible = [

            signal

            for signal
            in signals

            if (
                signal["side"]
                in (
                    "LONG",
                    "SHORT",
                )

                and signal[
                    "score"
                ]
                >= PERP_ENTRY_SCORE
            )
        ]

        if not eligible:
            return

        position = (
            open_perp_trade(
                eligible[0]
            )
        )

        if not position:
            return

        await context.bot.send_message(

            chat_id=chat_id,

            text=(
                "📥 PAPER LONG/SHORT OPEN\n\n"

                f"{position['symbol']} "
                f"{position['side']}\n"

                f"Score: "
                f"{position['score']}/100\n"

                f"Entry: "
                f"${position['entry_price']:,.4f}\n"

                f"Collateral: "
                f"${position['collateral']:.2f} USDC\n"

                f"Leverage: "
                f"{PERP_LEVERAGE:.1f}x\n"

                f"TP: "
                f"+{PERP_TAKE_PROFIT:.1f}%\n"

                f"SL: "
                f"-{PERP_STOP_LOSS:.1f}%"
            ),
        )

    except Exception as error:

        print(
            "auto_perp_scan error:",
            repr(error),
        )


# =========================================================
# ICP ALERT JOB
# =========================================================

async def icp_alert_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    global last_icp_alert_time
    global last_icp_alert_direction

    chat_id = (
        context.job.chat_id
    )

    try:

        markets = await asyncio.to_thread(
            get_major_markets
        )

        icp_market = (
            markets.get("ICP")
        )

        if not icp_market:
            return

        direction = None

        if (
            icp_market[
                "change_1h"
            ]
            >= ICP_ALERT_1H_PERCENT

            or icp_market[
                "change_24h"
            ]
            >= ICP_ALERT_24H_PERCENT
        ):

            direction = "UP"

        elif (
            icp_market[
                "change_1h"
            ]
            <= -ICP_ALERT_1H_PERCENT

            or icp_market[
                "change_24h"
            ]
            <= -ICP_ALERT_24H_PERCENT
        ):

            direction = "DOWN"

        if direction is None:
            return

        now = time.time()

        if (
            direction
            == last_icp_alert_direction

            and (
                now
                - last_icp_alert_time
            )
            < ICP_ALERT_COOLDOWN
        ):

            return

        last_icp_alert_time = now
        last_icp_alert_direction = (
            direction
        )

        emoji = (
            "🚀"
            if direction == "UP"
            else "🔻"
        )

        await context.bot.send_message(

            chat_id=chat_id,

            text=(
                f"{emoji} ICP ALERT — "
                f"{direction}\n\n"

                f"Price: "
                f"${icp_market['price']:.4f}\n"

                f"1h: "
                f"{icp_market['change_1h']:+.2f}%\n"

                f"24h: "
                f"{icp_market['change_24h']:+.2f}%"
            ),
        )

    except Exception as error:

        print(
            "icp_alert_job error:",
            repr(error),
        )


# =========================================================
# POSITION MONITOR
# =========================================================

async def monitor_positions(
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        context.job.chat_id
    )

    # MEMES

    for address in list(
        meme_positions.keys()
    ):

        position = (
            meme_positions.get(
                address
            )
        )

        if not position:
            continue

        try:

            current_price = await asyncio.to_thread(

                get_solana_token_price,

                address,
            )

        except Exception as error:

            print(
                "meme monitor error:",
                repr(error),
            )

            continue

        if not current_price:
            continue

        pnl_pct = (

            (
                current_price
                - position[
                    "entry_price"
                ]
            )

            / position[
                "entry_price"
            ]

            * 100
        )

        minutes_open = (

            time.time()

            - position[
                "opened_at"
            ]

        ) / 60

        reason = None

        if (
            pnl_pct
            >= MEME_TAKE_PROFIT
        ):

            reason = (
                "TAKE PROFIT"
            )

        elif (
            pnl_pct
            <= -MEME_STOP_LOSS
        ):

            reason = (
                "STOP LOSS"
            )

        elif (
            minutes_open
            >= MEME_MAX_HOLD_MINUTES
        ):

            reason = (
                "MAX HOLD"
            )

        if reason:

            trade = (
                close_meme_trade(
                    address,
                    current_price,
                    reason,
                )
            )

            if trade:

                await context.bot.send_message(

                    chat_id=chat_id,

                    text=(
                        "📤 MEME PAPER SELL\n\n"

                        f"{trade['name']} "
                        f"({trade['symbol']})\n"

                        f"Reason: "
                        f"{trade['reason']}\n"

                        f"P/L: "
                        f"${trade['pnl']:+.2f}\n"

                        f"P/L %: "
                        f"{trade['pnl_pct']:+.2f}%\n"

                        f"Cash: "
                        f"${meme_balance:.2f}"
                    ),
                )

    # MAJOR COINS

    for symbol in list(
        perp_positions.keys()
    ):

        position = (
            perp_positions.get(
                symbol
            )
        )

        if not position:
            continue

        try:

            current_price = await asyncio.to_thread(

                get_major_price,

                symbol,
            )

        except Exception as error:

            print(
                "perp monitor error:",
                repr(error),
            )

            continue

        if not current_price:
            continue

        if (
            position["side"]
            == "LONG"
        ):

            raw_pct = (

                (
                    current_price

                    - position[
                        "entry_price"
                    ]
                )

                / position[
                    "entry_price"
                ]

                * 100
            )

        else:

            raw_pct = (

                (
                    position[
                        "entry_price"
                    ]

                    - current_price
                )

                / position[
                    "entry_price"
                ]

                * 100
            )

        pnl_pct = (
            raw_pct
            * PERP_LEVERAGE
        )

        minutes_open = (

            time.time()

            - position[
                "opened_at"
            ]

        ) / 60

        reason = None

        if (
            pnl_pct
            >= PERP_TAKE_PROFIT
        ):

            reason = (
                "TAKE PROFIT"
            )

        elif (
            pnl_pct
            <= -PERP_STOP_LOSS
        ):

            reason = (
                "STOP LOSS"
            )

        elif (
            minutes_open
            >= PERP_MAX_HOLD_MINUTES
        ):

            reason = (
                "MAX HOLD"
            )

        if reason:

            trade = (
                close_perp_trade(
                    symbol,
                    current_price,
                    reason,
                )
            )

            if trade:

                await context.bot.send_message(

                    chat_id=chat_id,

                    text=(
                        "📤 PAPER LONG/SHORT CLOSE\n\n"

                        f"{trade['symbol']} "
                        f"{trade['side']}\n"

                        f"Reason: "
                        f"{trade['reason']}\n"

                        f"Net P/L: "
                        f"${trade['net_pnl']:+.2f}\n"

                        f"P/L: "
                        f"{trade['pnl_pct']:+.2f}%\n"

                        f"Balance: "
                        f"${perp_balance:.2f} USDC"
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

    jobs = [

        (
            auto_meme_scan,
            f"auto_meme_{chat_id}",
            AUTO_SCAN_INTERVAL,
            FIRST_MEME_SCAN,
        ),

        (
            auto_perp_scan,
            f"auto_perp_{chat_id}",
            AUTO_SCAN_INTERVAL,
            FIRST_PERP_SCAN,
        ),

        (
            monitor_positions,
            f"monitor_{chat_id}",
            POSITION_MONITOR_INTERVAL,
            POSITION_MONITOR_INTERVAL,
        ),

        (
            icp_alert_job,
            f"icp_alert_{chat_id}",
            AUTO_SCAN_INTERVAL,
            75,
        ),
    ]

    for (
        callback,
        name,
        interval,
        first,
    ) in jobs:

        if (
            job_queue
            .get_jobs_by_name(
                name
            )
        ):

            continue

        job_queue.run_repeating(

            callback,

            interval=interval,

            first=first,

            chat_id=chat_id,

            name=name,
        )


async def post_init(
    application,
):

    if not TELEGRAM_CHAT_ID:

        print(
            "TELEGRAM_CHAT_ID not set. "
            "Send /start to activate jobs."
        )

        return

    try:

        chat_id = int(
            TELEGRAM_CHAT_ID
        )

    except ValueError:

        print(
            "TELEGRAM_CHAT_ID must be numeric."
        )

        return

    schedule_jobs(

        application.job_queue,

        chat_id,
    )

    try:

        await application.bot.send_message(

            chat_id=chat_id,

            text=(
                "♻️ Caltanide Jarvis restarted.\n"
                "✅ Background jobs are active."
            ),
        )

    except Exception as error:

        print(
            "startup message error:",
            repr(error),
        )


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    schedule_jobs(

        context.job_queue,

        chat_id,
    )

    await update.message.reply_text(

        "🤖 CALTANIDE JARVIS PAPER TRADER\n\n"

        "✅ Paper trading only.\n"
        "✅ Background scanners active.\n\n"

        "SYSTEM\n"
        "/status\n"
        "/chatid\n\n"

        "SOLANA MEMES\n"
        "/hunt\n"
        "/paper\n"
        "/positions\n"
        "/history\n\n"

        "BTC / ETH / SOL / ICP\n"
        "/perpscan\n"
        "/perpaccount\n"
        "/perppositions\n"
        "/perphistory\n\n"

        "OTHER\n"
        "/icp\n"
        "/price bitcoin\n"
        "/analyse bitcoin"
    )


# =========================================================
# /CHATID
# =========================================================

async def chatid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(

        "🆔 Telegram chat ID:\n"

        f"{update.effective_chat.id}\n\n"

        "Put this number in Railway as:\n"
        "TELEGRAM_CHAT_ID"
    )


# =========================================================
# /STATUS
# =========================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    jobs = list(
        context.job_queue.jobs()
    )

    job_names = [
        job.name
        for job in jobs
    ]

    text = (

        "🟢 JARVIS STATUS\n\n"

        f"Background jobs: "
        f"{len(jobs)}\n"

        f"Meme cash: "
        f"${meme_balance:.2f}\n"

        f"Meme open positions: "
        f"{len(meme_positions)}\n"

        f"Meme closed trades: "
        f"{len(meme_history)}\n\n"

        f"Perp cash: "
        f"${perp_balance:.2f} USDC\n"

        f"Perp open positions: "
        f"{len(perp_positions)}\n"

        f"Perp closed trades: "
        f"{len(perp_history)}\n\n"

        "Jobs:\n"
    )

    if job_names:

        text += "\n".join(

            f"✅ {name}"

            for name
            in job_names
        )

    else:

        text += "❌ none"

    await update.message.reply_text(
        text
    )


# =========================================================
# /HUNT
# =========================================================

async def hunt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🔎 Jarvis is scanning Solana..."
    )

    try:

        results = await asyncio.to_thread(
            get_ranked_memes
        )

        if not results:

            await update.message.reply_text(

                "🔎 JARVIS SOLANA HUNT\n\n"
                "No tokens currently pass the filters."
            )

            return

        lines = [
            "🚨 JARVIS SOLANA HUNT",
            "",
        ]

        for (
            index,
            candidate,
        ) in enumerate(
            results[:5],
            1,
        ):

            lines.extend([

                (
                    f"{index}. "
                    f"{candidate['name']} "
                    f"({candidate['symbol']})"
                ),

                (
                    f"Score: "
                    f"{candidate['score']}/100"
                ),

                (
                    f"Signal: "
                    f"{candidate['label']}"
                ),

                (
                    f"Price: "
                    f"${candidate['price']:.10f}"
                ),

                (
                    f"Market cap: "
                    f"${candidate['market_cap']:,.0f}"
                ),

                (
                    f"Liquidity: "
                    f"${candidate['liquidity']:,.0f}"
                ),

                (
                    f"1h volume: "
                    f"${candidate['volume_1h']:,.0f}"
                ),

                (
                    f"Token: "
                    f"{candidate['address']}"
                ),

                "",
            ])

        await update.message.reply_text(

            "\n".join(
                lines
            )[:4000]
        )

    except Exception as error:

        print(
            "hunt error:",
            repr(error),
        )

        await update.message.reply_text(
            "⚠️ Solana scan temporarily unavailable."
        )


# =========================================================
# /PAPER
# =========================================================

async def paper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    realized = sum(

        trade["pnl"]

        for trade
        in meme_history
    )

    await update.message.reply_text(

        "💵 MEME PAPER ACCOUNT\n\n"

        f"Cash: "
        f"${meme_balance:.2f}\n"

        f"Open positions: "
        f"{len(meme_positions)}\n"

        f"Closed trades: "
        f"{len(meme_history)}\n"

        f"Realized P/L: "
        f"${realized:+.2f}"
    )


# =========================================================
# /POSITIONS
# =========================================================

async def positions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not meme_positions:

        await update.message.reply_text(
            "No meme paper positions open."
        )

        return

    lines = [
        "📊 MEME PAPER POSITIONS",
        "",
    ]

    for position in (
        meme_positions.values()
    ):

        try:

            current = await asyncio.to_thread(

                get_solana_token_price,

                position[
                    "address"
                ],
            )

        except Exception:

            current = None

        pnl = (

            (
                current

                - position[
                    "entry_price"
                ]
            )

            / position[
                "entry_price"
            ]

            * 100

            if current

            else 0
        )

        lines.extend([

            (
                f"{position['name']} "
                f"({position['symbol']})"
            ),

            (
                f"Entry: "
                f"${position['entry_price']:.10f}"
            ),

            (
                f"Current: "
                f"${current:.10f}"

                if current

                else "Current: unavailable"
            ),

            f"P/L: {pnl:+.2f}%",

            "",
        ])

    await update.message.reply_text(

        "\n".join(
            lines
        )[:4000]
    )


# =========================================================
# /HISTORY
# =========================================================

async def history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not meme_history:

        await update.message.reply_text(
            "No completed meme paper trades yet."
        )

        return

    lines = [
        "📚 MEME PAPER HISTORY",
        "",
    ]

    for trade in (
        meme_history[-10:]
    ):

        lines.extend([

            (
                f"{trade['name']} "
                f"({trade['symbol']})"
            ),

            (
                f"P/L: "
                f"{trade['pnl_pct']:+.2f}%"
            ),

            (
                f"Profit/Loss: "
                f"${trade['pnl']:+.2f}"
            ),

            (
                f"Exit: "
                f"{trade['reason']}"
            ),

            "",
        ])

    await update.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# /PERPSCAN
# =========================================================

async def perpscan(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        signals = await asyncio.to_thread(
            get_major_signals
        )

        lines = [

            "📈 PAPER LONG/SHORT SCANNER",

            "",

            "Assets: BTC / ETH / SOL / ICP",

            (
                f"Entry threshold: "
                f"{PERP_ENTRY_SCORE}/100"
            ),

            (
                f"Leverage: "
                f"{PERP_LEVERAGE:.1f}x"
            ),

            "",
        ]

        for signal in signals:

            lines.extend([

                (
                    f"{signal['symbol']} — "
                    f"{signal['side']}"
                ),

                (
                    f"Score: "
                    f"{signal['score']}/100"
                ),

                (
                    f"Price: "
                    f"${signal['price']:,.4f}"
                ),

                (
                    f"1h: "
                    f"{signal['change_1h']:+.2f}%"
                ),

                (
                    f"24h: "
                    f"{signal['change_24h']:+.2f}%"
                ),

                "",
            ])

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as error:

        print(
            "perpscan error:",
            repr(error),
        )

        await update.message.reply_text(
            "⚠️ Long/short scanner temporarily unavailable."
        )


# =========================================================
# /PERPACCOUNT
# =========================================================

async def perpaccount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    realized = sum(

        trade["net_pnl"]

        for trade
        in perp_history
    )

    await update.message.reply_text(

        "💵 PAPER LONG/SHORT ACCOUNT\n\n"

        f"USDC cash: "
        f"${perp_balance:.2f}\n"

        f"Open positions: "
        f"{len(perp_positions)}\n"

        f"Closed trades: "
        f"{len(perp_history)}\n"

        f"Realized P/L: "
        f"${realized:+.2f}\n"

        f"Leverage: "
        f"{PERP_LEVERAGE:.1f}x"
    )


# =========================================================
# /PERPPOSITIONS
# =========================================================

async def perppositions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not perp_positions:

        await update.message.reply_text(
            "No paper long/short positions open."
        )

        return

    lines = [
        "📊 OPEN PAPER LONG/SHORT",
        "",
    ]

    for position in (
        perp_positions.values()
    ):

        try:

            current = await asyncio.to_thread(

                get_major_price,

                position[
                    "symbol"
                ],
            )

        except Exception:

            current = None

        if current:

            if (
                position["side"]
                == "LONG"
            ):

                raw = (

                    current

                    - position[
                        "entry_price"
                    ]

                ) / position[
                    "entry_price"
                ]

            else:

                raw = (

                    position[
                        "entry_price"
                    ]

                    - current

                ) / position[
                    "entry_price"
                ]

            pnl = (
                raw
                * 100
                * PERP_LEVERAGE
            )

        else:

            pnl = 0

        lines.extend([

            (
                f"{position['symbol']} "
                f"{position['side']}"
            ),

            (
                f"Entry: "
                f"${position['entry_price']:,.4f}"
            ),

            (
                f"Current: "
                f"${current:,.4f}"

                if current

                else "Current: unavailable"
            ),

            f"P/L: {pnl:+.2f}%",

            (
                f"Collateral: "
                f"${position['collateral']:.2f}"
            ),

            "",
        ])

    await update.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# /PERPHISTORY
# =========================================================

async def perphistory(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not perp_history:

        await update.message.reply_text(
            "No completed paper long/short trades yet."
        )

        return

    lines = [
        "📚 PAPER LONG/SHORT HISTORY",
        "",
    ]

    for trade in (
        perp_history[-10:]
    ):

        lines.extend([

            (
                f"{trade['symbol']} "
                f"{trade['side']}"
            ),

            (
                f"P/L: "
                f"{trade['pnl_pct']:+.2f}%"
            ),

            (
                f"Profit/Loss: "
                f"${trade['net_pnl']:+.2f}"
            ),

            (
                f"Exit: "
                f"{trade['reason']}"
            ),

            "",
        ])

    await update.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# /ICP
# =========================================================

async def icp(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        markets = await asyncio.to_thread(
            get_major_markets
        )

        market = (
            markets.get("ICP")
        )

        if not market:

            await update.message.reply_text(
                "ICP data unavailable."
            )

            return

        (
            side,
            score,
        ) = score_major_market(
            market
        )

        await update.message.reply_text(

            "🟣 ICP STATUS\n\n"

            f"Price: "
            f"${market['price']:.4f}\n"

            f"1h: "
            f"{market['change_1h']:+.2f}%\n"

            f"24h: "
            f"{market['change_24h']:+.2f}%\n"

            f"Direction: "
            f"{side}\n"

            f"Score: "
            f"{score}/100"
        )

    except Exception as error:

        print(
            "icp error:",
            repr(error),
        )

        await update.message.reply_text(
            "ICP data unavailable."
        )


# =========================================================
# /PRICE
# =========================================================

async def price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.args:

        await update.message.reply_text(
            "Try: /price bitcoin"
        )

        return

    aliases = {

        "btc": "bitcoin",

        "bitcoin": "bitcoin",

        "eth": "ethereum",

        "ethereum": "ethereum",

        "sol": "solana",

        "solana": "solana",

        "icp": (
            "internet-computer"
        ),

        "internet-computer": (
            "internet-computer"
        ),
    }

    requested = (
        context.args[0]
        .lower()
    )

    coin_id = aliases.get(
        requested,
        requested,
    )

    try:

        data = await asyncio.to_thread(

            get_coin,

            coin_id,
        )

        if not data:

            await update.message.reply_text(
                "Coin not found."
            )

            return

        change_1h = (

            data.get(
                "price_change_percentage_1h_in_currency"
            )

            or 0
        )

        change_24h = (

            data.get(
                "price_change_percentage_24h_in_currency"
            )

            or data.get(
                "price_change_percentage_24h"
            )

            or 0
        )

        await update.message.reply_text(

            f"🪙 {data['name']} "
            f"({data['symbol'].upper()})\n\n"

            f"Price: "
            f"${data['current_price']:,}\n"

            f"1h: "
            f"{change_1h:+.2f}%\n"

            f"24h: "
            f"{change_24h:+.2f}%\n"

            f"Market cap: "
            f"${data['market_cap']:,}\n"

            f"24h volume: "
            f"${data['total_volume']:,}"
        )

    except Exception as error:

        print(
            "price error:",
            repr(error),
        )

        await update.message.reply_text(
            "Price data temporarily unavailable."
        )


# =========================================================
# /ANALYSE
# =========================================================

async def analyse(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.args:

        await update.message.reply_text(
            "Try: /analyse bitcoin"
        )

        return

    aliases = {

        "btc": "bitcoin",

        "bitcoin": "bitcoin",

        "eth": "ethereum",

        "ethereum": "ethereum",

        "sol": "solana",

        "solana": "solana",

        "icp": (
            "internet-computer"
        ),

        "internet-computer": (
            "internet-computer"
        ),
    }

    requested = (
        context.args[0]
        .lower()
    )

    coin_id = aliases.get(
        requested,
        requested,
    )

    try:

        data = await asyncio.to_thread(

            get_coin,

            coin_id,
        )

        if not data:

            await update.message.reply_text(
                "Coin not found."
            )

            return

        prompt = f"""
You are Caltanide Crypto Jarvis, a cautious cryptocurrency market-analysis assistant.

Coin: {data['name']}
Symbol: {data['symbol']}
Price: {data['current_price']}
1h change: {data.get('price_change_percentage_1h_in_currency')}
24h change: {data.get('price_change_percentage_24h_in_currency')}
Market cap: {data['market_cap']}
24h volume: {data['total_volume']}

Give a concise analysis covering:
1. Momentum
2. Volume
3. Bullish case
4. Bearish case
5. Main risks
6. What would confirm the setup
7. What would invalidate the setup

Do not guarantee profits.
Use only the supplied data.
"""

        response = await asyncio.to_thread(

            client.responses.create,

            model="gpt-5.6",

            input=prompt,
        )

        await update.message.reply_text(

            "🧠 CALTANIDE JARVIS ANALYSIS\n\n"

            + response.output_text
        )

    except Exception as error:

        print(
            "analyse error:",
            repr(error),
        )

        await update.message.reply_text(
            "AI analysis temporarily unavailable."
        )


# =========================================================
# RUN
# =========================================================

def main():

    app = (

        Application.builder()

        .token(
            TELEGRAM_TOKEN
        )

        .post_init(
            post_init
        )

        .build()
    )

    # SYSTEM
    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        CommandHandler(
            "status",
            status,
        )
    )

    app.add_handler(
        CommandHandler(
            "chatid",
            chatid,
        )
    )

    # SOLANA MEMES
    app.add_handler(
        CommandHandler(
            "hunt",
            hunt,
        )
    )

    app.add_handler(
        CommandHandler(
            "paper",
            paper,
        )
    )

    app.add_handler(
        CommandHandler(
            "positions",
            positions,
        )
    )

    app.add_handler(
        CommandHandler(
            "history",
            history,
        )
    )

    # BTC / ETH / SOL / ICP
    app.add_handler(
        CommandHandler(
            "perpscan",
            perpscan,
        )
    )

    app.add_handler(
        CommandHandler(
            "perpaccount",
            perpaccount,
        )
    )

    app.add_handler(
        CommandHandler(
            "perppositions",
            perppositions,
        )
    )

    app.add_handler(
        CommandHandler(
            "perphistory",
            perphistory,
        )
    )

    # OTHER
    app.add_handler(
        CommandHandler(
            "icp",
            icp,
        )
    )

    app.add_handler(
        CommandHandler(
            "price",
            price,
        )
    )

    app.add_handler(
        CommandHandler(
            "analyse",
            analyse,
        )
    )

    print(
        "Caltanide Jarvis PAPER trader online"
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
