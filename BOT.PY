import os
import json
import time
import asyncio
import logging

from dataclasses import dataclass, field, asdict
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Set, List

import aiohttp
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# JARVIS V4
# MULTI-SOURCE SOLANA MEME PAPER TRADER
# ============================================================
#
# PAPER TRADING ONLY
#
# SOURCES:
# - Pump.fun / PumpSwap via PumpPortal
# - DEX Screener profiles
# - DEX Screener boosts
# - DEX Screener ads
# - DEX Screener market data
# - Birdeye optional
# - GMGN optional
#
# STRATEGY:
# - Starting balance: $1,000
# - Daily target: +$50
# - Risk per trade: 1.5%
# - Max daily loss: 3%
# - Max 2 open positions
# - Pause after 2 consecutive losses
# - Stop loss: 6%
# - TP1: +10%, sell 40%
# - TP2: +18%, sell 35%
# - Final 25% trails
# ============================================================


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

PUMPPORTAL_API_KEY = os.getenv("PUMPPORTAL_API_KEY", "")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
GMGN_OPENAPI_URL = os.getenv("GMGN_OPENAPI_URL", "")

TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()
STATE_FILE = os.getenv("STATE_FILE", "jarvis_state.json")


# ============================================================
# SETTINGS
# ============================================================

TZ = ZoneInfo("Australia/Sydney")

STARTING_BALANCE = 1000.00
DAILY_PROFIT_TARGET = 50.00

RISK_PER_TRADE = 0.015
MAX_DAILY_LOSS = 0.03
MAX_CONSECUTIVE_LOSSES = 2
MAX_OPEN_POSITIONS = 2
MAX_DAILY_ENTRIES = 10

MAX_POSITION_EQUITY_PCT = 0.25
MIN_POSITION_USD = 10.00

ENTRY_SLIPPAGE = 0.005
EXIT_SLIPPAGE = 0.0075
FEE_PCT = 0.002

STOP_LOSS_PCT = 0.06

TP1_PCT = 0.10
TP1_SELL = 0.40

TP2_PCT = 0.18
TP2_SELL = 0.35

TRAIL_ACTIVATE_PCT = 0.20
TRAIL_DISTANCE_PCT = 0.08

ENTRY_SCORE = 80
ELITE_SCORE = 90

MIN_SOURCES_FOR_ENTRY = 2

MIN_LIQUIDITY = 40_000
MIN_VOLUME_24H = 50_000
MIN_VOLUME_1H = 8_000

MIN_MARKET_CAP = 40_000
MAX_MARKET_CAP = 5_000_000

MIN_PAIR_AGE_MIN = 3
MAX_PAIR_AGE_DAYS = 30

MIN_5M_CHANGE = 1.0
MAX_5M_CHANGE = 35.0

MIN_1H_CHANGE = -5.0
MAX_1H_CHANGE = 120.0

MIN_5M_BUYS = 5
MIN_BUY_RATIO = 0.58

CANDIDATE_TTL_MIN = 120
DISCOVERY_LIMIT = 40

DEX_SCAN_SEC = 25
BIRDEYE_SCAN_SEC = 35
GMGN_SCAN_SEC = 45
EVALUATION_SEC = 10
PRICE_SEC = 8
STATUS_SEC = 300

DEX_BASE = "https://api.dexscreener.com"
BIRDEYE_BASE = "https://public-api.birdeye.so"
PUMP_WS = "wss://pumpportal.fun/api/data"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("jarvis")


# ============================================================
# HELPERS
# ============================================================

def num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def now_ts():
    return time.time()


def today():
    return datetime.now(TZ).strftime("%Y-%m-%d")


async def get_json(session, url, headers=None, params=None):
    try:
        async with session.get(
            url,
            headers=headers or {},
            params=params or {},
            timeout=aiohttp.ClientTimeout(total=12),
        ) as response:

            if response.status != 200:
                text = await response.text()

                log.warning(
                    "GET %s -> %s %s",
                    url,
                    response.status,
                    text[:160],
                )

                return None

            return await response.json()

    except Exception as exc:
        log.warning("GET failed %s: %s", url, exc)
        return None


# ============================================================
# CANDIDATE MODEL
# ============================================================

@dataclass
class Candidate:
    mint: str

    symbol: str = ""
    name: str = ""

    sources: Set[str] = field(default_factory=set)

    first_seen: float = field(default_factory=now_ts)
    last_seen: float = field(default_factory=now_ts)

    price: float = 0.0
    liquidity: float = 0.0
    market_cap: float = 0.0

    volume_5m: float = 0.0
    volume_1h: float = 0.0
    volume_24h: float = 0.0

    buys_5m: int = 0
    sells_5m: int = 0

    change_5m: float = 0.0
    change_1h: float = 0.0

    pair_age_minutes: float = 0.0

    pump_launch: bool = False
    pump_migrated: bool = False

    dex_profile: bool = False
    dex_boosted: bool = False
    dex_ad: bool = False

    birdeye_seen: bool = False
    gmgn_seen: bool = False

    score: int = 0
    score_reason: str = ""


# ============================================================
# POSITION MODEL
# ============================================================

@dataclass
class Position:
    mint: str
    symbol: str

    entry_price: float
    current_price: float

    quantity: float
    remaining_quantity: float

    gross_cost: float

    stop_price: float
    highest_price: float

    opened_at: float

    tp1_done: bool = False
    tp2_done: bool = False

    trailing_active: bool = False
    trailing_stop: float = 0.0

    realized_pnl: float = 0.0

    entry_score: int = 0
    entry_sources: str = ""


# ============================================================
# CANDIDATE BOOK
# ============================================================

