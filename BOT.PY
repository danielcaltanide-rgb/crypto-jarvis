import os
import json
import time
import asyncio
import logging

from dataclasses import dataclass, field, asdict
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Set, List, Tuple

import aiohttp
import websockets

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# JARVIS V3
# MULTI-SOURCE SOLANA MEME PAPER TRADER
# ============================================================
#
# PAPER TRADING ONLY.
#
# DISCOVERY SOURCES:
#
# 1. Pump.fun / PumpSwap through PumpPortal
# 2. DEX Screener latest profiles
# 3. DEX Screener latest boosts
# 4. DEX Screener top boosts
# 5. DEX Screener ads
# 6. DEX Screener market/pair data
# 7. Birdeye new listings - optional
# 8. GMGN official API adapter - optional
#
#
# CURRENT STRATEGY:
#
# Starting account:        $1,000
# Daily target:            +$50
# Risk per trade:          1.5%
# Daily max loss:          3%
# Max open positions:      2
# Max trades/day:          10
# Consecutive loss pause:  2
#
# Stop loss:               6%
#
# TP1:                     +10%
# Sell:                    40%
#
# TP2:                     +18%
# Sell:                    35%
#
# Runner:                  25%
# Trail activates:         +20%
# Trailing distance:       8%
#
# ============================================================


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


# PumpPortal API key is optional for some realtime feeds.
PUMPPORTAL_API_KEY = os.getenv(
    "PUMPPORTAL_API_KEY",
    ""
)


# Optional Birdeye
BIRDEYE_API_KEY = os.getenv(
    "BIRDEYE_API_KEY",
    ""
)


# Optional GMGN
#
# GMGN API access varies depending on your account/product.
#
# Set:
#
# GMGN_API_KEY
# GMGN_OPENAPI_URL
#
# using the official values supplied by GMGN.
#
GMGN_API_KEY = os.getenv(
    "GMGN_API_KEY",
    ""
)

GMGN_OPENAPI_URL = os.getenv(
    "GMGN_OPENAPI_URL",
    ""
)


TRADING_MODE = os.getenv(
    "TRADING_MODE",
    "paper"
).lower()


STATE_FILE = os.getenv(
    "STATE_FILE",
    "jarvis_state.json"
)


# ============================================================
# TIMEZONE
# ============================================================

TZ = ZoneInfo(
    "Australia/Sydney"
)


# ============================================================
# ACCOUNT SETTINGS
# ============================================================

STARTING_BALANCE = 1000.00

DAILY_PROFIT_TARGET = 50.00


# ============================================================
# RISK SETTINGS
# ============================================================

RISK_PER_TRADE = 0.015

MAX_DAILY_LOSS = 0.03

MAX_CONSECUTIVE_LOSSES = 2

MAX_OPEN_POSITIONS = 2

MAX_DAILY_ENTRIES = 10


# Never put more than 25% equity
# into a single meme coin.
MAX_POSITION_EQUITY_PCT = 0.25

MIN_POSITION_USD = 10.00


# ============================================================
# SIMULATED EXECUTION
# ============================================================

# Paper trading should not pretend
# every order fills perfectly.

ENTRY_SLIPPAGE = 0.005

EXIT_SLIPPAGE = 0.0075

FEE_PCT = 0.002


# ============================================================
# EXIT SETTINGS
# ============================================================

STOP_LOSS_PCT = 0.06


TP1_PCT = 0.10

TP1_SELL = 0.40


TP2_PCT = 0.18

TP2_SELL = 0.35


TRAIL_ACTIVATE_PCT = 0.20

TRAIL_DISTANCE_PCT = 0.08


# ============================================================
# TOKEN FILTERS
# ============================================================

ENTRY_SCORE = 80

ELITE_SCORE = 90


# Require a token to appear
# through at least two discovery channels.
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


# ============================================================
# SCANNER TIMING
# ============================================================

CANDIDATE_TTL_MIN = 120

DISCOVERY_LIMIT = 40


DEX_SCAN_SEC = 25

BIRDEYE_SCAN_SEC = 35

GMGN_SCAN_SEC = 45

EVALUATION_SEC = 10

PRICE_SEC = 8

STATUS_SEC = 300


# ============================================================
# ENDPOINTS
# ============================================================

DEX_BASE = (
    "https://api.dexscreener.com"
)

BIRDEYE_BASE = (
    "https://public-api.birdeye.so"
)

PUMP_WS = (
    "wss://pumpportal.fun/api/data"
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    )
)

log = logging.getLogger(
    "jarvis"
)


# ============================================================
# HELPERS
# ============================================================

def num(
    value,
    default=0.0
):

    try:

        return float(
            value
        )

    except (
        TypeError,
        ValueError
    ):

        return default


def now_ts():

    return time.time()


def today():

    return datetime.now(
        TZ
    ).strftime(
        "%Y-%m-%d"
    )


async def get_json(
    session,
    url,
    headers=None,
    params=None
):

    try:

        async with session.get(
            url,
            headers=headers or {},
            params=params or {},
            timeout=aiohttp.ClientTimeout(
                total=12
            )
        ) as response:

            if response.status != 200:

                text = (
                    await response.text()
                )

                log.warning(
                    "GET %s -> %s %s",
                    url,
                    response.status,
                    text[:160]
                )

                return None

            return (
                await response.json()
            )

    except Exception as exc:

        log.warning(
            "GET failed %s: %s",
            url,
            exc
        )

        return None


