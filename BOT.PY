import os
import time
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from openai import OpenAI


# =========================================================
# ENVIRONMENT
# =========================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)


# =========================================================
# GENERAL SETTINGS
# =========================================================

REQUEST_TIMEOUT = 15

AUTO_HUNT_INTERVAL = 300
AUTO_HUNT_FIRST_SCAN = 60

POSITION_MONITOR_INTERVAL = 30


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

meme_balance = MEME_STARTING_BALANCE
meme_positions = {}
meme_history = []


# =========================================================
# MEME SCANNER FILTERS
# =========================================================

MIN_MARKET_CAP = 100_000
MAX_MARKET_CAP = 50_000_000

MIN_LIQUIDITY = 25_000
MIN_VOLUME_24H = 100_000
MIN_VOLUME_1H = 2_500

MIN_SCAN_SCORE = 35
MAX_TOKEN_AGE_DAYS = 30


# =========================================================
# PAPER PERPS SETTINGS
# =========================================================

PERP_STARTING_BALANCE = 1000.00

# USDC-style paper collateral per position
PERP_COLLATERAL = 50.00

# Start with 1x only
PERP_LEVERAGE = 1.0

PERP_ENTRY_SCORE = 60

PERP_TAKE_PROFIT = 2.0
PERP_STOP_LOSS = 1.0

PERP_MAX_HOLD_MINUTES = 120
PERP_MAX_OPEN_POSITIONS = 1

# Simulated execution costs
PERP_FEE_RATE = 0.0005
PERP_SLIPPAGE_RATE = 0.0005

PERP_ASSETS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}

perp_balance = PERP_STARTING_BALANCE
perp_positions = {}
perp_history = []


# =========================================================
# HTTP
# =========================================================

def get_json(url, params=None):
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

def get_coin(coin):
    url = "https://api.coingecko.com/api/v3/coins/markets"

    params = {
        "vs_currency": "usd",
        "ids": coin.lower(),
        "price_change_percentage": "1h,24h",
    }

    data = get_json(url, params=params)

    if not data:
        return None

    return data[0]


def get_perp_markets():
    url = "https://api.coingecko.com/api/v3/coins/markets"

    ids = ",".join(PERP_ASSETS.values())

    params = {
        "vs_currency": "usd",
        "ids": ids,
        "price_change_percentage": "1h,24h",
    }

    data = get_json(url, params=params)

    markets = {}

    for item in data:
        symbol = item.get("symbol", "").upper()

        markets[symbol] = {
            "id": item["id"],
            "symbol": symbol,
            "name": item["name"],
            "price": float(item.get("current_price") or 0),
            "market_cap": item.get("market_cap") or 0,
            "volume_24h": item.get("total_volume") or 0,
            "change_1h": (
                item.get(
                    "price_change_percentage_1h_in_currency"
                )
                or 0
            ),
            "change_24h": (
                item.get(
                    "price_change_percentage_24h_in_currency"
                )
                or item.get("price_change_percentage_24h")
                or 0
            ),
        }

    return markets


def get_perp_price(symbol):
    coin_id = PERP_ASSETS.get(symbol)

    if not coin_id:
        return None

    data = get_coin(coin_id)

    if not data:
        return None

    price = data.get("current_price")

    if price is None:
        return None

    return float(price)


# =========================================================
# DEXSCREENER SOLANA DISCOVERY
# =========================================================

