# ============================================================
# 🤖 JARVIS CRYPTO RESEARCH BOT — SAFER PAPER TRADING VERSION
# ============================================================
#
# PAPER TRADING ONLY
# NO REAL WALLET
# NO PRIVATE KEYS
# NO REAL ORDERS
#
# Designed for:
# - Solana token research
# - DexScreener market data
# - Telegram control
# - Stronger risk protection
# - Trade journaling
# - Daily reset
# - Loss-streak protection
# - Cooldown system
#
# ============================================================

import os
import json
import time
import math
import traceback
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ============================================================
# 1. SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

TIMEZONE = ZoneInfo("Australia/Sydney")

STATE_FILE = "jarvis_state.json"
JOURNAL_FILE = "jarvis_trade_journal.json"

STARTING_BALANCE = 1000.00


# -------------------------
# RISK SETTINGS
# -------------------------

RISK_PER_TRADE_PCT = 0.50
MAX_DAILY_LOSS_PCT = 2.00

MAX_OPEN_POSITIONS = 2
MAX_DAILY_ENTRIES = 10

MAX_CONSECUTIVE_LOSSES = 3

COOLDOWN_AFTER_LOSSES = 2
COOLDOWN_MINUTES = 60


# -------------------------
# EXIT SETTINGS
# -------------------------

STOP_LOSS_PCT = 6.0

TAKE_PROFIT_PCT = 15.0

TRAILING_ACTIVATION_PCT = 7.0
TRAILING_STOP_PCT = 5.0

MAX_HOLD_MINUTES = 180


# -------------------------
# TOKEN FILTERS
# -------------------------

MIN_LIQUIDITY = 50_000
MIN_VOLUME_24H = 100_000

MIN_MARKET_CAP = 75_000
MAX_MARKET_CAP = 5_000_000

MIN_PAIR_AGE_MINUTES = 15
MAX_PAIR_AGE_DAYS = 30

MIN_5M_CHANGE = 1.0
MAX_5M_CHANGE = 15.0

MIN_1H_CHANGE = 2.0
MAX_1H_CHANGE = 60.0

MIN_6H_CHANGE = -5.0
MAX_6H_CHANGE = 150.0

MIN_5M_BUYS = 8

MIN_BUY_RATIO = 0.58

MIN_SCORE = 76


# -------------------------
# LOOP TIMING
# -------------------------

SCAN_INTERVAL_SECONDS = 40
POSITION_UPDATE_SECONDS = 10
STATUS_INTERVAL_SECONDS = 600

MAX_TOKENS_PER_SCAN = 35


DEX_PROFILES_URL = (
    "https://api.dexscreener.com/token-profiles/latest/v1"
)

DEX_TOKEN_PAIRS_URL = (
    "https://api.dexscreener.com/token-pairs/v1/solana/{}"
)

TELEGRAM_BASE = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
)


# ============================================================
# 2. UTILITIES
# ============================================================

def now():
    return datetime.now(TIMEZONE)


def today_string():
    return now().strftime("%Y-%m-%d")


def timestamp_string():
    return now().isoformat()


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except:
        return default


def clamp(value, low, high):
    return max(low, min(value, high))


def money(value):
    return f"${value:,.2f}"


def pct(value):
    return f"{value:.2f}%"


# ============================================================
# 3. TELEGRAM
# ============================================================

def telegram_send(message, chat_id=None):

    target = str(chat_id or CHAT_ID).strip()

    if not BOT_TOKEN or not target:
        print(message)
        return

    try:

        requests.post(
            f"{TELEGRAM_BASE}/sendMessage",
            json={
                "chat_id": target,
                "text": message
            },
            timeout=15
        )

    except Exception as e:
        print("Telegram send error:", e)


def telegram_updates(offset=None):

    if not BOT_TOKEN:
        return []

    params = {
        "timeout": 2
    }

    if offset is not None:
        params["offset"] = offset

    try:

        response = requests.get(
            f"{TELEGRAM_BASE}/getUpdates",
            params=params,
            timeout=8
        )

        data = response.json()

        if not data.get("ok"):
            return []

        return data.get("result", [])

    except:
        return []


# ============================================================
# 4. STATE
# ============================================================

def default_state():

    return {

        "cash": STARTING_BALANCE,

        "positions": {},

        "closed_trades": [],

        "date": today_string(),

        "session_start_equity": STARTING_BALANCE,

        "daily_start_equity": STARTING_BALANCE,

        "daily_realised_pnl": 0.0,

        "daily_entries": 0,

        "wins": 0,

        "losses": 0,

        "consecutive_losses": 0,

        "entries_paused": False,

        "cooldown_until": None,

        "last_scan": None,

        "last_status": None,

        "telegram_offset": None,

        "total_scans": 0,

        "candidates_seen": 0,

        "candidates_rejected": 0,

        "candidates_passed": 0
    }