# ============================================================
# TOKEN CANDIDATE MODEL
# ============================================================

@dataclass
class Candidate:

    mint: str

    symbol: str = ""

    name: str = ""


    sources: Set[str] = field(
        default_factory=set
    )


    first_seen: float = field(
        default_factory=now_ts
    )

    last_seen: float = field(
        default_factory=now_ts
    )


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
# CANDIDATE DATABASE
# ============================================================

class CandidateBook:

    def __init__(self):

        self.items: Dict[
            str,
            Candidate
        ] = {}

        self.lock = (
            asyncio.Lock()
        )


    async def touch(
        self,
        mint,
        source,
        **kwargs
    ):

        if not mint:

            return None


        async with self.lock:

            candidate = (
                self.items.get(
                    mint
                )
            )


            if not candidate:

                candidate = Candidate(
                    mint=mint
                )

                self.items[
                    mint
                ] = candidate


            candidate.sources.add(
                source
            )


            candidate.last_seen = (
                now_ts()
            )


            for key, value in (
                kwargs.items()
            ):

                if (
                    hasattr(
                        candidate,
                        key
                    )
                    and value
                    not in (
                        None,
                        ""
                    )
                ):

                    setattr(
                        candidate,
                        key,
                        value
                    )


            return candidate


    async def snapshot(
        self
    ):

        cutoff = (
            now_ts()
            -
            CANDIDATE_TTL_MIN
            *
            60
        )


        async with self.lock:

            expired = [

                mint

                for mint, candidate
                in self.items.items()

                if (
                    candidate.last_seen
                    <
                    cutoff
                )

            ]


            for mint in expired:

                self.items.pop(
                    mint,
                    None
                )


            return list(
                self.items.values()
            )


# ============================================================
# ACCOUNT STATE
# ============================================================

class AccountState:

    def __init__(self):

        self.balance = (
            STARTING_BALANCE
        )


        self.positions: Dict[
            str,
            Position
        ] = {}


        self.trades = []


        self.realized_pnl = 0.0


        self.date = today()


        self.daily_start_equity = (
            STARTING_BALANCE
        )


        self.daily_realized_pnl = 0.0


        self.daily_entries = 0


        self.wins = 0

        self.losses = 0


        self.consecutive_losses = 0


        self.paused = False

        self.pause_reason = ""


    def position_value(
        self
    ):

        return sum(

            position.remaining_quantity
            *
            position.current_price

            for position
            in self.positions.values()

        )


    def equity(
        self
    ):

        return (
            self.balance
            +
            self.position_value()
        )


    def reset_day_if_needed(
        self
    ):

        if (
            self.date
            ==
            today()
        ):

            return


        self.date = today()


        self.daily_start_equity = (
            self.equity()
        )


        self.daily_realized_pnl = 0.0

        self.daily_entries = 0

        self.consecutive_losses = 0


        self.paused = False

        self.pause_reason = ""


        self.save()


    def save(
        self
    ):

        data = {

            "balance":
                self.balance,


            "positions": {

                mint:
                    asdict(position)

                for mint, position
                in self.positions.items()

            },


            "trades":
                self.trades,


            "realized_pnl":
                self.realized_pnl,


            "date":
                self.date,


            "daily_start_equity":
                self.daily_start_equity,


            "daily_realized_pnl":
                self.daily_realized_pnl,


            "daily_entries":
                self.daily_entries,


            "wins":
                self.wins,


            "losses":
                self.losses,


            "consecutive_losses":
                self.consecutive_losses,


            "paused":
                self.paused,


            "pause_reason":
                self.pause_reason,

        }


        with open(
            STATE_FILE,
            "w"
        ) as file:

            json.dump(
                data,
                file,
                indent=2
            )


    @classmethod
    def load(
        cls
    ):

        state = cls()


        if not os.path.exists(
            STATE_FILE
        ):

            state.save()

            return state


        try:

            with open(
                STATE_FILE
            ) as file:

                data = json.load(
                    file
                )


            state.balance = (
                data.get(
                    "balance",
                    STARTING_BALANCE
                )
            )


            state.positions = {

                mint:
                    Position(
                        **position_data
                    )

                for mint, position_data
                in data.get(
                    "positions",
                    {}
                ).items()

            }


            state.trades = (
                data.get(
                    "trades",
                    []
                )
            )


            state.realized_pnl = (
                data.get(
                    "realized_pnl",
                    0.0
                )
            )


            state.date = (
                data.get(
                    "date",
                    today()
                )
            )


            state.daily_start_equity = (
                data.get(
                    "daily_start_equity",
                    STARTING_BALANCE
                )
            )


            state.daily_realized_pnl = (
                data.get(
                    "daily_realized_pnl",
                    0.0
                )
            )


            state.daily_entries = (
                data.get(
                    "daily_entries",
                    0
                )
            )


            state.wins = (
                data.get(
                    "wins",
                    0
                )
            )


            state.losses = (
                data.get(
                    "losses",
                    0
                )
            )


            state.consecutive_losses = (
                data.get(
                    "consecutive_losses",
                    0
                )
            )


            state.paused = (
                data.get(
                    "paused",
                    False
                )
            )


            state.pause_reason = (
                data.get(
                    "pause_reason",
                    ""
                )
            )


            state.reset_day_if_needed()


            return state


        except Exception:

            log.exception(
                "State file failed to load."
            )


            state = cls()

            state.save()


            return state