def collect_solana_token_addresses():
    endpoints = [
        "https://api.dexscreener.com/token-profiles/latest/v1",
        "https://api.dexscreener.com/token-boosts/latest/v1",
        "https://api.dexscreener.com/token-boosts/top/v1",
        "https://api.dexscreener.com/community-takeovers/latest/v1",
    ]

    addresses = []
    seen = set()

    for url in endpoints:
        try:
            items = get_json(url)
        except requests.RequestException as error:
            print("Discovery error:", repr(error))
            continue

        if not isinstance(items, list):
            continue

        for item in items:
            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")

            if not address or address in seen:
                continue

            seen.add(address)
            addresses.append(address)

    return addresses


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def get_solana_candidates():
    addresses = collect_solana_token_addresses()

    if not addresses:
        return []

    all_pairs = []

    for batch in chunked(addresses, 30):
        joined = ",".join(batch)

        url = (
            "https://api.dexscreener.com/tokens/v1/"
            f"solana/{joined}"
        )

        try:
            pairs = get_json(url)
        except requests.RequestException as error:
            print("DEX pair error:", repr(error))
            continue

        if isinstance(pairs, list):
            all_pairs.extend(pairs)

    best_pairs = {}

    for pair in all_pairs:
        if pair.get("chainId") != "solana":
            continue

        base = pair.get("baseToken") or {}
        address = base.get("address")

        if not address:
            continue

        liquidity = (
            (pair.get("liquidity") or {}).get("usd")
            or 0
        )

        current = best_pairs.get(address)

        if current is None:
            best_pairs[address] = pair
            continue

        current_liquidity = (
            (current.get("liquidity") or {}).get("usd")
            or 0
        )

        if liquidity > current_liquidity:
            best_pairs[address] = pair

    candidates = []

    now_ms = int(time.time() * 1000)

    for address, pair in best_pairs.items():
        base = pair.get("baseToken") or {}
        volume = pair.get("volume") or {}
        txns = pair.get("txns") or {}
        change = pair.get("priceChange") or {}

        tx1 = txns.get("h1") or {}

        created = pair.get("pairCreatedAt")

        age_hours = None

        if created:
            try:
                age_hours = max(
                    0,
                    now_ms - int(created),
                ) / 3_600_000
            except Exception:
                pass

        try:
            price = float(pair.get("priceUsd") or 0)
        except Exception:
            price = 0

        candidates.append(
            {
                "name": base.get("name") or "Unknown",
                "symbol": base.get("symbol") or "?",
                "address": address,
                "price": price,
                "market_cap": (
                    pair.get("marketCap")
                    or pair.get("fdv")
                    or 0
                ),
                "liquidity": (
                    (pair.get("liquidity") or {}).get("usd")
                    or 0
                ),
                "volume_24h": volume.get("h24") or 0,
                "volume_1h": volume.get("h1") or 0,
                "buys_1h": tx1.get("buys") or 0,
                "sells_1h": tx1.get("sells") or 0,
                "price_change_1h": change.get("h1") or 0,
                "price_change_24h": change.get("h24") or 0,
                "age_hours": age_hours,
            }
        )

    return candidates


def get_token_price(address):
    url = (
        "https://api.dexscreener.com/token-pairs/v1/"
        f"solana/{address}"
    )

    pairs = get_json(url)

    if not isinstance(pairs, list) or not pairs:
        return None

    pair = max(
        pairs,
        key=lambda p: (
            (p.get("liquidity") or {}).get("usd")
            or 0
        ),
    )

    try:
        return float(pair.get("priceUsd") or 0)
    except Exception:
        return None


# =========================================================
# MEME SCORING
# =========================================================

