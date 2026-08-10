import os
import time
import json
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# JARVIS FAST V4.1
# LIVE SOLANA MARKET DATA
# PAPER TRADING ONLY
#
# KEY IMPROVEMENTS:
# - Persistent break-even protection
# - Persistent trailing-stop floor
# - 2-second batch price updates
# - 7% normal stop
# - 11% emergency gap stop
# - 8-minute cooldown after stopped trade
# - 60-minute same-token re-entry block
# - Better entry filters
# - /resetday
# - /status
# - /today
# - /positions
# - /trades
# - /stats
# - /scan
# ============================================================


SYDNEY = ZoneInfo("Australia/Sydney")

DEX_BASE = "https://api.dexscreener.com"

# Fresh state so V4.1 results are clean
STATE_FILE = "jarvis_v41_state.json"


CONFIG = {

    # ========================================================
    # PAPER ACCOUNT
    # ========================================================

    "starting_balance": 1000.00,


    # ========================================================
    # POSITION CONTROL
    # ========================================================

    "max_position_pct": 7.5,

    "max_open_positions": 3,

    "max_daily_entries": 30,


    # ========================================================
    # HARD LOSS CONTROL
    # ========================================================

    "stop_loss_pct": 7.0,

    # Only used when price gaps badly between updates
    "emergency_stop_pct": 11.0,


    # ========================================================
    # WINNER MANAGEMENT
    # ========================================================

    # Large runner target
    "take_profit_pct": 24.0,

    # Once trade has reached +6%, protect at approximately +0.5%
    "break_even_activation_pct": 6.0,

    "break_even_lock_pct": 0.5,

    # Once trade reaches +8%, activate trailing protection
    "trailing_activation_pct": 8.0,

    # Follow winner 4% below highest observed market price
    "trailing_stop_pct": 4.0,


    # ========================================================
    # DAILY SAFETY
    # ========================================================

    "max_daily_loss_pct": 4.0,


    # ========================================================
    # COOLDOWNS
    # ========================================================

    "stop_loss_cooldown_seconds": 8 * 60,

    "token_reentry_cooldown_seconds": 60 * 60,


    # ========================================================
    # ENTRY FILTERS
    # ========================================================

    "min_liquidity": 40000,

    "min_volume_24h": 100000,

    "min_market_cap": 50000,

    "max_market_cap": 4000000,

    "min_5m_change": 1.5,

    "max_5m_change": 14.0,

    "min_1h_change": -2.0,

    "max_1h_change": 50.0,

    "min_5m_buys": 8,

    "min_buy_pressure_pct": 56.0,

    "min_score": 66,

    "min_pair_age_minutes": 5,

    "max_pair_age_days": 21,


    # ========================================================
    # DISCOVERY
    # ========================================================

    "max_discovered_tokens": 120,

    "batch_size": 30,


    # ========================================================
    # SPEED
    # ========================================================

    "scan_every_seconds": 15,

    # All open positions refreshed together every 2 sec
    "price_update_seconds": 2,

    "status_every_seconds": 300,


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


def today_key():
    return now_sydney().strftime("%Y-%m-%d")


def safe_float(value, default=0.0):

    try:

        if value is None:
            return default

        return float(value)

    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):

    try:

        if value is None:
            return default

        return int(value)

    except (TypeError, ValueError):
        return default