# ============================================================
# PUMP.FUN / PUMPSWAP DISCOVERY
# ============================================================

async def pumpportal_loop(
    book
):

    url = PUMP_WS


    if PUMPPORTAL_API_KEY:

        url += (
            "?api-key="
            +
            PUMPPORTAL_API_KEY
        )


    while True:

        try:

            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10
            ) as websocket:


                await websocket.send(
                    json.dumps({
                        "method":
                            "subscribeNewToken"
                    })
                )


                await websocket.send(
                    json.dumps({
                        "method":
                            "subscribeMigration"
                    })
                )


                log.info(
                    "PumpPortal connected."
                )


                async for raw_message in (
                    websocket
                ):

                    try:

                        message = (
                            json.loads(
                                raw_message
                            )
                        )

                    except Exception:

                        continue


                    mint = (

                        message.get(
                            "mint"
                        )

                        or message.get(
                            "tokenAddress"
                        )

                        or message.get(
                            "address"
                        )

                    )


                    if not mint:

                        continue


                    event_type = str(

                        message.get(
                            "txType"
                        )

                        or message.get(
                            "type"
                        )

                        or message.get(
                            "event"
                        )

                        or ""

                    ).lower()


                    migrated = (

                        "migration"
                        in event_type

                        or "migrate"
                        in event_type

                    )


                    await book.touch(

                        mint,

                        "pump",

                        symbol=message.get(
                            "symbol",
                            ""
                        ),

                        name=message.get(
                            "name",
                            ""
                        ),

                        pump_launch=(
                            not migrated
                        ),

                        pump_migrated=(
                            migrated
                        )

                    )


        except Exception as exc:

            log.warning(
                "PumpPortal error: %s",
                exc
            )


            await asyncio.sleep(
                5
            )


# ============================================================
# DEX SCREENER DISCOVERY
# ============================================================

async def dexscreener_loop(
    book
):

    endpoints = [

        (
            "/token-profiles/latest/v1",
            "dex_profile"
        ),

        (
            "/token-boosts/latest/v1",
            "dex_boost"
        ),

        (
            "/token-boosts/top/v1",
            "dex_boost"
        ),

        (
            "/ads/latest/v1",
            "dex_ad"
        ),

    ]


    async with aiohttp.ClientSession() as session:

        while True:

            try:

                for path, source in endpoints:

                    data = await get_json(
                        session,
                        DEX_BASE + path
                    )


                    if not isinstance(
                        data,
                        list
                    ):

                        continue


                    for row in (
                        data[
                            :DISCOVERY_LIMIT
                        ]
                    ):


                        if (
                            row.get(
                                "chainId"
                            )
                            !=
                            "solana"
                        ):

                            continue


                        mint = (
                            row.get(
                                "tokenAddress"
                            )
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
                    "DEX Screener loop error"
                )


            await asyncio.sleep(
                DEX_SCAN_SEC
            )


# ============================================================
# BIRDEYE DISCOVERY
# ============================================================

async def birdeye_loop(
    book
):

    if not BIRDEYE_API_KEY:

        log.info(
            "Birdeye disabled."
        )

        return


    headers = {

        "X-API-KEY":
            BIRDEYE_API_KEY,

        "x-chain":
            "solana",

        "accept":
            "application/json",

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

                        "limit":
                            20,

                        "time_to":
                            int(
                                time.time()
                            ),

                        "meme_platform_enabled":
                            "true",

                    }

                )


                rows = []


                if isinstance(
                    data,
                    dict
                ):

                    payload = (
                        data.get(
                            "data",
                            data
                        )
                    )


                    if isinstance(
                        payload,
                        list
                    ):

                        rows = payload


                    elif isinstance(
                        payload,
                        dict
                    ):

                        rows = (

                            payload.get(
                                "items"
                            )

                            or payload.get(
                                "tokens"
                            )

                            or payload.get(
                                "list"
                            )

                            or []

                        )


                for row in rows:

                    mint = (

                        row.get(
                            "address"
                        )

                        or row.get(
                            "token_address"
                        )

                        or row.get(
                            "tokenAddress"
                        )

                        or row.get(
                            "mint"
                        )

                    )


                    await book.touch(

                        mint,

                        "birdeye",

                        symbol=row.get(
                            "symbol",
                            ""
                        ),

                        name=row.get(
                            "name",
                            ""
                        ),

                        birdeye_seen=True

                    )


            except Exception:

                log.exception(
                    "Birdeye loop error"
                )


            await asyncio.sleep(
                BIRDEYE_SCAN_SEC
            )


# ============================================================
# GMGN DISCOVERY
# ============================================================