def load_state():

    if not os.path.exists(STATE_FILE):
        return default_state()

    try:

        with open(STATE_FILE, "r") as f:
            saved = json.load(f)

        defaults = default_state()

        for key, value in defaults.items():

            if key not in saved:
                saved[key] = value

        return saved

    except Exception:

        return default_state()


def save_state():

    try:

        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    except Exception as e:
        print("State save error:", e)


state = load_state()


# ============================================================
# 5. JOURNAL
# ============================================================

def load_journal():

    if not os.path.exists(JOURNAL_FILE):
        return []

    try:

        with open(JOURNAL_FILE, "r") as f:
            return json.load(f)

    except:
        return []


def save_journal(journal):

    try:

        with open(JOURNAL_FILE, "w") as f:
            json.dump(journal, f, indent=2)

    except Exception as e:
        print("Journal error:", e)


def journal_trade(trade):

    journal = load_journal()

    journal.append(trade)

    save_journal(journal)


# ============================================================
# 6. DEXSCREENER
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "JarvisPaperResearchBot/2.0"
})


def get_latest_profiles():

    try:

        r = session.get(
            DEX_PROFILES_URL,
            timeout=15
        )

        r.raise_for_status()

        data = r.json()

        if not isinstance(data, list):
            return []

        solana = []

        for token in data:

            if token.get("chainId") == "solana":

                address = token.get("tokenAddress")

                if address:
                    solana.append(address)

        return solana[:MAX_TOKENS_PER_SCAN]

    except Exception as e:

        print("Profile error:", e)

        return []


def get_pairs(token_address):

    try:

        url = DEX_TOKEN_PAIRS_URL.format(
            token_address
        )

        r = session.get(
            url,
            timeout=15
        )

        r.raise_for_status()

        data = r.json()

        if isinstance(data, list):
            return data

        return []

    except:
        return []


def best_pair_for_token(token_address):

    pairs = get_pairs(token_address)

    if not pairs:
        return None

    pairs = [
        p for p in pairs
        if p.get("chainId") == "solana"
    ]

    if not pairs:
        return None

    pairs.sort(
        key=lambda p:
        safe_float(
            (p.get("liquidity") or {}).get("usd")
        ),
        reverse=True
    )

    return pairs[0]


# ============================================================
# 7. MARKET DATA EXTRACTION
# ============================================================

def pair_market_data(pair):

    price_change = pair.get("priceChange") or {}

    txns = pair.get("txns") or {}

    m5_tx = txns.get("m5") or {}
    h1_tx = txns.get("h1") or {}

    buys_5m = safe_int(
        m5_tx.get("buys")
    )

    sells_5m = safe_int(
        m5_tx.get("sells")
    )

    buys_1h = safe_int(
        h1_tx.get("buys")
    )

    sells_1h = safe_int(
        h1_tx.get("sells")
    )

    total_5m = buys_5m + sells_5m

    buy_ratio = (
        buys_5m / total_5m
        if total_5m > 0
        else 0
    )

    created_ms = safe_int(
        pair.get("pairCreatedAt")
    )

    age_minutes = 999999

    if created_ms > 0:

        created = datetime.fromtimestamp(
            created_ms / 1000,
            tz=TIMEZONE
        )

        age_minutes = (
            now() - created
        ).total_seconds() / 60

    market_cap = safe_float(
        pair.get("marketCap")
    )

    if market_cap <= 0:
        market_cap = safe_float(
            pair.get("fdv")
        )

    return {

        "pair_address":
            pair.get("pairAddress", ""),

        "token_address":
            (pair.get("baseToken") or {})
            .get("address", ""),

        "symbol":
            (pair.get("baseToken") or {})
            .get("symbol", "UNKNOWN"),

        "name":
            (pair.get("baseToken") or {})
            .get("name", "Unknown"),

        "price":
            safe_float(
                pair.get("priceUsd")
            ),

        "liquidity":
            safe_float(
                (pair.get("liquidity") or {})
                .get("usd")
            ),

        "volume24":
            safe_float(
                (pair.get("volume") or {})
                .get("h24")
            ),

        "volume6":
            safe_float(
                (pair.get("volume") or {})
                .get("h6")
            ),

        "volume1":
            safe_float(
                (pair.get("volume") or {})
                .get("h1")
            ),

        "volume5":
            safe_float(
                (pair.get("volume") or {})
                .get("m5")
            ),

        "market_cap":
            market_cap,

        "change5":
            safe_float(
                price_change.get("m5")
            ),

        "change1":
            safe_float(
                price_change.get("h1")
            ),

        "change6":
            safe_float(
                price_change.get("h6")
            ),

        "change24":
            safe_float(
                price_change.get("h24")
            ),

        "buys5":
            buys_5m,

        "sells5":
            sells_5m,

        "buys1":
            buys_1h,

        "sells1":
            sells_1h,

        "buy_ratio":
            buy_ratio,

        "age_minutes":
            age_minutes,

        "dex":
            pair.get("dexId", ""),

        "url":
            pair.get("url", "")
    }


