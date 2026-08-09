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

# Optional:
# If you later create a free CoinGecko Demo API key,
# add it in Railway as COINGECKO_API_KEY.
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

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
# SOLANA MEME PAPER SETTINGS
# =========================================================

MEME_STARTING_BALANCE = 1000.00
MEME_TRADE_SIZE = 50.00

MEME_ENTRY_SCORE = 45

MEME_TAKE_PROFIT = 5.0
MEME_STOP_LOSS = 2.0

MEME_MAX_HOLD_MINUTES = 60
MEME_MAX_OPEN_POSITIONS = 1

MIN_MARKET_CAP = 100_000
MAX_MARKET_CAP = 50_000_000

MIN_LIQUIDITY = 25_000

MIN_VOLUME_24H = 100_000
MIN_VOLUME_1H = 2_500

MIN_SCAN_SCORE = 35
MAX_TOKEN_AGE_DAYS = 30


# =========================================================
# BTC / ETH / SOL / ICP PAPER SETTINGS
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
# ICP ALERT SETTINGS
# =========================================================

ICP_ALERT_1H_PERCENT = 1.5
ICP_ALERT_24H_PERCENT = 5.0
ICP_ALERT_COOLDOWN = 1800


# =========================================================
# COINGECKO RATE-LIMIT SETTINGS
# =========================================================

# Keep one shared market snapshot for 2 minutes.
COINGECKO_CACHE_TTL = 120

# Never intentionally request fresh CoinGecko data
# more than once every 30 seconds.
COINGECKO_MIN_FETCH_GAP = 30

# Backoff starts at 60 seconds on HTTP 429.
COINGECKO_BACKOFF_BASE = 60
COINGECKO_BACKOFF_MAX = 900


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
# SHARED MARKET CACHE
# =========================================================

market_cache = {}
market_cache_updated_at = 0.0

last_coingecko_fetch_attempt = 0.0
coingecko_backoff_until = 0.0
coingecko_backoff_seconds = 0


# =========================================================
# HTTP HELPERS
# =========================================================

def http_get_json(
    url,
    params=None,
    headers=None,
):

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# =========================================================
# COINGECKO HELPERS
# =========================================================

def coingecko_headers():

    if COINGECKO_API_KEY:

        return {
            "x-cg-demo-api-key":
            COINGECKO_API_KEY
        }

    return {}


def _fetch_major_markets_from_coingecko():

    global last_coingecko_fetch_attempt
    global coingecko_backoff_until
    global coingecko_backoff_seconds

    now = time.time()

    if now < coingecko_backoff_until:

        remaining = int(
            coingecko_backoff_until
            - now
        )

        raise RuntimeError(
            f"CoinGecko cooling down for {remaining}s"
        )

    if (
        now
        - last_coingecko_fetch_attempt
        < COINGECKO_MIN_FETCH_GAP
    ):

        raise RuntimeError(
            "CoinGecko fetch gap active"
        )

    last_coingecko_fetch_attempt = now

    try:

        data = http_get_json(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(
                    PERP_ASSETS.values()
                ),
                "price_change_percentage":
                "1h,24h",
            },
            headers=coingecko_headers(),
        )

    except requests.HTTPError as error:

        status = (
            error.response.status_code
            if error.response is not None
            else None
        )

        if status == 429:

            if (
                coingecko_backoff_seconds
                == 0
            ):

                coingecko_backoff_seconds = (
                    COINGECKO_BACKOFF_BASE
                )

            else:

                coingecko_backoff_seconds = min(
                    COINGECKO_BACKOFF_MAX,
                    coingecko_backoff_seconds
                    * 2,
                )

            coingecko_backoff_until = (
                time.time()
                + coingecko_backoff_seconds
            )

        raise

    else:

        coingecko_backoff_seconds = 0
        coingecko_backoff_until = 0.0

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


def get_major_markets(
    force=False,
):

    global market_cache
    global market_cache_updated_at

    now = time.time()

    if (
        market_cache
        and not force
        and (
            now
            - market_cache_updated_at
            < COINGECKO_CACHE_TTL
        )
    ):

        return market_cache

    try:

        fresh = (
            _fetch_major_markets_from_coingecko()
        )

        if fresh:

            market_cache = fresh
            market_cache_updated_at = (
                time.time()
            )

            return market_cache

    except Exception as error:

        print(
            "CoinGecko refresh error:",
            repr(error),
        )

        # Important:
        # If CoinGecko rate-limits us,
        # keep using the last successful snapshot.
        if market_cache:
            return market_cache

        raise

    return market_cache


def get_major_market(
    symbol,
):

    return get_major_markets().get(
        symbol.upper()
    )