async def gmgn_loop(
    book
):

    if not (
        GMGN_API_KEY
        and
        GMGN_OPENAPI_URL
    ):

        log.info(
            "GMGN disabled. "
            "Add GMGN_API_KEY and "
            "GMGN_OPENAPI_URL."
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

                    headers=headers

                )


                rows = []


                if isinstance(
                    data,
                    list
                ):

                    rows = data


                elif isinstance(
                    data,
                    dict
                ):

                    payload = (
                        data.get(
                            "data",
                            data
                        )
                    )


                    if isinstance(
                        payload,
                        list
                    ):

                        rows = payload


                    elif isinstance(
                        payload,
                        dict
                    ):

                        rows = (

                            payload.get(
                                "items"
                            )

                            or payload.get(
                                "tokens"
                            )

                            or payload.get(
                                "list"
                            )

                            or []

                        )


                for row in rows[:50]:

                    mint = (

                        row.get(
                            "mint"
                        )

                        or row.get(
                            "address"
                        )

                        or row.get(
                            "token_address"
                        )

                        or row.get(
                            "tokenAddress"
                        )

                    )


                    if mint:

                        await book.touch(

                            mint,

                            "gmgn",

                            symbol=row.get(
                                "symbol",
                                ""
                            ),

                            name=row.get(
                                "name",
                                ""
                            ),

                            gmgn_seen=True

                        )


            except Exception:

                log.exception(
                    "GMGN loop error"
                )


            await asyncio.sleep(
                GMGN_SCAN_SEC
            )


# ============================================================
# DEX SCREENER MARKET ENRICHMENT
# ============================================================

async def enrich_candidate(
    session,
    candidate
):

    data = await get_json(

        session,

        (
            DEX_BASE
            +
            "/token-pairs/v1/"
            "solana/"
            +
            candidate.mint
        )

    )


    if (
        not isinstance(
            data,
            list
        )
        or
        not data
    ):

        return False


    def liquidity(
        pair
    ):

        return num(

            (
                pair.get(
                    "liquidity"
                )
                or {}
            ).get(
                "usd"
            )

        )


    # Use highest-liquidity pair.
    pair = max(
        data,
        key=liquidity
    )


    base_token = (
        pair.get(
            "baseToken"
        )
        or {}
    )


    candidate.symbol = (

        candidate.symbol

        or base_token.get(
            "symbol",
            ""
        )

    )


    candidate.name = (

        candidate.name

        or base_token.get(
            "name",
            ""
        )

    )


    candidate.price = num(
        pair.get(
            "priceUsd"
        )
    )


    candidate.liquidity = (
        liquidity(
            pair
        )
    )


    candidate.market_cap = num(

        pair.get(
            "marketCap"
        )

        or pair.get(
            "fdv"
        )

    )


    volume = (
        pair.get(
            "volume"
        )
        or {}
    )


    candidate.volume_5m = num(
        volume.get(
            "m5"
        )
    )


    candidate.volume_1h = num(
        volume.get(
            "h1"
        )
    )


    candidate.volume_24h = num(
        volume.get(
            "h24"
        )
    )


    transactions = (
        pair.get(
            "txns"
        )
        or {}
    )


    transactions_5m = (
        transactions.get(
            "m5"
        )
        or {}
    )


    candidate.buys_5m = int(
        transactions_5m.get(
            "buys"
        )
        or 0
    )


    candidate.sells_5m = int(
        transactions_5m.get(
            "sells"
        )
        or 0
    )


    changes = (
        pair.get(
            "priceChange"
        )
        or {}
    )


    candidate.change_5m = num(
        changes.get(
            "m5"
        )
    )


    candidate.change_1h = num(
        changes.get(
            "h1"
        )
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
                float(
                    created
                )
                /
                1000.0
            )
            /
            60.0

        )


    return (
        candidate.price
        >
        0
    )


# ============================================================
# TOKEN SCORING
# ============================================================