# ============================================================
# 8. QUALITY FILTER
# ============================================================

def reject_reason(data):

    if data["price"] <= 0:
        return "invalid price"

    if data["liquidity"] < MIN_LIQUIDITY:
        return "low liquidity"

    if data["volume24"] < MIN_VOLUME_24H:
        return "low volume"

    if not (
        MIN_MARKET_CAP
        <= data["market_cap"]
        <= MAX_MARKET_CAP
    ):
        return "market cap"

    if data["age_minutes"] < MIN_PAIR_AGE_MINUTES:
        return "too new"

    if data["age_minutes"] > MAX_PAIR_AGE_DAYS * 1440:
        return "too old"

    if data["buys5"] < MIN_5M_BUYS:
        return "not enough buys"

    if data["buy_ratio"] < MIN_BUY_RATIO:
        return "weak buy pressure"

    if not (
        MIN_5M_CHANGE
        <= data["change5"]
        <= MAX_5M_CHANGE
    ):
        return "5m momentum"

    if not (
        MIN_1H_CHANGE
        <= data["change1"]
        <= MAX_1H_CHANGE
    ):
        return "1h trend"

    if not (
        MIN_6H_CHANGE
        <= data["change6"]
        <= MAX_6H_CHANGE
    ):
        return "6h regime"

    # Avoid severe momentum disagreement

    if data["change1"] > 0 and data["change5"] < 0:
        return "momentum conflict"

    return None


# ============================================================
# 9. SCORE
# ============================================================

def calculate_score(d):

    score = 0


    # --------------------------------
    # Liquidity
    # --------------------------------

    if d["liquidity"] >= 250_000:
        score += 15

    elif d["liquidity"] >= 150_000:
        score += 12

    elif d["liquidity"] >= 100_000:
        score += 9

    else:
        score += 6


    # --------------------------------
    # 24h Volume
    # --------------------------------

    if d["volume24"] >= 1_000_000:
        score += 15

    elif d["volume24"] >= 500_000:
        score += 12

    elif d["volume24"] >= 250_000:
        score += 9

    else:
        score += 6


    # --------------------------------
    # 5m momentum
    # --------------------------------

    if 2 <= d["change5"] <= 7:
        score += 15

    elif 1 <= d["change5"] <= 10:
        score += 11

    else:
        score += 5


    # --------------------------------
    # 1h trend
    # --------------------------------

    if 5 <= d["change1"] <= 25:
        score += 15

    elif 2 <= d["change1"] <= 40:
        score += 10

    else:
        score += 4


    # --------------------------------
    # 6h regime filter
    # --------------------------------

    if 5 <= d["change6"] <= 60:
        score += 12

    elif 0 <= d["change6"] <= 100:
        score += 8

    else:
        score += 3


    # --------------------------------
    # Buy pressure
    # --------------------------------

    ratio = d["buy_ratio"]

    if ratio >= 0.72:
        score += 15

    elif ratio >= 0.65:
        score += 12

    elif ratio >= 0.58:
        score += 8


    # --------------------------------
    # Pair age
    # --------------------------------

    hours = d["age_minutes"] / 60

    if 1 <= hours <= 48:
        score += 8

    elif 0.25 <= hours <= 168:
        score += 6

    else:
        score += 3


    # --------------------------------
    # Market cap
    # --------------------------------

    if 150_000 <= d["market_cap"] <= 2_000_000:
        score += 5

    elif d["market_cap"] <= MAX_MARKET_CAP:
        score += 3


    return min(score, 100)


# ============================================================
# 10. EQUITY
# ============================================================

def get_equity():

    equity = state["cash"]

    for pos in state["positions"].values():

        equity += (
            pos["quantity"]
            * pos.get(
                "current_price",
                pos["entry_price"]
            )
        )

    return equity


def open_pnl():

    total = 0

    for pos in state["positions"].values():

        value_now = (
            pos["quantity"]
            * pos.get(
                "current_price",
                pos["entry_price"]
            )
        )

        total += (
            value_now
            - pos["position_value"]
        )

    return total


# ============================================================
# 11. DAILY LOSS
# ============================================================

def daily_loss_pct():

    start = state["daily_start_equity"]

    if start <= 0:
        return 0

    equity = get_equity()

    loss = max(
        0,
        start - equity
    )

    return (
        loss / start
    ) * 100


def daily_loss_dollars():

    start = state["daily_start_equity"]

    return max(
        0,
        start - get_equity()
    )


def maximum_daily_loss_dollars():

    return (
        state["daily_start_equity"]
        * MAX_DAILY_LOSS_PCT
        / 100
    )


# ============================================================
# 12. COOLDOWN
# ============================================================

def cooldown_active():

    value = state.get(
        "cooldown_until"
    )

    if not value:
        return False

    try:

        end = datetime.fromisoformat(value)

        if now() < end:
            return True

    except:
        pass

    state["cooldown_until"] = None

    return False


