import os
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from openai import OpenAI


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)


# =========================================================
# COINGECKO
# =========================================================

def get_coin(coin):
    url = "https://api.coingecko.com/api/v3/coins/markets"

    params = {
        "vs_currency": "usd",
        "ids": coin.lower(),
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()

    if not data:
        return None

    return data[0]


# =========================================================
# DEXSCREENER SOLANA HUNTER
# =========================================================

def get_solana_candidates():
    url = "https://api.dexscreener.com/token-boosts/top/v1"

    response = requests.get(url, timeout=15)
    response.raise_for_status()

    boosted = response.json()

    candidates = []
    seen = set()

    for token in boosted[:80]:
        if token.get("chainId") != "solana":
            continue

        address = token.get("tokenAddress")

        if not address or address in seen:
            continue

        seen.add(address)

        pair_url = (
            "https://api.dexscreener.com/token-pairs/v1/"
            f"solana/{address}"
        )

        try:
            pair_response = requests.get(pair_url, timeout=15)
            pair_response.raise_for_status()
            pairs = pair_response.json()
        except requests.RequestException:
            continue

        if not pairs:
            continue

        pair = max(
            pairs,
            key=lambda x: (x.get("liquidity") or {}).get("usd") or 0,
        )

        liquidity = (pair.get("liquidity") or {}).get("usd") or 0
        volume_24h = (pair.get("volume") or {}).get("h24") or 0
        volume_6h = (pair.get("volume") or {}).get("h6") or 0
        volume_1h = (pair.get("volume") or {}).get("h1") or 0

        txns_1h = (pair.get("txns") or {}).get("h1") or {}
        buys_1h = txns_1h.get("buys") or 0
        sells_1h = txns_1h.get("sells") or 0

        txns_6h = (pair.get("txns") or {}).get("h6") or {}
        buys_6h = txns_6h.get("buys") or 0
        sells_6h = txns_6h.get("sells") or 0

        market_cap = pair.get("marketCap") or pair.get("fdv") or 0

        price_change = pair.get("priceChange") or {}

        price_change_1h = price_change.get("h1") or 0
        price_change_6h = price_change.get("h6") or 0
        price_change_24h = price_change.get("h24") or 0

        base = pair.get("baseToken") or {}

        candidates.append(
            {
                "name": base.get("name") or "Unknown",
                "symbol": base.get("symbol") or "?",
                "address": address,
                "market_cap": market_cap,
                "liquidity": liquidity,
                "volume_24h": volume_24h,
                "volume_6h": volume_6h,
                "volume_1h": volume_1h,
                "buys_1h": buys_1h,
                "sells_1h": sells_1h,
                "buys_6h": buys_6h,
                "sells_6h": sells_6h,
                "price_change_1h": price_change_1h,
                "price_change_6h": price_change_6h,
                "price_change_24h": price_change_24h,
                "pair_url": pair.get("url") or "",
            }
        )

    return candidates


# =========================================================
# JARVIS SCORING
# =========================================================

def score_candidate(c):
    score = 0
    risks = []
    positives = []

    mc = c["market_cap"]
    liq = c["liquidity"]
    vol24 = c["volume_24h"]
    vol6 = c["volume_6h"]
    vol1 = c["volume_1h"]

    buys1 = c["buys_1h"]
    sells1 = c["sells_1h"]

    buys6 = c["buys_6h"]
    sells6 = c["sells_6h"]

    change1 = c["price_change_1h"]
    change6 = c["price_change_6h"]
    change24 = c["price_change_24h"]

    # -----------------------------------------------------
    # MARKET CAP
    # -----------------------------------------------------

    if 1_000_000 <= mc <= 10_000_000:
        score += 22
        positives.append("strong market-cap zone")

    elif 500_000 <= mc <= 25_000_000:
        score += 14

    else:
        risks.append("market cap outside target")

    # -----------------------------------------------------
    # LIQUIDITY
    # -----------------------------------------------------

    if liq >= 1_000_000:
        score += 22
        positives.append("strong liquidity")

    elif liq >= 500_000:
        score += 17

    elif liq >= 250_000:
        score += 11

    elif liq >= 150_000:
        score += 6

    else:
        risks.append("low liquidity")

    # -----------------------------------------------------
    # 24H VOLUME
    # -----------------------------------------------------

    if vol24 >= 10_000_000:
        score += 16
        positives.append("very high volume")

    elif vol24 >= 5_000_000:
        score += 13

    elif vol24 >= 2_000_000:
        score += 10

    elif vol24 >= 500_000:
        score += 5

    else:
        risks.append("weak 24h volume")

    # -----------------------------------------------------
    # RECENT VOLUME ACCELERATION
    # -----------------------------------------------------

    expected_hourly = vol24 / 24 if vol24 else 0

    if expected_hourly:
        if vol1 >= expected_hourly * 4:
            score += 20
            positives.append("strong 1h volume acceleration")

        elif vol1 >= expected_hourly * 2:
            score += 14

        elif vol1 >= expected_hourly * 1.25:
            score += 8

        elif vol1 < expected_hourly * 0.35:
            score -= 8
            risks.append("1h volume fading")

    # -----------------------------------------------------
    # BUY PRESSURE 1H
    # -----------------------------------------------------

    if buys1 >= 25:
        if sells1 == 0:
            score += 16
            positives.append("very strong buy pressure")

        else:
            ratio1 = buys1 / sells1

            if ratio1 >= 2.5:
                score += 16
                positives.append("strong buy pressure")

            elif ratio1 >= 1.6:
                score += 12

            elif ratio1 >= 1.2:
                score += 7

            elif ratio1 < 0.75:
                score -= 10
                risks.append("sell pressure dominates")

    # -----------------------------------------------------
    # BUY PRESSURE 6H
    # -----------------------------------------------------

    if buys6 and sells6:
        ratio6 = buys6 / sells6

        if ratio6 >= 1.5:
            score += 6

        elif ratio6 < 0.8:
            score -= 6
            risks.append("6h selling pressure")

    # -----------------------------------------------------
    # PRICE ACTION
    # -----------------------------------------------------

    if 2 <= change1 <= 18:
        score += 10
        positives.append("healthy short-term momentum")

    elif 18 < change1 <= 35:
        score += 5

    elif change1 > 60:
        score -= 20
        risks.append("extreme 1h pump")

    elif change1 > 35:
        score -= 10
        risks.append("already extended")

    elif change1 < -25:
        score -= 18
        risks.append("sharp 1h decline")

    elif change1 < -12:
        score -= 8
        risks.append("weak short-term momentum")

    # 6h context
    if -5 <= change6 <= 35:
        score += 5

    elif change6 > 100:
        score -= 12
        risks.append("extreme 6h move")

    elif change6 < -35:
        score -= 12
        risks.append("heavy 6h decline")

    # 24h extreme move penalty
    if change24 > 250:
        score -= 15
        risks.append("extreme 24h pump")

    elif change24 < -60:
        score -= 15
        risks.append("major 24h decline")

    # -----------------------------------------------------
    # LIQUIDITY / VALUATION
    # -----------------------------------------------------

    if mc and liq:
        liquidity_ratio = liq / mc

        if liquidity_ratio >= 0.20:
            score += 8
            positives.append("healthy liquidity ratio")

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
# BUILD HUNT MESSAGE
# =========================================================

def build_hunt_message():
    candidates = get_solana_candidates()

    results = []

    for candidate in candidates:

        # -------------------------------------------------
        # HARD FILTERS
        # -------------------------------------------------

        if candidate["market_cap"] < 500_000:
            continue

        if candidate["market_cap"] > 25_000_000:
            continue

        if candidate["liquidity"] < 150_000:
            continue

        if candidate["volume_24h"] < 500_000:
            continue

        if candidate["volume_1h"] < 25_000:
            continue

        score, risk, risks, positives = score_candidate(candidate)

        # Do not show weak candidates
        if score < 55:
            continue

        candidate["score"] = score
        candidate["risk"] = risk
        candidate["risks"] = risks
        candidate["positives"] = positives

        results.append(candidate)

    results.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    top = results[:5]

    if not top:
        return (
            "🔎 JARVIS SOLANA HUNT\n\n"
            "No tokens currently meet Jarvis's stronger filters.\n\n"
            "That is a valid result — Jarvis will wait rather than "
            "force a weak setup."
        )

    lines = [
        "🚨 JARVIS SOLANA HUNT",
        "",
        "Higher-quality screening mode.",
        "Screening only — not financial advice.",
        "",
    ]

    for i, c in enumerate(top, 1):

        sells = c["sells_1h"]

        if sells:
            pressure = c["buys_1h"] / sells
            pressure_text = f"{pressure:.2f}x"

        elif c["buys_1h"]:
            pressure_text = "buy-heavy"

        else:
            pressure_text = "N/A"

        risks = (
            ", ".join(c["risks"][:3])
            if c["risks"]
            else "none from current screen"
        )

        strengths = (
            ", ".join(c["positives"][:3])
            if c["positives"]
            else "none"
        )

        lines.extend(
            [
                f"{i}. {c['name']} ({c['symbol']})",
                f"Jarvis Score: {c['score']}/100",
                f"Signal: {c['risk']}",
                f"Market cap: ${c['market_cap']:,.0f}",
                f"Liquidity: ${c['liquidity']:,.0f}",
                f"24h volume: ${c['volume_24h']:,.0f}",
                f"6h volume: ${c['volume_6h']:,.0f}",
                f"1h volume: ${c['volume_1h']:,.0f}",
                f"1h buy pressure: {pressure_text}",
                f"1h price change: {c['price_change_1h']:.1f}%",
                f"6h price change: {c['price_change_6h']:.1f}%",
                f"24h price change: {c['price_change_24h']:.1f}%",
                f"Strengths: {strengths}",
                f"Flags: {risks}",
                f"Token: {c['address']}",
                "",
            ]
        )

    return "\n".join(lines)[:4000]


# =========================================================
# AUTOMATIC HUNT
# =========================================================

async def auto_hunt(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id

    if chat_id is None:
        return

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔎 Jarvis automatic scan starting...",
        )

        message = build_hunt_message()

        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
        )

    except Exception as error:
        print("Auto-hunt error:", error)


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    await update.message.reply_text(
        "🤖 Crypto Jarvis online.\n\n"
        "Commands:\n"
        "/price bitcoin\n"
        "/analyse bitcoin\n"
        "/hunt"
    )

    job_name = f"auto_hunt_{chat_id}"

    existing_jobs = context.job_queue.get_jobs_by_name(job_name)

    if not existing_jobs:
        context.job_queue.run_repeating(
            auto_hunt,
            interval=300,
            first=60,
            chat_id=chat_id,
            name=job_name,
        )

        await update.message.reply_text(
            "✅ Automatic hunt activated.\n"
            "First automatic scan in about 1 minute.\n"
            "Then every 5 minutes."
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

    coin = context.args[0]

    try:
        data = get_coin(coin)

    except requests.RequestException:
        await update.message.reply_text(
            "Market data is temporarily unavailable."
        )
        return

    if not data:
        await update.message.reply_text(
            "Coin not found."
        )
        return

    change = data.get(
        "price_change_percentage_24h"
    )

    change_text = (
        f"{change:.2f}%"
        if change is not None
        else "N/A"
    )

    text = (
        f"🪙 {data['name']} "
        f"({data['symbol'].upper()})\n\n"
        f"Price: ${data['current_price']:,}\n"
        f"24h change: {change_text}\n"
        f"Market cap: ${data['market_cap']:,}\n"
        f"24h volume: ${data['total_volume']:,}"
    )

    await update.message.reply_text(text)


async def analyse(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.args:
        await update.message.reply_text(
            "Try: /analyse bitcoin"
        )
        return

    coin = context.args[0]

    try:
        data = get_coin(coin)

    except requests.RequestException:
        await update.message.reply_text(
            "Market data is temporarily unavailable."
        )
        return

    if not data:
        await update.message.reply_text(
            "Coin not found."
        )
        return

    prompt = f"""
You are Crypto Jarvis, a cautious crypto analyst.

Coin: {data['name']}
Price: ${data['current_price']}
24h change: {data['price_change_percentage_24h']}%
Market cap: ${data['market_cap']}
24h volume: ${data['total_volume']}

Give a concise analysis covering:

1. Momentum
2. Volume
3. Risk
4. What would strengthen the setup
5. What would invalidate the setup

Never guarantee profits.
Never claim a coin will 10x.
Use only the supplied market data.
"""

    try:
        response = client.responses.create(
            model="gpt-5-mini",
            input=prompt,
        )

        await update.message.reply_text(
            "🧠 JARVIS ANALYSIS\n\n"
            + response.output_text
        )

    except Exception as error:
        print("Analysis error:", error)

        await update.message.reply_text(
            "Jarvis AI analysis is temporarily unavailable."
        )


async def hunt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🔎 Jarvis is scanning Solana meme coins..."
    )

    try:
        message = build_hunt_message()

        await update.message.reply_text(message)

    except requests.RequestException:
        await update.message.reply_text(
            "DEX market data is temporarily unavailable."
        )

    except Exception as error:
        print("Hunt error:", error)

        await update.message.reply_text(
            "Jarvis hunt encountered an error."
        )


# =========================================================
# RUN BOT
# =========================================================

def main():
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("price", price)
    )

    app.add_handler(
        CommandHandler("analyse", analyse)
    )

    app.add_handler(
        CommandHandler("hunt", hunt)
    )

    print("Crypto Jarvis online")

    app.run_polling()


if __name__ == "__main__":
    main()