def http_json(
    url,
    method="GET",
    data=None,
    timeout=15
):

    try:

        headers = {
            "User-Agent": "JarvisFastV41/1.0",
            "Accept": "application/json"
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
            method=method
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            return json.loads(
                response
                .read()
                .decode("utf-8")
            )

    except Exception as error:

        print(
            "HTTP ERROR:",
            url,
            error
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


    def api(
        self,
        method,
        data=None
    ):

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
            data=data
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
                "description": "Jarvis dashboard"
            },

            {
                "command": "today",
                "description": "Current paper session"
            },

            {
                "command": "positions",
                "description": "Open trades"
            },

            {
                "command": "trades",
                "description": "Recent trades"
            },

            {
                "command": "stats",
                "description": "Trading statistics"
            },

            {
                "command": "scan",
                "description": "Scanner activity"
            },

            {
                "command": "resetday",
                "description": "Reset paper session"
            },

            {
                "command": "pause",
                "description": "Pause new entries"
            },

            {
                "command": "resume",
                "description": "Resume entries"
            },

            {
                "command": "paper",
                "description": "Show trading mode"
            },

            {
                "command": "help",
                "description": "Show commands"
            }
        ])

        result = self.api(
            "setMyCommands",
            {
                "commands": commands
            }
        )

        print(
            "setMyCommands:",
            result
        )


    def send(
        self,
        message
    ):

        if not self.token:
            return

        if not self.chat_id:
            return

        self.api(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": message
            }
        )


    def get_updates(self):

        if not self.token:
            return []

        data = {
            "timeout": 1
        }

        if self.offset is not None:
            data["offset"] = self.offset

        result = self.api(
            "getUpdates",
            data
        )

        if not result:
            return []

        if not result.get("ok"):
            return []

        updates = result.get(
            "result",
            []
        )

        if updates:

            self.offset = (
                updates[-1]["update_id"]
                + 1
            )

        return updates


    def handle_commands(
        self,
        bot
    ):

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

                self.chat_id = (
                    incoming_chat_id
                )


            if incoming_chat_id != self.chat_id:
                continue


            command = (
                text
                .lower()
                .split()[0]
                .split("@")[0]
            )


            # =================================================
            # START
            # =================================================

            if command == "/start":

                self.send(

                    "🤖 JARVIS FAST V4.1 ONLINE\n\n"

                    "Market data: LIVE\n"
                    "Mode: PAPER TRADING\n\n"

                    "🛡 Persistent break-even protection\n"
                    "🛡 Dynamic trailing stop\n"
                    "⚡ 2-second position monitoring\n"
                    "🛡 Loss cooldown\n\n"

                    "/status\n"
                    "/today\n"
                    "/positions\n"
                    "/trades\n"
                    "/stats\n"
                    "/scan\n"
                    "/resetday\n"
                    "/pause\n"
                    "/resume\n"
                    "/paper\n"
                    "/help"
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


            elif command == "/resetday":

                old_entries = (
                    bot.daily_entries
                )

                old_profit = (
                    bot.daily_profit
                )

                equity = (
                    bot.get_equity()
                )

                bot.manual_reset_day()

                self.send(

                    "🔄 PAPER SESSION RESET\n\n"

                    f"Previous trades: "
                    f"{old_entries}/"
                    f"{CONFIG['max_daily_entries']}\n"

                    f"Previous session P/L: "
                    f"${old_profit:+.2f}\n\n"

                    "NEW SESSION\n"

                    f"Trades: 0/"
                    f"{CONFIG['max_daily_entries']}\n"

                    "Session P/L: $+0.00\n\n"

                    f"Equity kept: "
                    f"${equity:,.2f}\n\n"

                    "✅ Jarvis can scan again."
                )


            elif command == "/pause":

                bot.entries_paused = True

                bot.save_state()

                self.send(

                    "⏸ JARVIS PAUSED\n\n"

                    "No new entries.\n"
                    "Open positions remain protected."
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

                    "Execution: PAPER TRADING ONLY\n\n"

                    "No wallet connected."
                )


            elif command == "/help":

                self.send(

                    "🤖 JARVIS V4.1 COMMANDS\n\n"

                    "/status - Dashboard\n"

                    "/today - Session results\n"

                    "/positions - Open positions\n"

                    "/trades - Recent closed trades\n"

                    "/stats - Strategy statistics\n"

                    "/scan - Scanner information\n"

                    "/resetday - New paper session\n"

                    "/pause - Pause entries\n"

                    "/resume - Resume entries\n"

                    "/paper - Show mode"
                )


# ============================================================
# DEXSCREENER
# ============================================================

class DexScreener:

    def list_endpoint(
        self,
        path
    ):

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

        return self.list_endpoint(
            "/token-profiles/latest/v1"
        )


    def latest_boosts(self):

        return self.list_endpoint(
            "/token-boosts/latest/v1"
        )


    def top_boosts(self):

        return self.list_endpoint(
            "/token-boosts/top/v1"
        )


    def community_takeovers(self):

        return self.list_endpoint(
            "/community-takeovers/latest/v1"
        )


    # ========================================================
    # DISCOVERY
    # ========================================================

    def discover_addresses(self):

        feeds = [

            self.latest_profiles(),

            self.latest_boosts(),

            self.top_boosts(),

            self.community_takeovers()
        ]


        addresses = []

        seen = set()


        max_length = max(
            [len(feed) for feed in feeds]
            +
            [0]
        )


        for index in range(
            max_length
        ):

            for feed in feeds:

                if index >= len(feed):
                    continue


                item = feed[index]


                if (
                    item.get("chainId")
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


                addresses.append(
                    address
                )


                if (
                    len(addresses)
                    >=
                    CONFIG[
                        "max_discovered_tokens"
                    ]
                ):

                    return addresses


        return addresses


    # ========================================================
    # BATCH TOKEN LOOKUP
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

        all_pairs = []


        batch_size = CONFIG[
            "batch_size"
        ]


        for start in range(
            0,
            len(addresses),
            batch_size
        ):

            batch = addresses[
                start:
                start + batch_size
            ]


            all_pairs.extend(
                self.token_pairs_batch(
                    batch
                )
            )


            time.sleep(
                0.10
            )


        return all_pairs


    # ========================================================
    # BEST PAIR PER TOKEN
    # ========================================================

    def best_pairs_by_token(
        self,
        pairs
    ):

        best = {}


        for pair in pairs:

            if (
                pair.get("chainId")
                !=
                "solana"
            ):
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


            old_pair = best.get(
                address
            )


            if old_pair is None:

                best[address] = pair

                continue


            old_liquidity = safe_float(

                old_pair.get(
                    "liquidity",
                    {}
                ).get(
                    "usd"
                )
            )


            if liquidity > old_liquidity:

                best[address] = pair


        return best


# ============================================================
# JARVIS BOT
# ============================================================

class JarvisBot:

    def __init__(self):

        self.dex = DexScreener()

        self.telegram = TelegramManager()


        # ====================================================
        # ACCOUNT
        # ====================================================

        self.balance = CONFIG[
            "starting_balance"
        ]

        self.positions = {}

        self.trade_history = []


        # ====================================================
        # STATS
        # ====================================================

        self.total_wins = 0

        self.total_losses = 0


        # ====================================================
        # SESSION
        # ====================================================

        self.daily_profit = 0.0

        self.daily_entries = 0

        self.day_key = today_key()

        self.day_start_equity = CONFIG[
            "starting_balance"
        ]


        # ====================================================
        # CONTROL
        # ====================================================

        self.entries_paused = False

        self.last_scan = 0

        self.last_status = 0


        # ====================================================
        # COOLDOWNS
        # ====================================================

        self.last_stop_loss_time = 0.0

        self.token_last_exit = {}


        # ====================================================
        # SCANNER STATS
        # ====================================================

        self.last_scan_time = None

        self.last_discovered = 0

        self.last_pairs = 0

        self.last_qualified = 0

        self.last_best_symbol = "NONE"

        self.last_best_score = 0


        self.load_state()

        self.rebuild_stats()

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


            self.entries_paused = bool(
                data.get(
                    "entries_paused",
                    False
                )
            )


            self.last_stop_loss_time = safe_float(
                data.get(
                    "last_stop_loss_time",
                    0
                )
            )


            self.token_last_exit = data.get(
                "token_last_exit",
                {}
            )


            # ------------------------------------------------
            # MIGRATE / REPAIR POSITION FIELDS
            # ------------------------------------------------

            for address, position in self.positions.items():

                position.setdefault(
                    "protection_armed",
                    False
                )

                position.setdefault(
                    "trailing_armed",
                    False
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
                    "highest_price",
                    safe_float(
                        position.get(
                            "entry_price"
                        )
                    )
                )


            print(
                "✅ JARVIS V4.1 STATE LOADED"
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
                    self.trade_history[-1000:],

                "daily_profit":
                    self.daily_profit,

                "daily_entries":
                    self.daily_entries,

                "day_key":
                    self.day_key,

                "day_start_equity":
                    self.day_start_equity,

                "entries_paused":
                    self.entries_paused,

                "last_stop_loss_time":
                    self.last_stop_loss_time,

                "token_last_exit":
                    self.token_last_exit
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
    # REBUILD STATS
    # ========================================================

    def rebuild_stats(self):

        self.total_wins = 0

        self.total_losses = 0


        for trade in self.trade_history:

            profit = safe_float(
                trade.get(
                    "profit"
                )
            )


            if profit > 0:

                self.total_wins += 1

            else:

                self.total_losses += 1


    # ========================================================
    # ACCOUNT
    # ========================================================

    def get_equity(self):

        open_value = 0.0


        for position in self.positions.values():

            open_value += (

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


        return (
            self.balance
            +
            open_value
        )


    def total_profit(self):

        return (

            self.get_equity()

            -

            CONFIG[
                "starting_balance"
            ]
        )


    def unrealized_profit(self):

        total = 0.0


        for position in self.positions.values():

            current_value = (

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


            total += (

                current_value

                -

                safe_float(
                    position.get(
                        "amount"
                    )
                )
            )


        return total


    # ========================================================
    # DAILY RESET
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

        self.last_scan = 0

        self.save_state()


        self.telegram.send(

            "🌅 NEW JARVIS V4.1 DAY\n\n"

            f"Starting equity: "
            f"${self.day_start_equity:,.2f}\n"

            f"Trades reset: "
            f"0/{CONFIG['max_daily_entries']}"
        )


    def manual_reset_day(self):

        self.daily_profit = 0.0

        self.daily_entries = 0

        self.day_key = today_key()

        self.day_start_equity = (
            self.get_equity()
        )

        self.last_scan = 0

        self.save_state()


    # ========================================================
    # DAILY LOSS
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
    # COOLDOWN
    # ========================================================

    def stop_cooldown_remaining(self):

        if self.last_stop_loss_time <= 0:
            return 0


        elapsed = (

            time.time()

            -

            self.last_stop_loss_time
        )


        remaining = (

            CONFIG[
                "stop_loss_cooldown_seconds"
            ]

            -

            elapsed
        )


        return max(
            0,
            int(remaining)
        )


    def stop_cooldown_active(self):

        return (
            self.stop_cooldown_remaining()
            >
            0
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
                "token_reentry_cooldown_seconds"
            ]
        )


    # ========================================================
    # TOKEN DATA
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


        total_transactions = (

            buys

            +

            sells
        )


        buy_pressure = (

            buys

            /

            total_transactions

            *

            100

            if total_transactions > 0

            else 0.0
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
                )
        }


    # ========================================================
    # SCORE
    # ========================================================

    def score_token(
        self,
        token
    ):

        score = 0


        # LIQUIDITY

        if token["liquidity"] >= 250000:

            score += 20

        elif token["liquidity"] >= 100000:

            score += 18

        elif token["liquidity"] >= 60000:

            score += 14

        elif token["liquidity"] >= 40000:

            score += 10


        # VOLUME

        if token["volume"] >= 1000000:

            score += 20

        elif token["volume"] >= 500000:

            score += 18

        elif token["volume"] >= 250000:

            score += 15

        elif token["volume"] >= 100000:

            score += 11


        # 5M MOMENTUM

        if 3 <= token["change5"] <= 8:

            score += 22

        elif 1.5 <= token["change5"] < 3:

            score += 15

        elif 8 < token["change5"] <= 11:

            score += 14

        elif 11 < token["change5"] <= 14:

            score += 7


        # 1H MOMENTUM

        if 3 <= token["change1h"] <= 25:

            score += 15

        elif -2 <= token["change1h"] < 3:

            score += 8

        elif 25 < token["change1h"] <= 50:

            score += 6


        # BUY PRESSURE

        if token["buy_pressure"] >= 70:

            score += 20

        elif token["buy_pressure"] >= 63:

            score += 17

        elif token["buy_pressure"] >= 59:

            score += 13

        elif token["buy_pressure"] >= 56:

            score += 9


        # MARKET CAP

        if (
            75000
            <=
            token["market_cap"]
            <=
            1000000
        ):

            score += 10

        elif (
            1000000
            <
            token["market_cap"]
            <=
            2500000
        ):

            score += 7

        elif (
            2500000
            <
            token["market_cap"]
            <=
            4000000
        ):

            score += 4


        return min(
            score,
            100
        )


    # ========================================================
    # FILTER
    # ========================================================

    def valid_token(
        self,
        token
    ):

        if token["price"] <= 0:
            return False


        if (
            token["liquidity"]
            <
            CONFIG["min_liquidity"]
        ):
            return False


        if (
            token["volume"]
            <
            CONFIG["min_volume_24h"]
        ):
            return False


        if not (
            CONFIG["min_market_cap"]
            <=
            token["market_cap"]
            <=
            CONFIG["max_market_cap"]
        ):
            return False


        if not (
            CONFIG["min_5m_change"]
            <=
            token["change5"]
            <=
            CONFIG["max_5m_change"]
        ):
            return False


        if not (
            CONFIG["min_1h_change"]
            <=
            token["change1h"]
            <=
            CONFIG["max_1h_change"]
        ):
            return False


        if (
            token["buys5"]
            <
            CONFIG["min_5m_buys"]
        ):
            return False


        if (
            token["buy_pressure"]
            <
            CONFIG[
                "min_buy_pressure_pct"
            ]
        ):
            return False


        age = token["age"]


        if age is not None:

            if (
                age
                <
                CONFIG[
                    "min_pair_age_minutes"
                ]
            ):
                return False


            max_age_minutes = (

                CONFIG[
                    "max_pair_age_days"
                ]

                *

                24

                *

                60
            )


            if age > max_age_minutes:
                return False


        token["score"] = (
            self.score_token(
                token
            )
        )


        return (

            token["score"]

            >=

            CONFIG["min_score"]
        )


    # ========================================================
    # SCAN MARKET
    # ========================================================

    def scan_market(self):

        addresses = (
            self.dex
            .discover_addresses()
        )


        self.last_discovered = len(
            addresses
        )


        self.last_scan_time = (
            now_sydney()
        )


        if not addresses:

            self.last_pairs = 0

            self.last_qualified = 0

            self.last_best_symbol = "NONE"

            self.last_best_score = 0

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


        self.last_pairs = len(
            best_pairs
        )


        candidates = []


        for address in addresses:

            pair = best_pairs.get(
                address
            )


            if not pair:
                continue


            token = self.pair_to_token(
                pair
            )


            if self.valid_token(
                token
            ):

                candidates.append(
                    token
                )


        candidates.sort(

            key=lambda token:
                token["score"],

            reverse=True
        )


        self.last_qualified = len(
            candidates
        )


        if candidates:

            self.last_best_symbol = (
                candidates[0]["symbol"]
            )

            self.last_best_score = (
                candidates[0]["score"]
            )

        else:

            self.last_best_symbol = "NONE"

            self.last_best_score = 0


        print(

            "V4.1 SCAN |",

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

    def position_size(self):

        equity = (
            self.get_equity()
        )


        maximum = (

            equity

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


        amount = self.position_size()

        price = token["price"]


        if amount <= 0:
            return False


        if price <= 0:
            return False


        quantity = (
            amount
            /
            price
        )


        self.positions[
            address
        ] = {

            "symbol":
                token["symbol"],

            "name":
                token["name"],

            "entry_price":
                price,

            "current_price":
                price,

            "highest_price":
                price,

            "quantity":
                quantity,

            "amount":
                amount,

            "score":
                token["score"],

            # NEW: persistent protection fields

            "protection_armed":
                False,

            "trailing_armed":
                False,

            "protected_stop_price":
                0.0,

            "protected_reason":
                "",

            "opened_timestamp":
                time.time(),

            "opened_at":
                now_sydney()
                .isoformat()
        }


        self.balance -= amount

        self.daily_entries += 1

        self.save_state()


        self.telegram.send(

            "🟢 JARVIS V4.1 PAPER ENTRY\n\n"

            f"{token['name']} "
            f"({token['symbol']})\n\n"

            f"Score: "
            f"{token['score']}/100\n"

            f"Entry: "
            f"${price:.10f}\n"

            f"Position: "
            f"${amount:.2f}\n\n"

            f"5m: "
            f"{token['change5']:+.2f}%\n"

            f"1h: "
            f"{token['change1h']:+.2f}%\n"

            f"Buy pressure: "
            f"{token['buy_pressure']:.1f}%\n\n"

            f"Liquidity: "
            f"${token['liquidity']:,.0f}\n"

            f"24h volume: "
            f"${token['volume']:,.0f}\n"

            f"Market cap: "
            f"${token['market_cap']:,.0f}\n\n"

            f"Hard stop: "
            f"-{CONFIG['stop_loss_pct']}%\n"

            f"Break-even arms: "
            f"+{CONFIG['break_even_activation_pct']}%\n"

            f"Trailing arms: "
            f"+{CONFIG['trailing_activation_pct']}%\n"

            f"Take profit: "
            f"+{CONFIG['take_profit_pct']}%\n\n"

            f"Session trades: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}"
        )


        return True


    # ========================================================
    # ARM / UPDATE PROTECTION
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


        highest = safe_float(
            position.get(
                "highest_price"
            )
        )


        if entry <= 0:
            return


        if highest <= 0:
            return


        highest_profit_pct = (

            (
                highest
                -
                entry
            )

            /

            entry

            *

            100
        )


        # ----------------------------------------------------
        # ARM BREAK-EVEN ONCE
        # ----------------------------------------------------

        if (

            not position.get(
                "protection_armed",
                False
            )

            and

            highest_profit_pct

            >=

            CONFIG[
                "break_even_activation_pct"
            ]
        ):

            position[
                "protection_armed"
            ] = True


            break_even_floor = (

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


            old_stop = safe_float(

                position.get(
                    "protected_stop_price"
                )
            )


            position[
                "protected_stop_price"
            ] = max(
                old_stop,
                break_even_floor
            )


            position[
                "protected_reason"
            ] = "BREAK EVEN PROTECT"


            print(

                position[
                    "symbol"
                ],

                "BREAK EVEN ARMED AT",

                position[
                    "protected_stop_price"
                ]
            )


        # ----------------------------------------------------
        # ARM TRAILING
        # ----------------------------------------------------

        if (

            highest_profit_pct

            >=

            CONFIG[
                "trailing_activation_pct"
            ]
        ):

            position[
                "trailing_armed"
            ] = True


        # ----------------------------------------------------
        # MOVE TRAILING FLOOR UP
        # NEVER MOVE IT DOWN
        # ----------------------------------------------------

        if position.get(
            "trailing_armed",
            False
        ):

            trailing_floor = (

                highest

                *

                (
                    1

                    -

                    CONFIG[
                        "trailing_stop_pct"
                    ]

                    /

                    100
                )
            )


            old_stop = safe_float(

                position.get(
                    "protected_stop_price"
                )
            )


            if trailing_floor > old_stop:

                position[
                    "protected_stop_price"
                ] = (
                    trailing_floor
                )


                position[
                    "protected_reason"
                ] = (
                    "TRAILING PROFIT"
                )


    # ========================================================
    # UPDATE ALL POSITIONS IN ONE BATCH
    # ========================================================

    def update_positions(self):

        if not self.positions:

            return


        addresses = list(
            self.positions.keys()
        )


        # Only maximum 3 open, so one API request
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


            price = safe_float(
                pair.get(
                    "priceUsd"
                )
            )


            if price <= 0:
                continue


            position = (
                self.positions[
                    address
                ]
            )


            position[
                "current_price"
            ] = price


            old_high = safe_float(

                position.get(
                    "highest_price"
                )
            )


            if price > old_high:

                position[
                    "highest_price"
                ] = price


            entry = safe_float(
                position.get(
                    "entry_price"
                )
            )


            if entry <= 0:
                continue


            pnl_pct = (

                (
                    price
                    -
                    entry
                )

                /

                entry

                *

                100
            )


            # Update persistent protection first
            self.update_protection(
                position
            )


            protected_stop_price = (
                safe_float(

                    position.get(
                        "protected_stop_price"
                    )
                )
            )


            protected_reason = (
                position.get(
                    "protected_reason",
                    ""
                )
            )


            print(

                position["symbol"],

                "| P/L",

                f"{pnl_pct:+.2f}%",

                "| Protection:",

                (
                    f"${protected_stop_price:.10f}"

                    if protected_stop_price > 0

                    else "OFF"
                )
            )


            # =================================================
            # 1. PROTECTED STOP
            # =================================================

            if (

                protected_stop_price > 0

                and

                price <= protected_stop_price
            ):

                self.sell(
                    address,
                    protected_reason
                    or
                    "PROTECTED EXIT"
                )

                continue


            # =================================================
            # 2. EMERGENCY GAP STOP
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
                    "stop_loss_pct"
                ]
            ):

                self.sell(
                    address,
                    "STOP LOSS"
                )

                continue


            # =================================================
            # 4. TAKE PROFIT
            # =================================================

            if (

                pnl_pct

                >=

                CONFIG[
                    "take_profit_pct"
                ]
            ):

                self.sell(
                    address,
                    "TAKE PROFIT"
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


        exit_price = safe_float(
            position.get(
                "current_price"
            )
        )


        quantity = safe_float(
            position.get(
                "quantity"
            )
        )


        amount = safe_float(
            position.get(
                "amount"
            )
        )


        value = (
            quantity
            *
            exit_price
        )


        profit = (
            value
            -
            amount
        )


        pnl_pct = (

            profit
            /
            amount
            *
            100

            if amount > 0

            else 0.0
        )


        self.balance += value

        self.daily_profit += profit


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

            "token_address":
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

            "protection_armed":
                position.get(
                    "protection_armed",
                    False
                ),

            "trailing_armed":
                position.get(
                    "trailing_armed",
                    False
                ),

            "closed_at":
                now_sydney()
                .isoformat()
        })


        self.token_last_exit[
            address
        ] = time.time()


        if reason in (
            "STOP LOSS",
            "EMERGENCY STOP"
        ):

            self.last_stop_loss_time = (
                time.time()
            )


        del self.positions[
            address
        ]


        self.rebuild_stats()

        self.save_state()


        icon = (
            "🟢"
            if profit > 0
            else "🔴"
        )


        cooldown_text = ""


        if reason in (
            "STOP LOSS",
            "EMERGENCY STOP"
        ):

            cooldown_text = (

                "\n\n"
                "⏳ 8-MINUTE ENTRY COOLDOWN"
            )


        self.telegram.send(

            f"{icon} JARVIS V4.1 TRADE CLOSED\n\n"

            f"{position.get('name')} "
            f"({position.get('symbol')})\n\n"

            f"Reason: "
            f"{reason}\n"

            f"Exit: "
            f"${exit_price:.10f}\n"

            f"Return: "
            f"{pnl_pct:+.2f}%\n"

            f"Trade P/L: "
            f"${profit:+.2f}\n\n"

            f"Session P/L: "
            f"${self.daily_profit:+.2f}\n"

            f"Total P/L: "
            f"${self.total_profit():+,.2f}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}"

            f"{cooldown_text}"
        )


    # ========================================================
    # ENTRY ENGINE
    # ========================================================

    def try_new_entries(self):

        if self.entries_paused:
            return


        if self.daily_loss_hit():
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


        candidates = self.scan_market()


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
    # STATUS
    # ========================================================

    def status_message(self):

        closed = (
            self.total_wins
            +
            self.total_losses
        )


        win_rate = (

            self.total_wins
            /
            closed
            *
            100

            if closed > 0

            else 0.0
        )


        if self.daily_loss_hit():

            status = (
                "🛑 DAILY LOSS STOP"
            )


        elif self.entries_paused:

            status = (
                "⏸ PAUSED"
            )


        elif self.stop_cooldown_active():

            status = (

                "⏳ COOLDOWN "

                f"{self.stop_cooldown_remaining() / 60:.1f} MIN"
            )


        elif (
            self.daily_entries
            >=
            CONFIG[
                "max_daily_entries"
            ]
        ):

            status = (
                "🟡 SESSION LIMIT"
            )


        else:

            status = (
                "🟢 ACTIVE"
            )


        return (

            "🤖 JARVIS V4.1 STATUS\n\n"

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

            f"Wins: "
            f"{self.total_wins}\n"

            f"Losses: "
            f"{self.total_losses}\n"

            f"Win rate: "
            f"{win_rate:.1f}%\n"

            f"Open trades: "
            f"{len(self.positions)}/"
            f"{CONFIG['max_open_positions']}\n"

            f"Session trades: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n\n"

            f"Loss used: "
            f"{self.daily_loss_pct():.2f}%/"
            f"{CONFIG['max_daily_loss_pct']:.2f}%\n"

            f"Status: "
            f"{status}\n"

            "Mode: PAPER TRADING"
        )


    # ========================================================
    # TODAY
    # ========================================================

    def today_message(self):

        return (

            "📅 JARVIS V4.1 SESSION\n\n"

            f"Session P/L: "
            f"${self.daily_profit:+,.2f}\n"

            f"Trades: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n"

            f"Open trades: "
            f"{len(self.positions)}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}\n\n"

            f"Loss used: "
            f"{self.daily_loss_pct():.2f}%/"
            f"{CONFIG['max_daily_loss_pct']:.2f}%"
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


        for position in self.positions.values():

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


            protected_stop = safe_float(

                position.get(
                    "protected_stop_price"
                )
            )


            protection_text = (

                f"${protected_stop:.10f}"

                if protected_stop > 0

                else "OFF"
            )


            message += (

                f"{position.get('symbol')}\n"

                f"Entry: "
                f"${entry:.10f}\n"

                f"Current: "
                f"${current:.10f}\n"

                f"P/L: "
                f"{pnl:+.2f}%\n"

                f"Size: "
                f"${safe_float(position.get('amount')):.2f}\n"

                f"Protected stop: "
                f"{protection_text}\n"

                f"Protection: "
                f"{position.get('protected_reason') or 'OFF'}\n\n"
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
            self.trade_history[-10:]
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

                f"${profit:+.2f}\n\n"
            )


        return message


    # ========================================================
    # STATS
    # ========================================================

    def stats_message(self):

        if not self.trade_history:

            return (

                "📈 JARVIS V4.1 STATS\n\n"

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


        wins = [
            value
            for value in profits
            if value > 0
        ]


        losses = [
            value
            for value in profits
            if value <= 0
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

            if total
            else 0
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


        average_trade = (

            sum(profits)
            /
            total

            if total
            else 0
        )


        gross_profit = sum(
            wins
        )


        gross_loss = abs(
            sum(losses)
        )


        if gross_loss > 0:

            profit_factor = (
                gross_profit
                /
                gross_loss
            )

            pf_text = (
                f"{profit_factor:.2f}"
            )

        elif gross_profit > 0:

            pf_text = "∞"

        else:

            pf_text = "0.00"


        return (

            "📈 JARVIS V4.1 STATS\n\n"

            f"Closed trades: "
            f"{total}\n"

            f"Wins: "
            f"{len(wins)}\n"

            f"Losses: "
            f"{len(losses)}\n"

            f"Win rate: "
            f"{win_rate:.1f}%\n\n"

            f"Net P/L: "
            f"${sum(profits):+,.2f}\n"

            f"Average trade: "
            f"${average_trade:+,.2f}\n"

            f"Average win: "
            f"${average_win:+,.2f}\n"

            f"Average loss: "
            f"${average_loss:+,.2f}\n"

            f"Profit factor: "
            f"{pf_text}\n\n"

            f"Best trade: "
            f"${max(profits):+,.2f}\n"

            f"Worst trade: "
            f"${min(profits):+,.2f}\n"

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


        return (

            "🔎 JARVIS V4.1 SCANNER\n\n"

            f"Last scan: "
            f"{scan_time}\n"

            f"Tokens discovered: "
            f"{self.last_discovered}\n"

            f"Pairs found: "
            f"{self.last_pairs}\n"

            f"Qualified: "
            f"{self.last_qualified}\n\n"

            f"Best candidate: "
            f"{self.last_best_symbol}\n"

            f"Best score: "
            f"{self.last_best_score}/100\n\n"

            "Scanner: ⚡ FAST + PROTECTED"
        )


    # ========================================================
    # RUN
    # ========================================================

    def run(self):

        self.telegram.send(

            "🤖 JARVIS FAST V4.1 ONLINE\n\n"

            "Real Solana market data\n"

            "Execution: PAPER ONLY\n\n"

            f"Starting balance: "
            f"${self.get_equity():,.2f}\n\n"

            "NEW PROTECTION ENGINE:\n"

            f"🛡 Hard stop: "
            f"-{CONFIG['stop_loss_pct']}%\n"

            f"🔒 Break-even arms: "
            f"+{CONFIG['break_even_activation_pct']}%\n"

            f"📈 Break-even lock: "
            f"+{CONFIG['break_even_lock_pct']}%\n"

            f"🔄 Trailing arms: "
            f"+{CONFIG['trailing_activation_pct']}%\n"

            f"📉 Trailing distance: "
            f"{CONFIG['trailing_stop_pct']}%\n"

            f"🎯 Take profit: "
            f"+{CONFIG['take_profit_pct']}%\n\n"

            "Positions checked every 2 seconds.\n\n"

            "Send /status anytime."
        )


        last_price_update = 0


        while True:

            try:

                self.reset_day_if_needed()


                self.telegram.handle_commands(
                    self
                )


                current = time.time()


                # ============================================
                # FAST POSITION MONITORING
                # ============================================

                if (

                    current
                    -
                    last_price_update

                    >=

                    CONFIG[
                        "price_update_seconds"
                    ]
                ):

                    self.update_positions()

                    last_price_update = (
                        current
                    )


                # ============================================
                # MARKET SCAN
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
                # AUTO STATUS
                # ============================================

                if (

                    current
                    -
                    self.last_status

                    >=

                    CONFIG[
                        "status_every_seconds"
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

                print(
                    "JARVIS V4.1 STOPPED"
                )

                break


            except Exception as error:

                print(
                    "BOT ERROR:",
                    error
                )


                self.telegram.send(

                    "⚠️ JARVIS V4.1 ERROR\n\n"

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