def start_cooldown():

    end = now() + timedelta(
        minutes=COOLDOWN_MINUTES
    )

    state["cooldown_until"] = (
        end.isoformat()
    )

    telegram_send(
        "🧊 JARVIS COOLDOWN\n\n"
        f"{state['consecutive_losses']} consecutive losses.\n"
        f"New entries paused for "
        f"{COOLDOWN_MINUTES} minutes.\n\n"
        f"Cooldown ends:\n"
        f"{end.strftime('%I:%M %p')}"
    )

    save_state()


# ============================================================
# 13. ENTRY PERMISSION
# ============================================================

def entry_allowed():

    reset_day_if_needed()

    if state["entries_paused"]:
        return False, "manual pause"

    if cooldown_active():
        return False, "cooldown"

    if (
        state["consecutive_losses"]
        >= MAX_CONSECUTIVE_LOSSES
    ):

        return False, "loss streak stop"

    if (
        state["daily_entries"]
        >= MAX_DAILY_ENTRIES
    ):

        return False, "daily entry limit"

    if (
        len(state["positions"])
        >= MAX_OPEN_POSITIONS
    ):

        return False, "position limit"

    if (
        daily_loss_pct()
        >= MAX_DAILY_LOSS_PCT
    ):

        return False, "daily loss stop"

    return True, "OK"


# ============================================================
# 14. POSITION SIZING
# ============================================================

def calculate_position_size(entry_price):

    if entry_price <= 0:
        return 0, 0

    equity = get_equity()

    risk_dollars = (
        equity
        * RISK_PER_TRADE_PCT
        / 100
    )

    stop_fraction = (
        STOP_LOSS_PCT / 100
    )

    position_value = (
        risk_dollars
        / stop_fraction
    )

    # Never use more than 10% of equity

    max_position = equity * 0.10

    position_value = min(
        position_value,
        max_position,
        state["cash"]
    )

    quantity = (
        position_value
        / entry_price
    )

    return position_value, quantity


def prospective_stop_loss(position_value):

    return (
        position_value
        * STOP_LOSS_PCT
        / 100
    )


def prospective_daily_loss_ok(
    position_value
):

    current_loss = daily_loss_dollars()

    possible_new_loss = (
        prospective_stop_loss(
            position_value
        )
    )

    maximum_loss = (
        maximum_daily_loss_dollars()
    )

    if (
        current_loss
        + possible_new_loss
        > maximum_loss
    ):

        return False

    return True


# ============================================================
# 15. OPEN PAPER POSITION
# ============================================================

def open_position(data, score):

    allowed, reason = entry_allowed()

    if not allowed:
        return False

    token = data["token_address"]

    if token in state["positions"]:
        return False

    price = data["price"]

    position_value, quantity = (
        calculate_position_size(price)
    )

    if position_value <= 0:
        return False


    # --------------------------------
    # PRE-TRADE DAILY LOSS CHECK
    # --------------------------------

    if not prospective_daily_loss_ok(
        position_value
    ):

        telegram_send(
            "🛡️ JARVIS BLOCKED ENTRY\n\n"
            f"{data['symbol']}\n\n"
            "Reason: potential stop-loss "
            "would exceed today's "
            "maximum loss limit."
        )

        return False


    state["cash"] -= position_value


    stop_price = (
        price
        * (
            1
            - STOP_LOSS_PCT / 100
        )
    )

    target_price = (
        price
        * (
            1
            + TAKE_PROFIT_PCT / 100
        )
    )


    state["positions"][token] = {

        "token_address":
            token,

        "symbol":
            data["symbol"],

        "name":
            data["name"],

        "entry_price":
            price,

        "current_price":
            price,

        "highest_price":
            price,

        "quantity":
            quantity,

        "position_value":
            position_value,

        "stop_price":
            stop_price,

        "target_price":
            target_price,

        "score":
            score,

        "opened_at":
            timestamp_string(),

        "liquidity":
            data["liquidity"],

        "volume24":
            data["volume24"],

        "market_cap":
            data["market_cap"],

        "change5":
            data["change5"],

        "change1":
            data["change1"],

        "change6":
            data["change6"],

        "buy_ratio":
            data["buy_ratio"],

        "url":
            data["url"]
    }


    state["daily_entries"] += 1

    save_state()


    telegram_send(
        "🧪 JARVIS PAPER ENTRY\n\n"
        f"Token: {data['symbol']}\n"
        f"Score: {score}/100\n\n"
        f"Entry: ${price:.10f}\n"
        f"Position: {money(position_value)}\n"
        f"Risk: {RISK_PER_TRADE_PCT:.2f}%\n"
        f"Stop: -{STOP_LOSS_PCT:.1f}%\n"
        f"Target: +{TAKE_PROFIT_PCT:.1f}%\n\n"
        f"Liquidity: "
        f"{money(data['liquidity'])}\n"
        f"24h Volume: "
        f"{money(data['volume24'])}\n"
        f"Market Cap: "
        f"{money(data['market_cap'])}\n\n"
        f"5m: {data['change5']:+.2f}%\n"
        f"1h: {data['change1']:+.2f}%\n"
        f"6h: {data['change6']:+.2f}%\n"
        f"Buy ratio: "
        f"{data['buy_ratio']*100:.1f}%\n\n"
        "Mode: PAPER TRADING"
    )

    return True