class CandidateBook:

    def __init__(self):
        self.items: Dict[str, Candidate] = {}
        self.lock = asyncio.Lock()

    async def touch(self, mint, source, **kwargs):
        if not mint:
            return None

        async with self.lock:
            candidate = self.items.get(mint)

            if not candidate:
                candidate = Candidate(mint=mint)
                self.items[mint] = candidate

            candidate.sources.add(source)
            candidate.last_seen = now_ts()

            for key, value in kwargs.items():
                if (
                    hasattr(candidate, key)
                    and value not in (None, "")
                ):
                    setattr(candidate, key, value)

            return candidate

    async def snapshot(self):
        cutoff = now_ts() - CANDIDATE_TTL_MIN * 60

        async with self.lock:
            expired = [
                mint
                for mint, candidate in self.items.items()
                if candidate.last_seen < cutoff
            ]

            for mint in expired:
                self.items.pop(mint, None)

            return list(self.items.values())


# ============================================================
# ACCOUNT STATE
# ============================================================

class AccountState:

    def __init__(self):
        self.balance = STARTING_BALANCE

        self.positions: Dict[str, Position] = {}
        self.trades: List[dict] = []

        self.realized_pnl = 0.0

        self.date = today()

        self.daily_start_equity = STARTING_BALANCE
        self.daily_realized_pnl = 0.0
        self.daily_entries = 0

        self.wins = 0
        self.losses = 0

        self.consecutive_losses = 0

        self.paused = False
        self.pause_reason = ""

    def position_value(self):
        return sum(
            position.remaining_quantity
            * position.current_price
            for position in self.positions.values()
        )

    def equity(self):
        return self.balance + self.position_value()

    def reset_day_if_needed(self):
        if self.date == today():
            return

        self.date = today()
        self.daily_start_equity = self.equity()

        self.daily_realized_pnl = 0.0
        self.daily_entries = 0
        self.consecutive_losses = 0

        self.paused = False
        self.pause_reason = ""

        self.save()

    def save(self):
        data = {
            "balance": self.balance,
            "positions": {
                mint: asdict(position)
                for mint, position in self.positions.items()
            },
            "trades": self.trades,
            "realized_pnl": self.realized_pnl,
            "date": self.date,
            "daily_start_equity": self.daily_start_equity,
            "daily_realized_pnl": self.daily_realized_pnl,
            "daily_entries": self.daily_entries,
            "wins": self.wins,
            "losses": self.losses,
            "consecutive_losses": self.consecutive_losses,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
        }

        with open(STATE_FILE, "w") as file:
            json.dump(data, file, indent=2)

    @classmethod
    def load(cls):
        state = cls()

        if not os.path.exists(STATE_FILE):
            state.save()
            return state

        try:
            with open(STATE_FILE) as file:
                data = json.load(file)

            state.balance = data.get(
                "balance",
                STARTING_BALANCE,
            )

            state.positions = {
                mint: Position(**position_data)
                for mint, position_data in data.get(
                    "positions",
                    {},
                ).items()
            }

            state.trades = data.get(
                "trades",
                [],
            )

            state.realized_pnl = data.get(
                "realized_pnl",
                0.0,
            )

            state.date = data.get(
                "date",
                today(),
            )

            state.daily_start_equity = data.get(
                "daily_start_equity",
                STARTING_BALANCE,
            )

            state.daily_realized_pnl = data.get(
                "daily_realized_pnl",
                0.0,
            )

            state.daily_entries = data.get(
                "daily_entries",
                0,
            )

            state.wins = data.get(
                "wins",
                0,
            )

            state.losses = data.get(
                "losses",
                0,
            )

            state.consecutive_losses = data.get(
                "consecutive_losses",
                0,
            )

            state.paused = data.get(
                "paused",
                False,
            )

            state.pause_reason = data.get(
                "pause_reason",
                "",
            )

            state.reset_day_if_needed()

            return state

        except Exception:
            log.exception("State file failed to load.")

            state = cls()
            state.save()

            return state


# ============================================================
# PUMP.FUN / PUMPSWAP
# NO websockets PACKAGE USED
# ============================================================

async def pumpportal_loop(book):

    url = PUMP_WS

    if PUMPPORTAL_API_KEY:
        url += "?api-key=" + PUMPPORTAL_API_KEY

    while True:
        try:
            timeout = aiohttp.ClientTimeout(
                total=None,
                sock_connect=15,
                sock_read=None,
            )

            async with aiohttp.ClientSession(
                timeout=timeout
            ) as session:

                async with session.ws_connect(
                    url,
                    heartbeat=20,
                    autoping=True,
                    autoclose=True,
                ) as websocket:

                    await websocket.send_json({
                        "method": "subscribeNewToken"
                    })

                    await websocket.send_json({
                        "method": "subscribeMigration"
                    })

                    log.info("PumpPortal connected.")

                    async for ws_message in websocket:

                        if ws_message.type == aiohttp.WSMsgType.TEXT:
                            try:
                                message = json.loads(
                                    ws_message.data
                                )
                            except Exception:
                                continue

                            mint = (
                                message.get("mint")
                                or message.get("tokenAddress")
                                or message.get("address")
                            )

                            if not mint:
                                continue

                            event_type = str(
                                message.get("txType")
                                or message.get("type")
                                or message.get("event")
                                or ""
                            ).lower()

                            migrated = (
                                "migration" in event_type
                                or "migrate" in event_type
                                or "complete" in event_type
                            )

                            await book.touch(
                                mint,
                                "pump",
                                symbol=message.get(
                                    "symbol",
                                    "",
                                ),
                                name=message.get(
                                    "name",
                                    "",
                                ),
                                pump_launch=not migrated,
                                pump_migrated=migrated,
                            )

                        elif ws_message.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING,
                        ):
                            break

                        elif (
                            ws_message.type
                            ==
                            aiohttp.WSMsgType.ERROR
                        ):
                            break

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            log.warning(
                "PumpPortal reconnecting: %s",
                exc,
            )

            await asyncio.sleep(5)


