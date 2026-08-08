import os
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from openai import OpenAI

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

# -------------------------
# COINGECKO
# -------------------------

def get_coin(coin):
    url = "https://api.coingecko.com/api/v3/coins/markets"

    params = {
        "vs_currency": "usd",
        "ids": coin.lower()
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()

    if not data:
        return None

    return data[0]


# -------------------------
# DEXSCREENER SOLANA HUNTER
# -------------------------

def get_solana_candidates():
    """
    Pull currently boosted/trending tokens from DexScreener,
    then inspect their Solana trading pairs.
    """

    url = "https://api.dexscreener.com/token-boosts/top/v1"

    response = requests.get(url, timeout=15)
    response.raise_for_status()

    boosted = response.json()

    candidates = []
    seen = set()

    for token in boosted[:60]:

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

        # Prefer the pool with the most USD liquidity.
        pair = max(
            pairs,
            key=lambda x: (x.get("liquidity") or {}).get("usd") or 0
        )

        liquidity = (pair.get("liquidity") or {}).get("usd") or 0
        volume_24h = (pair.get("volume") or {}).get("h24") or 0
        volume_1h = (pair.get("volume") or {}).get("h1") or 0

        txns_1h = (pair.get("txns") or {}).get("h1") or {}
        buys_1h = txns_1h.get("buys") or 0
        sells_1h = txns_1h.get("sells") or 0

        market_cap = pair.get("marketCap") or pair.get("fdv") or 0
        price_change_1h = (
            (pair.get("priceChange") or {}).get("h1") or 0
        )

        base = pair.get("baseToken") or {}

        candidates.append({
            "name": base.get("name") or "Unknown",
            "symbol": base.get("symbol") or "?",
            "address": address,
            "price": pair.get("priceUsd") or "0",
            "market_cap": market_cap,
            "liquidity": liquidity,
            "volume_24h": volume_24h,
            "volume_1h": volume_1h,
            "buys_1h": buys_1h,
            "sells_1h": sells_1h,
            "price_change_1h": price_change_1h,
            "url": pair.get("url") or ""
        })

    return candidates


def score_candidate(c):
    """
    Jarvis screening score.
    0-100 means how strongly a token matches our setup.
    It is NOT a probability of making money or doing a 10x.
    """

    score = 0
    risks = []

    mc = c["market_cap"]
    liq = c["liquidity"]
    vol24 = c["volume_24h"]
    vol1 = c["volume_1h"]
    buys = c["buys_1h"]
    sells = c["sells_1h"]
    change1 = c["price_change_1h"]

    # Market-cap sweet spot
    if 1_000_000 <= mc <= 20_000_000:
        score += 20
    elif 500_000 <= mc <= 50_000_000:
        score += 12
    else:
        risks.append("market cap outside target")

    # Liquidity
    if liq >= 1_000_000:
        score += 20
    elif liq >= 500_000:
        score += 15
    elif liq >= 200_000:
        score += 8
    else:
        risks.append("low liquidity")

    # 24h volume
    if vol24 >= 5_000_000:
        score += 15
    elif vol24 >= 1_000_000:
        score += 10
    elif vol24 >= 250_000:
        score += 5

    # Recent volume acceleration
    expected_hourly = vol24 / 24 if vol24 else 0

    if expected_hourly and vol1 >= expected_hourly * 3:
        score += 20
    elif expected_hourly and vol1 >= expected_hourly * 1.5:
        score += 12

    # Buy pressure
    if buys >= 20:
        if sells == 0:
            score += 15
        elif buys / sells >= 2:
            score += 15
        elif buys / sells >= 1.3:
            score += 10

    # Avoid chasing huge immediate pumps
    if 0 <= change1 <= 20:
        score += 10
    elif change1 > 50:
        score -= 15
        risks.append("already pumping hard")
    elif change1 < -20:
        score -= 10
        risks.append("sharp short-term decline")

    # Basic liquidity/valuation sanity check
    if mc and liq:
        liquidity_ratio = liq / mc

        if liquidity_ratio >= 0.10:
            score += 5
        elif liquidity_ratio < 0.02:
            score -= 10
            risks.append("thin liquidity vs valuation")

    score = max(0, min(100, score))

    if score >= 80:
        risk_label = "HIGH INTEREST / HIGH RISK"
    elif score >= 60:
        risk_label = "WATCH"
    else:
        risk_label = "WEAK SETUP"

    return score, risk_label, risks


# -------------------------
# TELEGRAM COMMANDS
# -------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Crypto Jarvis online.\n\n"
        "Commands:\n"
        "/price bitcoin\n"
        "/analyse bitcoin\n"
        "/hunt"
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text("Try: /price bitcoin")
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
        await update.message.reply_text("Coin not found.")
        return

    change = data.get("price_change_percentage_24h")

    change_text = (
        f"{change:.2f}%"
        if change is not None
        else "N/A"
    )

    text = (
        f"🪙 {data['name']} ({data['symbol'].upper()})\n\n"
        f"Price: ${data['current_price']:,}\n"
        f"24h change: {change_text}\n"
        f"Market cap: ${data['market_cap']:,}\n"
        f"24h volume: ${data['total_volume']:,}"
    )

    await update.message.reply_text(text)


async def analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text("Try: /analyse bitcoin")
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
        await update.message.reply_text("Coin not found.")
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
            input=prompt
        )

        await update.message.reply_text(
            "🧠 JARVIS ANALYSIS\n\n"
            + response.output_text
        )

    except Exception:
        await update.message.reply_text(
            "Jarvis AI analysis is temporarily unavailable."
        )