def score_meme(c):
    score = 0
    positives = []
    risks = []

    mc = c["market_cap"]
    liq = c["liquidity"]
    vol24 = c["volume_24h"]
    vol1 = c["volume_1h"]

    buys = c["buys_1h"]
    sells = c["sells_1h"]

    change1 = c["price_change_1h"]
    change24 = c["price_change_24h"]

    age = c["age_hours"]

    if age is not None:
        if age < 1:
            score -= 8
            risks.append("extremely new")
        elif age <= 72:
            score += 8
            positives.append("early-stage")

    if 500_000 <= mc <= 10_000_000:
        score += 20
        positives.append("good market cap")
    elif 100_000 <= mc <= 50_000_000:
        score += 10

    if liq >= 1_000_000:
        score += 20
    elif liq >= 500_000:
        score += 16
    elif liq >= 250_000:
        score += 12
    elif liq >= 100_000:
        score += 8
    elif liq >= 25_000:
        score += 4

    if vol24 >= 5_000_000:
        score += 12
    elif vol24 >= 1_000_000:
        score += 9
    elif vol24 >= 100_000:
        score += 5

    expected_hourly = vol24 / 24 if vol24 else 0

    if expected_hourly:
        acceleration = vol1 / expected_hourly

        if acceleration >= 4:
            score += 18
            positives.append("strong acceleration")
        elif acceleration >= 2:
            score += 13
        elif acceleration >= 1.2:
            score += 7

    if buys + sells >= 10:
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
                risks.append("selling pressure")

    if 1 <= change1 <= 15:
        score += 10
    elif 15 < change1 <= 30:
        score += 5
    elif change1 > 60:
        score -= 20
        risks.append("extreme pump")
    elif change1 < -20:
        score -= 15
        risks.append("sharp decline")

    if change24 > 300:
        score -= 15

    if mc and liq:
        ratio = liq / mc

        if ratio >= 0.20:
            score += 8
        elif ratio >= 0.08:
            score += 4
        elif ratio < 0.02:
            score -= 12

    score = max(0, min(100, score))

    return score, positives, risks


def get_ranked_memes():
    results = []

    for c in get_solana_candidates():
        if c["price"] <= 0:
            continue

        if not (
            MIN_MARKET_CAP
            <= c["market_cap"]
            <= MAX_MARKET_CAP
        ):
            continue

        if c["liquidity"] < MIN_LIQUIDITY:
            continue

        if c["volume_24h"] < MIN_VOLUME_24H:
            continue

        if c["volume_1h"] < MIN_VOLUME_1H:
            continue

        if c["age_hours"] is not None:
            if c["age_hours"] > MAX_TOKEN_AGE_DAYS * 24:
                continue

        score, positives, risks = score_meme(c)

        if score < MIN_SCAN_SCORE:
            continue

        c["score"] = score
        c["positives"] = positives
        c["risks"] = risks

        results.append(c)

    results.sort(
        key=lambda c: c["score"],
        reverse=True,
    )

    return results


# =========================================================
# MEME PAPER TRADING
# =========================================================

def meme_buy(candidate):
    global meme_balance

    if len(meme_positions) >= MEME_MAX_OPEN_POSITIONS:
        return None

    if meme_balance < MEME_TRADE_SIZE:
        return None

    if candidate["address"] in meme_positions:
        return None

    quantity = MEME_TRADE_SIZE / candidate["price"]

    position = {
        "address": candidate["address"],
        "name": candidate["name"],
        "symbol": candidate["symbol"],
        "entry_price": candidate["price"],
        "quantity": quantity,
        "cost": MEME_TRADE_SIZE,
        "score": candidate["score"],
        "opened_at": time.time(),
    }

    meme_positions[candidate["address"]] = position

    meme_balance -= MEME_TRADE_SIZE

    return position


def meme_sell(address, price, reason):
    global meme_balance

    position = meme_positions.get(address)

    if not position:
        return None

    proceeds = position["quantity"] * price

    pnl = proceeds - position["cost"]

    pnl_pct = pnl / position["cost"] * 100

    meme_balance += proceeds

    trade = {
        **position,
        "exit_price": price,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "reason": reason,
    }

    meme_history.append(trade)

    del meme_positions[address]

    return trade


# =========================================================
# PERP SIGNAL ENGINE
# =========================================================