# ============================================================
# DEX SCREENER DISCOVERY
# ============================================================

async def dexscreener_loop(book):

    endpoints = [
        (
            "/token-profiles/latest/v1",
            "dex_profile",
        ),
        (
            "/token-boosts/latest/v1",
            "dex_boost",
        ),
        (
            "/token-boosts/top/v1",
            "dex_boost",
        ),
        (
            "/ads/latest/v1",
            "dex_ad",
        ),
    ]

    async with aiohttp.ClientSession() as session:

        while True:
            try:
                for path, source in endpoints:

                    data = await get_json(
                        session,
                        DEX_BASE + path,
                    )

                    if not isinstance(
                        data,
                        list,
                    ):
                        continue

                    for row in data[:DISCOVERY_LIMIT]:

                        if (
                            row.get("chainId")
                            !=
                            "solana"
                        ):
                            continue

                        mint = row.get(
                            "tokenAddress"
                        )

                        await book.touch(
                            mint,
                            source,
                            dex_profile=(
                                source
                                ==
                                "dex_profile"
                            ),
                            dex_boosted=(
                                source
                                ==
                                "dex_boost"
                            ),
                            dex_ad=(
                                source
                                ==
                                "dex_ad"
                            ),
                        )

            except Exception:
                log.exception(
                    "DEX Screener discovery error"
                )

            await asyncio.sleep(
                DEX_SCAN_SEC
            )


# ============================================================
# BIRDEYE
# ============================================================

async def birdeye_loop(book):

    if not BIRDEYE_API_KEY:
        log.info(
            "Birdeye disabled."
        )
        return

    headers = {
        "X-API-KEY": BIRDEYE_API_KEY,
        "x-chain": "solana",
        "accept": "application/json",
    }

    async with aiohttp.ClientSession() as session:

        while True:
            try:
                data = await get_json(
                    session,
                    (
                        BIRDEYE_BASE
                        +
                        "/defi/v2/tokens/new_listing"
                    ),
                    headers=headers,
                    params={
                        "limit": 20,
                        "time_to": int(
                            time.time()
                        ),
                        "meme_platform_enabled":
                            "true",
                    },
                )

                rows = []

                if isinstance(data, dict):
                    payload = data.get(
                        "data",
                        data,
                    )

                    if isinstance(
                        payload,
                        list,
                    ):
                        rows = payload

                    elif isinstance(
                        payload,
                        dict,
                    ):
                        rows = (
                            payload.get("items")
                            or payload.get("tokens")
                            or payload.get("list")
                            or []
                        )

                for row in rows:

                    mint = (
                        row.get("address")
                        or row.get("token_address")
                        or row.get("tokenAddress")
                        or row.get("mint")
                    )

                    await book.touch(
                        mint,
                        "birdeye",
                        symbol=row.get(
                            "symbol",
                            "",
                        ),
                        name=row.get(
                            "name",
                            "",
                        ),
                        birdeye_seen=True,
                    )

            except Exception:
                log.exception(
                    "Birdeye error"
                )

            await asyncio.sleep(
                BIRDEYE_SCAN_SEC
            )


# ============================================================
# GMGN
# ============================================================

async def gmgn_loop(book):

    if not (
        GMGN_API_KEY
        and
        GMGN_OPENAPI_URL
    ):
        log.info(
            "GMGN disabled."
        )
        return

    headers = {
        "Authorization":
            f"Bearer {GMGN_API_KEY}",
        "x-api-key":
            GMGN_API_KEY,
        "accept":
            "application/json",
    }

    async with aiohttp.ClientSession() as session:

        while True:
            try:
                data = await get_json(
                    session,
                    GMGN_OPENAPI_URL,
                    headers=headers,
                )

                rows = []

                if isinstance(
                    data,
                    list,
                ):
                    rows = data

                elif isinstance(
                    data,
                    dict,
                ):
                    payload = data.get(
                        "data",
                        data,
                    )

                    if isinstance(
                        payload,
                        list,
                    ):
                        rows = payload

                    elif isinstance(
                        payload,
                        dict,
                    ):
                        rows = (
                            payload.get("items")
                            or payload.get("tokens")
                            or payload.get("list")
                            or []
                        )

                for row in rows[:50]:

                    mint = (
                        row.get("mint")
                        or row.get("address")
                        or row.get("token_address")
                        or row.get("tokenAddress")
                    )

                    if not mint:
                        continue

                    await book.touch(
                        mint,
                        "gmgn",
                        symbol=row.get(
                            "symbol",
                            "",
                        ),
                        name=row.get(
                            "name",
                            "",
                        ),
                        gmgn_seen=True,
                    )

            except Exception:
                log.exception(
                    "GMGN error"
                )

            await asyncio.sleep(
                GMGN_SCAN_SEC
            )


# ============================================================
# DEX MARKET DATA
# ============================================================