def score_candidate(
    candidate
):

    if candidate.price <= 0:

        return (
            0,
            "No price"
        )


    if (
        candidate.liquidity
        <
        MIN_LIQUIDITY
    ):

        return (
            0,
            "Low liquidity"
        )


    if (
        candidate.volume_24h
        <
        MIN_VOLUME_24H
    ):

        return (
            0,
            "Low 24h volume"
        )


    if (
        candidate.volume_1h
        <
        MIN_VOLUME_1H
    ):

        return (
            0,
            "Low 1h volume"
        )


    if not (
        MIN_MARKET_CAP
        <=
        candidate.market_cap
        <=
        MAX_MARKET_CAP
    ):

        return (
            0,
            "Market cap rejected"
        )


    if (
        candidate.pair_age_minutes
        <=
        0
    ):

        return (
            0,
            "Unknown age"
        )


    if (
        candidate.pair_age_minutes
        <
        MIN_PAIR_AGE_MIN
    ):

        return (
            0,
            "Too fresh"
        )


    if (
        candidate.pair_age_minutes
        >
        MAX_PAIR_AGE_DAYS
        *
        1440
    ):

        return (
            0,
            "Too old"
        )


    if not (
        MIN_5M_CHANGE
        <=
        candidate.change_5m
        <=
        MAX_5M_CHANGE
    ):

        return (
            0,
            "5m momentum rejected"
        )


    if not (
        MIN_1H_CHANGE
        <=
        candidate.change_1h
        <=
        MAX_1H_CHANGE
    ):

        return (
            0,
            "1h momentum rejected"
        )


    if (
        candidate.buys_5m
        <
        MIN_5M_BUYS
    ):

        return (
            0,
            "Too few buyers"
        )


    total_transactions = (

        candidate.buys_5m
        +
        candidate.sells_5m

    )


    buy_ratio = (

        candidate.buys_5m
        /
        total_transactions

        if total_transactions

        else 0

    )


    if (
        buy_ratio
        <
        MIN_BUY_RATIO
    ):

        return (
            0,
            "Buy pressure weak"
        )


    score = 0


    # ========================================================
    # SOURCE CONFIRMATION
    # MAX 15
    # ========================================================

    score += min(

        12,

        len(
            candidate.sources
        )
        *
        4

    )


    if candidate.pump_migrated:

        score += 3


    # ========================================================
    # LIQUIDITY
    # MAX 15
    # ========================================================

    if (
        candidate.liquidity
        >=
        150_000
    ):

        score += 15


    elif (
        candidate.liquidity
        >=
        100_000
    ):

        score += 13


    elif (
        candidate.liquidity
        >=
        60_000
    ):

        score += 11


    else:

        score += 8


    # ========================================================
    # VOLUME / ACCELERATION
    # MAX 15
    # ========================================================

    if (
        candidate.volume_5m
        >=
        25_000
    ):

        score += 10


    elif (
        candidate.volume_5m
        >=
        10_000
    ):

        score += 8


    elif (
        candidate.volume_5m
        >=
        5_000
    ):

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


    # ========================================================
    # BUY PRESSURE
    # MAX 15
    # ========================================================

    if buy_ratio >= 0.72:

        score += 15


    elif buy_ratio >= 0.66:

        score += 13


    elif buy_ratio >= 0.62:

        score += 11


    else:

        score += 8


    # ========================================================
    # MOMENTUM
    # MAX 15
    # ========================================================

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


    # ========================================================
    # MARKET CAP
    # MAX 10
    # ========================================================

    if (
        75_000
        <=
        candidate.market_cap
        <=
        1_000_000
    ):

        score += 10


    elif (
        candidate.market_cap
        <=
        2_000_000
    ):

        score += 8


    else:

        score += 5


    # ========================================================
    # PAIR AGE
    # MAX 5
    # ========================================================

    if (
        5
        <=
        candidate.pair_age_minutes
        <=
        180
    ):

        score += 5


    elif (
        candidate.pair_age_minutes
        <=
        720
    ):

        score += 4


    else:

        score += 2


    # ========================================================
    # OPTIONAL INTELLIGENCE
    # MAX 5
    # ========================================================

    if candidate.gmgn_seen:

        score += 3


    if candidate.birdeye_seen:

        score += 2


    score = min(
        100,
        int(
            score
        )
    )


    explanation = (

        f"sources="
        f"{','.join(sorted(candidate.sources))}; "

        f"buy ratio="
        f"{buy_ratio:.2f}; "

        f"liquidity="
        f"${candidate.liquidity:,.0f}"

    )


    return (
        score,
        explanation
    )


# ============================================================
# DAILY SAFETY ENGINE
# ============================================================