def score_perp_market(market):
    long_score = 0
    short_score = 0

    change1 = market["change_1h"]
    change24 = market["change_24h"]

    market_cap = market["market_cap"]
    volume = market["volume_24h"]

    volume_ratio = (
        volume / market_cap
        if market_cap
        else 0
    )

    # Liquidity/activity quality
    if volume_ratio >= 0.05:
        long_score += 10
        short_score += 10

    # LONG momentum
    if 0.4 <= change1 <= 4:
        long_score += 35
    elif change1 > 4:
        long_score += 15

    if 0.5 <= change24 <= 12:
        long_score += 30
    elif change24 > 12:
        long_score += 10

    if change1 > 0 and change24 > 0:
        long_score += 20

    # SHORT momentum
    if -4 <= change1 <= -0.4:
        short_score += 35
    elif change1 < -4:
        short_score += 15

    if -12 <= change24 <= -0.5:
        short_score += 30
    elif change24 < -12:
        short_score += 10

    if change1 < 0 and change24 < 0:
        short_score += 20

    # Avoid chasing extreme moves
    if abs(change1) > 8:
        long_score -= 20
        short_score -= 20

    if abs(change24) > 20:
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

    if long_score > short_score:
        return "LONG", long_score

    if short_score > long_score:
        return "SHORT", short_score

    return "NONE", 0


def get_perp_signals():
    markets = get_perp_markets()

    signals = []

    for symbol, market in markets.items():
        side, score = score_perp_market(market)

        market["side"] = side
        market["score"] = score

        signals.append(market)

    signals.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    return signals


# =========================================================
# PAPER PERP OPEN
# =========================================================

def open_perp_position(signal):
    global perp_balance

    if len(perp_positions) >= PERP_MAX_OPEN_POSITIONS:
        return None

    if perp_balance < PERP_COLLATERAL:
        return None

    symbol = signal["symbol"]

    if symbol in perp_positions:
        return None

    market_price = signal["price"]

    if market_price <= 0:
        return None

    side = signal["side"]

    notional = (
        PERP_COLLATERAL
        * PERP_LEVERAGE
    )

    entry_fee = (
        notional
        * PERP_FEE_RATE
    )

    if side == "LONG":
        entry_price = (
            market_price
            * (1 + PERP_SLIPPAGE_RATE)
        )
    else:
        entry_price = (
            market_price
            * (1 - PERP_SLIPPAGE_RATE)
        )

    if perp_balance < (
        PERP_COLLATERAL + entry_fee
    ):
        return None

    perp_balance -= (
        PERP_COLLATERAL
        + entry_fee
    )

    position = {
        "symbol": symbol,
        "name": signal["name"],
        "side": side,
        "score": signal["score"],
        "entry_price": entry_price,
        "collateral": PERP_COLLATERAL,
        "notional": notional,
        "entry_fee": entry_fee,
        "opened_at": time.time(),
    }

    perp_positions[symbol] = position

    return position


# =========================================================
# PAPER PERP CLOSE
# =========================================================

def close_perp_position(
    symbol,
    market_price,
    reason,
):
    global perp_balance

    position = perp_positions.get(symbol)

    if not position:
        return None

    if position["side"] == "LONG":
        exit_price = (
            market_price
            * (1 - PERP_SLIPPAGE_RATE)
        )

        return_pct = (
            exit_price
            - position["entry_price"]
        ) / position["entry_price"]

    else:
        exit_price = (
            market_price
            * (1 + PERP_SLIPPAGE_RATE)
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
        - exit_fee
    )

    perp_balance += (
        position["collateral"]
        + net_pnl
    )

    pnl_pct = (
        net_pnl
        / position["collateral"]
    ) * 100

    trade = {
        **position,
        "exit_price": exit_price,
        "gross_pnl": gross_pnl,
        "exit_fee": exit_fee,
        "net_pnl": net_pnl,
        "pnl_pct": pnl_pct,
        "reason": reason,
        "closed_at": time.time(),
    }

    perp_history.append(trade)

    del perp_positions[symbol]

    return trade


# =========================================================
# POSITION MONITOR
# =========================================================

