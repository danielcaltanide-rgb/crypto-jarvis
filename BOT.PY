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
# SETTINGS
# =========================================================

AUTO_HUNT_INTERVAL = 300
AUTO_HUNT_FIRST_SCAN = 60
PAPER_MONITOR_INTERVAL = 30

MIN_MARKET_CAP = 500_000
MAX_MARKET_CAP = 25_000_000
MIN_LIQUIDITY = 150_000
MIN_VOLUME_24H = 500_000
MIN_VOLUME_1H = 25_000
MIN_SCORE = 55

MAX_TOKEN_AGE_DAYS = 30

REQUEST_TIMEOUT = 15


# =========================================================
# PAPER TRADING SETTINGS
# =========================================================

PAPER_STARTING_BALANCE = 1000.00
PAPER_TRADE_SIZE = 50.00

PAPER_MIN_ENTRY_SCORE = 70

TAKE_PROFIT_PERCENT = 5.0
STOP_LOSS_PERCENT = 2.0

MAX_OPEN_POSITIONS = 1

paper_balance = PAPER_STARTING_BALANCE
paper_positions = {}
paper_trade_history = []


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
    }

    data = get_json(url, params=params)

    if not data:
        return None

    return data[0]


# =========================================================
# DEXSCREENER DISCOVERY
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
            print("Discovery error:", error)
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
            print("Pair batch error:", error)
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

        existing = best_pairs.get(address)

        if existing is None:
            best_pairs[address] = pair
            continue

        old_liquidity = (
            (existing.get("liquidity") or {}).get("usd")
            or 0
        )

        if liquidity > old_liquidity:
            best_pairs[address] = pair

    candidates = []
    now_ms = int(time.time() * 1000)

    for address, pair in best_pairs.items():
        volume = pair.get("volume") or {}
        txns = pair.get("txns") or {}
        price_change = pair.get("priceChange") or {}

        txns_1h = txns.get("h1") or {}
        txns_6h = txns.get("h6") or {}

        pair_created_at = pair.get("pairCreatedAt")

        age_hours = None

        if pair_created_at:
            age_hours = max(
                0,
                now_ms - int(pair_created_at),
            ) / 3_600_000

        base = pair.get("baseToken") or {}

        candidates.append(
            {
                "name": base.get("name") or "Unknown",
                "symbol": base.get("symbol") or "?",
                "address": address,
                "price": float(pair.get("priceUsd") or 0),
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
                "volume_6h": volume.get("h6") or 0,
                "volume_1h": volume.get("h1") or 0,
                "buys_1h": txns_1h.get("buys") or 0,
                "sells_1h": txns_1h.get("sells") or 0,
                "buys_6h": txns_6h.get("buys") or 0,
                "sells_6h": txns_6h.get("sells") or 0,
                "price_change_1h": price_change.get("h1") or 0,
                "price_change_6h": price_change.get("h6") or 0,
                "price_change_24h": price_change.get("h24") or 0,
                "age_hours": age_hours,
                "dex_id": pair.get("dexId") or "",
            }
        )

    return candidates


# =========================================================
# LIVE PRICE LOOKUP FOR PAPER POSITIONS
# =========================================================

def get_token_price(address):
    url = (
        "https://api.dexscreener.com/tokens/v1/"
        f"solana/{address}"
    )

    pairs = get_json(url)

    if not isinstance(pairs, list) or not pairs:
        return None

    pair = max(
        pairs,
        key=lambda x: (
            (x.get("liquidity") or {}).get("usd")
            or 0
        ),
    )

    price = pair.get("priceUsd")

    if not price:
        return None

    return float(price)


# =========================================================
# SCORING
# =========================================================