async def enrich_candidate(
    session,
    candidate,
):

    data = await get_json(
        session,
        (
            f"{DEX_BASE}"
            f"/token-pairs/v1/"
            f"solana/"
            f"{candidate.mint}"
        ),
    )

    if (
        not isinstance(
            data,
            list,
        )
        or
        not data
    ):
        return False

    def pair_liquidity(pair):
        return num(
            (
                pair.get("liquidity")
                or {}
            ).get("usd")
        )

    pair = max(
        data,
        key=pair_liquidity,
    )

    base = (
        pair.get("baseToken")
        or {}
    )

    candidate.symbol = (
        candidate.symbol
        or
        base.get("symbol", "")
    )

    candidate.name = (
        candidate.name
        or
        base.get("name", "")
    )

    candidate.price = num(
        pair.get("priceUsd")
    )

    candidate.liquidity = (
        pair_liquidity(pair)
    )

    candidate.market_cap = num(
        pair.get("marketCap")
        or
        pair.get("fdv")
    )

    volume = (
        pair.get("volume")
        or {}
    )

    candidate.volume_5m = num(
        volume.get("m5")
    )

    candidate.volume_1h = num(
        volume.get("h1")
    )

    candidate.volume_24h = num(
        volume.get("h24")
    )

    tx5 = (
        (
            pair.get("txns")
            or {}
        ).get("m5")
        or {}
    )

    candidate.buys_5m = int(
        tx5.get("buys")
        or 0
    )

    candidate.sells_5m = int(
        tx5.get("sells")
        or 0
    )

    changes = (
        pair.get("priceChange")
        or {}
    )

    candidate.change_5m = num(
        changes.get("m5")
    )

    candidate.change_1h = num(
        changes.get("h1")
    )

    created = pair.get(
        "pairCreatedAt"
    )

    if created:
        candidate.pair_age_minutes = max(
            0.0,
            (
                time.time()
                -
                float(created)
                /
                1000
            )
            /
            60,
        )

    return candidate.price > 0


# ============================================================
# SCORING
# ============================================================

def score_candidate(candidate):

    if candidate.price <= 0:
        return 0, "No price"

    if (
        candidate.liquidity
        <
        MIN_LIQUIDITY
    ):
        return 0, "Low liquidity"

    if (
        candidate.volume_24h
        <
        MIN_VOLUME_24H
    ):
        return 0, "Low volume"

    if (
        candidate.volume_1h
        <
        MIN_VOLUME_1H
    ):
        return 0, "Low 1h volume"

    if not (
        MIN_MARKET_CAP
        <=
        candidate.market_cap
        <=
        MAX_MARKET_CAP
    ):
        return 0, "Market cap rejected"

    if (
        candidate.pair_age_minutes
        <=
        0
    ):
        return 0, "Unknown age"

    if (
        candidate.pair_age_minutes
        <
        MIN_PAIR_AGE_MIN
    ):
        return 0, "Too fresh"

    if (
        candidate.pair_age_minutes
        >
        MAX_PAIR_AGE_DAYS
        *
        1440
    ):
        return 0, "Too old"

    if not (
        MIN_5M_CHANGE
        <=
        candidate.change_5m
        <=
        MAX_5M_CHANGE
    ):
        return 0, "5m momentum rejected"

    if not (
        MIN_1H_CHANGE
        <=
        candidate.change_1h
        <=
        MAX_1H_CHANGE
    ):
        return 0, "1h momentum rejected"

    if (
        candidate.buys_5m
        <
        MIN_5M_BUYS
    ):
        return 0, "Too few buyers"

    total_5m = (
        candidate.buys_5m
        +
        candidate.sells_5m
    )

    buy_ratio = (
        candidate.buys_5m
        /
        total_5m
        if total_5m
        else 0.0
    )

    if (
        buy_ratio
        <
        MIN_BUY_RATIO
    ):
        return 0, "Weak buy pressure"

    score = 0

    # Source confirmation
    score += min(
        12,
        len(candidate.sources) * 4,
    )

    if candidate.pump_migrated:
        score += 3

    # Liquidity
    if candidate.liquidity >= 150_000:
        score += 15

    elif candidate.liquidity >= 100_000:
        score += 13

    elif candidate.liquidity >= 60_000:
        score += 11

    else:
        score += 8

    # Volume
    if candidate.volume_5m >= 25_000:
        score += 10

    elif candidate.volume_5m >= 10_000:
        score += 8

    elif candidate.volume_5m >= 5_000:
        score += 5

    acceleration = (
        candidate.volume_5m
        /
        candidate.volume_1h
        if candidate.volume_1h
        else 0
    )

    if acceleration >= 0.20:
        score += 5

    elif acceleration >= 0.10:
        score += 3

    # Buy pressure
    if buy_ratio >= 0.72:
        score += 15

    elif buy_ratio >= 0.66:
        score += 13

    elif buy_ratio >= 0.62:
        score += 11

    else:
        score += 8

    # Momentum
    if (
        3
        <=
        candidate.change_5m
        <=
        15
    ):
        score += 8

    elif (
        1
        <=
        candidate.change_5m
        <=
        20
    ):
        score += 6

    else:
        score += 3

    if (
        5
        <=
        candidate.change_1h
        <=
        40
    ):
        score += 7

    elif (
        0
        <=
        candidate.change_1h
        <=
        70
    ):
        score += 5

    else:
        score += 2

    # Market cap
    if (
        75_000
        <=
        candidate.market_cap
        <=
        1_000_000
    ):
        score += 10

    elif candidate.market_cap <= 2_000_000:
        score += 8

    else:
        score += 5

    # Age
    if (
        5
        <=
        candidate.pair_age_minutes
        <=
        180
    ):
        score += 5

    elif candidate.pair_age_minutes <= 720:
        score += 4

    else:
        score += 2

    # Optional sources
    if candidate.gmgn_seen:
        score += 3

    if candidate.birdeye_seen:
        score += 2

    score = min(
        100,
        int(score),
    )

    return (
        score,
        (
            f"sources="
            f"{','.join(sorted(candidate.sources))}; "
            f"buy_ratio="
            f"{buy_ratio:.2f}"
        ),
    )


