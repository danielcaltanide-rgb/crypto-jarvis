import os
import time
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from openai import OpenAI


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)


# =========================================================
# SETTINGS
# =========================================================

AUTO_HUNT_INTERVAL = 300
AUTO_HUNT_FIRST_SCAN = 60

MIN_MARKET_CAP = 500_000
MAX_MARKET_CAP = 25_000_000

MIN_LIQUIDITY = 150_000
MIN_VOLUME_24H = 500_000
MIN_VOLUME_1H = 25_000

MIN_SCORE = 55

MAX_TOKEN_AGE_DAYS = 30

REQUEST_TIMEOUT = 15


# =========================================================
# HTTP HELPER
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
    """
    Build a broader candidate universe from multiple
    DexScreener discovery feeds.
    """

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
            print("Discovery feed error:", url, error)
            continue

        if not isinstance(items, list):
            continue

        for item in items:
            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")

            if not address:
                continue

            if address in seen:
                continue

            seen.add(address)
            addresses.append(address)

    return addresses


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def get_solana_candidates():
    """
    Fetch pools for discovered Solana token addresses.

    DexScreener supports up to 30 comma-separated
    token addresses per request.
    """

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

        if not isinstance(pairs, list):
            continue

        all_pairs.extend(pairs)

    # Keep the most liquid pool for each base token.
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

        existing_liquidity = (
            (existing.get("liquidity") or {}).get("usd")
            or 0
        )

        if liquidity > existing_liquidity:
            best_pairs[address] = pair

    candidates = []

    now_ms = int(time.time() * 1000)

    for address, pair in best_pairs.items():
        liquidity = (
            (pair.get("liquidity") or {}).get("usd")
            or 0
        )

        volume = pair.get("volume") or {}

        volume_24h = volume.get("h24") or 0
        volume_6h = volume.get("h6") or 0
        volume_1h = volume.get("h1") or 0

        txns = pair.get("txns") or {}

        txns_1h = txns.get("h1") or {}
        txns_6h = txns.get("h6") or {}

        buys_1h = txns_1h.get("buys") or 0
        sells_1h = txns_1h.get("sells") or 0

        buys_6h = txns_6h.get("buys") or 0
        sells_6h = txns_6h.get("sells") or 0

        price_change = pair.get("priceChange") or {}

        price_change_1h = price_change.get("h1") or 0
        price_change_6h = price_change.get("h6") or 0
        price_change_24h = price_change.get("h24") or 0

        market_cap = (
            pair.get("marketCap")
            or pair.get("fdv")
            or 0
        )

        pair_created_at = pair.get("pairCreatedAt")

        age_hours = None

        if pair_created_at:
            age_ms = max(
                0,
                now_ms - int(pair_created_at),
            )

            age_hours = age_ms / 3_600_000

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
                "age_hours": age_hours,
                "pair_url": pair.get("url") or "",
                "dex_id": pair.get("dexId") or "",
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

    age_hours = c["age_hours"]


    # -----------------------------------------------------
    # TOKEN AGE
    # -----------------------------------------------------

    if age_hours is not None:
        if age_hours < 1:
            score -= 12
            risks.append("extremely new pair")

        elif age_hours < 6:
            score += 3
            positives.append("very early")

        elif age_hours < 72:
            score += 7
            positives.append("early-stage pair")

        elif age_hours <= 24 * 14:
            score += 4


    # -----------------------------------------------------
    # MARKET CAP
    # -----------------------------------------------------

    if 1_000_000 <= mc <= 10_000_000:
        score += 20
        positives.append("strong market-cap zone")

    elif 500_000 <= mc <= 25_000_000:
        score += 12

    else:
        risks.append("market cap outside target")


    # -----------------------------------------------------
    # LIQUIDITY
    # -----------------------------------------------------

    if liq >= 1_000_000:
        score += 20
        positives.append("strong liquidity")

    elif liq >= 500_000:
        score += 16

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
        score += 14
        positives.append("very high volume")

    elif vol24 >= 5_000_000:
        score += 12

    elif vol24 >= 2_000_000:
        score += 9

    elif vol24 >= 500_000:
        score += 5

    else:
        risks.append("weak 24h volume")


    # -----------------------------------------------------
    # RECENT VOLUME ACCELERATION
    # -----------------------------------------------------

    expected_hourly = (
        vol24 / 24
        if vol24
        else 0
    )

    if expected_hourly:
        volume_ratio = vol1 / expected_hourly

        if volume_ratio >= 4:
            score += 18
            positives.append(
                "strong 1h volume acceleration"
            )

        elif volume_ratio >= 2:
            score += 13
            positives.append(
                "volume accelerating"
            )

        elif volume_ratio >= 1.25:
            score += 7

        elif volume_ratio < 0.35:
            score -= 8
            risks.append("1h volume fading")


    # -----------------------------------------------------
    # 1H BUY / SELL PRESSURE
    # -----------------------------------------------------

    if buys1 + sells1 >= 30:
        if sells1 == 0:
            score += 14
            positives.append(
                "very strong buy pressure"
            )

        else:
            ratio1 = buys1 / sells1

            if ratio1 >= 2.5:
                score += 14
                positives.append(
                    "strong buy pressure"
                )

            elif ratio1 >= 1.6:
                score += 11

            elif ratio1 >= 1.2:
                score += 6

            elif ratio1 < 0.75:
                score -= 10
                risks.append(
                    "sell pressure dominates"
                )


    # -----------------------------------------------------
    # 6H BUY / SELL PRESSURE
    # -----------------------------------------------------

    if buys6 + sells6 >= 50:
        if sells6 == 0:
            score += 5

        else:
            ratio6 = buys6 / sells6

            if ratio6 >= 1.5:
                score += 5

            elif ratio6 < 0.8:
                score -= 6
                risks.append(
                    "6h selling pressure"
                )


    # -----------------------------------------------------
    # PRICE ACTION
    # -----------------------------------------------------

    if 2 <= change1 <= 18:
        score += 9
        positives.append(
            "healthy short-term momentum"
        )

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

    elif change1 < -12:
        score -= 8
        risks.append(
            "weak short-term momentum"
        )


    if -5 <= change6 <= 35:
        score += 4

    elif change6 > 100:
        score -= 12
        risks.append("extreme 6h move")

    elif change6 < -35:
        score -= 12
        risks.append("heavy 6h decline")


    if change24 > 250:
        score -= 15
        risks.append("extreme 24h pump")

    elif change24 < -60:
        score -= 15
        risks.append("major 24h decline")


    # -----------------------------------------------------
    # LIQUIDITY / MARKET CAP RATIO
    # -----------------------------------------------------

    if mc and liq:
        liquidity_ratio = liq / mc

        if liquidity_ratio >= 0.20:
            score += 8
            positives.append(
                "healthy liquidity ratio"
            )

        elif liquidity_ratio >= 0.08:
            score += 4

        elif liquidity_ratio < 0.025:
            score -= 15
            risks.append(
                "thin liquidity vs valuation"
            )


    score = max(
        0,
        min(100, score),
    )

    if score >= 82:
        label = "🔥 STRONG WATCH"

    elif score >= 70:
        label = "👀 WATCH"

    elif score >= 55:
        label = "⚠️ SPECULATIVE"

    else:
        label = "WEAK SETUP"

    return (
        score,
        label,
        risks,
        positives,
    )