def score_candidate(c):
    score = 0
    risks = []
    positives = []

    mc = c["market_cap"]
    liq = c["liquidity"]
    vol24 = c["volume_24h"]
    vol1 = c["volume_1h"]

    buys1 = c["buys_1h"]
    sells1 = c["sells_1h"]

    change1 = c["price_change_1h"]
    change6 = c["price_change_6h"]
    change24 = c["price_change_24h"]

    age_hours = c["age_hours"]

    if age_hours is not None:
        if age_hours < 1:
            score -= 12
            risks.append("extremely new pair")
        elif age_hours < 72:
            score += 7
            positives.append("early-stage pair")
        elif age_hours <= 24 * 14:
            score += 4

    if 1_000_000 <= mc <= 10_000_000:
        score += 20
        positives.append("strong market-cap zone")
    elif 500_000 <= mc <= 25_000_000:
        score += 12

    if liq >= 1_000_000:
        score += 20
        positives.append("strong liquidity")
    elif liq >= 500_000:
        score += 16
    elif liq >= 250_000:
        score += 11
    elif liq >= 150_000:
        score += 6

    if vol24 >= 10_000_000:
        score += 14
    elif vol24 >= 5_000_000:
        score += 12
    elif vol24 >= 2_000_000:
        score += 9
    elif vol24 >= 500_000:
        score += 5

    expected_hourly = vol24 / 24 if vol24 else 0

    if expected_hourly:
        ratio = vol1 / expected_hourly

        if ratio >= 4:
            score += 18
            positives.append("strong volume acceleration")
        elif ratio >= 2:
            score += 13
        elif ratio >= 1.25:
            score += 7
        elif ratio < 0.35:
            score -= 8
            risks.append("volume fading")

    if buys1 + sells1 >= 30:
        if sells1 == 0:
            score += 14
        else:
            buy_ratio = buys1 / sells1

            if buy_ratio >= 2.5:
                score += 14
                positives.append("strong buy pressure")
            elif buy_ratio >= 1.6:
                score += 11
            elif buy_ratio >= 1.2:
                score += 6
            elif buy_ratio < 0.75:
                score -= 10
                risks.append("sell pressure dominates")

    if 2 <= change1 <= 18:
        score += 9
        positives.append("healthy short-term momentum")
    elif 18 < change1 <= 35:
        score += 4
    elif change1 > 60:
        score -= 20
        risks.append("extreme 1h pump")
    elif change1 > 35:
        score -= 10
        risks.append("already extended")
    elif change1 < -25:
        score -= 18
        risks.append("sharp 1h decline")

    if change6 > 100:
        score -= 12
    elif change6 < -35:
        score -= 12

    if change24 > 250:
        score -= 15
    elif change24 < -60:
        score -= 15

    if mc and liq:
        liquidity_ratio = liq / mc

        if liquidity_ratio >= 0.20:
            score += 8
        elif liquidity_ratio >= 0.08:
            score += 4
        elif liquidity_ratio < 0.025:
            score -= 15
            risks.append("thin liquidity vs valuation")

    score = max(0, min(100, score))

    if score >= 82:
        label = "🔥 STRONG WATCH"
    elif score >= 70:
        label = "👀 WATCH"
    elif score >= 55:
        label = "⚠️ SPECULATIVE"
    else:
        label = "WEAK SETUP"

    return score, label, risks, positives


# =========================================================
# FILTER + RANK
# =========================================================

def get_ranked_candidates():
    candidates = get_solana_candidates()
    results = []

    for candidate in candidates:
        if candidate["market_cap"] < MIN_MARKET_CAP:
            continue

        if candidate["market_cap"] > MAX_MARKET_CAP:
            continue

        if candidate["liquidity"] < MIN_LIQUIDITY:
            continue

        if candidate["volume_24h"] < MIN_VOLUME_24H:
            continue

        if candidate["volume_1h"] < MIN_VOLUME_1H:
            continue

        if candidate["price"] <= 0:
            continue

        age = candidate["age_hours"]

        if age is not None and age > MAX_TOKEN_AGE_DAYS * 24:
            continue

        score, label, risks, positives = score_candidate(candidate)

        if score < MIN_SCORE:
            continue

        candidate["score"] = score
        candidate["label"] = label
        candidate["risks"] = risks
        candidate["positives"] = positives

        results.append(candidate)

    results.sort(
        key=lambda x: (
            x["score"],
            x["volume_1h"],
            x["liquidity"],
        ),
        reverse=True,
    )

    return results


# =========================================================
# PAPER BUY
# =========================================================

def paper_buy(candidate):
    global paper_balance

    if len(paper_positions) >= MAX_OPEN_POSITIONS:
        return None

    if paper_balance < PAPER_TRADE_SIZE:
        return None

    price = candidate["price"]

    if price <= 0:
        return None

    quantity = PAPER_TRADE_SIZE / price

    position = {
        "address": candidate["address"],
        "name": candidate["name"],
        "symbol": candidate["symbol"],
        "entry_price": price,
        "quantity": quantity,
        "cost": PAPER_TRADE_SIZE,
        "score": candidate["score"],
        "opened_at": time.time(),
    }

    paper_positions[candidate["address"]] = position

    paper_balance -= PAPER_TRADE_SIZE

    return position