# ============================================================
# 16. CLOSE POSITION
# ============================================================

def close_position(
    token,
    current_price,
    reason
):

    pos = state["positions"].get(token)

    if not pos:
        return


    exit_value = (
        pos["quantity"]
        * current_price
    )

    pnl = (
        exit_value
        - pos["position_value"]
    )

    pnl_pct = (
        pnl
        / pos["position_value"]
        * 100
    )


    state["cash"] += exit_value

    state["daily_realised_pnl"] += pnl


    if pnl > 0:

        state["wins"] += 1

        state["consecutive_losses"] = 0

        emoji = "✅"

    else:

        state["losses"] += 1

        state["consecutive_losses"] += 1

        emoji = "❌"


    opened = datetime.fromisoformat(
        pos["opened_at"]
    )

    duration_minutes = (
        now() - opened
    ).total_seconds() / 60


    trade = {

        "symbol":
            pos["symbol"],

        "token_address":
            token,

        "entry_price":
            pos["entry_price"],

        "exit_price":
            current_price,

        "position_value":
            pos["position_value"],

        "pnl":
            pnl,

        "pnl_pct":
            pnl_pct,

        "score":
            pos["score"],

        "entry_liquidity":
            pos["liquidity"],

        "entry_volume24":
            pos["volume24"],

        "entry_market_cap":
            pos["market_cap"],

        "entry_change5":
            pos["change5"],

        "entry_change1":
            pos["change1"],

        "entry_change6":
            pos["change6"],

        "entry_buy_ratio":
            pos["buy_ratio"],

        "opened_at":
            pos["opened_at"],

        "closed_at":
            timestamp_string(),

        "duration_minutes":
            duration_minutes,

        "exit_reason":
            reason
    }


    state["closed_trades"].append(
        trade
    )

    state["closed_trades"] = (
        state["closed_trades"][-200:]
    )


    del state["positions"][token]


    journal_trade(trade)

    save_state()


    telegram_send(
        f"{emoji} JARVIS PAPER EXIT\n\n"
        f"{pos['symbol']}\n\n"
        f"Reason: {reason}\n"
        f"P/L: {money(pnl)} "
        f"({pnl_pct:+.2f}%)\n"
        f"Held: {duration_minutes:.0f} min\n\n"
        f"Loss streak: "
        f"{state['consecutive_losses']}\n"
        f"Daily loss: "
        f"{daily_loss_pct():.2f}%/"
        f"{MAX_DAILY_LOSS_PCT:.2f}%"
    )


    # --------------------------------
    # COOLDOWN
    # --------------------------------

    if (
        pnl < 0
        and state["consecutive_losses"]
        >= COOLDOWN_AFTER_LOSSES
        and state["consecutive_losses"]
        < MAX_CONSECUTIVE_LOSSES
    ):

        start_cooldown()


    # --------------------------------
    # HARD LOSS-STREAK STOP
    # --------------------------------

    if (
        state["consecutive_losses"]
        >= MAX_CONSECUTIVE_LOSSES
    ):

        telegram_send(
            "🛑 JARVIS LOSS-STREAK STOP\n\n"
            f"{MAX_CONSECUTIVE_LOSSES} "
            "consecutive losses reached.\n\n"
            "No more entries today unless "
            "you manually reset the day.\n\n"
            "Recommendation: review trades "
            "instead of immediately resetting."
        )


# ============================================================
# 17. POSITION MANAGEMENT
# ============================================================

def update_positions():

    tokens = list(
        state["positions"].keys()
    )

    for token in tokens:

        try:

            pair = best_pair_for_token(
                token
            )

            if not pair:
                continue

            data = pair_market_data(pair)

            price = data["price"]

            if price <= 0:
                continue

            pos = state["positions"].get(
                token
            )

            if not pos:
                continue

            pos["current_price"] = price


            # --------------------------------
            # Highest price
            # --------------------------------

            if (
                price
                > pos["highest_price"]
            ):

                pos["highest_price"] = price


            pnl_pct = (
                (
                    price
                    - pos["entry_price"]
                )
                / pos["entry_price"]
                * 100
            )


            # --------------------------------
            # Stop loss
            # --------------------------------

            if price <= pos["stop_price"]:

                close_position(
                    token,
                    price,
                    "STOP LOSS"
                )

                continue


            # --------------------------------
            # Take profit
            # --------------------------------

            if price >= pos["target_price"]:

                close_position(
                    token,
                    price,
                    "TAKE PROFIT"
                )

                continue


            # --------------------------------
            # Trailing stop
            # --------------------------------

            if (
                pnl_pct
                >= TRAILING_ACTIVATION_PCT
            ):

                trailing_price = (
                    pos["highest_price"]
                    * (
                        1
                        - TRAILING_STOP_PCT
                        / 100
                    )
                )

                if (
                    price
                    <= trailing_price
                ):

                    close_position(
                        token,
                        price,
                        "TRAILING STOP"
                    )

                    continue


            # --------------------------------
            # Maximum hold time
            # --------------------------------

            opened = datetime.fromisoformat(
                pos["opened_at"]
            )

            minutes_open = (
                now() - opened
            ).total_seconds() / 60

            if (
                minutes_open
                >= MAX_HOLD_MINUTES
            ):

                close_position(
                    token,
                    price,
                    "TIME EXIT"
                )

                continue


        except Exception as e:

            print(
                "Position update error:",
                e
            )


    save_state()