# ============================================================
# SAFETY
# ============================================================

def apply_guards(state):

    state.reset_day_if_needed()

    if (
        state.daily_realized_pnl
        >=
        DAILY_PROFIT_TARGET
    ):
        state.paused = True
        state.pause_reason = (
            "Daily +$50 target reached"
        )

    max_loss = (
        state.daily_start_equity
        *
        MAX_DAILY_LOSS
    )

    if (
        state.daily_realized_pnl
        <=
        -max_loss
    ):
        state.paused = True
        state.pause_reason = (
            "3% daily loss breaker"
        )

    if (
        state.consecutive_losses
        >=
        MAX_CONSECUTIVE_LOSSES
    ):
        state.paused = True
        state.pause_reason = (
            "2 consecutive losses"
        )

    return not state.paused


def can_enter(state):

    if not apply_guards(state):
        return False

    if (
        len(state.positions)
        >=
        MAX_OPEN_POSITIONS
    ):
        return False

    if (
        state.daily_entries
        >=
        MAX_DAILY_ENTRIES
    ):
        return False

    return True


# ============================================================
# POSITION SIZE
# ============================================================

def calculate_position_value(state):

    risk_dollars = (
        state.equity()
        *
        RISK_PER_TRADE
    )

    position_value = (
        risk_dollars
        /
        STOP_LOSS_PCT
    )

    position_value = min(
        position_value,
        state.equity()
        *
        MAX_POSITION_EQUITY_PCT,
        state.balance,
    )

    return max(
        0.0,
        position_value,
    )


# ============================================================
# PAPER TRADING
# ============================================================

def entry_fill(raw_price):
    return (
        raw_price
        *
        (
            1
            +
            ENTRY_SLIPPAGE
        )
    )


def exit_fill(raw_price):
    return (
        raw_price
        *
        (
            1
            -
            EXIT_SLIPPAGE
        )
    )


def open_paper_position(
    state,
    candidate,
):

    gross = (
        calculate_position_value(
            state
        )
    )

    if gross < MIN_POSITION_USD:
        return None

    fill = entry_fill(
        candidate.price
    )

    entry_fee = (
        gross
        *
        FEE_PCT
    )

    usable = (
        gross
        -
        entry_fee
    )

    quantity = (
        usable
        /
        fill
    )

    state.balance -= gross

    position = Position(
        mint=candidate.mint,
        symbol=(
            candidate.symbol
            or
            candidate.mint[:6]
        ),
        entry_price=fill,
        current_price=candidate.price,
        quantity=quantity,
        remaining_quantity=quantity,
        gross_cost=gross,
        stop_price=(
            fill
            *
            (
                1
                -
                STOP_LOSS_PCT
            )
        ),
        highest_price=fill,
        opened_at=now_ts(),
        entry_score=candidate.score,
        entry_sources=",".join(
            sorted(
                candidate.sources
            )
        ),
    )

    state.positions[
        candidate.mint
    ] = position

    state.daily_entries += 1

    state.save()

    return position


def sell_quantity(
    state,
    position,
    quantity,
    raw_price,
):

    quantity = min(
        quantity,
        position.remaining_quantity,
    )

    if quantity <= 0:
        return 0.0

    fill = exit_fill(
        raw_price
    )

    gross_proceeds = (
        quantity
        *
        fill
    )

    exit_fee = (
        gross_proceeds
        *
        FEE_PCT
    )

    net_proceeds = (
        gross_proceeds
        -
        exit_fee
    )

    allocated_entry = (
        quantity
        *
        position.entry_price
    )

    pnl = (
        net_proceeds
        -
        allocated_entry
    )

    position.remaining_quantity -= (
        quantity
    )

    position.realized_pnl += (
        pnl
    )

    state.balance += (
        net_proceeds
    )

    state.realized_pnl += (
        pnl
    )

    state.daily_realized_pnl += (
        pnl
    )

    return pnl


def partial_sell(
    state,
    position,
    fraction,
    raw_price,
):

    quantity = (
        position.quantity
        *
        fraction
    )

    pnl = sell_quantity(
        state,
        position,
        quantity,
        raw_price,
    )

    state.save()

    return pnl


def close_position(
    state,
    position,
    raw_price,
    reason,
):

    sell_quantity(
        state,
        position,
        position.remaining_quantity,
        raw_price,
    )

    trade_pnl = (
        position.realized_pnl
    )

    if trade_pnl > 0:
        state.wins += 1
        state.consecutive_losses = 0

    else:
        state.losses += 1
        state.consecutive_losses += 1

    state.trades.append({
        "mint":
            position.mint,
        "symbol":
            position.symbol,
        "entry":
            position.entry_price,
        "exit":
            raw_price,
        "pnl":
            trade_pnl,
        "reason":
            reason,
        "entry_score":
            position.entry_score,
        "sources":
            position.entry_sources,
        "opened_at":
            position.opened_at,
        "closed_at":
            now_ts(),
    })

    state.positions.pop(
        position.mint,
        None,
    )

    apply_guards(
        state
    )

    state.save()

    return trade_pnl


# ============================================================
# MANAGE POSITION
# ============================================================