async def monitor_positions(
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = context.job.chat_id

    # ---------------- MEMES ----------------

    for address in list(meme_positions.keys()):
        p = meme_positions.get(address)

        try:
            current = get_token_price(address)
        except Exception:
            continue

        if not current:
            continue

        pnl_pct = (
            (current - p["entry_price"])
            / p["entry_price"]
        ) * 100

        minutes = (
            time.time()
            - p["opened_at"]
        ) / 60

        reason = None

        if pnl_pct >= MEME_TAKE_PROFIT:
            reason = "TAKE PROFIT"

        elif pnl_pct <= -MEME_STOP_LOSS:
            reason = "STOP LOSS"

        elif minutes >= MEME_MAX_HOLD_MINUTES:
            reason = "MAX HOLD"

        if reason:
            trade = meme_sell(
                address,
                current,
                reason,
            )

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "📤 MEME PAPER SELL\n\n"
                    f"{trade['symbol']}\n"
                    f"Reason: {trade['reason']}\n"
                    f"P/L: {trade['pnl_pct']:+.2f}%\n"
                    f"Cash: ${meme_balance:.2f}"
                ),
            )

    # ---------------- PERPS ----------------

    for symbol in list(perp_positions.keys()):
        p = perp_positions.get(symbol)

        try:
            current = get_perp_price(symbol)
        except Exception:
            continue

        if not current:
            continue

        if p["side"] == "LONG":
            raw_pct = (
                current - p["entry_price"]
            ) / p["entry_price"] * 100
        else:
            raw_pct = (
                p["entry_price"] - current
            ) / p["entry_price"] * 100

        leveraged_pct = (
            raw_pct
            * PERP_LEVERAGE
        )

        minutes = (
            time.time()
            - p["opened_at"]
        ) / 60

        reason = None

        if leveraged_pct >= PERP_TAKE_PROFIT:
            reason = "TAKE PROFIT"

        elif leveraged_pct <= -PERP_STOP_LOSS:
            reason = "STOP LOSS"

        elif minutes >= PERP_MAX_HOLD_MINUTES:
            reason = "MAX HOLD"

        if reason:
            trade = close_perp_position(
                symbol,
                current,
                reason,
            )

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "📤 PERP PAPER CLOSE\n\n"
                    f"{trade['symbol']} "
                    f"{trade['side']}\n"
                    f"Reason: {trade['reason']}\n"
                    f"Net P/L: "
                    f"${trade['net_pnl']:+.2f}\n"
                    f"P/L: "
                    f"{trade['pnl_pct']:+.2f}%\n"
                    f"Perp balance: "
                    f"${perp_balance:.2f} USDC"
                ),
            )


# =========================================================
# AUTO MEME SCAN
# =========================================================

async def auto_meme_scan(
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = context.job.chat_id

    try:
        results = get_ranked_memes()

        if not results:
            return

        if meme_positions:
            return

        eligible = [
            c
            for c in results
            if c["score"] >= MEME_ENTRY_SCORE
        ]

        if not eligible:
            return

        position = meme_buy(
            eligible[0]
        )

        if position:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "📥 MEME PAPER BUY\n\n"
                    f"{position['name']} "
                    f"({position['symbol']})\n"
                    f"Score: {position['score']}/100\n"
                    f"Size: ${position['cost']:.2f}\n"
                    f"Entry: "
                    f"${position['entry_price']:.10f}"
                ),
            )

    except Exception as error:
        print(
            "Auto meme error:",
            repr(error),
        )


# =========================================================
# AUTO PERP SCAN
# =========================================================