# ============================================================
# 18. SCANNER
# ============================================================

def scan_market():

    allowed, reason = entry_allowed()

    if not allowed:
        return


    addresses = get_latest_profiles()

    if not addresses:
        return


    state["total_scans"] += 1

    candidates = []


    for address in addresses:

        try:

            if address in state["positions"]:
                continue

            pair = best_pair_for_token(
                address
            )

            if not pair:
                continue

            data = pair_market_data(pair)

            state["candidates_seen"] += 1

            reason = reject_reason(data)

            if reason:

                state[
                    "candidates_rejected"
                ] += 1

                continue


            score = calculate_score(data)

            if score < MIN_SCORE:

                state[
                    "candidates_rejected"
                ] += 1

                continue


            state["candidates_passed"] += 1

            candidates.append(
                (
                    score,
                    data
                )
            )


            time.sleep(0.20)


        except:
            continue


    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )


    # Only take best available setup

    for score, data in candidates:

        allowed, reason = entry_allowed()

        if not allowed:
            break

        opened = open_position(
            data,
            score
        )

        if opened:
            break


    state["last_scan"] = (
        timestamp_string()
    )

    save_state()


# ============================================================
# 19. DAILY RESET
# ============================================================

def reset_day_if_needed():

    current = today_string()

    if state["date"] == current:
        return


    equity = get_equity()

    state["date"] = current

    state["daily_start_equity"] = equity

    state["daily_realised_pnl"] = 0

    state["daily_entries"] = 0

    state["wins"] = 0

    state["losses"] = 0

    state["consecutive_losses"] = 0

    state["cooldown_until"] = None

    state["entries_paused"] = False


    save_state()


    telegram_send(
        "🌅 JARVIS NEW DAY RESET\n\n"
        f"Date: {current}\n"
        f"Starting equity: "
        f"{money(equity)}\n\n"
        "Daily counters reset.\n"
        "Paper positions remain managed."
    )


def manual_reset_day():

    equity = get_equity()

    state["date"] = today_string()

    state["daily_start_equity"] = equity

    state["daily_realised_pnl"] = 0

    state["daily_entries"] = 0

    state["wins"] = 0

    state["losses"] = 0

    state["consecutive_losses"] = 0

    state["cooldown_until"] = None

    state["entries_paused"] = False

    save_state()


    telegram_send(
        "🔄 JARVIS DAY RESET\n\n"
        f"New starting equity: "
        f"{money(equity)}\n\n"
        "Daily counters cleared.\n\n"
        "⚠️ This does not recover losses."
    )


# ============================================================
# 20. STATUS
# ============================================================

def status_message():

    equity = get_equity()

    pnl = (
        equity
        - STARTING_BALANCE
    )

    total_closed = (
        state["wins"]
        + state["losses"]
    )

    if total_closed:

        win_rate = (
            state["wins"]
            / total_closed
            * 100
        )

    else:
        win_rate = 0


    allowed, reason = entry_allowed()


    if daily_loss_pct() >= MAX_DAILY_LOSS_PCT:

        status = "🛑 DAILY LOSS STOP"

    elif (
        state["consecutive_losses"]
        >= MAX_CONSECUTIVE_LOSSES
    ):

        status = "🛑 LOSS STREAK STOP"

    elif cooldown_active():

        status = "🧊 COOLDOWN"

    elif state["entries_paused"]:

        status = "⏸ PAUSED"

    else:

        status = "🟢 RESEARCHING"


    return (
        "🤖 JARVIS RESEARCH STATUS\n\n"

        f"Cash: {money(state['cash'])}\n"
        f"Equity: {money(equity)}\n"
        f"Total P/L: {money(pnl)}\n"
        f"Open P/L: "
        f"{money(open_pnl())}\n\n"

        f"Today's wins: "
        f"{state['wins']}\n"
        f"Today's losses: "
        f"{state['losses']}\n"
        f"Win rate: "
        f"{win_rate:.1f}%\n\n"

        f"Open positions: "
        f"{len(state['positions'])}/"
        f"{MAX_OPEN_POSITIONS}\n"

        f"Entries: "
        f"{state['daily_entries']}/"
        f"{MAX_DAILY_ENTRIES}\n"

        f"Loss streak: "
        f"{state['consecutive_losses']}/"
        f"{MAX_CONSECUTIVE_LOSSES}\n"

        f"Daily loss: "
        f"{daily_loss_pct():.2f}%/"
        f"{MAX_DAILY_LOSS_PCT:.2f}%\n\n"

        f"Risk/trade: "
        f"{RISK_PER_TRADE_PCT:.2f}%\n"
        f"Stop: {STOP_LOSS_PCT:.1f}%\n"
        f"Target: +"
        f"{TAKE_PROFIT_PCT:.1f}%\n"
        f"Minimum score: "
        f"{MIN_SCORE}/100\n\n"

        f"Status: {status}\n"
        "Mode: 🧪 PAPER TRADING"
    )