def manage_position(
    state,
    position,
    raw_price,
):

    events = []

    position.current_price = (
        raw_price
    )

    if (
        raw_price
        >
        position.highest_price
    ):
        position.highest_price = (
            raw_price
        )

    gain = (
        raw_price
        /
        position.entry_price
        -
        1
    )

    # STOP
    if (
        raw_price
        <=
        position.stop_price
    ):
        pnl = close_position(
            state,
            position,
            raw_price,
            "STOP LOSS",
        )

        events.append(
            (
                "close",
                pnl,
            )
        )

        return events

    # TP1
    if (
        gain >= TP1_PCT
        and
        not position.tp1_done
    ):
        position.tp1_done = True

        pnl = partial_sell(
            state,
            position,
            TP1_SELL,
            raw_price,
        )

        events.append(
            (
                "TP1 +10%",
                pnl,
            )
        )

    # TP2
    if (
        gain >= TP2_PCT
        and
        not position.tp2_done
    ):
        position.tp2_done = True

        pnl = partial_sell(
            state,
            position,
            TP2_SELL,
            raw_price,
        )

        events.append(
            (
                "TP2 +18%",
                pnl,
            )
        )

    # TRAIL
    if gain >= TRAIL_ACTIVATE_PCT:
        position.trailing_active = True

    if position.trailing_active:

        new_stop = (
            position.highest_price
            *
            (
                1
                -
                TRAIL_DISTANCE_PCT
            )
        )

        position.trailing_stop = max(
            position.trailing_stop,
            new_stop,
            position.entry_price,
        )

        if (
            raw_price
            <=
            position.trailing_stop
        ):
            pnl = close_position(
                state,
                position,
                raw_price,
                "TRAILING STOP",
            )

            events.append(
                (
                    "close",
                    pnl,
                )
            )

            return events

    state.save()

    return events


# ============================================================
# TELEGRAM
# ============================================================

async def notify(
    application,
    text,
):

    if not TELEGRAM_CHAT_ID:
        return

    try:
        await application.bot.send_message(
            chat_id=
                TELEGRAM_CHAT_ID,
            text=
                text,
        )

    except Exception as exc:
        log.warning(
            "Telegram error: %s",
            exc,
        )


def win_rate(state):

    total = (
        state.wins
        +
        state.losses
    )

    if not total:
        return 0.0

    return (
        state.wins
        /
        total
        *
        100
    )


# ============================================================
# COMMANDS
# ============================================================

async def cmd_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    state = (
        context.application.bot_data[
            "state"
        ]
    )

    state.reset_day_if_needed()

    await update.message.reply_text(
        "🤖 JARVIS V4 — PAPER\n\n"
        f"Cash: ${state.balance:.2f}\n"
        f"Equity: ${state.equity():.2f}\n"
        f"Total P/L: ${state.realized_pnl:+.2f}\n"
        f"Today's P/L: ${state.daily_realized_pnl:+.2f}\n\n"
        f"Wins: {state.wins}\n"
        f"Losses: {state.losses}\n"
        f"Win rate: {win_rate(state):.1f}%\n\n"
        f"Open positions: {len(state.positions)}/{MAX_OPEN_POSITIONS}\n"
        f"Entries: {state.daily_entries}/{MAX_DAILY_ENTRIES}\n"
        f"Loss streak: {state.consecutive_losses}/{MAX_CONSECUTIVE_LOSSES}\n\n"
        f"Risk/trade: {RISK_PER_TRADE*100:.1f}%\n"
        f"Stop: {STOP_LOSS_PCT*100:.1f}%\n"
        f"TP1: +{TP1_PCT*100:.0f}%\n"
        f"TP2: +{TP2_PCT*100:.0f}%\n"
        f"Minimum score: {ENTRY_SCORE}/100\n\n"
        f"Status: "
        f"{'🔴 PAUSED — ' + state.pause_reason if state.paused else '🟢 RESEARCHING'}\n"
        "Mode: 🧪 PAPER TRADING"
    )