# =========================================================
# BUILD HUNT MESSAGE
# =========================================================

def build_hunt_message():
    candidates = get_solana_candidates()

    results = []

    for candidate in candidates:

        mc = candidate["market_cap"]
        liq = candidate["liquidity"]
        vol24 = candidate["volume_24h"]
        vol1 = candidate["volume_1h"]
        age = candidate["age_hours"]


        # -------------------------------------------------
        # HARD FILTERS
        # -------------------------------------------------

        if mc < MIN_MARKET_CAP:
            continue

        if mc > MAX_MARKET_CAP:
            continue

        if liq < MIN_LIQUIDITY:
            continue

        if vol24 < MIN_VOLUME_24H:
            continue

        if vol1 < MIN_VOLUME_1H:
            continue

        if age is not None:
            if age > MAX_TOKEN_AGE_DAYS * 24:
                continue


        (
            score,
            risk,
            risks,
            positives,
        ) = score_candidate(candidate)


        if score < MIN_SCORE:
            continue


        candidate["score"] = score
        candidate["risk"] = risk
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


    top = results[:5]


    if not top:
        return (
            "🔎 JARVIS SOLANA HUNT\n\n"
            "No tokens currently meet Jarvis's "
            "stronger filters.\n\n"
            "Jarvis will wait rather than force "
            "a weak setup."
        )


    lines = [
        "🚨 JARVIS SOLANA HUNT",
        "",
        "Broader discovery + strict screening.",
        "Screening only — not financial advice.",
        "",
    ]


    for i, c in enumerate(top, 1):

        sells = c["sells_1h"]

        if sells:
            pressure = (
                c["buys_1h"] / sells
            )

            pressure_text = (
                f"{pressure:.2f}x"
            )

        elif c["buys_1h"]:
            pressure_text = "buy-heavy"

        else:
            pressure_text = "N/A"


        age_hours = c["age_hours"]

        if age_hours is None:
            age_text = "Unknown"

        elif age_hours < 24:
            age_text = (
                f"{age_hours:.1f} hours"
            )

        else:
            age_text = (
                f"{age_hours / 24:.1f} days"
            )


        risks = (
            ", ".join(c["risks"][:3])
            if c["risks"]
            else "none from current screen"
        )


        strengths = (
            ", ".join(
                c["positives"][:3]
            )
            if c["positives"]
            else "none"
        )


        lines.extend(
            [
                f"{i}. {c['name']} ({c['symbol']})",
                f"Jarvis Score: {c['score']}/100",
                f"Signal: {c['risk']}",
                f"Age: {age_text}",
                f"DEX: {c['dex_id']}",
                f"Market cap: ${c['market_cap']:,.0f}",
                f"Liquidity: ${c['liquidity']:,.0f}",
                f"24h volume: ${c['volume_24h']:,.0f}",
                f"6h volume: ${c['volume_6h']:,.0f}",
                f"1h volume: ${c['volume_1h']:,.0f}",
                f"1h buy pressure: {pressure_text}",
                f"1h change: {c['price_change_1h']:.1f}%",
                f"6h change: {c['price_change_6h']:.1f}%",
                f"24h change: {c['price_change_24h']:.1f}%",
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

async def auto_hunt(
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = context.job.chat_id

    if chat_id is None:
        return

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔎 Jarvis automatic "
                "scan starting..."
            ),
        )

        message = build_hunt_message()

        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
        )

    except Exception as error:
        print(
            "Auto-hunt error:",
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
        "🤖 Crypto Jarvis online.\n\n"
        "Commands:\n"
        "/price bitcoin\n"
        "/analyse bitcoin\n"
        "/hunt"
    )


    job_name = (
        f"auto_hunt_{chat_id}"
    )


    existing_jobs = (
        context.job_queue
        .get_jobs_by_name(job_name)
    )


    if not existing_jobs:
        context.job_queue.run_repeating(
            auto_hunt,
            interval=AUTO_HUNT_INTERVAL,
            first=AUTO_HUNT_FIRST_SCAN,
            chat_id=chat_id,
            name=job_name,
        )

        await update.message.reply_text(
            "✅ Automatic hunt activated.\n"
            "First automatic scan in about "
            "1 minute.\n"
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
            "Market data is temporarily "
            "unavailable."
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
        f"Market cap: "
        f"${data['market_cap']:,}\n"
        f"24h volume: "
        f"${data['total_volume']:,}"
    )


    await update.message.reply_text(
        text
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


    coin = context.args[0]


    try:
        data = get_coin(coin)

    except requests.RequestException:
        await update.message.reply_text(
            "Market data is temporarily "
            "unavailable."
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
        response = (
            client.responses.create(
                model="gpt-5-mini",
                input=prompt,
            )
        )


        await update.message.reply_text(
            "🧠 JARVIS ANALYSIS\n\n"
            + response.output_text
        )


    except Exception as error:
        print(
            "Analysis error:",
            repr(error),
        )

        await update.message.reply_text(
            "Jarvis AI analysis is "
            "temporarily unavailable."
        )


async def hunt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🔎 Jarvis is scanning a broader "
        "Solana candidate pool..."
    )


    try:
        message = build_hunt_message()

        await update.message.reply_text(
            message
        )


    except requests.RequestException:
        await update.message.reply_text(
            "DEX market data is temporarily "
            "unavailable."
        )


    except Exception as error:
        print(
            "Hunt error:",
            repr(error),
        )

        await update.message.reply_text(
            "Jarvis hunt encountered "
            "an error."
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
        CommandHandler(
            "start",
            start,
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


    app.add_handler(
        CommandHandler(
            "hunt",
            hunt,
        )
    )


    print(
        "Crypto Jarvis online"
    )


    app.run_polling()


if __name__ == "__main__":
    main()