# =========================================================
# PAPER SELL
# =========================================================

def paper_sell(address, current_price, reason):
    global paper_balance

    position = paper_positions.get(address)

    if not position:
        return None

    proceeds = position["quantity"] * current_price
    pnl = proceeds - position["cost"]

    pnl_percent = (
        pnl / position["cost"]
    ) * 100

    paper_balance += proceeds

    trade = {
        **position,
        "exit_price": current_price,
        "proceeds": proceeds,
        "pnl": pnl,
        "pnl_percent": pnl_percent,
        "reason": reason,
        "closed_at": time.time(),
    }

    paper_trade_history.append(trade)

    del paper_positions[address]

    return trade


# =========================================================
# PAPER POSITION MONITOR
# =========================================================

async def monitor_paper_positions(
    context: ContextTypes.DEFAULT_TYPE,
):
    if not paper_positions:
        return

    chat_id = context.job.chat_id

    for address in list(paper_positions.keys()):
        position = paper_positions.get(address)

        if not position:
            continue

        try:
            current_price = get_token_price(address)
        except Exception as error:
            print("Price-monitor error:", error)
            continue

        if current_price is None:
            continue

        entry = position["entry_price"]

        change_percent = (
            (current_price - entry) / entry
        ) * 100

        reason = None

        if change_percent >= TAKE_PROFIT_PERCENT:
            reason = "TAKE PROFIT"

        elif change_percent <= -STOP_LOSS_PERCENT:
            reason = "STOP LOSS"

        if reason:
            trade = paper_sell(
                address,
                current_price,
                reason,
            )

            if trade and chat_id:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"📤 PAPER SELL\n\n"
                        f"{trade['name']} "
                        f"({trade['symbol']})\n"
                        f"Reason: {trade['reason']}\n"
                        f"Entry: ${trade['entry_price']:.10f}\n"
                        f"Exit: ${trade['exit_price']:.10f}\n"
                        f"P/L: ${trade['pnl']:.2f} "
                        f"({trade['pnl_percent']:.2f}%)\n"
                        f"Paper balance: ${paper_balance:.2f}"
                    ),
                )


# =========================================================
# HUNT MESSAGE
# =========================================================

def build_hunt_message(results):
    top = results[:5]

    if not top:
        return (
            "🔎 JARVIS SOLANA HUNT\n\n"
            "No tokens currently meet Jarvis's filters."
        )

    lines = [
        "🚨 JARVIS SOLANA HUNT",
        "",
        "Paper-trading mode.",
        "",
    ]

    for i, c in enumerate(top, 1):
        lines.extend(
            [
                f"{i}. {c['name']} ({c['symbol']})",
                f"Score: {c['score']}/100",
                f"Signal: {c['label']}",
                f"Price: ${c['price']:.10f}",
                f"Market cap: ${c['market_cap']:,.0f}",
                f"Liquidity: ${c['liquidity']:,.0f}",
                f"24h volume: ${c['volume_24h']:,.0f}",
                f"1h volume: ${c['volume_1h']:,.0f}",
                f"Token: {c['address']}",
                "",
            ]
        )

    return "\n".join(lines)[:4000]


# =========================================================
# AUTOMATIC HUNT + PAPER ENTRY
# =========================================================