async def cmd_today(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    state = (
        context.application.bot_data[
            "state"
        ]
    )

    await update.message.reply_text(
        "📅 TODAY\n\n"
        f"P&L: ${state.daily_realized_pnl:+.2f}\n"
        f"Target: +${DAILY_PROFIT_TARGET:.2f}\n"
        f"Entries: {state.daily_entries}/{MAX_DAILY_ENTRIES}\n"
        f"Positions: {len(state.positions)}\n"
        f"Loss streak: {state.consecutive_losses}/{MAX_CONSECUTIVE_LOSSES}"
    )


async def cmd_positions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    state = (
        context.application.bot_data[
            "state"
        ]
    )

    if not state.positions:
        await update.message.reply_text(
            "No open paper positions."
        )
        return

    lines = [
        "📊 OPEN POSITIONS"
    ]

    for position in (
        state.positions.values()
    ):

        pnl_pct = (
            position.current_price
            /
            position.entry_price
            -
            1
        ) * 100

        lines.append(
            f"\n{position.symbol}\n"
            f"P&L: {pnl_pct:+.1f}%\n"
            f"Score: {position.entry_score}/100\n"
            f"Entry: ${position.entry_price:.10f}\n"
            f"Current: ${position.current_price:.10f}\n"
            f"Sources: {position.entry_sources}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def cmd_trades(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    state = (
        context.application.bot_data[
            "state"
        ]
    )

    if not state.trades:
        await update.message.reply_text(
            "No completed paper trades."
        )
        return

    lines = [
        "📜 RECENT TRADES"
    ]

    for trade in reversed(
        state.trades[-10:]
    ):

        icon = (
            "✅"
            if trade["pnl"] > 0
            else "❌"
        )

        lines.append(
            f"{icon} "
            f"{trade['symbol']} "
            f"${trade['pnl']:+.2f} | "
            f"{trade['reason']} | "
            f"score {trade.get('entry_score', 0)}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def cmd_sources(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    lines = [
        "🔎 JARVIS SOURCES\n",
        "✅ Pump.fun/PumpSwap",
        "✅ DEX Screener profiles",
        "✅ DEX Screener latest boosts",
        "✅ DEX Screener top boosts",
        "✅ DEX Screener ads",
        "✅ DEX Screener pair data",
    ]

    if BIRDEYE_API_KEY:
        lines.append(
            "✅ Birdeye"
        )
    else:
        lines.append(
            "⚪ Birdeye disabled"
        )

    if (
        GMGN_API_KEY
        and
        GMGN_OPENAPI_URL
    ):
        lines.append(
            "✅ GMGN"
        )
    else:
        lines.append(
            "⚪ GMGN disabled"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def cmd_pause(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    state = (
        context.application.bot_data[
            "state"
        ]
    )

    state.paused = True
    state.pause_reason = (
        "Manual pause"
    )

    state.save()

    await update.message.reply_text(
        "⏸ Jarvis paused."
    )


async def cmd_resume(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    state = (
        context.application.bot_data[
            "state"
        ]
    )

    state.reset_day_if_needed()

    max_loss = (
        state.daily_start_equity
        *
        MAX_DAILY_LOSS
    )

    if (
        state.daily_realized_pnl
        >=
        DAILY_PROFIT_TARGET
    ):
        await update.message.reply_text(
            "🎯 Daily target reached. "
            "Jarvis remains locked until tomorrow."
        )
        return

    if (
        state.daily_realized_pnl
        <=
        -max_loss
    ):
        await update.message.reply_text(
            "🛑 Daily loss limit reached. "
            "Jarvis remains locked until tomorrow."
        )
        return

    if (
        state.consecutive_losses
        >=
        MAX_CONSECUTIVE_LOSSES
    ):
        await update.message.reply_text(
            "🛑 Two-loss breaker reached. "
            "Jarvis remains locked until tomorrow."
        )
        return

    state.paused = False
    state.pause_reason = ""

    state.save()

    await update.message.reply_text(
        "▶️ Jarvis resumed."
    )


async def cmd_paper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🧪 PAPER MODE ONLY.\n\n"
        "No private keys.\n"
        "No wallet signing.\n"
        "No real-money trades."
    )


async def cmd_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🤖 JARVIS COMMANDS\n\n"
        "/status\n"
        "/today\n"
        "/positions\n"
        "/trades\n"
        "/sources\n"
        "/pause\n"
        "/resume\n"
        "/paper\n"
        "/help"
    )


# ============================================================
# EVALUATION LOOP
# ============================================================

async def evaluation_loop(
    application,
    book,
    state,
):

    async with aiohttp.ClientSession() as session:

        while True:
            try:
                state.reset_day_if_needed()

                candidates = (
                    await book.snapshot()
                )

                discovered = len(
                    candidates
                )

                priced = 0
                qualified = 0

                ranked = []

                for candidate in candidates:

                    if (
                        candidate.mint
                        in
                        state.positions
                    ):
                        continue

                    market_ok = (
                        await enrich_candidate(
                            session,
                            candidate,
                        )
                    )

                    if not market_ok:
                        continue

                    priced += 1

                    (
                        candidate.score,
                        candidate.score_reason,
                    ) = score_candidate(
                        candidate
                    )

                    if (
                        candidate.score
                        >=
                        ENTRY_SCORE
                        and
                        len(candidate.sources)
                        >=
                        MIN_SOURCES_FOR_ENTRY
                    ):
                        qualified += 1
                        ranked.append(
                            candidate
                        )

                ranked.sort(
                    key=lambda candidate: (
                        candidate.score,
                        len(candidate.sources),
                        candidate.volume_5m,
                    ),
                    reverse=True,
                )

                log.info(
                    "Scan: discovered=%s "
                    "priced=%s "
                    "qualified=%s",
                    discovered,
                    priced,
                    qualified,
                )

                for candidate in ranked:

                    if not can_enter(state):
                        break

                    if (
                        candidate.mint
                        in
                        state.positions
                    ):
                        continue

                    position = (
                        open_paper_position(
                            state,
                            candidate,
                        )
                    )

                    if not position:
                        continue

                    tier = (
                        "🔥 ELITE"
                        if candidate.score
                        >=
                        ELITE_SCORE
                        else
                        "✅ QUALIFIED"
                    )

                    await notify(
                        application,
                        "🚀 PAPER ENTRY\n\n"
                        f"{tier}\n"
                        f"{position.symbol}\n"
                        f"Score: {candidate.score}/100\n"
                        f"Sources: {', '.join(sorted(candidate.sources))}\n\n"
                        f"Entry: ${position.entry_price:.10f}\n"
                        f"Position: ${position.gross_cost:.2f}\n"
                        f"Risk: {RISK_PER_TRADE*100:.1f}%\n"
                        f"Stop: ${position.stop_price:.10f}\n\n"
                        f"Liquidity: ${candidate.liquidity:,.0f}\n"
                        f"Market cap: ${candidate.market_cap:,.0f}\n"
                        f"5m volume: ${candidate.volume_5m:,.0f}\n"
                        f"1h volume: ${candidate.volume_1h:,.0f}\n"
                        f"5m move: {candidate.change_5m:+.1f}%\n"
                        f"1h move: {candidate.change_1h:+.1f}%\n"
                        f"5m buys/sells: "
                        f"{candidate.buys_5m}/"
                        f"{candidate.sells_5m}"
                    )

            except Exception:
                log.exception(
                    "Evaluation loop error"
                )

            await asyncio.sleep(
                EVALUATION_SEC
            )


# ============================================================
# PRICE LOOP
# ============================================================

async def price_loop(
    application,
    state,
):

    async with aiohttp.ClientSession() as session:

        while True:
            try:
                positions = list(
                    state.positions.values()
                )

                for position in positions:

                    candidate = Candidate(
                        mint=position.mint
                    )

                    market_ok = (
                        await enrich_candidate(
                            session,
                            candidate,
                        )
                    )

                    if not market_ok:
                        continue

                    events = (
                        manage_position(
                            state,
                            position,
                            candidate.price,
                        )
                    )

                    for event, pnl in events:

                        if event == "close":

                            await notify(
                                application,
                                "🏁 PAPER TRADE CLOSED\n\n"
                                f"{position.symbol}\n"
                                f"Trade P&L: ${pnl:+.2f}\n"
                                f"Daily P&L: ${state.daily_realized_pnl:+.2f}\n"
                                f"Equity: ${state.equity():.2f}\n"
                                f"Paused: "
                                f"{'YES — ' + state.pause_reason if state.paused else 'NO'}"
                            )

                        else:

                            await notify(
                                application,
                                f"💰 {event}\n\n"
                                f"{position.symbol}\n"
                                f"Realized: ${pnl:+.2f}\n"
                                f"Daily P&L: ${state.daily_realized_pnl:+.2f}"
                            )

            except Exception:
                log.exception(
                    "Price loop error"
                )

            await asyncio.sleep(
                PRICE_SEC
            )


# ============================================================
# STATUS LOOP
# ============================================================

async def status_loop(
    application,
    state,
):

    while True:
        await asyncio.sleep(
            STATUS_SEC
        )

        state.reset_day_if_needed()

        apply_guards(
            state
        )

        await notify(
            application,
            "🤖 JARVIS V4 STATUS\n\n"
            f"Cash: ${state.balance:.2f}\n"
            f"Equity: ${state.equity():.2f}\n"
            f"Daily P&L: ${state.daily_realized_pnl:+.2f}\n"
            f"Target: +${DAILY_PROFIT_TARGET:.0f}\n\n"
            f"Positions: {len(state.positions)}/{MAX_OPEN_POSITIONS}\n"
            f"Entries: {state.daily_entries}/{MAX_DAILY_ENTRIES}\n"
            f"W/L: {state.wins}/{state.losses} "
            f"({win_rate(state):.1f}%)\n"
            f"Loss streak: "
            f"{state.consecutive_losses}/"
            f"{MAX_CONSECUTIVE_LOSSES}\n\n"
            f"Status: "
            f"{'🔴 PAUSED — ' + state.pause_reason if state.paused else '🟢 RESEARCHING'}"
        )


# ============================================================
# STARTUP
# ============================================================

async def post_init(application):

    state = (
        application.bot_data[
            "state"
        ]
    )

    book = (
        application.bot_data[
            "book"
        ]
    )

    application.create_task(
        pumpportal_loop(
            book
        )
    )

    application.create_task(
        dexscreener_loop(
            book
        )
    )

    application.create_task(
        birdeye_loop(
            book
        )
    )

    application.create_task(
        gmgn_loop(
            book
        )
    )

    application.create_task(
        evaluation_loop(
            application,
            book,
            state,
        )
    )

    application.create_task(
        price_loop(
            application,
            state,
        )
    )

    application.create_task(
        status_loop(
            application,
            state,
        )
    )

    active_sources = [
        "Pump.fun/PumpSwap",
        "DEX Screener",
    ]

    if BIRDEYE_API_KEY:
        active_sources.append(
            "Birdeye"
        )

    if (
        GMGN_API_KEY
        and
        GMGN_OPENAPI_URL
    ):
        active_sources.append(
            "GMGN"
        )

    await notify(
        application,
        "🤖 JARVIS V4 ONLINE\n\n"
        "Mode: PAPER ONLY\n"
        f"Equity: ${state.equity():.2f}\n"
        f"Daily target: +${DAILY_PROFIT_TARGET:.0f}\n"
        f"Risk/trade: {RISK_PER_TRADE*100:.1f}%\n"
        f"Daily loss limit: {MAX_DAILY_LOSS*100:.1f}%\n"
        f"Maximum positions: {MAX_OPEN_POSITIONS}\n"
        f"Loss streak stop: {MAX_CONSECUTIVE_LOSSES}\n"
        f"Entry score: {ENTRY_SCORE}/100\n"
        f"Sources: {', '.join(active_sources)}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if TRADING_MODE != "paper":
        raise RuntimeError(
            "This version supports "
            "TRADING_MODE=paper only."
        )

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN "
            "environment variable."
        )

    state = (
        AccountState.load()
    )

    book = (
        CandidateBook()
    )

    application = (
        ApplicationBuilder()
        .token(
            TELEGRAM_BOT_TOKEN
        )
        .post_init(
            post_init
        )
        .build()
    )

    application.bot_data[
        "state"
    ] = state

    application.bot_data[
        "book"
    ] = book

    application.add_handler(
        CommandHandler(
            "status",
            cmd_status,
        )
    )

    application.add_handler(
        CommandHandler(
            "today",
            cmd_today,
        )
    )

    application.add_handler(
        CommandHandler(
            "positions",
            cmd_positions,
        )
    )

    application.add_handler(
        CommandHandler(
            "trades",
            cmd_trades,
        )
    )

    application.add_handler(
        CommandHandler(
            "sources",
            cmd_sources,
        )
    )

    application.add_handler(
        CommandHandler(
            "pause",
            cmd_pause,
        )
    )

    application.add_handler(
        CommandHandler(
            "resume",
            cmd_resume,
        )
    )

    application.add_handler(
        CommandHandler(
            "paper",
            cmd_paper,
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            cmd_help,
        )
    )

    log.info(
        "Starting Jarvis V4..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
