import os
import time
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# JARVIS RESEARCH EDITION
# ------------------------------------------------------------
# Inspired by the architecture disclosed in:
# "Hour-Aware Adaptive Risk Management for Autonomous
# Memecoin Trading on Solana DEXs" (2026)
#
# IMPORTANT:
# - This is NOT the authors' unpublished strategy.
# - Exact paper parameters were not disclosed.
# - This is an independent implementation of the
#   architecture and design lessons they DID disclose.
#
# LIVE MARKET DATA
# PAPER TRADING ONLY
# ============================================================


SYDNEY = ZoneInfo("Australia/Sydney")
UTC = ZoneInfo("UTC")

DEX_BASE = "https://api.dexscreener.com"

STATE_FILE = "jarvis_research_state.json"
REJECT_FILE = "jarvis_rejections.json"


CONFIG = {

    # ========================================================
    # PAPER ACCOUNT
    # ========================================================

    "starting_balance": 1000.00,

    # ========================================================
    # POSITION SIZING
    # ========================================================

    "max_position_pct": 7.0,
    "max_open_positions": 3,
    "max_daily_entries": 30,

    # ========================================================
    # SIMULATED REAL-WORLD EXECUTION
    # ========================================================

    # Applied at entry and exit
    "slippage_pct": 0.35,

    # Applied at entry and exit
    "fee_pct": 0.15,

    # ========================================================
    # HARD RISK CONTROL
    # ========================================================

    "hard_stop_pct": 7.0,
    "emergency_stop_pct": 12.0,

    "max_daily_loss_pct": 4.0,

    # ========================================================
    # RUNNER MANAGEMENT
    # ========================================================

    # Do not take profits too early.
    "hard_take_profit_pct": 45.0,

    # Protect winners once momentum proves itself.
    "break_even_activation_pct": 7.0,
    "break_even_lock_pct": 0.25,

    # Dynamic trailing system.
    "trailing_activation_pct": 10.0,
    "trailing_distance_pct": 5.0,

    # Once a trade becomes a large runner,
    # loosen trailing slightly to avoid cutting it too early.
    "runner_activation_pct": 22.0,
    "runner_trailing_distance_pct": 8.0,

    # ========================================================
    # TIME STOPS
    # ========================================================

    # Bad momentum trades should fail quickly.
    "weak_trade_minutes": 8,
    "weak_trade_max_pct": 0.0,

    # Allow genuine winners more time.
    "max_hold_minutes": 90,

    # ========================================================
    # CIRCUIT BREAKERS
    # ========================================================

    # Pause after this many consecutive losing trades.
    "max_consecutive_losses": 4,

    "loss_streak_pause_seconds": 30 * 60,

    # Short cooldown after each hard stop.
    "stop_cooldown_seconds": 6 * 60,

    # Don't immediately recycle the same failed coin.
    "token_reentry_seconds": 60 * 60,

    # ========================================================
    # HARD ENTRY GATES
    # ========================================================

    "min_liquidity": 40000,
    "min_volume_24h": 100000,

    "min_market_cap": 50000,
    "max_market_cap": 5000000,

    "min_pair_age_minutes": 5,
    "max_pair_age_days": 30,

    # Avoid buying completely flat coins.
    "min_5m_change": 1.0,

    # Avoid chasing extreme 5-minute spikes.
    "max_5m_change": 18.0,

    "min_1h_change": -3.0,
    "max_1h_change": 70.0,

    "min_5m_buys": 8,
    "min_buy_pressure_pct": 55.0,

    # ========================================================
    # SIGNAL SCORE
    # ========================================================

    "min_score": 64,

    # ========================================================
    # HOUR ANALYSIS
    # ========================================================

    # The paper observed weak performance at UTC 2,13,23,
    # but explicitly says this was exploratory / not
    # statistically confirmed.
    #
    # Therefore OFF by default.
    "hour_blacklist_enabled": False,

    "hour_blacklist_utc": [2, 13, 23],

    # ========================================================
    # DISCOVERY
    # ========================================================

    "max_discovered_tokens": 120,
    "batch_size": 30,

    # ========================================================
    # SPEED
    # ========================================================

    "scan_every_seconds": 15,
    "position_update_seconds": 2,
    "telegram_status_seconds": 300,

    # ========================================================
    # REJECTION TRACKING
    # ========================================================

    "max_rejection_records": 1000,

    # ========================================================
    # MODE
    # ========================================================

    "paper_mode": True,
}


# ============================================================
# HELPERS
# ============================================================

def now_sydney():
    return datetime.now(SYDNEY)


def now_utc():
    return datetime.now(UTC)


def today_key():
    return now_sydney().strftime("%Y-%m-%d")


def safe_float(value, default=0.0):

    try:

        if value is None:
            return default

        value = float(value)

        if not math.isfinite(value):
            return default

        return value

    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):

    try:

        if value is None:
            return default

        return int(value)

    except (TypeError, ValueError):
        return default