async def auto_hunt(
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = context.job.chat_id

    if chat_id is None:
        return

    try:
        results = get_ranked_candidates()

        await context.bot.send_message(
            chat_id=chat_id,
            text=build_hunt_message(results),
        )

        if paper_positions:
            return

        entry_candidates = [
            c
            for c in results
            if c["score"] >= PAPER_MIN_ENTRY_SCORE
        ]

        if not entry_candidates:
            return

        candidate = entry_candidates[0]

        position = paper_buy(candidate)

        if position:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"📥 PAPER BUY\n\n"
                    f"{position['name']} "
                    f"({position['symbol']})\n"
                    f"Score: {position['score']}/100\n"
                    f"Entry: ${position['entry_price']:.10f}\n"
                    f"Size: ${position['cost']:.2f}\n"
                    f"Take profit: +{TAKE_PROFIT_PERCENT:.1f}%\n"
                    f"Stop loss: -{STOP_LOSS_PERCENT:.1f}%\n"
                    f"Paper balance remaining: "
                    f"${paper_balance:.2f}"
                ),
            )

    except Exception as error:
        print("Auto-hunt error:", repr(error))


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    await update.message.reply_text(
        "🤖 Crypto Jarvis PAPER SCALPER online.\n\n"
        "Commands:\n"
        "/hunt\n"
        "/paper\n"
        "/positions\n"
        "/history\n"
        "/price bitcoin\n"
        "/analyse bitcoin"
    )

    hunt_name = f"auto_hunt_{chat_id}"
    monitor_name = f"paper_monitor_{chat_id}"

    if not context.job_queue.get_jobs_by_name(hunt_name):
        context.job_queue.run_repeating(
            auto_hunt,
            interval=AUTO_HUNT_INTERVAL,
            first=AUTO_HUNT_FIRST_SCAN,
            chat_id=chat_id,
            name=hunt_name,
        )

    if not context.job_queue.get_jobs_by_name(monitor_name):
        context.job_queue.run_repeating(
            monitor_paper_positions,
            interval=PAPER_MONITOR_INTERVAL,
            first=PAPER_MONITOR_INTERVAL,
            chat_id=chat_id,
            name=monitor_name,
        )

    await update.message.reply_text(
        "✅ Paper scalping activated.\n"
        "No real money will be used."
    )


async def paper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    total_pnl = sum(
        trade["pnl"]
        for trade in paper_trade_history
    )

    await update.message.reply_text(
        f"💵 PAPER ACCOUNT\n\n"
        f"Cash: ${paper_balance:.2f}\n"
        f"Open positions: {len(paper_positions)}\n"
        f"Closed trades: {len(paper_trade_history)}\n"
        f"Realized P/L: ${total_pnl:.2f}"
    )


async def positions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not paper_positions:
        await update.message.reply_text(
            "No paper positions are currently open."
        )
        return

    lines = ["📊 OPEN PAPER POSITIONS", ""]

    for position in paper_positions.values():
        try:
            current_price = get_token_price(
                position["address"]
            )
        except Exception:
            current_price = None

        if current_price:
            pnl_percent = (
                (current_price - position["entry_price"])
                / position["entry_price"]
            ) * 100
        else:
            pnl_percent = 0

        lines.extend(
            [
                f"{position['name']} ({position['symbol']})",
                f"Entry: ${position['entry_price']:.10f}",
                f"Current P/L: {pnl_percent:.2f}%",
                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not paper_trade_history:
        await update.message.reply_text(
            "No completed paper trades yet."
        )
        return

    lines = ["📚 PAPER TRADE HISTORY", ""]

    for trade in paper_trade_history[-10:]:
        lines.extend(
            [
                f"{trade['symbol']}: "
                f"{trade['pnl_percent']:+.2f}%",
                f"Exit: {trade['reason']}",
                "",
            ]
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def hunt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🔎 Jarvis is scanning..."
    )

    try:
        results = get_ranked_candidates()

        await update.message.reply_text(
            build_hunt_message(results)
        )

    except Exception as error:
        print("Hunt error:", repr(error))

        await update.message.reply_text(
            "Jarvis hunt encountered an error."
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
        data = get_coin(context.args[0])

        if not data:
            await update.message.reply_text(
                "Coin not found."
            )
            return

        await update.message.reply_text(
            f"🪙 {data['name']}\n\n"
            f"Price: ${data['current_price']:,}\n"
            f"Market cap: ${data['market_cap']:,}\n"
            f"24h volume: ${data['total_volume']:,}"
        )

    except Exception:
        await update.message.reply_text(
            "Market data unavailable."
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
        data = get_coin(context.args[0])

        if not data:
            await update.message.reply_text(
                "Coin not found."
            )
            return

        prompt = f"""
Analyze this cryptocurrency cautiously.

Name: {data['name']}
Price: {data['current_price']}
24h change: {data['price_change_percentage_24h']}
Market cap: {data['market_cap']}
24h volume: {data['total_volume']}

Discuss momentum, volume, and risks.
Do not guarantee profit.
"""

        response = client.responses.create(
            model="gpt-5-mini",
            input=prompt,
        )

        await update.message.reply_text(
            response.output_text
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
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("analyse", analyse))

    print("Crypto Jarvis paper scalper online")

    app.run_polling()


if __name__ == "__main__":
    main()