# ============================================================
# 21. POSITIONS MESSAGE
# ============================================================

def positions_message():

    if not state["positions"]:

        return (
            "📭 JARVIS POSITIONS\n\n"
            "No open positions."
        )


    text = "📊 JARVIS POSITIONS\n\n"


    for pos in state[
        "positions"
    ].values():

        current = pos.get(
            "current_price",
            pos["entry_price"]
        )

        pnl_pct = (
            (
                current
                - pos["entry_price"]
            )
            / pos["entry_price"]
            * 100
        )

        pnl_dollars = (
            pos["quantity"]
            * current
            - pos["position_value"]
        )


        text += (
            f"{pos['symbol']}\n"
            f"Score: "
            f"{pos['score']}/100\n"
            f"Position: "
            f"{money(pos['position_value'])}\n"
            f"P/L: "
            f"{money(pnl_dollars)} "
            f"({pnl_pct:+.2f}%)\n\n"
        )


    return text


# ============================================================
# 22. RECENT TRADES
# ============================================================

def trades_message():

    trades = state[
        "closed_trades"
    ][-10:]


    if not trades:

        return (
            "📚 JARVIS TRADES\n\n"
            "No completed trades yet."
        )


    text = (
        "📚 LAST 10 PAPER TRADES\n\n"
    )


    for trade in reversed(trades):

        emoji = (
            "✅"
            if trade["pnl"] > 0
            else "❌"
        )

        text += (
            f"{emoji} "
            f"{trade['symbol']} "
            f"{trade['pnl_pct']:+.2f}% "
            f"{money(trade['pnl'])}\n"
            f"Score: "
            f"{trade['score']} | "
            f"{trade['exit_reason']}\n\n"
        )


    return text


# ============================================================
# 23. PERFORMANCE ANALYSIS
# ============================================================

def performance_message():

    journal = load_journal()

    if not journal:

        return (
            "📈 JARVIS PERFORMANCE\n\n"
            "No completed trades yet."
        )


    recent = journal[-100:]


    wins = [
        t for t in recent
        if t["pnl"] > 0
    ]

    losses = [
        t for t in recent
        if t["pnl"] <= 0
    ]


    n = len(recent)

    win_rate = (
        len(wins) / n * 100
    )


    avg_win = (
        sum(t["pnl"] for t in wins)
        / len(wins)
        if wins
        else 0
    )

    avg_loss = (
        sum(t["pnl"] for t in losses)
        / len(losses)
        if losses
        else 0
    )


    expectancy = (
        sum(t["pnl"] for t in recent)
        / n
    )


    total_pnl = sum(
        t["pnl"]
        for t in recent
    )


    return (
        "📈 JARVIS PERFORMANCE\n\n"

        f"Trades analysed: {n}\n"
        f"Wins: {len(wins)}\n"
        f"Losses: {len(losses)}\n"
        f"Win rate: "
        f"{win_rate:.1f}%\n\n"

        f"Average winner: "
        f"{money(avg_win)}\n"
        f"Average loser: "
        f"{money(avg_loss)}\n\n"

        f"Expectancy/trade: "
        f"{money(expectancy)}\n"

        f"Total P/L: "
        f"{money(total_pnl)}\n\n"

        "A strategy should not be judged "
        "from only a handful of trades."
    )


# ============================================================
# 24. TELEGRAM COMMANDS
# ============================================================