def http_json(url, method="GET", data=None, timeout=15):

    try:

        headers = {
            "User-Agent": "JarvisResearchPaperBot/1.0",
            "Accept": "application/json",
        }

        body = None

        if data is not None:

            body = urllib.parse.urlencode(
                data
            ).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            return json.loads(
                response.read().decode("utf-8")
            )

    except Exception as error:

        print(
            "HTTP ERROR:",
            url,
            error,
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

class TelegramManager:

    def __init__(self):

        self.token = os.getenv(
            "TELEGRAM_BOT_TOKEN",
            ""
        ).strip()

        self.chat_id = os.getenv(
            "TELEGRAM_CHAT_ID",
            ""
        ).strip()

        self.offset = None

        if not self.token:

            print(
                "WARNING: TELEGRAM_BOT_TOKEN missing"
            )

            return

        print(
            "Telegram token loaded."
        )

        self.delete_webhook()
        self.set_commands()


    def api(self, method, data=None):

        if not self.token:
            return None

        url = (
            "https://api.telegram.org/"
            f"bot{self.token}/"
            f"{method}"
        )

        return http_json(
            url,
            method="POST",
            data=data,
        )


    def delete_webhook(self):

        self.api(
            "deleteWebhook",
            {
                "drop_pending_updates": "false"
            }
        )


    def set_commands(self):

        commands = json.dumps([

            {
                "command": "status",
                "description": "Jarvis dashboard",
            },

            {
                "command": "today",
                "description": "Current session",
            },

            {
                "command": "positions",
                "description": "Open trades",
            },

            {
                "command": "trades",
                "description": "Recent trades",
            },

            {
                "command": "stats",
                "description": "Performance statistics",
            },

            {
                "command": "scan",
                "description": "Scanner diagnostics",
            },

            {
                "command": "breaker",
                "description": "Circuit breaker status",
            },

            {
                "command": "rejections",
                "description": "Rejected-token stats",
            },

            {
                "command": "resetday",
                "description": "Reset PAPER session",
            },

            {
                "command": "pause",
                "description": "Pause new entries",
            },

            {
                "command": "resume",
                "description": "Resume entries",
            },

            {
                "command": "paper",
                "description": "Show trading mode",
            },

            {
                "command": "help",
                "description": "Show commands",
            },
        ])

        result = self.api(
            "setMyCommands",
            {
                "commands": commands
            }
        )

        print(
            "Telegram commands:",
            result
        )


    def send(self, message):

        if not self.token:
            return

        if not self.chat_id:
            return

        self.api(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": message,
            }
        )


    def get_updates(self):

        if not self.token:
            return []

        data = {
            "timeout": 1
        }

        if self.offset is not None:

            data[
                "offset"
            ] = self.offset

        result = self.api(
            "getUpdates",
            data
        )

        if not result:
            return []

        if not result.get(
            "ok"
        ):
            return []

        updates = result.get(
            "result",
            []
        )

        if updates:

            self.offset = (
                updates[-1]["update_id"]
                +
                1
            )

        return updates


    def handle_commands(self, bot):

        for update in self.get_updates():

            message = update.get(
                "message",
                {}
            )

            chat = message.get(
                "chat",
                {}
            )

            incoming_chat_id = str(
                chat.get(
                    "id",
                    ""
                )
            )

            text = message.get(
                "text",
                ""
            ).strip()

            if not incoming_chat_id:
                continue

            if not text:
                continue

            if not self.chat_id:
                self.chat_id = incoming_chat_id

            if incoming_chat_id != self.chat_id:
                continue

            command = (
                text.lower()
                .split()[0]
                .split("@")[0]
            )


            if command == "/start":

                self.send(

                    "🤖 JARVIS RESEARCH EDITION ONLINE\n\n"

                    "Live Solana market data\n"
                    "Execution: PAPER ONLY\n\n"

                    "Architecture:\n"
                    "• momentum scoring\n"
                    "• hard entry gates\n"
                    "• runner-friendly exits\n"
                    "• time stops\n"
                    "• circuit breaker\n"
                    "• rejection logging\n"
                    "• simulated fees/slippage\n\n"

                    "/status\n"
                    "/today\n"
                    "/positions\n"
                    "/trades\n"
                    "/stats\n"
                    "/scan\n"
                    "/breaker\n"
                    "/rejections\n"
                    "/resetday\n"
                    "/pause\n"
                    "/resume"
                )


            elif command == "/status":

                self.send(
                    bot.status_message()
                )


            elif command == "/today":

                self.send(
                    bot.today_message()
                )


            elif command == "/positions":

                bot.update_positions()

                self.send(
                    bot.positions_message()
                )


            elif command == "/trades":

                self.send(
                    bot.trades_message()
                )


            elif command == "/stats":

                self.send(
                    bot.stats_message()
                )


            elif command == "/scan":

                self.send(
                    bot.scan_message()
                )


            elif command == "/breaker":

                self.send(
                    bot.breaker_message()
                )


            elif command == "/rejections":

                self.send(
                    bot.rejection_message()
                )


            elif command == "/resetday":

                old_entries = bot.daily_entries
                old_profit = bot.daily_profit

                equity = bot.get_equity()

                bot.manual_reset_day()

                self.send(

                    "🔄 PAPER SESSION RESET\n\n"

                    f"Previous entries: "
                    f"{old_entries}/"
                    f"{CONFIG['max_daily_entries']}\n"

                    f"Previous P/L: "
                    f"${old_profit:+.2f}\n\n"

                    "New session: 0/"
                    f"{CONFIG['max_daily_entries']}\n"

                    "Session P/L: $+0.00\n\n"

                    f"Equity kept: "
                    f"${equity:,.2f}\n\n"

                    "⚠️ PAPER TESTING ONLY"
                )


            elif command == "/pause":

                bot.entries_paused = True

                bot.save_state()

                self.send(

                    "⏸ JARVIS PAUSED\n\n"

                    "No new entries.\n"
                    "Open positions remain managed."
                )


            elif command == "/resume":

                bot.entries_paused = False

                bot.save_state()

                self.send(
                    "▶️ JARVIS RESUMED"
                )


            elif command == "/paper":

                self.send(

                    "📝 MODE\n\n"

                    "Market data: LIVE\n"
                    "Execution: PAPER ONLY\n\n"

                    "Simulated fees and slippage enabled.\n"
                    "No wallet connected."
                )


            elif command == "/help":

                self.send(

                    "🤖 COMMANDS\n\n"

                    "/status\n"
                    "/today\n"
                    "/positions\n"
                    "/trades\n"
                    "/stats\n"
                    "/scan\n"
                    "/breaker\n"
                    "/rejections\n"
                    "/resetday\n"
                    "/pause\n"
                    "/resume\n"
                    "/paper"
                )


# ============================================================
# DEXSCREENER
# ============================================================