async def auto_perp_scan(
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = context.job.chat_id

    if perp_positions:
        return

    try:
        signals = get_perp_signals()

        eligible = [
            s
            for s in signals
            if (
                s["side"] != "NONE"
                and s["score"] >= PERP_ENTRY_SCORE
            )
        ]

        if not eligible:
            return

        signal = eligible[0]

        position = open_perp_position(
            signal
        )

        if position:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "📥 PERP PAPER OPEN\n\n"
                    f"{position['symbol']} "
                    f"{position['side']}\n"
                    f"Signal score: "
                    f"{position['score']}/100\n"
                    f"Entry: "
                    f"${position['entry_price']:,.4f}\n"
                    f"Collateral: "
                    f"${position['collateral']:.2f} USDC\n"
                    f"Leverage: "
                    f"{PERP_LEVERAGE:.1f}x\n"
                    f"Take profit: "
                    f"+{PERP_TAKE_PROFIT:.1f}%\n"
                    f"Stop loss: "
                    f"-{PERP_STOP_LOSS:.1f}%"
                ),
            )

    except Exception as error:
        print(
            "Auto perp error:",
            repr(error),
        )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    await update.message.reply_text(
        "🤖 CALTANIDE JARVIS PAPER TRADER\n\n"
        "Meme commands:\n"
        "/hunt\n"
        "/paper\n"
        "/positions\n\n"
        "Perp commands:\n"
        "/perpscan\n"
        "/perpaccount\n"
        "/perppositions\n"
        "/perphistory\n\n"
        "Other:\n"
        "/price bitcoin\n"
        "/analyse bitcoin"
    )

    jobs = [
        (
            auto_meme_scan,
            "auto_meme",
            AUTO_HUNT_INTERVAL,
            60,
        ),
        (
            auto_perp_scan,
            "auto_perp",
            AUTO_HUNT_INTERVAL,
            90,
        ),
        (
            monitor_positions,
            "position_monitor",
            POSITION_MONITOR_INTERVAL,
            30,
        ),
    ]

    for callback, prefix, interval, first in jobs:
        name = f"{prefix}_{chat_id}"

        if not context.job_queue.get_jobs_by_name(name):
            context.job_queue.run_repeating(
                callback,
                interval=interval,
                first=first,
                chat_id=chat_id,
                name=name,
            )

    await update.message.reply_text(
        "✅ Automatic PAPER trading active.\n"
        "Meme scalping + BTC/ETH/SOL long/short.\n"
        "Perps use simulated USDC at 1x leverage.\n"
        "No real money is being used."
    )


async def hunt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🔎 Scanning Solana..."
    )

    try:
        results = get_ranked_memes()

        if not results:
            await update.message.reply_text(
                "No meme candidates currently pass the filter."
            )
            return

        lines = [
            "🚨 SOLANA PAPER HUNT",
            "",
        ]

        for c in results[:5]:
            lines.extend(
                [
                    f"{c['name']} ({c['symbol']})",
                    f"Score: {c['score']}/100",
                    f"Market cap: ${c['market_cap']:,.0f}",
                    f"Liquidity: ${c['liquidity']:,.0f}",
                    f"1h volume: ${c['volume_1h']:,.0f}",
                    "",
                ]
            )

        await update.message.reply_text(
            "\n".join(lines)[:4000]
        )

    except Exception as error:
        print("Hunt:", repr(error))

        await update.message.reply_text(
            "Meme scan unavailable."
        )