def handle_command(
    command,
    chat_id
):

    cmd = command.lower().strip()

    cmd = cmd.split("@")[0]


    if cmd in (
        "/start",
        "/help"
    ):

        telegram_send(
            "🤖 JARVIS COMMANDS\n\n"

            "/status - bot status\n"
            "/today - today's status\n"
            "/positions - open positions\n"
            "/trades - recent trades\n"
            "/performance - statistics\n"
            "/scan - run a scan now\n"
            "/pause - pause entries\n"
            "/resume - resume entries\n"
            "/resetday - reset daily counters\n"
            "/paper - confirm trading mode\n"
            "/help - commands\n\n"

            "Mode: PAPER TRADING ONLY",
            chat_id
        )


    elif cmd in (
        "/status",
        "/today"
    ):

        telegram_send(
            status_message(),
            chat_id
        )


    elif cmd == "/positions":

        telegram_send(
            positions_message(),
            chat_id
        )


    elif cmd == "/trades":

        telegram_send(
            trades_message(),
            chat_id
        )


    elif cmd == "/performance":

        telegram_send(
            performance_message(),
            chat_id
        )


    elif cmd == "/pause":

        state["entries_paused"] = True

        save_state()

        telegram_send(
            "⏸ JARVIS PAUSED\n\n"
            "No new entries.\n"
            "Open positions will still "
            "be managed.",
            chat_id
        )


    elif cmd == "/resume":

        if (
            state["consecutive_losses"]
            >= MAX_CONSECUTIVE_LOSSES
        ):

            telegram_send(
                "🛑 Cannot resume.\n\n"
                "Maximum consecutive-loss "
                "limit has been reached.\n\n"
                "Wait for tomorrow or use "
                "/resetday after reviewing "
                "the trades.",
                chat_id
            )

            return

        if (
            daily_loss_pct()
            >= MAX_DAILY_LOSS_PCT
        ):

            telegram_send(
                "🛑 Cannot resume.\n\n"
                "Daily loss limit reached.",
                chat_id
            )

            return


        state["entries_paused"] = False

        save_state()

        telegram_send(
            "▶️ JARVIS RESUMED\n\n"
            "Research entries enabled.",
            chat_id
        )


    elif cmd == "/resetday":

        manual_reset_day()


    elif cmd == "/paper":

        telegram_send(
            "🧪 JARVIS MODE\n\n"
            "PAPER TRADING ONLY\n\n"
            "No real wallet connected.\n"
            "No real orders are placed.",
            chat_id
        )


    elif cmd == "/scan":

        telegram_send(
            "🔎 JARVIS SCAN STARTED",
            chat_id
        )

        scan_market()

        telegram_send(
            "✅ Scan finished.",
            chat_id
        )


# ============================================================
# 25. POLL TELEGRAM
# ============================================================

def process_telegram():

    updates = telegram_updates(
        state.get(
            "telegram_offset"
        )
    )


    for update in updates:

        try:

            update_id = update[
                "update_id"
            ]

            state[
                "telegram_offset"
            ] = update_id + 1


            message = update.get(
                "message"
            )

            if not message:
                continue


            text = message.get(
                "text",
                ""
            ).strip()


            chat_id = (
                message.get(
                    "chat",
                    {}
                ).get("id")
            )


            if text.startswith("/"):

                command = (
                    text.split()[0]
                )

                handle_command(
                    command,
                    chat_id
                )


        except Exception:

            traceback.print_exc()


    save_state()


# ============================================================
# 26. STARTUP
# ============================================================

def startup():

    reset_day_if_needed()

    telegram_send(
        "🤖 JARVIS ONLINE\n\n"

        "Safer paper research mode started.\n\n"

        f"Starting balance: "
        f"{money(STARTING_BALANCE)}\n"

        f"Risk per trade: "
        f"{RISK_PER_TRADE_PCT:.2f}%\n"

        f"Daily loss limit: "
        f"{MAX_DAILY_LOSS_PCT:.2f}%\n"

        f"Max loss streak: "
        f"{MAX_CONSECUTIVE_LOSSES}\n"

        f"Max daily entries: "
        f"{MAX_DAILY_ENTRIES}\n"

        f"Minimum score: "
        f"{MIN_SCORE}/100\n\n"

        "Mode: 🧪 PAPER TRADING"
    )


# ============================================================
# 27. MAIN LOOP
# ============================================================

def main():

    startup()


    last_scan_time = 0

    last_position_time = 0

    last_status_time = time.time()


    while True:

        try:

            reset_day_if_needed()

            process_telegram()


            current = time.time()


            # --------------------------------
            # Update open positions
            # --------------------------------

            if (
                current
                - last_position_time
                >= POSITION_UPDATE_SECONDS
            ):

                update_positions()

                last_position_time = current


            # --------------------------------
            # Scan
            # --------------------------------

            if (
                current
                - last_scan_time
                >= SCAN_INTERVAL_SECONDS
            ):

                scan_market()

                last_scan_time = current


            # --------------------------------
            # Periodic status
            # --------------------------------

            if (
                current
                - last_status_time
                >= STATUS_INTERVAL_SECONDS
            ):

                telegram_send(
                    status_message()
                )

                last_status_time = current


            time.sleep(2)


        except KeyboardInterrupt:

            print(
                "JARVIS stopped."
            )

            break


        except Exception as e:

            print(
                "Main loop error:",
                e
            )

            traceback.print_exc()

            time.sleep(10)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