class DexScreener:

    def get_list(self, path):

        result = http_json(
            DEX_BASE + path
        )

        if isinstance(
            result,
            list
        ):
            return result

        return []


    def latest_profiles(self):

        return self.get_list(
            "/token-profiles/latest/v1"
        )


    def latest_boosts(self):

        return self.get_list(
            "/token-boosts/latest/v1"
        )


    def top_boosts(self):

        return self.get_list(
            "/token-boosts/top/v1"
        )


    def community_takeovers(self):

        return self.get_list(
            "/community-takeovers/latest/v1"
        )


    # ========================================================
    # TOKEN DISCOVERY
    # ========================================================

    def discover_addresses(self):

        feeds = [

            self.latest_profiles(),

            self.latest_boosts(),

            self.top_boosts(),

            self.community_takeovers(),
        ]

        discovered = []

        seen = set()

        maximum = max(
            [len(feed) for feed in feeds]
            +
            [0]
        )

        for index in range(
            maximum
        ):

            for feed in feeds:

                if index >= len(feed):
                    continue

                item = feed[
                    index
                ]

                if (
                    item.get(
                        "chainId"
                    )
                    !=
                    "solana"
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

                discovered.append(
                    address
                )

                if (
                    len(discovered)
                    >=
                    CONFIG[
                        "max_discovered_tokens"
                    ]
                ):

                    return discovered

        return discovered


    # ========================================================
    # BATCH DATA
    # ========================================================

    def token_pairs_batch(
        self,
        addresses
    ):

        if not addresses:
            return []

        joined = ",".join(
            addresses
        )

        result = http_json(

            DEX_BASE

            +

            "/tokens/v1/solana/"

            +

            joined
        )

        if isinstance(
            result,
            list
        ):
            return result

        return []


    def fetch_pairs(
        self,
        addresses
    ):

        pairs = []

        size = CONFIG[
            "batch_size"
        ]

        for start in range(
            0,
            len(addresses),
            size
        ):

            batch = addresses[
                start:
                start + size
            ]

            pairs.extend(
                self.token_pairs_batch(
                    batch
                )
            )

            time.sleep(
                0.10
            )

        return pairs


    # ========================================================
    # CHOOSE HIGHEST LIQUIDITY POOL
    # ========================================================

    def best_pairs_by_token(
        self,
        pairs
    ):

        result = {}

        for pair in pairs:

            if pair.get(
                "chainId"
            ) != "solana":

                continue

            base = pair.get(
                "baseToken",
                {}
            )

            address = base.get(
                "address"
            )

            if not address:
                continue

            liquidity = safe_float(

                pair.get(
                    "liquidity",
                    {}
                ).get(
                    "usd"
                )
            )

            if liquidity <= 0:
                continue

            existing = result.get(
                address
            )

            if existing is None:

                result[
                    address
                ] = pair

                continue

            old_liquidity = safe_float(

                existing.get(
                    "liquidity",
                    {}
                ).get(
                    "usd"
                )
            )

            if liquidity > old_liquidity:

                result[
                    address
                ] = pair

        return result


# ============================================================
# JARVIS
# ============================================================

class JarvisBot:

    def __init__(self):

        self.dex = DexScreener()

        self.telegram = (
            TelegramManager()
        )


        # ====================================================
        # ACCOUNT
        # ====================================================

        self.balance = (
            CONFIG[
                "starting_balance"
            ]
        )

        self.positions = {}

        self.trade_history = []


        # ====================================================
        # SESSION
        # ====================================================

        self.day_key = (
            today_key()
        )

        self.day_start_equity = (
            CONFIG[
                "starting_balance"
            ]
        )

        self.daily_profit = 0.0

        self.daily_entries = 0


        # ====================================================
        # CONTROL
        # ====================================================

        self.entries_paused = False

        self.last_scan = 0

        self.last_status = 0


        # ====================================================
        # CIRCUIT BREAKER
        # ====================================================

        self.consecutive_losses = 0

        self.breaker_until = 0.0

        self.last_stop_time = 0.0


        # ====================================================
        # TOKEN MEMORY
        # ====================================================

        self.token_last_exit = {}


        # ====================================================
        # SCANNER TELEMETRY
        # ====================================================

        self.last_scan_time = None

        self.last_discovered = 0

        self.last_pairs = 0

        self.last_qualified = 0

        self.last_best_symbol = "NONE"

        self.last_best_score = 0

        self.rejection_counts = {}


        # ====================================================
        # REJECTION TELEMETRY
        # ====================================================

        self.rejections = []


        self.load_state()

        self.load_rejections()

        self.reset_day_if_needed()


    # ========================================================
    # STATE
    # ========================================================

    def load_state(self):

        if not os.path.exists(
            STATE_FILE
        ):
            return

        try:

            with open(
                STATE_FILE,
                "r"
            ) as file:

                data = json.load(
                    file
                )

            self.balance = safe_float(
                data.get(
                    "balance"
                ),
                self.balance
            )

            self.positions = data.get(
                "positions",
                {}
            )

            self.trade_history = data.get(
                "trade_history",
                []
            )

            self.day_key = data.get(
                "day_key",
                today_key()
            )

            self.day_start_equity = safe_float(
                data.get(
                    "day_start_equity"
                ),
                CONFIG[
                    "starting_balance"
                ]
            )

            self.daily_profit = safe_float(
                data.get(
                    "daily_profit"
                )
            )

            self.daily_entries = safe_int(
                data.get(
                    "daily_entries"
                )
            )

            self.entries_paused = bool(
                data.get(
                    "entries_paused",
                    False
                )
            )

            self.consecutive_losses = safe_int(
                data.get(
                    "consecutive_losses"
                )
            )

            self.breaker_until = safe_float(
                data.get(
                    "breaker_until"
                )
            )

            self.last_stop_time = safe_float(
                data.get(
                    "last_stop_time"
                )
            )

            self.token_last_exit = data.get(
                "token_last_exit",
                {}
            )


            # Backwards-safe position repair

            for position in (
                self.positions.values()
            ):

                position.setdefault(
                    "highest_price",
                    safe_float(
                        position.get(
                            "entry_price"
                        )
                    )
                )

                position.setdefault(
                    "protected_stop_price",
                    0.0
                )

                position.setdefault(
                    "protected_reason",
                    ""
                )

                position.setdefault(
                    "break_even_armed",
                    False
                )

                position.setdefault(
                    "trailing_armed",
                    False
                )

                position.setdefault(
                    "runner_mode",
                    False
                )

                position.setdefault(
                    "opened_timestamp",
                    time.time()
                )

            print(
                "✅ Research state loaded"
            )

        except Exception as error:

            print(
                "STATE LOAD ERROR:",
                error
            )


    def save_state(self):

        try:

            data = {

                "balance":
                    self.balance,

                "positions":
                    self.positions,

                "trade_history":
                    self.trade_history[-2000:],

                "day_key":
                    self.day_key,

                "day_start_equity":
                    self.day_start_equity,

                "daily_profit":
                    self.daily_profit,

                "daily_entries":
                    self.daily_entries,

                "entries_paused":
                    self.entries_paused,

                "consecutive_losses":
                    self.consecutive_losses,

                "breaker_until":
                    self.breaker_until,

                "last_stop_time":
                    self.last_stop_time,

                "token_last_exit":
                    self.token_last_exit,
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

        except Exception as error:

            print(
                "STATE SAVE ERROR:",
                error
            )


    # ========================================================
    # REJECTION LOGGING
    # ========================================================

    def load_rejections(self):

        if not os.path.exists(
            REJECT_FILE
        ):
            return

        try:

            with open(
                REJECT_FILE,
                "r"
            ) as file:

                self.rejections = json.load(
                    file
                )

        except Exception:

            self.rejections = []


    def save_rejections(self):

        try:

            self.rejections = (
                self.rejections[
                    -CONFIG[
                        "max_rejection_records"
                    ]:
                ]
            )

            with open(
                REJECT_FILE,
                "w"
            ) as file:

                json.dump(
                    self.rejections,
                    file,
                    indent=2
                )

        except Exception as error:

            print(
                "REJECTION SAVE ERROR:",
                error
            )


    def log_rejection(
        self,
        token,
        reason
    ):

        self.rejection_counts[
            reason
        ] = (

            self.rejection_counts.get(
                reason,
                0
            )

            +

            1
        )

        # Avoid enormous duplicate logs from every scan.

        address = token.get(
            "address",
            ""
        )

        recent_duplicate = False

        now_ts = time.time()

        for item in reversed(
            self.rejections[-50:]
        ):

            if (
                item.get(
                    "address"
                )
                ==
                address
            ):

                if (
                    now_ts
                    -
                    safe_float(
                        item.get(
                            "timestamp"
                        )
                    )
                    <
                    900
                ):

                    recent_duplicate = True

                break

        if recent_duplicate:
            return

        self.rejections.append({

            "timestamp":
                now_ts,

            "time":
                now_utc()
                .isoformat(),

            "address":
                address,

            "symbol":
                token.get(
                    "symbol",
                    "UNKNOWN"
                ),

            "reason":
                reason,

            "reference_price":
                token.get(
                    "price",
                    0
                ),

            "liquidity":
                token.get(
                    "liquidity",
                    0
                ),

            "volume":
                token.get(
                    "volume",
                    0
                ),

            "market_cap":
                token.get(
                    "market_cap",
                    0
                ),

            "change5":
                token.get(
                    "change5",
                    0
                ),

            "change1h":
                token.get(
                    "change1h",
                    0
                ),

            "buy_pressure":
                token.get(
                    "buy_pressure",
                    0
                ),
        })


    # ========================================================
    # ACCOUNT
    # ========================================================

    def get_equity(self):

        value = self.balance

        for position in (
            self.positions.values()
        ):

            value += (

                safe_float(
                    position.get(
                        "quantity"
                    )
                )

                *

                safe_float(
                    position.get(
                        "current_price"
                    )
                )
            )

        return value


    def total_profit(self):

        return (

            self.get_equity()

            -

            CONFIG[
                "starting_balance"
            ]
        )


    def unrealized_profit(self):

        result = 0.0

        for position in (
            self.positions.values()
        ):

            value = (

                safe_float(
                    position.get(
                        "quantity"
                    )
                )

                *

                safe_float(
                    position.get(
                        "current_price"
                    )
                )
            )

            result += (

                value

                -

                safe_float(
                    position.get(
                        "amount"
                    )
                )
            )

        return result


    # ========================================================
    # DAY RESET
    # ========================================================

    def reset_day_if_needed(self):

        current = today_key()

        if current == self.day_key:
            return

        self.day_key = current

        self.daily_profit = 0.0

        self.daily_entries = 0

        self.day_start_equity = (
            self.get_equity()
        )

        self.consecutive_losses = 0

        self.breaker_until = 0

        self.last_scan = 0

        self.save_state()

        self.telegram.send(

            "🌅 NEW JARVIS RESEARCH DAY\n\n"

            f"Starting equity: "
            f"${self.day_start_equity:,.2f}\n"

            "Daily counters reset."
        )


    def manual_reset_day(self):

        self.daily_profit = 0.0

        self.daily_entries = 0

        self.day_start_equity = (
            self.get_equity()
        )

        self.day_key = today_key()

        # Paper session reset also clears session breaker.
        self.consecutive_losses = 0
        self.breaker_until = 0

        self.last_scan = 0

        self.save_state()


    # ========================================================
    # DAILY RISK
    # ========================================================

    def daily_loss_pct(self):

        if self.day_start_equity <= 0:
            return 0.0

        change = (

            self.get_equity()

            -

            self.day_start_equity
        )

        if change >= 0:
            return 0.0

        return (

            abs(change)

            /

            self.day_start_equity

            *

            100
        )


    def daily_loss_hit(self):

        return (

            self.daily_loss_pct()

            >=

            CONFIG[
                "max_daily_loss_pct"
            ]
        )


    # ========================================================
    # CIRCUIT BREAKER
    # ========================================================

    def breaker_active(self):

        return (
            time.time()
            <
            self.breaker_until
        )


    def breaker_remaining(self):

        return max(
            0,
            int(
                self.breaker_until
                -
                time.time()
            )
        )


    def stop_cooldown_active(self):

        if self.last_stop_time <= 0:
            return False

        return (

            time.time()

            -

            self.last_stop_time

            <

            CONFIG[
                "stop_cooldown_seconds"
            ]
        )


    def stop_cooldown_remaining(self):

        if not self.stop_cooldown_active():
            return 0

        return max(

            0,

            int(

                CONFIG[
                    "stop_cooldown_seconds"
                ]

                -

                (
                    time.time()

                    -

                    self.last_stop_time
                )
            )
        )


    def token_reentry_allowed(
        self,
        address
    ):

        last = safe_float(

            self.token_last_exit.get(
                address,
                0
            )
        )

        if last <= 0:
            return True

        return (

            time.time()

            -

            last

            >=

            CONFIG[
                "token_reentry_seconds"
            ]
        )


    # ========================================================
    # TOKEN NORMALIZATION
    # ========================================================

    def pair_age_minutes(
        self,
        pair
    ):

        created = safe_int(
            pair.get(
                "pairCreatedAt"
            )
        )

        if created <= 0:
            return None

        return (

            time.time()

            -

            created / 1000

        ) / 60


    def pair_to_token(
        self,
        pair
    ):

        base = pair.get(
            "baseToken",
            {}
        )

        changes = pair.get(
            "priceChange",
            {}
        )

        m5 = (

            pair.get(
                "txns",
                {}
            )

            .get(
                "m5",
                {}
            )
        )

        buys = safe_int(
            m5.get(
                "buys"
            )
        )

        sells = safe_int(
            m5.get(
                "sells"
            )
        )

        transactions = (
            buys
            +
            sells
        )

        buy_pressure = (

            buys
            /
            transactions
            *
            100

            if transactions > 0

            else 0
        )

        market_cap = safe_float(
            pair.get(
                "marketCap"
            )
        )

        if market_cap <= 0:

            market_cap = safe_float(
                pair.get(
                    "fdv"
                )
            )

        return {

            "symbol":
                base.get(
                    "symbol",
                    "UNKNOWN"
                ),

            "name":
                base.get(
                    "name",
                    "Unknown"
                ),

            "address":
                base.get(
                    "address",
                    ""
                ),

            "price":
                safe_float(
                    pair.get(
                        "priceUsd"
                    )
                ),

            "liquidity":
                safe_float(

                    pair.get(
                        "liquidity",
                        {}
                    ).get(
                        "usd"
                    )
                ),

            "volume":
                safe_float(

                    pair.get(
                        "volume",
                        {}
                    ).get(
                        "h24"
                    )
                ),

            "market_cap":
                market_cap,

            "change5":
                safe_float(
                    changes.get(
                        "m5"
                    )
                ),

            "change1h":
                safe_float(
                    changes.get(
                        "h1"
                    )
                ),

            "buys5":
                buys,

            "sells5":
                sells,

            "buy_pressure":
                buy_pressure,

            "age":
                self.pair_age_minutes(
                    pair
                ),
        }


    # ========================================================
    # SIGNAL SCORE
    # ========================================================

    def score_token(
        self,
        token
    ):

        score = 0


        # ---------------- LIQUIDITY ----------------

        liquidity = token[
            "liquidity"
        ]

        if liquidity >= 250000:
            score += 20

        elif liquidity >= 120000:
            score += 18

        elif liquidity >= 70000:
            score += 15

        elif liquidity >= 40000:
            score += 10


        # ---------------- VOLUME ----------------

        volume = token[
            "volume"
        ]

        if volume >= 1500000:
            score += 20

        elif volume >= 700000:
            score += 18

        elif volume >= 300000:
            score += 15

        elif volume >= 100000:
            score += 10


        # ---------------- MOMENTUM ----------------

        change5 = token[
            "change5"
        ]

        if 3 <= change5 <= 8:
            score += 22

        elif 1 <= change5 < 3:
            score += 14

        elif 8 < change5 <= 12:
            score += 15

        elif 12 < change5 <= 18:
            score += 7


        change1h = token[
            "change1h"
        ]

        if 4 <= change1h <= 30:
            score += 15

        elif -3 <= change1h < 4:
            score += 8

        elif 30 < change1h <= 50:
            score += 9

        elif 50 < change1h <= 70:
            score += 4


        # ---------------- ORDER FLOW ----------------

        pressure = token[
            "buy_pressure"
        ]

        if pressure >= 72:
            score += 20

        elif pressure >= 65:
            score += 17

        elif pressure >= 60:
            score += 13

        elif pressure >= 55:
            score += 8


        # ---------------- MARKET CAP ----------------

        cap = token[
            "market_cap"
        ]

        if 75000 <= cap <= 800000:
            score += 10

        elif 800000 < cap <= 2500000:
            score += 7

        elif 2500000 < cap <= 5000000:
            score += 4


        return min(
            score,
            100
        )


    # ========================================================
    # HARD GATES
    # ========================================================

    def rejection_reason(
        self,
        token
    ):

        if token["price"] <= 0:
            return "price"

        if (
            token["liquidity"]
            <
            CONFIG[
                "min_liquidity"
            ]
        ):
            return "liquidity"

        if (
            token["volume"]
            <
            CONFIG[
                "min_volume_24h"
            ]
        ):
            return "volume"

        if not (
            CONFIG["min_market_cap"]
            <=
            token["market_cap"]
            <=
            CONFIG["max_market_cap"]
        ):
            return "market_cap"

        if not (
            CONFIG["min_5m_change"]
            <=
            token["change5"]
            <=
            CONFIG["max_5m_change"]
        ):
            return "momentum_5m"

        if not (
            CONFIG["min_1h_change"]
            <=
            token["change1h"]
            <=
            CONFIG["max_1h_change"]
        ):
            return "momentum_1h"

        if (
            token["buys5"]
            <
            CONFIG[
                "min_5m_buys"
            ]
        ):
            return "buys"

        if (
            token["buy_pressure"]
            <
            CONFIG[
                "min_buy_pressure_pct"
            ]
        ):
            return "buy_pressure"

        age = token[
            "age"
        ]

        if age is not None:

            if (
                age
                <
                CONFIG[
                    "min_pair_age_minutes"
                ]
            ):
                return "too_new"

            max_age = (

                CONFIG[
                    "max_pair_age_days"
                ]
                *
                24
                *
                60
            )

            if age > max_age:
                return "too_old"


        # Exploratory only.
        if CONFIG[
            "hour_blacklist_enabled"
        ]:

            current_hour = (
                now_utc().hour
            )

            if (
                current_hour
                in
                CONFIG[
                    "hour_blacklist_utc"
                ]
            ):

                return "hour_blacklist"


        token["score"] = (
            self.score_token(
                token
            )
        )

        if (
            token["score"]
            <
            CONFIG[
                "min_score"
            ]
        ):
            return "score"

        return None


    # ========================================================
    # SCANNER
    # ========================================================

    def scan_market(self):

        addresses = (
            self.dex
            .discover_addresses()
        )

        self.last_discovered = (
            len(addresses)
        )

        self.last_scan_time = (
            now_sydney()
        )

        self.rejection_counts = {}

        if not addresses:

            self.last_pairs = 0
            self.last_qualified = 0

            return []


        pairs = self.dex.fetch_pairs(
            addresses
        )

        best_pairs = (
            self.dex
            .best_pairs_by_token(
                pairs
            )
        )

        self.last_pairs = (
            len(best_pairs)
        )

        candidates = []


        for address in addresses:

            pair = best_pairs.get(
                address
            )

            if not pair:
                continue

            token = (
                self.pair_to_token(
                    pair
                )
            )

            reason = (
                self.rejection_reason(
                    token
                )
            )

            if reason:

                self.log_rejection(
                    token,
                    reason
                )

                continue

            candidates.append(
                token
            )


        candidates.sort(

            key=lambda item:
                item[
                    "score"
                ],

            reverse=True
        )

        self.last_qualified = (
            len(candidates)
        )

        if candidates:

            self.last_best_symbol = (
                candidates[0][
                    "symbol"
                ]
            )

            self.last_best_score = (
                candidates[0][
                    "score"
                ]
            )

        else:

            self.last_best_symbol = "NONE"
            self.last_best_score = 0


        self.save_rejections()


        print(

            "RESEARCH SCAN |",

            "discovered:",
            self.last_discovered,

            "pairs:",
            self.last_pairs,

            "qualified:",
            self.last_qualified,

            "best:",
            self.last_best_symbol,

            self.last_best_score
        )


        return candidates


    # ========================================================
    # POSITION SIZE
    # ========================================================

    def position_size(
        self
    ):

        maximum = (

            self.get_equity()

            *

            CONFIG[
                "max_position_pct"
            ]

            /

            100
        )

        return max(

            0,

            min(
                maximum,
                self.balance
            )
        )


    # ========================================================
    # SIMULATED EXECUTION
    # ========================================================

    def buy_fill_price(
        self,
        price
    ):

        return (

            price

            *

            (
                1

                +

                CONFIG[
                    "slippage_pct"
                ]

                /

                100
            )
        )


    def sell_fill_price(
        self,
        price
    ):

        return (

            price

            *

            (
                1

                -

                CONFIG[
                    "slippage_pct"
                ]

                /

                100
            )
        )


    # ========================================================
    # BUY
    # ========================================================

    def buy(
        self,
        token
    ):

        if self.entries_paused:
            return False

        if self.daily_loss_hit():
            return False

        if self.breaker_active():
            return False

        if self.stop_cooldown_active():
            return False

        if (
            self.daily_entries
            >=
            CONFIG[
                "max_daily_entries"
            ]
        ):
            return False

        if (
            len(self.positions)
            >=
            CONFIG[
                "max_open_positions"
            ]
        ):
            return False


        address = token[
            "address"
        ]

        if not address:
            return False

        if address in self.positions:
            return False

        if not self.token_reentry_allowed(
            address
        ):
            return False


        amount = (
            self.position_size()
        )

        if amount <= 0:
            return False


        market_price = token[
            "price"
        ]

        fill_price = (
            self.buy_fill_price(
                market_price
            )
        )


        entry_fee = (

            amount

            *

            CONFIG[
                "fee_pct"
            ]

            /

            100
        )


        investable = (
            amount
            -
            entry_fee
        )


        quantity = (

            investable

            /

            fill_price
        )


        self.positions[
            address
        ] = {

            "symbol":
                token[
                    "symbol"
                ],

            "name":
                token[
                    "name"
                ],

            "entry_price":
                fill_price,

            "market_entry_price":
                market_price,

            "current_price":
                market_price,

            "highest_price":
                market_price,

            "quantity":
                quantity,

            "amount":
                amount,

            "entry_fee":
                entry_fee,

            "score":
                token[
                    "score"
                ],

            "break_even_armed":
                False,

            "trailing_armed":
                False,

            "runner_mode":
                False,

            "protected_stop_price":
                0.0,

            "protected_reason":
                "",

            "entry_utc_hour":
                now_utc().hour,

            "opened_timestamp":
                time.time(),

            "opened_at":
                now_sydney()
                .isoformat(),
        }


        self.balance -= amount

        self.daily_entries += 1

        self.save_state()


        self.telegram.send(

            "🚀 RESEARCH PAPER ENTRY\n\n"

            f"{token['name']} "
            f"({token['symbol']})\n\n"

            f"Score: "
            f"{token['score']}/100\n"

            f"Position: "
            f"${amount:.2f}\n"

            f"Market: "
            f"${market_price:.10f}\n"

            f"Simulated fill: "
            f"${fill_price:.10f}\n\n"

            f"5m momentum: "
            f"{token['change5']:+.2f}%\n"

            f"1h momentum: "
            f"{token['change1h']:+.2f}%\n"

            f"Buy pressure: "
            f"{token['buy_pressure']:.1f}%\n\n"

            f"Liquidity: "
            f"${token['liquidity']:,.0f}\n"

            f"Volume: "
            f"${token['volume']:,.0f}\n"

            f"Market cap: "
            f"${token['market_cap']:,.0f}\n\n"

            f"UTC entry hour: "
            f"{now_utc().hour}\n"

            f"Session: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}"
        )


        return True


    # ========================================================
    # PROFIT PROTECTION
    # ========================================================

    def update_protection(
        self,
        position
    ):

        entry = safe_float(
            position.get(
                "entry_price"
            )
        )

        high = safe_float(
            position.get(
                "highest_price"
            )
        )

        if entry <= 0 or high <= 0:
            return


        high_pct = (

            (
                high
                -
                entry
            )

            /

            entry

            *

            100
        )


        # ---------------- BREAK EVEN ----------------

        if (

            not position.get(
                "break_even_armed",
                False
            )

            and

            high_pct
            >=
            CONFIG[
                "break_even_activation_pct"
            ]
        ):

            position[
                "break_even_armed"
            ] = True


            floor = (

                entry

                *

                (
                    1

                    +

                    CONFIG[
                        "break_even_lock_pct"
                    ]

                    /

                    100
                )
            )


            position[
                "protected_stop_price"
            ] = max(

                safe_float(
                    position.get(
                        "protected_stop_price"
                    )
                ),

                floor
            )


            position[
                "protected_reason"
            ] = (
                "BREAK EVEN"
            )


        # ---------------- TRAILING ----------------

        if (

            high_pct

            >=

            CONFIG[
                "trailing_activation_pct"
            ]
        ):

            position[
                "trailing_armed"
            ] = True


        # ---------------- RUNNER MODE ----------------

        if (

            high_pct

            >=

            CONFIG[
                "runner_activation_pct"
            ]
        ):

            position[
                "runner_mode"
            ] = True


        if position.get(
            "trailing_armed",
            False
        ):

            if position.get(
                "runner_mode",
                False
            ):

                distance = (

                    CONFIG[
                        "runner_trailing_distance_pct"
                    ]
                    /
                    100
                )

                reason = (
                    "RUNNER TRAILING"
                )

            else:

                distance = (

                    CONFIG[
                        "trailing_distance_pct"
                    ]
                    /
                    100
                )

                reason = (
                    "TRAILING PROFIT"
                )


            floor = (

                high

                *

                (
                    1
                    -
                    distance
                )
            )


            old_floor = safe_float(

                position.get(
                    "protected_stop_price"
                )
            )


            if floor > old_floor:

                position[
                    "protected_stop_price"
                ] = floor

                position[
                    "protected_reason"
                ] = reason


    # ========================================================
    # POSITION UPDATE
    # ========================================================

    def update_positions(self):

        if not self.positions:
            return


        addresses = list(
            self.positions.keys()
        )


        pairs = (
            self.dex
            .token_pairs_batch(
                addresses
            )
        )


        best_pairs = (
            self.dex
            .best_pairs_by_token(
                pairs
            )
        )


        for address in list(
            self.positions.keys()
        ):

            if address not in self.positions:
                continue


            pair = best_pairs.get(
                address
            )

            if not pair:
                continue


            market_price = safe_float(
                pair.get(
                    "priceUsd"
                )
            )

            if market_price <= 0:
                continue


            position = (
                self.positions[
                    address
                ]
            )


            position[
                "current_price"
            ] = market_price


            if (

                market_price

                >

                safe_float(
                    position.get(
                        "highest_price"
                    )
                )
            ):

                position[
                    "highest_price"
                ] = market_price


            entry = safe_float(
                position.get(
                    "entry_price"
                )
            )

            if entry <= 0:
                continue


            pnl_pct = (

                (
                    market_price
                    -
                    entry
                )

                /

                entry

                *

                100
            )


            held_minutes = (

                time.time()

                -

                safe_float(
                    position.get(
                        "opened_timestamp"
                    ),
                    time.time()
                )

            ) / 60


            self.update_protection(
                position
            )


            protected_stop = safe_float(

                position.get(
                    "protected_stop_price"
                )
            )


            # =================================================
            # 1. PROFIT PROTECTION
            # =================================================

            if (

                protected_stop > 0

                and

                market_price
                <=
                protected_stop
            ):

                self.sell(

                    address,

                    position.get(
                        "protected_reason"
                    )
                    or
                    "PROTECTED EXIT"
                )

                continue


            # =================================================
            # 2. EMERGENCY STOP
            # =================================================

            if (

                pnl_pct

                <=

                -CONFIG[
                    "emergency_stop_pct"
                ]
            ):

                self.sell(
                    address,
                    "EMERGENCY STOP"
                )

                continue


            # =================================================
            # 3. NORMAL STOP
            # =================================================

            if (

                pnl_pct

                <=

                -CONFIG[
                    "hard_stop_pct"
                ]
            ):

                self.sell(
                    address,
                    "STOP LOSS"
                )

                continue


            # =================================================
            # 4. LARGE WINNER TARGET
            # =================================================

            if (

                pnl_pct

                >=

                CONFIG[
                    "hard_take_profit_pct"
                ]
            ):

                self.sell(
                    address,
                    "TAKE PROFIT"
                )

                continue


            # =================================================
            # 5. TIME STOP FOR NON-PERFORMERS
            # =================================================

            if (

                held_minutes

                >=

                CONFIG[
                    "weak_trade_minutes"
                ]

                and

                pnl_pct

                <=

                CONFIG[
                    "weak_trade_max_pct"
                ]

                and

                not position.get(
                    "break_even_armed",
                    False
                )
            ):

                self.sell(
                    address,
                    "TIME STOP WEAK"
                )

                continue


            # =================================================
            # 6. MAX HOLD
            # =================================================

            if (

                held_minutes

                >=

                CONFIG[
                    "max_hold_minutes"
                ]
            ):

                self.sell(
                    address,
                    "MAX HOLD EXIT"
                )

                continue


        self.save_state()


    # ========================================================
    # SELL
    # ========================================================

    def sell(
        self,
        address,
        reason
    ):

        if address not in self.positions:
            return


        position = (
            self.positions[
                address
            ]
        )


        market_price = safe_float(
            position.get(
                "current_price"
            )
        )


        fill_price = (
            self.sell_fill_price(
                market_price
            )
        )


        quantity = safe_float(
            position.get(
                "quantity"
            )
        )


        gross_value = (

            quantity

            *

            fill_price
        )


        exit_fee = (

            gross_value

            *

            CONFIG[
                "fee_pct"
            ]

            /

            100
        )


        net_value = (

            gross_value

            -

            exit_fee
        )


        cost = safe_float(
            position.get(
                "amount"
            )
        )


        profit = (

            net_value

            -

            cost
        )


        pnl_pct = (

            profit

            /

            cost

            *

            100

            if cost > 0

            else 0
        )


        held_minutes = (

            time.time()

            -

            safe_float(
                position.get(
                    "opened_timestamp"
                ),
                time.time()
            )

        ) / 60


        self.balance += net_value

        self.daily_profit += profit


        # ====================================================
        # LOSS STREAK / CIRCUIT BREAKER
        # ====================================================

        if profit > 0:

            self.consecutive_losses = 0

        else:

            self.consecutive_losses += 1


        if reason in (
            "STOP LOSS",
            "EMERGENCY STOP"
        ):

            self.last_stop_time = (
                time.time()
            )


        if (

            self.consecutive_losses

            >=

            CONFIG[
                "max_consecutive_losses"
            ]
        ):

            self.breaker_until = (

                time.time()

                +

                CONFIG[
                    "loss_streak_pause_seconds"
                ]
            )


        self.token_last_exit[
            address
        ] = time.time()


        self.trade_history.append({

            "symbol":
                position.get(
                    "symbol",
                    "UNKNOWN"
                ),

            "name":
                position.get(
                    "name",
                    "Unknown"
                ),

            "address":
                address,

            "profit":
                profit,

            "pnl_pct":
                pnl_pct,

            "reason":
                reason,

            "score":
                position.get(
                    "score",
                    0
                ),

            "held_minutes":
                held_minutes,

            "entry_utc_hour":
                position.get(
                    "entry_utc_hour"
                ),

            "entry_fee":
                position.get(
                    "entry_fee",
                    0
                ),

            "exit_fee":
                exit_fee,

            "closed_at":
                now_sydney()
                .isoformat(),
        })


        del self.positions[
            address
        ]


        self.save_state()


        icon = (
            "🟢"
            if profit > 0
            else "🔴"
        )


        breaker_text = ""

        if self.breaker_active():

            breaker_text = (

                "\n\n🛑 LOSS-STREAK CIRCUIT BREAKER\n"

                f"Paused for approximately "
                f"{self.breaker_remaining() / 60:.0f} min"
            )


        self.telegram.send(

            f"{icon} RESEARCH TRADE CLOSED\n\n"

            f"{position.get('name')} "
            f"({position.get('symbol')})\n\n"

            f"Reason: "
            f"{reason}\n"

            f"Return after simulated costs: "
            f"{pnl_pct:+.2f}%\n"

            f"P/L: "
            f"${profit:+.2f}\n"

            f"Held: "
            f"{held_minutes:.1f} min\n\n"

            f"Consecutive losses: "
            f"{self.consecutive_losses}\n"

            f"Session P/L: "
            f"${self.daily_profit:+.2f}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}"

            f"{breaker_text}"
        )


    # ========================================================
    # ENTRY ENGINE
    # ========================================================

    def try_new_entries(self):

        if self.entries_paused:
            return

        if self.daily_loss_hit():
            return

        if self.breaker_active():
            return

        if self.stop_cooldown_active():
            return

        if (
            self.daily_entries
            >=
            CONFIG[
                "max_daily_entries"
            ]
        ):
            return

        if (
            len(self.positions)
            >=
            CONFIG[
                "max_open_positions"
            ]
        ):
            return


        candidates = (
            self.scan_market()
        )


        for token in candidates:

            if (
                len(self.positions)
                >=
                CONFIG[
                    "max_open_positions"
                ]
            ):
                break

            if (
                self.daily_entries
                >=
                CONFIG[
                    "max_daily_entries"
                ]
            ):
                break

            address = token[
                "address"
            ]

            if address in self.positions:
                continue

            if not self.token_reentry_allowed(
                address
            ):
                continue

            if self.buy(
                token
            ):

                time.sleep(
                    0.20
                )


    # ========================================================
    # STATS HELPERS
    # ========================================================

    def closed_trades(
        self
    ):

        return len(
            self.trade_history
        )


    def wins(
        self
    ):

        return [

            trade

            for trade in self.trade_history

            if safe_float(
                trade.get(
                    "profit"
                )
            )
            >
            0
        ]


    def losses(
        self
    ):

        return [

            trade

            for trade in self.trade_history

            if safe_float(
                trade.get(
                    "profit"
                )
            )
            <=
            0
        ]


    # ========================================================
    # STATUS
    # ========================================================

    def status_message(self):

        total = (
            self.closed_trades()
        )

        wins = len(
            self.wins()
        )

        losses = len(
            self.losses()
        )


        win_rate = (

            wins
            /
            total
            *
            100

            if total

            else 0
        )


        if self.daily_loss_hit():

            status = (
                "🛑 DAILY LOSS STOP"
            )

        elif self.breaker_active():

            status = (

                "🛑 CIRCUIT BREAKER "

                f"{self.breaker_remaining() / 60:.1f} MIN"
            )

        elif self.stop_cooldown_active():

            status = (

                "⏳ STOP COOLDOWN "

                f"{self.stop_cooldown_remaining() / 60:.1f} MIN"
            )

        elif self.entries_paused:

            status = (
                "⏸ PAUSED"
            )

        elif (
            self.daily_entries
            >=
            CONFIG[
                "max_daily_entries"
            ]
        ):

            status = (
                "🟡 ENTRY LIMIT"
            )

        else:

            status = (
                "🟢 ACTIVE"
            )


        return (

            "🤖 JARVIS RESEARCH STATUS\n\n"

            f"Cash: "
            f"${self.balance:,.2f}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}\n"

            f"Total P/L: "
            f"${self.total_profit():+,.2f}\n"

            f"Session P/L: "
            f"${self.daily_profit:+,.2f}\n"

            f"Open P/L: "
            f"${self.unrealized_profit():+,.2f}\n\n"

            f"Closed trades: "
            f"{total}\n"

            f"Wins: "
            f"{wins}\n"

            f"Losses: "
            f"{losses}\n"

            f"Win rate: "
            f"{win_rate:.1f}%\n\n"

            f"Open positions: "
            f"{len(self.positions)}/"
            f"{CONFIG['max_open_positions']}\n"

            f"Session entries: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n"

            f"Loss streak: "
            f"{self.consecutive_losses}\n"

            f"Daily loss: "
            f"{self.daily_loss_pct():.2f}%/"
            f"{CONFIG['max_daily_loss_pct']:.2f}%\n\n"

            f"Status: "
            f"{status}\n"

            "Mode: PAPER TRADING"
        )


    # ========================================================
    # TODAY
    # ========================================================

    def today_message(self):

        return (

            "📅 CURRENT PAPER SESSION\n\n"

            f"Session P/L: "
            f"${self.daily_profit:+.2f}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}\n"

            f"Entries: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n"

            f"Open positions: "
            f"{len(self.positions)}\n"

            f"Loss streak: "
            f"{self.consecutive_losses}\n"

            f"Daily drawdown: "
            f"{self.daily_loss_pct():.2f}%"
        )


    # ========================================================
    # POSITIONS
    # ========================================================

    def positions_message(self):

        if not self.positions:

            return (

                "📊 OPEN POSITIONS\n\n"

                "No open paper trades."
            )


        message = (
            "📊 OPEN POSITIONS\n\n"
        )


        for position in (
            self.positions.values()
        ):

            entry = safe_float(
                position.get(
                    "entry_price"
                )
            )

            current = safe_float(
                position.get(
                    "current_price"
                )
            )

            pnl = (

                (
                    current
                    -
                    entry
                )

                /
                entry
                *
                100

                if entry > 0

                else 0
            )

            high = safe_float(
                position.get(
                    "highest_price"
                )
            )

            high_pct = (

                (
                    high
                    -
                    entry
                )

                /
                entry
                *
                100

                if entry > 0

                else 0
            )

            protected = safe_float(

                position.get(
                    "protected_stop_price"
                )
            )


            message += (

                f"{position.get('symbol')}\n"

                f"P/L: "
                f"{pnl:+.2f}%\n"

                f"High: "
                f"{high_pct:+.2f}%\n"

                f"Size: "
                f"${safe_float(position.get('amount')):.2f}\n"

                f"Protection: "
                f"{position.get('protected_reason') or 'OFF'}\n"

                f"Runner mode: "
                f"{'YES' if position.get('runner_mode') else 'NO'}\n"

                f"Protected price: "
                f"{protected:.10f}\n\n"
            )


        return message


    # ========================================================
    # RECENT TRADES
    # ========================================================

    def trades_message(self):

        if not self.trade_history:

            return (

                "📜 RECENT TRADES\n\n"

                "No closed trades."
            )


        message = (
            "📜 RECENT TRADES\n\n"
        )


        for trade in reversed(
            self.trade_history[
                -10:
            ]
        ):

            profit = safe_float(
                trade.get(
                    "profit"
                )
            )

            icon = (
                "🟢"
                if profit > 0
                else "🔴"
            )

            message += (

                f"{icon} "
                f"{trade.get('symbol', 'UNKNOWN')}\n"

                f"{trade.get('reason', '')}\n"

                f"{safe_float(trade.get('pnl_pct')):+.2f}% | "

                f"${profit:+.2f}\n"

                f"Held: "
                f"{safe_float(trade.get('held_minutes')):.1f}m\n\n"
            )


        return message


    # ========================================================
    # STATS
    # ========================================================

    def stats_message(self):

        if not self.trade_history:

            return (

                "📈 RESEARCH STATS\n\n"

                "No closed trades yet."
            )


        profits = [

            safe_float(
                trade.get(
                    "profit"
                )
            )

            for trade
            in self.trade_history
        ]


        returns = [

            safe_float(
                trade.get(
                    "pnl_pct"
                )
            )

            for trade
            in self.trade_history
        ]


        wins = [
            p
            for p in profits
            if p > 0
        ]


        losses = [
            p
            for p in profits
            if p <= 0
        ]


        total = len(
            profits
        )


        win_rate = (

            len(wins)
            /
            total
            *
            100
        )


        average_win = (

            sum(wins)
            /
            len(wins)

            if wins
            else 0
        )


        average_loss = (

            sum(losses)
            /
            len(losses)

            if losses
            else 0
        )


        gross_profit = sum(
            wins
        )

        gross_loss = abs(
            sum(losses)
        )


        if gross_loss > 0:

            profit_factor_text = (

                f"{gross_profit / gross_loss:.2f}"
            )

        elif gross_profit > 0:

            profit_factor_text = "∞"

        else:

            profit_factor_text = "0.00"


        # ====================================================
        # FRAGILITY TEST
        # Remove largest winners.
        # ====================================================

        sorted_returns = sorted(
            returns,
            reverse=True
        )


        total_return = sum(
            returns
        )


        remove_1 = sum(
            sorted_returns[
                1:
            ]
        ) if len(
            sorted_returns
        ) > 1 else 0


        remove_3 = sum(
            sorted_returns[
                3:
            ]
        ) if len(
            sorted_returns
        ) > 3 else 0


        average_return = (

            total_return

            /

            total

            if total

            else 0
        )


        return (

            "📈 JARVIS RESEARCH STATS\n\n"

            f"Closed trades: "
            f"{total}\n"

            f"Win rate: "
            f"{win_rate:.1f}%\n"

            f"Net P/L: "
            f"${sum(profits):+,.2f}\n"

            f"Average return/trade: "
            f"{average_return:+.2f}%\n"

            f"Average winner: "
            f"${average_win:+.2f}\n"

            f"Average loser: "
            f"${average_loss:+.2f}\n"

            f"Profit factor: "
            f"{profit_factor_text}\n\n"

            "🧪 FRAGILITY TEST\n"

            f"Sum of returns: "
            f"{total_return:+.2f}%\n"

            f"Remove best trade: "
            f"{remove_1:+.2f}%\n"

            f"Remove best 3: "
            f"{remove_3:+.2f}%\n\n"

            f"Best return: "
            f"{max(returns):+.2f}%\n"

            f"Worst return: "
            f"{min(returns):+.2f}%\n\n"

            f"Equity: "
            f"${self.get_equity():,.2f}"
        )


    # ========================================================
    # SCAN
    # ========================================================

    def scan_message(self):

        scan_time = (

            self.last_scan_time.strftime(
                "%H:%M:%S"
            )

            if self.last_scan_time

            else "Waiting"
        )


        reject_text = ""

        for reason, count in sorted(

            self.rejection_counts.items(),

            key=lambda item:
                item[1],

            reverse=True
        )[:5]:

            reject_text += (

                f"{reason}: "
                f"{count}\n"
            )


        if not reject_text:

            reject_text = (
                "No rejection data yet.\n"
            )


        return (

            "🔎 RESEARCH SCANNER\n\n"

            f"Last scan: "
            f"{scan_time}\n"

            f"Discovered: "
            f"{self.last_discovered}\n"

            f"Pairs: "
            f"{self.last_pairs}\n"

            f"Qualified: "
            f"{self.last_qualified}\n"

            f"Best: "
            f"{self.last_best_symbol} "
            f"{self.last_best_score}/100\n\n"

            "TOP REJECTION REASONS\n"

            f"{reject_text}\n"

            f"UTC hour: "
            f"{now_utc().hour}\n"

            f"Hour blacklist: "
            f"{'ON' if CONFIG['hour_blacklist_enabled'] else 'OFF'}"
        )


    # ========================================================
    # BREAKER
    # ========================================================

    def breaker_message(self):

        if self.breaker_active():

            breaker = (

                "🛑 ACTIVE\n"

                f"Remaining: "
                f"{self.breaker_remaining() / 60:.1f} min"
            )

        else:

            breaker = (
                "🟢 NOT ACTIVE"
            )


        return (

            "🛡 CIRCUIT BREAKER\n\n"

            f"{breaker}\n\n"

            f"Current consecutive losses: "
            f"{self.consecutive_losses}/"
            f"{CONFIG['max_consecutive_losses']}\n"

            f"Pause duration: "
            f"{CONFIG['loss_streak_pause_seconds'] / 60:.0f} min\n"

            f"Daily loss: "
            f"{self.daily_loss_pct():.2f}%/"
            f"{CONFIG['max_daily_loss_pct']:.2f}%"
        )


    # ========================================================
    # REJECTION STATS
    # ========================================================

    def rejection_message(self):

        if not self.rejections:

            return (

                "🧪 REJECTION LOG\n\n"

                "No rejected candidates logged yet."
            )


        counts = {}

        for item in self.rejections:

            reason = item.get(
                "reason",
                "unknown"
            )

            counts[
                reason
            ] = (

                counts.get(
                    reason,
                    0
                )

                +

                1
            )


        ranked = sorted(

            counts.items(),

            key=lambda item:
                item[1],

            reverse=True
        )


        message = (

            "🧪 REJECTION LOG\n\n"

            f"Recorded candidates: "
            f"{len(self.rejections)}\n\n"
        )


        for reason, count in ranked[
            :10
        ]:

            message += (

                f"{reason}: "
                f"{count}\n"
            )


        message += (

            "\nThese are PAPER research telemetry, "
            "not proof that each filter improves returns."
        )


        return message


    # ========================================================
    # MAIN LOOP
    # ========================================================

    def run(self):

        self.telegram.send(

            "🤖 JARVIS RESEARCH EDITION ONLINE\n\n"

            "LIVE Solana market data\n"

            "PAPER TRADING ONLY\n\n"

            f"Starting balance: "
            f"${self.get_equity():,.2f}\n"

            f"Position size max: "
            f"{CONFIG['max_position_pct']}%\n"

            f"Open positions: "
            f"{CONFIG['max_open_positions']}\n"

            f"Entry limit: "
            f"{CONFIG['max_daily_entries']}\n\n"

            "Architecture:\n"

            "✅ signal scoring\n"
            "✅ hard gates\n"
            "✅ position sizing\n"
            "✅ tiered exits\n"
            "✅ time stops\n"
            "✅ circuit breaker\n"
            "✅ rejection telemetry\n"
            "✅ fragility statistics\n"
            "✅ simulated fees/slippage\n\n"

            "Send /status"
        )


        last_position_update = 0


        while True:

            try:

                self.reset_day_if_needed()


                self.telegram.handle_commands(
                    self
                )


                current = time.time()


                # ============================================
                # POSITION MONITORING
                # ============================================

                if (

                    current

                    -

                    last_position_update

                    >=

                    CONFIG[
                        "position_update_seconds"
                    ]
                ):

                    self.update_positions()

                    last_position_update = (
                        current
                    )


                # ============================================
                # SCANNER
                # ============================================

                if (

                    current

                    -

                    self.last_scan

                    >=

                    CONFIG[
                        "scan_every_seconds"
                    ]
                ):

                    self.try_new_entries()

                    self.last_scan = (
                        current
                    )


                # ============================================
                # STATUS
                # ============================================

                if (

                    current

                    -

                    self.last_status

                    >=

                    CONFIG[
                        "telegram_status_seconds"
                    ]
                ):

                    self.telegram.send(
                        self.status_message()
                    )

                    self.last_status = (
                        current
                    )


                time.sleep(
                    1
                )


            except KeyboardInterrupt:

                self.save_state()
                self.save_rejections()

                break


            except Exception as error:

                print(
                    "BOT ERROR:",
                    error
                )

                self.telegram.send(

                    "⚠️ JARVIS ERROR\n\n"

                    f"{error}"
                )

                time.sleep(
                    5
                )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    bot = JarvisBot()

    bot.run()