def get_major_price(
    symbol,
):

    market = get_major_market(
        symbol
    )

    if not market:
        return None

    return market["price"]


def coin_alias_to_symbol(
    value,
):

    aliases = {
        "btc": "BTC",
        "bitcoin": "BTC",
        "eth": "ETH",
        "ethereum": "ETH",
        "sol": "SOL",
        "solana": "SOL",
        "icp": "ICP",
        "internet-computer": "ICP",
    }

    return aliases.get(
        value.lower()
    )


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

            items = http_get_json(
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

            pairs = http_get_json(
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
                (
                    pair.get("liquidity")
                    or {}
                ).get("usd")
            )
            or 0
        )

        old_pair = (
            best_pairs.get(
                address
            )
        )

        old_liquidity = float(
            (
                (
                    (
                        old_pair
                        or {}
                    ).get("liquidity")
                    or {}
                ).get("usd")
            )
            or 0
        )

        if (
            old_pair is None
            or liquidity
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

        tx_1h = (
            (
                pair.get("txns")
                or {}
            ).get("h1")
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

            except Exception:

                age_hours = None

        try:

            price = float(
                pair.get("priceUsd")
                or 0
            )

        except Exception:

            price = 0

        candidates.append(
            {
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
                        (
                            pair.get(
                                "liquidity"
                            )
                            or {}
                        ).get("usd")
                    )
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
                "age_hours":
                    age_hours,
            }
        )

    return candidates


def get_solana_token_price(
    address,
):

    pairs = http_get_json(
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
                (
                    p.get("liquidity")
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
# MEME SCORING
# =========================================================

def score_meme(
    candidate,
):

    score = 0

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

        elif age <= 72:
            score += 8

        elif age <= 14 * 24:
            score += 4

    if (
        500_000
        <= mc
        <= 10_000_000
    ):

        score += 20

    elif (
        100_000
        <= mc
        <= 50_000_000
    ):

        score += 10

    if liquidity >= 1_000_000:
        score += 20

    elif liquidity >= 500_000:
        score += 16

    elif liquidity >= 250_000:
        score += 12

    elif liquidity >= 100_000:
        score += 8

    elif liquidity >= 25_000:
        score += 4

    if volume_24h >= 5_000_000:
        score += 12

    elif volume_24h >= 1_000_000:
        score += 9

    elif volume_24h >= 100_000:
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

        if acceleration >= 4:
            score += 18

        elif acceleration >= 2:
            score += 13

        elif acceleration >= 1.2:
            score += 7

        elif acceleration < 0.35:
            score -= 5

    if (
        buys + sells
        >= 10
    ):

        if sells == 0:
            score += 12

        else:

            ratio = buys / sells

            if ratio >= 2:
                score += 14

            elif ratio >= 1.4:
                score += 10

            elif ratio >= 1.1:
                score += 5

            elif ratio < 0.7:
                score -= 8

    if (
        1
        <= change_1h
        <= 15
    ):

        score += 10

    elif (
        15
        < change_1h
        <= 30
    ):

        score += 5

    elif change_1h > 60:
        score -= 20

    elif change_1h > 35:
        score -= 10

    elif change_1h < -20:
        score -= 15

    elif change_1h < -10:
        score -= 6

    if change_24h > 300:
        score -= 15

    if mc and liquidity:

        ratio = (
            liquidity / mc
        )

        if ratio >= 0.20:
            score += 8

        elif ratio >= 0.08:
            score += 4

        elif ratio < 0.02:
            score -= 12

    score = max(
        0,
        min(
            100,
            score,
        ),
    )

    if score >= 80:
        label = "🔥 STRONG WATCH"

    elif score >= 65:
        label = "👀 WATCH"

    elif score >= 35:
        label = "⚠️ SPECULATIVE"

    else:
        label = "WEAK"

    return (
        score,
        label,
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
            and candidate["age_hours"]
            > MAX_TOKEN_AGE_DAYS
            * 24
        ):
            continue

        (
            score,
            label,
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
# MEME PAPER TRADES
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

    if (
        candidate["address"]
        in meme_positions
    ):
        return None

    if (
        candidate["price"]
        <= 0
    ):
        return None

    position = {
        "address":
            candidate["address"],
        "name":
            candidate["name"],
        "symbol":
            candidate["symbol"],
        "entry_price":
            candidate["price"],
        "quantity":
            MEME_TRADE_SIZE
            / candidate["price"],
        "cost":
            MEME_TRADE_SIZE,
        "score":
            candidate["score"],
        "opened_at":
            time.time(),
    }

    meme_positions[
        candidate["address"]
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

    trade = {
        **position,
        "exit_price":
            current_price,
        "pnl":
            pnl,
        "pnl_pct":
            pnl
            / position["cost"]
            * 100,
        "reason":
            reason,
        "closed_at":
            time.time(),
    }

    meme_balance += (
        proceeds
    )

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
        if market["market_cap"]
        else 0
    )

    if (
        activity_ratio
        >= 0.03
    ):
        long_score += 10
        short_score += 10

    if (
        0.4
        <= change_1h
        <= 4
    ):
        long_score += 35

    elif change_1h > 4:
        long_score += 15

    if (
        0.5
        <= change_24h
        <= 12
    ):
        long_score += 30

    elif change_24h > 12:
        long_score += 10

    if (
        change_1h > 0
        and change_24h > 0
    ):
        long_score += 20

    if (
        -4
        <= change_1h
        <= -0.4
    ):
        short_score += 35

    elif change_1h < -4:
        short_score += 15

    if (
        -12
        <= change_24h
        <= -0.5
    ):
        short_score += 30

    elif change_24h < -12:
        short_score += 10

    if (
        change_1h < 0
        and change_24h < 0
    ):
        short_score += 20

    if abs(change_1h) > 8:
        long_score -= 20
        short_score -= 20

    if abs(change_24h) > 20:
        long_score -= 15
        short_score -= 15

    long_score = max(
        0,
        min(100, long_score),
    )

    short_score = max(
        0,
        min(100, short_score),
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

        signals.append(
            {
                **market,
                "side":
                    side,
                "score":
                    score,
            }
        )

    signals.sort(
        key=lambda item:
            item["score"],
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

    if (
        signal["symbol"]
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

    if (
        signal["price"]
        <= 0
    ):
        return None

    notional = (
        PERP_COLLATERAL
        * PERP_LEVERAGE
    )

    entry_fee = (
        notional
        * PERP_FEE_RATE
    )

    if (
        perp_balance
        < PERP_COLLATERAL
        + entry_fee
    ):
        return None

    if (
        signal["side"]
        == "LONG"
    ):

        entry_price = (
            signal["price"]
            * (
                1
                + PERP_SLIPPAGE_RATE
            )
        )

    else:

        entry_price = (
            signal["price"]
            * (
                1
                - PERP_SLIPPAGE_RATE
            )
        )

    perp_balance -= (
        PERP_COLLATERAL
        + entry_fee
    )

    position = {
        "symbol":
            signal["symbol"],
        "name":
            signal["name"],
        "side":
            signal["side"],
        "score":
            signal["score"],
        "entry_price":
            entry_price,
        "collateral":
            PERP_COLLATERAL,
        "notional":
            notional,
        "entry_fee":
            entry_fee,
        "opened_at":
            time.time(),
    }

    perp_positions[
        signal["symbol"]
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
            - position["entry_price"]
        ) / position["entry_price"]

    else:

        exit_price = (
            market_price
            * (
                1
                + PERP_SLIPPAGE_RATE
            )
        )

        return_pct = (
            position["entry_price"]
            - exit_price
        ) / position["entry_price"]

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
        - position["entry_fee"]
        - exit_fee
    )

    perp_balance += (
        position["collateral"]
        + net_pnl
    )

    trade = {
        **position,
        "exit_price":
            exit_price,
        "net_pnl":
            net_pnl,
        "pnl_pct":
            net_pnl
            / position["collateral"]
            * 100,
        "reason":
            reason,
        "closed_at":
            time.time(),
    }

    perp_history.append(
        trade
    )

    del perp_positions[
        symbol
    ]

    return trade


# =========================================================
# BACKGROUND MARKET REFRESH
# =========================================================

async def market_refresh_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        await asyncio.to_thread(
            get_major_markets,
            True,
        )

        print(
            "Major market cache refreshed."
        )

    except Exception as error:

        print(
            "market_refresh_job error:",
            repr(error),
        )


# =========================================================
# AUTO MEME SCAN
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
                        f"Candidates: {len(results)}\n"
                        f"Best: {best['name']} "
                        f"({best['symbol']})\n"
                        f"Score: {best['score']}/100\n"
                        f"Signal: {best['label']}\n"
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
            for candidate in results
            if (
                candidate["score"]
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

        if position:

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
# AUTO PERP SCAN
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
            for signal in signals
            if (
                signal["side"]
                in (
                    "LONG",
                    "SHORT",
                )
                and signal["score"]
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

        if position:

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
# ICP ALERT
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

        market = (
            await asyncio.to_thread(
                get_major_markets
            )
        ).get("ICP")

        if not market:
            return

        direction = None

        if (
            market["change_1h"]
            >= ICP_ALERT_1H_PERCENT
            or market["change_24h"]
            >= ICP_ALERT_24H_PERCENT
        ):

            direction = "UP"

        elif (
            market["change_1h"]
            <= -ICP_ALERT_1H_PERCENT
            or market["change_24h"]
            <= -ICP_ALERT_24H_PERCENT
        ):

            direction = "DOWN"

        if not direction:
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
                f"${market['price']:.4f}\n"
                f"1h: "
                f"{market['change_1h']:+.2f}%\n"
                f"24h: "
                f"{market['change_24h']:+.2f}%"
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

    for address in list(
        meme_positions
    ):

        position = (
            meme_positions.get(
                address
            )
        )

        if not position:
            continue

        try:

            current_price = (
                await asyncio.to_thread(
                    get_solana_token_price,
                    address,
                )
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
            current_price
            - position["entry_price"]
        ) / position["entry_price"] * 100

        minutes_open = (
            time.time()
            - position["opened_at"]
        ) / 60

        reason = None

        if (
            pnl_pct
            >= MEME_TAKE_PROFIT
        ):

            reason = "TAKE PROFIT"

        elif (
            pnl_pct
            <= -MEME_STOP_LOSS
        ):

            reason = "STOP LOSS"

        elif (
            minutes_open
            >= MEME_MAX_HOLD_MINUTES
        ):

            reason = "MAX HOLD"

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

    markets = {}

    if perp_positions:

        try:

            markets = (
                await asyncio.to_thread(
                    get_major_markets
                )
            )

        except Exception as error:

            print(
                "perp monitor market error:",
                repr(error),
            )

    for symbol in list(
        perp_positions
    ):

        position = (
            perp_positions.get(
                symbol
            )
        )

        market = markets.get(
            symbol
        )

        if (
            not position
            or not market
            or not market["price"]
        ):

            continue

        current_price = (
            market["price"]
        )

        if (
            position["side"]
            == "LONG"
        ):

            raw_pct = (
                current_price
                - position["entry_price"]
            ) / position["entry_price"] * 100

        else:

            raw_pct = (
                position["entry_price"]
                - current_price
            ) / position["entry_price"] * 100

        pnl_pct = (
            raw_pct
            * PERP_LEVERAGE
        )

        minutes_open = (
            time.time()
            - position["opened_at"]
        ) / 60

        reason = None

        if (
            pnl_pct
            >= PERP_TAKE_PROFIT
        ):

            reason = "TAKE PROFIT"

        elif (
            pnl_pct
            <= -PERP_STOP_LOSS
        ):

            reason = "STOP LOSS"

        elif (
            minutes_open
            >= PERP_MAX_HOLD_MINUTES
        ):

            reason = "MAX HOLD"

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
            market_refresh_job,
            f"market_refresh_{chat_id}",
            AUTO_SCAN_INTERVAL,
            5,
        ),
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
            120,
        ),
    ]

    for (
        callback,
        name,
        interval,
        first,
    ) in jobs:

        if not (
            job_queue
            .get_jobs_by_name(
                name
            )
        ):

            job_queue.run_repeating(
                callback,
                interval=interval,
                first=first,
                chat_id=chat_id,
                name=name,
            )


# =========================================================
# STARTUP
# =========================================================

async def post_init(
    application,
):

    if TELEGRAM_CHAT_ID:

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
                    "♻️ Caltanide Jarvis restarted.\n"
                    "✅ Background jobs are active."
                ),
            )

        except Exception as error:

            print(
                "post_init error:",
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


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    jobs = list(
        context.job_queue.jobs()
    )

    if market_cache_updated_at:

        cache_age = int(
            time.time()
            - market_cache_updated_at
        )

    else:

        cache_age = None

    message = (
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
        f"Market cache: "
        f"{'ready' if market_cache else 'empty'}"
    )

    if (
        cache_age
        is not None
    ):

        message += (
            f" ({cache_age}s old)"
        )

    message += "\n\nJobs:\n"

    if jobs:

        message += "\n".join(
            f"✅ {job.name}"
            for job in jobs
        )

    else:

        message += "❌ none"

    await update.message.reply_text(
        message
    )


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

            lines.extend(
                [
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
                ]
            )

        await update.message.reply_text(
            "\n".join(lines)[:4000]
        )

    except Exception as error:

        print(
            "hunt error:",
            repr(error),
        )

        await update.message.reply_text(
            "⚠️ Solana scan temporarily unavailable."
        )


async def paper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    realized = sum(
        trade["pnl"]
        for trade in meme_history
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

            current = (
                await asyncio.to_thread(
                    get_solana_token_price,
                    position["address"],
                )
            )

        except Exception:

            current = None

        if current:

            pnl = (
                current
                - position["entry_price"]
            ) / position["entry_price"] * 100

        else:

            pnl = 0

        lines.extend(
            [
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
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)[:4000]
    )


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

        lines.extend(
            [
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
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


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

            lines.extend(
                [
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
                ]
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as error:

        print(
            "perpscan error:",
            repr(error),
        )

        await update.message.reply_text(
            "⚠️ Market data unavailable right now. "
            "Jarvis will retry automatically."
        )


async def perpaccount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    realized = sum(
        trade["net_pnl"]
        for trade in perp_history
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


async def perppositions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not perp_positions:

        await update.message.reply_text(
            "No paper long/short positions open."
        )

        return

    try:

        markets = await asyncio.to_thread(
            get_major_markets
        )

    except Exception:

        markets = {}

    lines = [
        "📊 OPEN PAPER LONG/SHORT",
        "",
    ]

    for position in (
        perp_positions.values()
    ):

        market = markets.get(
            position["symbol"]
        )

        current = (
            market["price"]
            if market
            else None
        )

        if current:

            if (
                position["side"]
                == "LONG"
            ):

                raw = (
                    current
                    - position["entry_price"]
                ) / position["entry_price"]

            else:

                raw = (
                    position["entry_price"]
                    - current
                ) / position["entry_price"]

            pnl = (
                raw
                * 100
                * PERP_LEVERAGE
            )

        else:

            pnl = 0

        lines.extend(
            [
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
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


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

        lines.extend(
            [
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
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def icp(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        market = (
            await asyncio.to_thread(
                get_major_markets
            )
        ).get("ICP")

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
            "ICP data temporarily unavailable. "
            "Jarvis will retry automatically."
        )


async def price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.args:

        await update.message.reply_text(
            "Try: /price bitcoin"
        )

        return

    symbol = coin_alias_to_symbol(
        context.args[0]
    )

    if not symbol:

        await update.message.reply_text(
            "Supported: BTC, ETH, SOL, ICP"
        )

        return

    try:

        market = (
            await asyncio.to_thread(
                get_major_markets
            )
        ).get(symbol)

        if not market:

            await update.message.reply_text(
                "Price data unavailable."
            )

            return

        await update.message.reply_text(
            f"🪙 {market['name']} "
            f"({symbol})\n\n"
            f"Price: "
            f"${market['price']:,.6f}\n"
            f"1h: "
            f"{market['change_1h']:+.2f}%\n"
            f"24h: "
            f"{market['change_24h']:+.2f}%\n"
            f"Market cap: "
            f"${market['market_cap']:,.0f}\n"
            f"24h volume: "
            f"${market['volume_24h']:,.0f}"
        )

    except Exception as error:

        print(
            "price error:",
            repr(error),
        )

        await update.message.reply_text(
            "Price data temporarily unavailable. "
            "Jarvis is cooling down the market-data API."
        )


async def analyse(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.args:

        await update.message.reply_text(
            "Try: /analyse bitcoin"
        )

        return

    symbol = coin_alias_to_symbol(
        context.args[0]
    )

    if not symbol:

        await update.message.reply_text(
            "Supported: BTC, ETH, SOL, ICP"
        )

        return

    try:

        market = (
            await asyncio.to_thread(
                get_major_markets
            )
        ).get(symbol)

        if not market:

            await update.message.reply_text(
                "Market data unavailable."
            )

            return

        prompt = f"""
You are Caltanide Crypto Jarvis,
a cautious cryptocurrency market-analysis assistant.

Asset: {market['name']} ({symbol})
Price: {market['price']}
1h change: {market['change_1h']}%
24h change: {market['change_24h']}%
Market cap: {market['market_cap']}
24h volume: {market['volume_24h']}

Give a concise analysis of:
1. Momentum
2. Volume
3. Bullish case
4. Bearish case
5. Main risks
6. Confirmation
7. Invalidation

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

    handlers = [
        ("start", start),
        ("status", status),
        ("chatid", chatid),

        ("hunt", hunt),
        ("paper", paper),
        ("positions", positions),
        ("history", history),

        ("perpscan", perpscan),
        ("perpaccount", perpaccount),
        ("perppositions", perppositions),
        ("perphistory", perphistory),

        ("icp", icp),
        ("price", price),
        ("analyse", analyse),
    ]

    for (
        name,
        function,
    ) in handlers:

        app.add_handler(
            CommandHandler(
                name,
                function,
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