def apply_guards(
    state
):

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


    maximum_loss = (

        state.daily_start_equity
        *
        MAX_DAILY_LOSS

    )


    if (
        state.daily_realized_pnl
        <=
        -maximum_loss
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


    return (
        not state.paused
    )


def can_enter(
    state
):

    if not apply_guards(
        state
    ):

        return False


    if (
        len(
            state.positions
        )
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

def calculate_position_value(
    state
):

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
        MAX_POSITION_EQUITY_PCT

    )


    position_value = min(

        position_value,

        state.balance

    )


    return max(
        0.0,
        position_value
    )


# ============================================================
# PAPER EXECUTION HELPERS
# ============================================================

def entry_fill(
    raw_price
):

    return (

        raw_price
        *
        (
            1
            +
            ENTRY_SLIPPAGE
        )

    )


def exit_fill(
    raw_price
):

    return (

        raw_price
        *
        (
            1
            -
            EXIT_SLIPPAGE
        )

    )


# ============================================================
# OPEN PAPER POSITION
# ============================================================

def open_paper_position(
    state,
    candidate
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


    usable_capital = (

        gross
        -
        entry_fee

    )


    quantity = (

        usable_capital
        /
        fill

    )


    state.balance -= gross


    position = Position(

        mint=
            candidate.mint,

        symbol=
            (
                candidate.symbol
                or
                candidate.mint[:6]
            ),

        entry_price=
            fill,

        current_price=
            candidate.price,

        quantity=
            quantity,

        remaining_quantity=
            quantity,

        gross_cost=
            gross,

        stop_price=
            (
                fill
                *
                (
                    1
                    -
                    STOP_LOSS_PCT
                )
            ),

        highest_price=
            fill,

        opened_at=
            now_ts(),

        entry_score=
            candidate.score,

        entry_sources=
            ",".join(
                sorted(
                    candidate.sources
                )
            )

    )


    state.positions[
        candidate.mint
    ] = position


    state.daily_entries += 1


    state.save()


    return position


# ============================================================
# SELL QUANTITY
# ============================================================

def sell_quantity(
    state,
    position,
    quantity,
    raw_price
):

    quantity = min(

        quantity,

        position.remaining_quantity

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


    fee = (

        gross_proceeds
        *
        FEE_PCT

    )


    net_proceeds = (

        gross_proceeds
        -
        fee

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


    position.realized_pnl += pnl


    state.balance += (
        net_proceeds
    )


    state.realized_pnl += pnl


    state.daily_realized_pnl += (
        pnl
    )


    return pnl


# ============================================================
# PARTIAL SELL
# ============================================================

def partial_sell(
    state,
    position,
    original_fraction,
    raw_price
):

    quantity = (

        position.quantity
        *
        original_fraction

    )


    pnl = sell_quantity(

        state,

        position,

        quantity,

        raw_price

    )


    state.save()


    return pnl


# ============================================================
# CLOSE POSITION
# ============================================================

def close_position(
    state,
    position,
    raw_price,
    reason
):

    sell_quantity(

        state,

        position,

        position.remaining_quantity,

        raw_price

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

        "exit_raw":
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
        None
    )


    apply_guards(
        state
    )


    state.save()


    return trade_pnl


# ============================================================
# POSITION MANAGEMENT
# ============================================================

def manage_position(
    state,
    position,
    raw_price
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


    # ========================================================
    # HARD STOP
    # ========================================================

    if (
        raw_price
        <=
        position.stop_price
    ):

        pnl = close_position(

            state,

            position,

            raw_price,

            "STOP LOSS"

        )


        events.append(
            (
                "close",
                pnl
            )
        )


        return events


    # ========================================================
    # TAKE PROFIT 1
    # ========================================================

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

            raw_price

        )


        events.append(
            (
                "TP1 +10%",
                pnl
            )
        )


    # ========================================================
    # TAKE PROFIT 2
    # ========================================================

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

            raw_price

        )


        events.append(
            (
                "TP2 +18%",
                pnl
            )
        )


    # ========================================================
    # RUNNER / TRAILING STOP
    # ========================================================

    if (
        gain
        >=
        TRAIL_ACTIVATE_PCT
    ):

        position.trailing_active = (
            True
        )


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

            position.entry_price

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

                "TRAILING STOP"

            )


            events.append(
                (
                    "close",
                    pnl
                )
            )


            return events


    state.save()


    return events


# ============================================================
# TELEGRAM NOTIFICATIONS
# ============================================================

async def notify(
    application,
    text
):

    if not TELEGRAM_CHAT_ID:

        return


    try:

        await application.bot.send_message(

            chat_id=
                TELEGRAM_CHAT_ID,

            text=
                text

        )


    except Exception as exc:

        log.warning(
            "Telegram notification failed: %s",
            exc
        )


# ============================================================
# WIN RATE
# ============================================================

def win_rate(
    state
):

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
# /STATUS
# ============================================================

async def cmd_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    state = (
        context.application.bot_data[
            "state"
        ]
    )


    state.reset_day_if_needed()


    await update.message.reply_text(

        "🤖 JARVIS V3 — PAPER\n\n"

        f"Equity: "
        f"${state.equity():.2f}\n"

        f"Cash: "
        f"${state.balance:.2f}\n"

        f"Daily P&L: "
        f"${state.daily_realized_pnl:+.2f}\n"

        f"Daily target: "
        f"+${DAILY_PROFIT_TARGET:.2f}\n\n"

        f"Risk/trade: "
        f"{RISK_PER_TRADE*100:.1f}%\n"

        f"Daily loss: "
        f"{MAX_DAILY_LOSS*100:.1f}%\n"

        f"Stop: "
        f"{STOP_LOSS_PCT*100:.1f}%\n\n"

        f"Positions: "
        f"{len(state.positions)}"
        f"/{MAX_OPEN_POSITIONS}\n"

        f"Entries today: "
        f"{state.daily_entries}"
        f"/{MAX_DAILY_ENTRIES}\n\n"

        f"Wins: "
        f"{state.wins}\n"

        f"Losses: "
        f"{state.losses}\n"

        f"Win rate: "
        f"{win_rate(state):.1f}%\n"

        f"Loss streak: "
        f"{state.consecutive_losses}"
        f"/{MAX_CONSECUTIVE_LOSSES}\n\n"

        f"Paused: "
        f"{'YES — ' + state.pause_reason if state.paused else 'NO'}"

    )


# ============================================================
# /TODAY
# ============================================================

async def cmd_today(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    state = (
        context.application.bot_data[
            "state"
        ]
    )


    await update.message.reply_text(

        "📅 TODAY\n\n"

        f"P&L: "
        f"${state.daily_realized_pnl:+.2f}\n"

        f"Target: "
        f"+${DAILY_PROFIT_TARGET:.2f}\n"

        f"Entries: "
        f"{state.daily_entries}"
        f"/{MAX_DAILY_ENTRIES}\n"

        f"Open positions: "
        f"{len(state.positions)}"

    )


# ============================================================
# /POSITIONS
# ============================================================

async def cmd_positions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
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


    output = [
        "📊 OPEN POSITIONS"
    ]


    for position in (
        state.positions.values()
    ):

        percent = (

            position.current_price
            /
            position.entry_price
            -
            1

        ) * 100


        output.append(

            f"\n{position.symbol}\n"

            f"P&L: "
            f"{percent:+.1f}%\n"

            f"Score: "
            f"{position.entry_score}\n"

            f"Entry: "
            f"${position.entry_price:.10f}\n"

            f"Current: "
            f"${position.current_price:.10f}\n"

            f"Sources: "
            f"{position.entry_sources}"

        )


    await update.message.reply_text(
        "\n".join(
            output
        )
    )


# ============================================================
# /TRADES
# ============================================================

async def cmd_trades(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    state = (
        context.application.bot_data[
            "state"
        ]
    )


    if not state.trades:

        await update.message.reply_text(
            "No completed trades yet."
        )

        return


    output = [
        "📜 RECENT TRADES"
    ]


    for trade in reversed(
        state.trades[-10:]
    ):

        icon = (

            "✅"

            if trade[
                "pnl"
            ] > 0

            else "❌"

        )


        output.append(

            f"{icon} "

            f"{trade['symbol']} "

            f"${trade['pnl']:+.2f} | "

            f"{trade['reason']} | "

            f"Score "
            f"{trade.get('entry_score', 0)}"

        )


    await update.message.reply_text(
        "\n".join(
            output
        )
    )


# ============================================================
# /SOURCES
# ============================================================

async def cmd_sources(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    sources = [

        "✅ Pump.fun/PumpSwap",

        "✅ DEX Screener profiles",

        "✅ DEX Screener latest boosts",

        "✅ DEX Screener top boosts",

        "✅ DEX Screener ads",

        "✅ DEX Screener market data",

    ]


    if BIRDEYE_API_KEY:

        sources.append(
            "✅ Birdeye new listings"
        )

    else:

        sources.append(
            "⚪ Birdeye disabled — add API key"
        )


    if (
        GMGN_API_KEY
        and
        GMGN_OPENAPI_URL
    ):

        sources.append(
            "✅ GMGN"
        )

    else:

        sources.append(
            "⚪ GMGN disabled — add key + official API URL"
        )


    await update.message.reply_text(

        "🔎 JARVIS SOURCES\n\n"
        +
        "\n".join(
            sources
        )

    )


# ============================================================
# /PAUSE
# ============================================================

async def cmd_pause(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
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


# ============================================================
# /RESUME
# ============================================================

async def cmd_resume(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    state = (
        context.application.bot_data[
            "state"
        ]
    )


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
            "Jarvis stays locked until tomorrow."

        )

        return


    if (
        state.daily_realized_pnl
        <=
        -max_loss
    ):

        await update.message.reply_text(

            "🛑 Daily loss limit reached. "
            "Jarvis stays locked until tomorrow."

        )

        return


    if (
        state.consecutive_losses
        >=
        MAX_CONSECUTIVE_LOSSES
    ):

        await update.message.reply_text(

            "🛑 Two-loss breaker reached. "
            "Jarvis stays locked until tomorrow."

        )

        return


    state.paused = False

    state.pause_reason = ""


    state.save()


    await update.message.reply_text(
        "▶️ Jarvis resumed."
    )


# ============================================================
# /PAPER
# ============================================================

async def cmd_paper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "🧪 PAPER MODE ONLY.\n\n"

        "No private keys.\n"
        "No wallet signing.\n"
        "No real-money swaps."

    )


# ============================================================
# /HELP
# ============================================================

async def cmd_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
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
# MAIN SCORING / ENTRY LOOP
# ============================================================

async def evaluation_loop(
    application,
    book,
    state
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
                            candidate
                        )
                    )


                    if not market_ok:

                        continue


                    priced += 1


                    (
                        candidate.score,
                        candidate.score_reason
                    ) = score_candidate(
                        candidate
                    )


                    if (

                        candidate.score
                        >=
                        ENTRY_SCORE

                        and

                        len(
                            candidate.sources
                        )
                        >=
                        MIN_SOURCES_FOR_ENTRY

                    ):

                        qualified += 1

                        ranked.append(
                            candidate
                        )


                ranked.sort(

                    key=lambda token: (

                        token.score,

                        len(
                            token.sources
                        ),

                        token.volume_5m

                    ),

                    reverse=True

                )


                log.info(

                    "Scan | discovered=%s "
                    "priced=%s "
                    "qualified=%s",

                    discovered,

                    priced,

                    qualified

                )


                for candidate in ranked:


                    if not can_enter(
                        state
                    ):

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
                            candidate
                        )
                    )


                    if not position:

                        continue


                    tier = (

                        "🔥 ELITE"

                        if (
                            candidate.score
                            >=
                            ELITE_SCORE
                        )

                        else
                        "✅ QUALIFIED"

                    )


                    await notify(

                        application,

                        "🚀 PAPER ENTRY\n\n"

                        f"{tier}\n"

                        f"{position.symbol}\n"

                        f"Score: "
                        f"{candidate.score}/100\n"

                        f"Sources: "
                        f"{', '.join(sorted(candidate.sources))}\n\n"

                        f"Entry: "
                        f"${position.entry_price:.10f}\n"

                        f"Position: "
                        f"${position.gross_cost:.2f}\n"

                        f"Risk: "
                        f"{RISK_PER_TRADE*100:.1f}%\n"

                        f"Stop: "
                        f"${position.stop_price:.10f}\n\n"

                        f"Liquidity: "
                        f"${candidate.liquidity:,.0f}\n"

                        f"Market cap: "
                        f"${candidate.market_cap:,.0f}\n"

                        f"5m volume: "
                        f"${candidate.volume_5m:,.0f}\n"

                        f"1h volume: "
                        f"${candidate.volume_1h:,.0f}\n"

                        f"5m move: "
                        f"{candidate.change_5m:+.1f}%\n"

                        f"1h move: "
                        f"{candidate.change_1h:+.1f}%\n"

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
# PRICE / EXIT LOOP
# ============================================================

async def price_loop(
    application,
    state
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
                            candidate
                        )
                    )


                    if not market_ok:

                        continue


                    events = manage_position(

                        state,

                        position,

                        candidate.price

                    )


                    for event, pnl in events:


                        if event == "close":

                            await notify(

                                application,

                                "🏁 PAPER TRADE CLOSED\n\n"

                                f"{position.symbol}\n"

                                f"Trade P&L: "
                                f"${pnl:+.2f}\n"

                                f"Daily P&L: "
                                f"${state.daily_realized_pnl:+.2f}\n"

                                f"Equity: "
                                f"${state.equity():.2f}\n"

                                f"Paused: "
                                f"{'YES — ' + state.pause_reason if state.paused else 'NO'}"

                            )


                        else:

                            await notify(

                                application,

                                f"💰 {event}\n\n"

                                f"{position.symbol}\n"

                                f"Realized: "
                                f"${pnl:+.2f}\n"

                                f"Daily P&L: "
                                f"${state.daily_realized_pnl:+.2f}"

                            )


            except Exception:

                log.exception(
                    "Price loop error"
                )


            await asyncio.sleep(
                PRICE_SEC
            )


# ============================================================
# PERIODIC STATUS
# ============================================================

async def status_loop(
    application,
    state
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

            "🤖 JARVIS STATUS\n\n"

            f"Equity: "
            f"${state.equity():.2f}\n"

            f"Daily P&L: "
            f"${state.daily_realized_pnl:+.2f}\n"

            f"Target: "
            f"+${DAILY_PROFIT_TARGET:.0f}\n"

            f"Positions: "
            f"{len(state.positions)}"
            f"/{MAX_OPEN_POSITIONS}\n"

            f"Entries: "
            f"{state.daily_entries}"
            f"/{MAX_DAILY_ENTRIES}\n"

            f"W/L: "
            f"{state.wins}/"
            f"{state.losses} "
            f"({win_rate(state):.1f}%)\n"

            f"Loss streak: "
            f"{state.consecutive_losses}\n"

            f"Paused: "
            f"{'YES — ' + state.pause_reason if state.paused else 'NO'}"

        )


# ============================================================
# STARTUP
# ============================================================

async def post_init(
    application
):

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


    # Discovery engines

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


    # Trading engines

    application.create_task(
        evaluation_loop(
            application,
            book,
            state
        )
    )


    application.create_task(
        price_loop(
            application,
            state
        )
    )


    application.create_task(
        status_loop(
            application,
            state
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

        "🤖 JARVIS V3 ONLINE\n\n"

        "Mode: PAPER ONLY\n"

        f"Equity: "
        f"${state.equity():.2f}\n"

        f"Daily target: "
        f"+${DAILY_PROFIT_TARGET:.0f}\n"

        f"Risk/trade: "
        f"{RISK_PER_TRADE*100:.1f}%\n"

        f"Daily loss limit: "
        f"{MAX_DAILY_LOSS*100:.1f}%\n"

        f"Max positions: "
        f"{MAX_OPEN_POSITIONS}\n"

        f"Loss-streak stop: "
        f"{MAX_CONSECUTIVE_LOSSES}\n"

        f"Entry score: "
        f"{ENTRY_SCORE}/100\n"

        f"Sources: "
        f"{', '.join(active_sources)}"

    )


# ============================================================
# MAIN
# ============================================================

def main():


    if (
        TRADING_MODE
        !=
        "paper"
    ):

        raise RuntimeError(

            "This version supports "
            "TRADING_MODE=paper only."

        )


    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(

            "Missing "
            "TELEGRAM_BOT_TOKEN."

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
            cmd_status
        )
    )


    application.add_handler(
        CommandHandler(
            "today",
            cmd_today
        )
    )


    application.add_handler(
        CommandHandler(
            "positions",
            cmd_positions
        )
    )


    application.add_handler(
        CommandHandler(
            "trades",
            cmd_trades
        )
    )


    application.add_handler(
        CommandHandler(
            "sources",
            cmd_sources
        )
    )


    application.add_handler(
        CommandHandler(
            "pause",
            cmd_pause
        )
    )


    application.add_handler(
        CommandHandler(
            "resume",
            cmd_resume
        )
    )


    application.add_handler(
        CommandHandler(
            "paper",
            cmd_paper
        )
    )


    application.add_handler(
        CommandHandler(
            "help",
            cmd_help
        )
    )


    log.info(
        "Starting Jarvis V3..."
    )


    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":

    main()