async def hunt(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🔎 Jarvis is scanning Solana meme coins..."
    )

    try:
        candidates = get_solana_candidates()
    except requests.RequestException:
        await update.message.reply_text(
            "DEX market data is temporarily unavailable."
        )
        return

    results = []

    for candidate in candidates:

        # Hard filters before scoring
        if candidate["market_cap"] < 100_000:
            continue

        if candidate["market_cap"] > 50_000_000:
            continue

        if candidate["liquidity"] < 50_000:
            continue

        if candidate["volume_24h"] < 100_000:
            continue

        score, risk, risks = score_candidate(candidate)

        candidate["score"] = score
        candidate["risk"] = risk
        candidate["risks"] = risks

        results.append(candidate)

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    top = results[:5]

    if not top:
        await update.message.reply_text(
            "No Solana tokens currently match "
            "Jarvis's minimum filters."
        )
        return

    lines = [
        "🚨 JARVIS SOLANA HUNT",
        "",
        "Screening only — not a prediction of a 10x.",
        ""
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
            ", ".join(c["risks"][:2])
            if c["risks"]
            else "none from basic screen"
        )

        lines.extend([
            f"{i}. {c['name']} ({c['symbol']})",
            f"Jarvis Score: {c['score']}/100",
            f"Signal: {c['risk']}",
            f"Market cap: ${c['market_cap']:,.0f}",
            f"Liquidity: ${c['liquidity']:,.0f}",
            f"24h volume: ${c['volume_24h']:,.0f}",
            f"1h volume: ${c['volume_1h']:,.0f}",
            f"1h buy pressure: {pressure_text}",
            f"1h price change: {c['price_change_1h']:.1f}%",
            f"Flags: {risks}",
            f"Token: {c['address']}",
            ""
        ])

    message = "\n".join(lines)

    # Telegram has a message-size limit.
    await update.message.reply_text(message[:4000])


async def auto_hunt(context: ContextTypes.DEFAULT_TYPE):
    # Scan automatically, but do NOT place trades.
    # Keep execution manual until the scanner is tested.
         bot = context.bot
    chat_id = context.job.chat_id

    await bot.send_message(
        chat_id=chat_id,
        text="🔎 Jarvis auto-hunt scan running..."
    )   

# -------------------------
# RUN BOT
# -------------------------

def main():

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("analyse", analyse))
    app.add_handler(CommandHandler("hunt", hunt))
    app.job_queue.run_repeating(auto_hunt, interval=300, first=60)

    print("Crypto Jarvis online")

    app.run_polling()


if __name__ == "__main__":
    main()
