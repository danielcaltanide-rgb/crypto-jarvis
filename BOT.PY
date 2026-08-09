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

# Check open paper positions every 30 seconds
PAPER_MONITOR_INTERVAL = 30


# =========================================================
# PAPER TRADING SETTINGS
# =========================================================

PAPER_STARTING_BALANCE = 1000.00
PAPER_TRADE_SIZE = 50.00

# Softer threshold while TESTING paper trading
PAPER_MIN_ENTRY_SCORE = 55

TAKE_PROFIT_PERCENT = 5.0
STOP_LOSS_PERCENT = 2.0

# Close stale scalps after 60 minutes
MAX_HOLD_MINUTES = 60

MAX_OPEN_POSITIONS = 1

paper_balance = PAPER_STARTING_BALANCE
paper_positions = {}
paper_trade_history = []


# =========================================================
# PAPER-MODE SCANNER FILTERS
# =========================================================

# These are intentionally softer than before so we can
# actually test simulated entries/exits.

MIN_MARKET_CAP = 250_000
MAX_MARKET_CAP = 30_000_000

MIN_LIQUIDITY = 75_000
MIN_VOLUME_24H = 250_000
MIN_VOLUME_1H = 10_000

MIN_SCAN_SCORE = 45

MAX_TOKEN_AGE_DAYS = 30


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
    """
    Build a broader Solana candidate list from several
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
            print("Discovery feed error:", repr(error))
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
    addresses = collect_solana_token_addresses()

    if not addresses:
        return []

    all_pairs = []

    # DexScreener accepts batches of token addresses.
    for batch in chunked(addresses, 30):
        joined = ",".join(batch)

        url = (
            "https://api.dexscreener.com/tokens/v1/"
            f"solana/{joined}"
        )

        try:
            pairs = get_json(url)

        except requests.RequestException as error:
            print("Pair lookup error:", repr(error))
            continue

        if isinstance(pairs, list):
            all_pairs.extend(pairs)

    # Keep the most liquid pool for each token.
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
        base = pair.get("baseToken") or {}

        volume = pair.get("volume") or {}
        txns = pair.get("txns") or {}
        change = pair.get("priceChange") or {}

        txns_1h = txns.get("h1") or {}
        txns_6h = txns.get("h6") or {}

        pair_created_at = pair.get("pairCreatedAt")

        age_hours = None

        if pair_created_at:
            try:
                age_hours = max(
                    0,
                    now_ms - int(pair_created_at),
                ) / 3_600_000

            except (TypeError, ValueError):
                age_hours = None

        try:
            price = float(pair.get("priceUsd") or 0)

        except (TypeError, ValueError):
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
                "volume_6h": volume.get("h6") or 0,
                "volume_1h": volume.get("h1") or 0,

                "buys_1h": txns_1h.get("buys") or 0,
                "sells_1h": txns_1h.get("sells") or 0,

                "buys_6h": txns_6h.get("buys") or 0,
                "sells_6h": txns_6h.get("sells") or 0,

                "price_change_1h": change.get("h1") or 0,
                "price_change_6h": change.get("h6") or 0,
                "price_change_24h": change.get("h24") or 0,

                "age_hours": age_hours,
                "dex_id": pair.get("dexId") or "",
                "pair_url": pair.get("url") or "",
            }
        )

    return candidates


# =========================================================
# CURRENT TOKEN PRICE
# =========================================================

def get_token_price(address):
    url = (
        "https://api.dexscreener.com/token-pairs/v1/"
        f"solana/{address}"
    )

    pairs = get_json(url)

    if not isinstance(pairs, list):
        return None

    if not pairs:
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

    except (TypeError, ValueError):
        return None


# =========================================================
# SCORING
# =========================================================

def score_candidate(c):
    score = 0
    positives = []
    risks = []

    mc = c["market_cap"]
    liq = c["liquidity"]

    vol24 = c["volume_24h"]
    vol1 = c["volume_1h"]

    buys1 = c["buys_1h"]
    sells1 = c["sells_1h"]

    change1 = c["price_change_1h"]
    change6 = c["price_change_6h"]
    change24 = c["price_change_24h"]

    age = c["age_hours"]


    # -----------------------------------------------------
    # AGE
    # -----------------------------------------------------

    if age is not None:
        if age < 1:
            score -= 10
            risks.append("extremely new")

        elif age <= 72:
            score += 8
            positives.append("early-stage")

        elif age <= 14 * 24:
            score += 4


    # -----------------------------------------------------
    # MARKET CAP
    # -----------------------------------------------------

    if 750_000 <= mc <= 10_000_000:
        score += 20
        positives.append("good market-cap zone")

    elif 250_000 <= mc <= 30_000_000:
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
        score += 12

    elif liq >= 100_000:
        score += 8

    elif liq >= 75_000:
        score += 4

    else:
        risks.append("low liquidity")


    # -----------------------------------------------------
    # 24H VOLUME
    # -----------------------------------------------------

    if vol24 >= 10_000_000:
        score += 15

    elif vol24 >= 5_000_000:
        score += 12

    elif vol24 >= 1_000_000:
        score += 9

    elif vol24 >= 250_000:
        score += 5


    # -----------------------------------------------------
    # VOLUME ACCELERATION
    # -----------------------------------------------------

    expected_hourly = vol24 / 24 if vol24 else 0

    if expected_hourly > 0:
        acceleration = vol1 / expected_hourly

        if acceleration >= 4:
            score += 18
            positives.append("strong volume acceleration")

        elif acceleration >= 2:
            score += 13
            positives.append("volume accelerating")

        elif acceleration >= 1.2:
            score += 7

        elif acceleration < 0.35:
            score -= 6
            risks.append("volume fading")


    # -----------------------------------------------------
    # BUY PRESSURE
    # -----------------------------------------------------

    trades1 = buys1 + sells1

    if trades1 >= 20:
        if sells1 == 0:
            score += 12
            positives.append("strong buy pressure")

        else:
            ratio = buys1 / sells1

            if ratio >= 2:
                score += 14
                positives.append("strong buy pressure")

            elif ratio >= 1.4:
                score += 10

            elif ratio >= 1.1:
                score += 5

            elif ratio < 0.70:
                score -= 10
                risks.append("selling pressure")


    # -----------------------------------------------------
    # SHORT-TERM PRICE ACTION
    # -----------------------------------------------------

    if 1 <= change1 <= 15:
        score += 10
        positives.append("healthy momentum")

    elif 15 < change1 <= 30:
        score += 5

    elif change1 > 60:
        score -= 20
        risks.append("extreme 1h pump")

    elif change1 > 35:
        score -= 10
        risks.append("price extended")

    elif change1 < -20:
        score -= 15
        risks.append("sharp decline")

    elif change1 < -10:
        score -= 6
        risks.append("weak momentum")


    # -----------------------------------------------------
    # BIGGER-TIMEFRAME EXTREMES
    # -----------------------------------------------------

    if change6 > 120:
        score -= 10
        risks.append("extreme 6h pump")

    if change24 > 300:
        score -= 15
        risks.append("extreme 24h pump")

    if change24 < -60:
        score -= 15
        risks.append("major 24h decline")


    # -----------------------------------------------------
    # LIQUIDITY / VALUE
    # -----------------------------------------------------

    if mc and liq:
        liquidity_ratio = liq / mc

        if liquidity_ratio >= 0.20:
            score += 8
            positives.append("healthy liquidity ratio")

        elif liquidity_ratio >= 0.08:
            score += 4

        elif liquidity_ratio < 0.02:
            score -= 12
            risks.append("thin liquidity vs valuation")


    score = max(0, min(100, score))


    if score >= 80:
        label = "🔥 STRONG WATCH"

    elif score >= 65:
        label = "👀 WATCH"

    elif score >= 45:
        label = "⚠️ SPECULATIVE"

    else:
        label = "WEAK"


    return score, label, positives, risks


# =========================================================
# FILTER + RANK
# =========================================================

def get_ranked_candidates():
    candidates = get_solana_candidates()

    results = []

    for c in candidates:
        if c["price"] <= 0:
            continue

        if c["market_cap"] < MIN_MARKET_CAP:
            continue

        if c["market_cap"] > MAX_MARKET_CAP:
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

        score, label, positives, risks = score_candidate(c)

        if score < MIN_SCAN_SCORE:
            continue

        c["score"] = score
        c["label"] = label
        c["positives"] = positives
        c["risks"] = risks

        results.append(c)


    results.sort(
        key=lambda c: (
            c["score"],
            c["volume_1h"],
            c["liquidity"],
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

    address = candidate["address"]

    if address in paper_positions:
        return None

    entry_price = candidate["price"]

    if entry_price <= 0:
        return None

    quantity = PAPER_TRADE_SIZE / entry_price

    position = {
        "address": address,
        "name": candidate["name"],
        "symbol": candidate["symbol"],
        "entry_price": entry_price,
        "quantity": quantity,
        "cost": PAPER_TRADE_SIZE,
        "score": candidate["score"],
        "opened_at": time.time(),
    }

    paper_positions[address] = position

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

    proceeds = (
        position["quantity"]
        * current_price
    )

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
            print(
                "Paper monitor price error:",
                repr(error),
            )
            continue

        if not current_price:
            continue

        entry = position["entry_price"]

        pnl_percent = (
            (current_price - entry)
            / entry
        ) * 100

        age_minutes = (
            time.time()
            - position["opened_at"]
        ) / 60

        exit_reason = None

        if pnl_percent >= TAKE_PROFIT_PERCENT:
            exit_reason = "TAKE PROFIT"

        elif pnl_percent <= -STOP_LOSS_PERCENT:
            exit_reason = "STOP LOSS"

        elif age_minutes >= MAX_HOLD_MINUTES:
            exit_reason = "MAX HOLD TIME"

        if not exit_reason:
            continue

        trade = paper_sell(
            address,
            current_price,
            exit_reason,
        )

        if trade and chat_id:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "📤 PAPER SELL\n\n"
                    f"{trade['name']} "
                    f"({trade['symbol']})\n\n"
                    f"Reason: {trade['reason']}\n"
                    f"Entry: ${trade['entry_price']:.10f}\n"
                    f"Exit: ${trade['exit_price']:.10f}\n"
                    f"P/L: ${trade['pnl']:+.2f}\n"
                    f"P/L %: {trade['pnl_percent']:+.2f}%\n\n"
                    f"Paper cash: ${paper_balance:.2f}"
                ),
            )


# =========================================================
# BUILD HUNT MESSAGE
# =========================================================

def build_hunt_message(results):
    top = results[:5]

    if not top:
        return (
            "🔎 JARVIS SOLANA HUNT\n\n"
            "No tokens currently meet "
            "Jarvis's paper-testing filters."
        )

    lines = [
        "🚨 JARVIS SOLANA HUNT",
        "",
        "🧪 PAPER TEST MODE",
        "",
    ]

    for i, c in enumerate(top, 1):
        sells = c["sells_1h"]

        if sells:
            buy_pressure = c["buys_1h"] / sells
            buy_pressure_text = f"{buy_pressure:.2f}x"

        elif c["buys_1h"]:
            buy_pressure_text = "buy-heavy"

        else:
            buy_pressure_text = "N/A"


        if c["age_hours"] is None:
            age_text = "Unknown"

        elif c["age_hours"] < 24:
            age_text = f"{c['age_hours']:.1f}h"

        else:
            age_text = (
                f"{c['age_hours'] / 24:.1f}d"
            )


        strengths = (
            ", ".join(c["positives"][:3])
            if c["positives"]
            else "none"
        )

        risks = (
            ", ".join(c["risks"][:3])
            if c["risks"]
            else "none"
        )


        lines.extend(
            [
                f"{i}. {c['name']} ({c['symbol']})",
                f"Score: {c['score']}/100",
                f"Signal: {c['label']}",
                f"Age: {age_text}",
                f"Price: ${c['price']:.10f}",
                f"Market cap: ${c['market_cap']:,.0f}",
                f"Liquidity: ${c['liquidity']:,.0f}",
                f"24h volume: ${c['volume_24h']:,.0f}",
                f"1h volume: ${c['volume_1h']:,.0f}",
                f"1h buy pressure: {buy_pressure_text}",
                f"1h change: {c['price_change_1h']:.1f}%",
                f"Strengths: {strengths}",
                f"Flags: {risks}",
                f"Token: {c['address']}",
                "",
            ]
        )

    return "\n".join(lines)[:4000]


# =========================================================
# FIND PAPER ENTRY
# =========================================================

async def try_paper_entry(
    context,
    results,
    chat_id,
):
    if paper_positions:
        return

    eligible = [
        candidate
        for candidate in results
        if candidate["score"] >= PAPER_MIN_ENTRY_SCORE
    ]

    if not eligible:
        return

    candidate = eligible[0]

    position = paper_buy(candidate)

    if not position:
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "📥 PAPER BUY\n\n"
            f"{position['name']} "
            f"({position['symbol']})\n\n"
            f"Jarvis score: "
            f"{position['score']}/100\n"
            f"Entry: "
            f"${position['entry_price']:.10f}\n"
            f"Paper size: "
            f"${position['cost']:.2f}\n\n"
            f"Take profit: "
            f"+{TAKE_PROFIT_PERCENT:.1f}%\n"
            f"Stop loss: "
            f"-{STOP_LOSS_PERCENT:.1f}%\n"
            f"Max hold: "
            f"{MAX_HOLD_MINUTES} minutes\n\n"
            f"Cash remaining: "
            f"${paper_balance:.2f}"
        ),
    )


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
        results = get_ranked_candidates()

        await context.bot.send_message(
            chat_id=chat_id,
            text=build_hunt_message(results),
        )

        await try_paper_entry(
            context,
            results,
            chat_id,
        )

    except Exception as error:
        print(
            "Auto hunt error:",
            repr(error),
        )


# =========================================================
# TELEGRAM /START
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

    if not context.job_queue.get_jobs_by_name(
        hunt_name
    ):
        context.job_queue.run_repeating(
            auto_hunt,
            interval=AUTO_HUNT_INTERVAL,
            first=AUTO_HUNT_FIRST_SCAN,
            chat_id=chat_id,
            name=hunt_name,
        )

    if not context.job_queue.get_jobs_by_name(
        monitor_name
    ):
        context.job_queue.run_repeating(
            monitor_paper_positions,
            interval=PAPER_MONITOR_INTERVAL,
            first=PAPER_MONITOR_INTERVAL,
            chat_id=chat_id,
            name=monitor_name,
        )

    await update.message.reply_text(
        "✅ Paper scalping activated.\n"
        "Paper entry threshold: "
        f"{PAPER_MIN_ENTRY_SCORE}/100\n"
        "No real money will be used."
    )


# =========================================================
# /PAPER
# =========================================================

async def paper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    realized_pnl = sum(
        trade["pnl"]
        for trade in paper_trade_history
    )

    account_value = paper_balance

    for position in paper_positions.values():
        try:
            current_price = get_token_price(
                position["address"]
            )

            if current_price:
                account_value += (
                    position["quantity"]
                    * current_price
                )

        except Exception:
            account_value += position["cost"]


    await update.message.reply_text(
        "💵 PAPER ACCOUNT\n\n"
        f"Cash: ${paper_balance:.2f}\n"
        f"Account value: ${account_value:.2f}\n"
        f"Open positions: {len(paper_positions)}\n"
        f"Closed trades: {len(paper_trade_history)}\n"
        f"Realized P/L: ${realized_pnl:+.2f}"
    )


# =========================================================
# /POSITIONS
# =========================================================

async def positions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not paper_positions:
        await update.message.reply_text(
            "No paper positions are open."
        )
        return

    lines = [
        "📊 OPEN PAPER POSITIONS",
        "",
    ]

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

            current_value = (
                current_price
                * position["quantity"]
            )

        else:
            pnl_percent = 0
            current_value = position["cost"]


        minutes_open = (
            time.time()
            - position["opened_at"]
        ) / 60


        lines.extend(
            [
                f"{position['name']} "
                f"({position['symbol']})",
                f"Score: {position['score']}/100",
                f"Entry: "
                f"${position['entry_price']:.10f}",
                f"Current value: "
                f"${current_value:.2f}",
                f"P/L: {pnl_percent:+.2f}%",
                f"Open: {minutes_open:.0f} min",
                "",
            ]
        )


    await update.message.reply_text(
        "\n".join(lines)[:4000]
    )


# =========================================================
# /HISTORY
# =========================================================

async def history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not paper_trade_history:
        await update.message.reply_text(
            "No completed paper trades yet."
        )
        return


    lines = [
        "📚 PAPER TRADE HISTORY",
        "",
    ]


    for trade in paper_trade_history[-10:]:
        lines.extend(
            [
                f"{trade['name']} "
                f"({trade['symbol']})",
                f"{trade['pnl_percent']:+.2f}%",
                f"P/L: ${trade['pnl']:+.2f}",
                f"Exit: {trade['reason']}",
                "",
            ]
        )


    await update.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# MANUAL /HUNT
# =========================================================

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

        # Manual /hunt can also trigger a PAPER entry.
        await try_paper_entry(
            context,
            results,
            update.effective_chat.id,
        )

    except Exception as error:
        print(
            "Manual hunt error:",
            repr(error),
        )

        await update.message.reply_text(
            "Jarvis hunt encountered an error."
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

    try:
        data = get_coin(
            context.args[0]
        )

        if not data:
            await update.message.reply_text(
                "Coin not found."
            )
            return

        change = data.get(
            "price_change_percentage_24h"
        )

        if change is None:
            change_text = "N/A"

        else:
            change_text = (
                f"{change:+.2f}%"
            )


        await update.message.reply_text(
            f"🪙 {data['name']} "
            f"({data['symbol'].upper()})\n\n"
            f"Price: "
            f"${data['current_price']:,}\n"
            f"24h: {change_text}\n"
            f"Market cap: "
            f"${data['market_cap']:,}\n"
            f"24h volume: "
            f"${data['total_volume']:,}"
        )

    except Exception as error:
        print(
            "Price error:",
            repr(error),
        )

        await update.message.reply_text(
            "Market data unavailable."
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
You are Crypto Jarvis, a cautious cryptocurrency analyst.

Coin: {data['name']}
Symbol: {data['symbol']}
Price: ${data['current_price']}
24h change: {data['price_change_percentage_24h']}
Market cap: ${data['market_cap']}
24h volume: ${data['total_volume']}

Give a concise analysis covering:
1. Momentum
2. Volume
3. Risk
4. Bullish confirmation
5. Invalidation

Do not guarantee profits.
Do not claim that the coin will multiply in value.
Use only the supplied market data.
"""


        response = client.responses.create(
            model="gpt-5-mini",
            input=prompt,
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
            "Jarvis analysis unavailable."
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
        "Crypto Jarvis PAPER scalper online"
    )

    app.run_polling()


if __name__ == "__main__":
    main()