async def perpscan(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        signals = get_perp_signals()

        lines = [
            "📈 PAPER PERP SCANNER",
            "",
            "Collateral currency: simulated USDC",
            f"Leverage: {PERP_LEVERAGE:.1f}x",
            "",
        ]

        for s in signals:
            lines.extend(
                [
                    f"{s['symbol']} — {s['side']}",
                    f"Score: {s['score']}/100",
                    f"Price: ${s['price']:,.4f}",
                    f"1h: {s['change_1h']:+.2f}%",
                    f"24h: {s['change_24h']:+.2f}%",
                    "",
                ]
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as error:
        print("Perp scan:", repr(error))

        await update.message.reply_text(
            "Perp scan unavailable."
        )


async def paper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    pnl = sum(
        t["pnl"]
        for t in meme_history
    )

    await update.message.reply_text(
        "💵 MEME PAPER ACCOUNT\n\n"
        f"Cash: ${meme_balance:.2f}\n"
        f"Open positions: {len(meme_positions)}\n"
        f"Closed trades: {len(meme_history)}\n"
        f"Realized P/L: ${pnl:+.2f}"
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

    for p in meme_positions.values():
        current = get_token_price(
            p["address"]
        )

        if current:
            pnl = (
                current - p["entry_price"]
            ) / p["entry_price"] * 100
        else:
            pnl = 0

        lines.extend(
            [
                f"{p['symbol']}",
                f"Entry: ${p['entry_price']:.10f}",
                f"P/L: {pnl:+.2f}%",
                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def perpaccount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    realized = sum(
        t["net_pnl"]
        for t in perp_history
    )

    await update.message.reply_text(
        "💵 PAPER PERP ACCOUNT\n\n"
        f"USDC balance: ${perp_balance:.2f}\n"
        f"Open positions: {len(perp_positions)}\n"
        f"Closed trades: {len(perp_history)}\n"
        f"Realized P/L: ${realized:+.2f}\n"
        f"Leverage: {PERP_LEVERAGE:.1f}x"
    )


async def perppositions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not perp_positions:
        await update.message.reply_text(
            "No paper perp positions open."
        )
        return

    lines = [
        "📊 OPEN PAPER PERPS",
        "",
    ]

    for p in perp_positions.values():
        current = get_perp_price(
            p["symbol"]
        )

        if current:
            if p["side"] == "LONG":
                pnl = (
                    current - p["entry_price"]
                ) / p["entry_price"] * 100
            else:
                pnl = (
                    p["entry_price"] - current
                ) / p["entry_price"] * 100

            pnl *= PERP_LEVERAGE

        else:
            pnl = 0

        lines.extend(
            [
                f"{p['symbol']} {p['side']}",
                f"Entry: ${p['entry_price']:,.4f}",
                f"P/L: {pnl:+.2f}%",
                f"Collateral: ${p['collateral']:.2f}",
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
            "No completed paper perp trades yet."
        )
        return

    lines = [
        "📚 PAPER PERP HISTORY",
        "",
    ]

    for t in perp_history[-10:]:
        lines.extend(
            [
                f"{t['symbol']} {t['side']}",
                f"P/L: {t['pnl_pct']:+.2f}%",
                f"${t['net_pnl']:+.2f}",
                f"Exit: {t['reason']}",
                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)
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

    try:
        data = get_coin(
            context.args[0]
        )

        if not data:
            await update.message.reply_text(
                "Coin not found."
            )
            return

        await update.message.reply_text(
            f"🪙 {data['name']}\n\n"
            f"Price: ${data['current_price']:,}\n"
            f"24h: "
            f"{data.get('price_change_percentage_24h') or 0:+.2f}%\n"
            f"Market cap: ${data['market_cap']:,}"
        )

    except Exception:
        await update.message.reply_text(
            "Price unavailable."
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

    try:
        data = get_coin(
            context.args[0]
        )

        if not data:
            await update.message.reply_text(
                "Coin not found."
            )
            return

        prompt = f"""
You are Crypto Jarvis.

Analyze this cryptocurrency cautiously.

Coin: {data['name']}
Price: {data['current_price']}
1h change: {data.get('price_change_percentage_1h_in_currency')}
24h change: {data.get('price_change_percentage_24h_in_currency')}
Market cap: {data['market_cap']}
Volume: {data['total_volume']}

Discuss momentum, risk, bullish confirmation,
and bearish invalidation.

Do not guarantee profits.
"""

        response = client.responses.create(
            model="gpt-5-mini",
            input=prompt,
        )

        await update.message.reply_text(
            "🧠 JARVIS ANALYSIS\n\n"
            + response.output_text
        )

    except Exception:
        await update.message.reply_text(
            "Analysis unavailable."
        )


# =========================================================
# RUN
# =========================================================

def main():
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", start))

    app.add_handler(CommandHandler("hunt", hunt))
    app.add_handler(CommandHandler("paper", paper))
    app.add_handler(CommandHandler("positions", positions))

    app.add_handler(CommandHandler("perpscan", perpscan))
    app.add_handler(CommandHandler("perpaccount", perpaccount))
    app.add_handler(CommandHandler("perppositions", perppositions))
    app.add_handler(CommandHandler("perphistory", perphistory))

    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("analyse", analyse))

    print(
        "Caltanide Jarvis PAPER trader online"
    )

    app.run_polling()


if __name__ == "__main__":
    main()
