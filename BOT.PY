import os
import time
import json
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# JARVIS X - FAST PROFIT HUNTER
# LIVE SOLANA MARKET DATA
# PAPER TRADING ONLY
# ============================================================

SYDNEY = ZoneInfo("Australia/Sydney")

DEX_BASE = "https://api.dexscreener.com"

# Separate state from old versions
STATE_FILE = "jarvis_x_state.json"


CONFIG = {

    # ========================================================
    # PAPER ACCOUNT
    # ========================================================

    "starting_balance": 1000.00,

    # Display goal only. Not guaranteed.
    "daily_profit_target": 500.00,


    # ========================================================
    # POSITION / RISK
    # ========================================================

    # Around $70 max position on a $1,000 account
    "max_position_pct": 7.0,

    "max_open_positions": 3,

    # Daily account protection
    "max_daily_loss_pct": 3.0,

    "max_daily_entries": 30,


    # ========================================================
    # REALISM
    # ========================================================

    # Simulated execution friction.
    # 0.35% entry + 0.35% exit slippage
    "slippage_pct": 0.35,

    # Simulated fee each side
    "fee_pct": 0.15,


    # ========================================================
    # EXITS
    # ========================================================

    "stop_loss_pct": 6.0,

    # Hard take profit is deliberately high because
    # trailing protection can capture runners.
    "take_profit_pct": 25.0,

    # Start protecting once up 6%
    "trailing_activation_pct": 6.0,

    # Exit after 3.5% retracement from high
    "trailing_stop_pct": 3.5,

    # If trade goes nowhere
    "weak_trade_minutes": 20,

    "weak_trade_exit_pct": -1.5,

    # Don't hold these speculative trades forever
    "max_hold_minutes": 75,


    # ========================================================
    # COOLDOWNS
    # ========================================================

    # Short cooldown after stop loss
    "stop_loss_cooldown_seconds": 5 * 60,

    # Same-token re-entry block
    "token_reentry_seconds": 30 * 60,


    # ========================================================
    # TOKEN FILTERS
    # ========================================================

    "min_liquidity": 30000,

    "min_volume_24h": 60000,

    "min_market_cap": 35000,

    "max_market_cap": 6000000,

    # Require positive momentum but avoid gigantic pumps
    "min_5m_change": 0.75,

    "max_5m_change": 16.0,

    "min_1h_change": -5.0,

    "max_1h_change": 70.0,

    "min_5m_buys": 5,

    "min_buy_pressure_pct": 53.0,

    "min_score": 60,

    # Avoid ultra-new pools
    "min_pair_age_minutes": 2,

    "max_pair_age_days": 30,


    # ========================================================
    # DISCOVERY
    # ========================================================

    "max_discovered_tokens": 120,

    "batch_size": 30,


    # ========================================================
    # SPEED
    # ========================================================

    "scan_every_seconds": 12,

    "price_update_seconds": 6,

    "status_every_seconds": 300,


    # ========================================================
    # TELEGRAM
    # ========================================================

    "candidate_alert_cooldown": 600,

    "paper_mode": True
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
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def http_json(url, method="GET", data=None, timeout=15):

    try:

        headers = {
            "User-Agent": "JarvisXPaperBot/1.0",
            "Accept": "application/json"
        }

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

        else:

            request = urllib.request.Request(
                url,
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

        else:

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
                "description": "Jarvis X dashboard"
            },

            {
                "command": "scan",
                "description": "Scanner diagnostics"
            },

            {
                "command": "live",
                "description": "Live positions"
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
                "description": "Strategy statistics"
            },

            {
                "command": "today",
                "description": "Today's result"
            },

            {
                "command": "pause",
                "description": "Pause entries"
            },

            {
                "command": "resume",
                "description": "Resume entries"
            },

            {
                "command": "paper",
                "description": "Show mode"
            },

            {
                "command": "help",
                "description": "Commands"
            }
        ])

        self.api(
            "setMyCommands",
            {
                "commands": commands
            }
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

            if not self.chat_id:
                self.chat_id = incoming_chat_id

            if incoming_chat_id != self.chat_id:
                continue

            command = (
                text.lower().split()[0]
                if text
                else ""
            )

            if command in ["/start", "start"]:

                self.send(

                    "⚡ JARVIS X ONLINE\n\n"

                    "FAST PROFIT HUNTER\n"
                    "Market data: LIVE\n"
                    "Execution: PAPER ONLY\n\n"

                    "⚡ 12-second scanning\n"
                    "⚡ Immediate qualifying entries\n"
                    "⚡ Up to 3 positions\n"
                    "⚡ Up to 30 entries/day\n"
                    "🛡 Daily loss protection\n"
                    "📊 Fees + slippage simulated\n\n"

                    "/status\n"
                    "/scan\n"
                    "/live\n"
                    "/positions\n"
                    "/trades\n"
                    "/stats\n"
                    "/today\n"
                    "/pause\n"
                    "/resume\n"
                    "/paper"
                )


            elif command == "/status":

                self.send(
                    bot.status_message()
                )


            elif command == "/scan":

                self.send(
                    bot.scan_message()
                )


            elif command == "/live":

                bot.update_positions()

                self.send(
                    bot.live_message()
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


            elif command == "/today":

                self.send(
                    bot.today_message()
                )


            elif command == "/pause":

                bot.entries_paused = True

                bot.save_state()

                self.send(

                    "⏸ JARVIS X PAUSED\n\n"
                    "No new positions.\n"
                    "Existing positions remain managed."
                )


            elif command == "/resume":

                bot.entries_paused = False

                bot.save_state()

                self.send(
                    "▶️ JARVIS X RESUMED"
                )


            elif command == "/paper":

                self.send(

                    "📝 JARVIS X MODE\n\n"

                    "Market data: LIVE\n"
                    "Trading: PAPER ONLY\n\n"

                    "No wallet connected.\n"
                    "No real funds are being traded."
                )


            elif command == "/help":

                self.send(

                    "⚡ JARVIS X COMMANDS\n\n"

                    "/status - Dashboard\n"
                    "/scan - Scanner activity\n"
                    "/live - Live P/L\n"
                    "/positions - Open trades\n"
                    "/trades - Recent trades\n"
                    "/stats - Full statistics\n"
                    "/today - Today's results\n"
                    "/pause - Stop new trades\n"
                    "/resume - Resume trades\n"
                    "/paper - Show mode"
                )


# ============================================================
# DEXSCREENER
# ============================================================

class DexScreener:

    def latest_profiles(self):

        result = http_json(
            DEX_BASE
            +
            "/token-profiles/latest/v1"
        )

        return (
            result
            if isinstance(result, list)
            else []
        )


    def latest_boosts(self):

        result = http_json(
            DEX_BASE
            +
            "/token-boosts/latest/v1"
        )

        return (
            result
            if isinstance(result, list)
            else []
        )


    def top_boosts(self):

        result = http_json(
            DEX_BASE
            +
            "/token-boosts/top/v1"
        )

        return (
            result
            if isinstance(result, list)
            else []
        )


    def community_takeovers(self):

        result = http_json(
            DEX_BASE
            +
            "/community-takeovers/latest/v1"
        )

        return (
            result
            if isinstance(result, list)
            else []
        )


    def discover_solana_addresses(self):

        sources = {

            "profile":
                self.latest_profiles(),

            "latest_boost":
                self.latest_boosts(),

            "top_boost":
                self.top_boosts(),

            "community":
                self.community_takeovers()
        }


        addresses = []

        seen = set()

        address_sources = {}

        source_counts = {
            "profile": 0,
            "latest_boost": 0,
            "top_boost": 0,
            "community": 0
        }


        max_length = max(
            [
                len(records)
                for records
                in sources.values()
            ]
            +
            [0]
        )


        # Round-robin discovery
        for i in range(max_length):

            for source_name, records in sources.items():

                if i >= len(records):
                    continue

                record = records[i]

                if record.get("chainId") != "solana":
                    continue

                address = record.get(
                    "tokenAddress"
                )

                if not address:
                    continue

                source_counts[
                    source_name
                ] += 1


                if address not in address_sources:

                    address_sources[
                        address
                    ] = []


                if (
                    source_name
                    not in
                    address_sources[address]
                ):

                    address_sources[
                        address
                    ].append(
                        source_name
                    )


                if address in seen:
                    continue


                seen.add(address)

                addresses.append(address)


                if (
                    len(addresses)
                    >=
                    CONFIG[
                        "max_discovered_tokens"
                    ]
                ):

                    return (
                        addresses,
                        source_counts,
                        address_sources
                    )


        return (
            addresses,
            source_counts,
            address_sources
        )


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


        return (
            result
            if isinstance(result, list)
            else []
        )


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


            pairs = self.token_pairs_batch(
                batch
            )


            all_pairs.extend(
                pairs
            )


            time.sleep(
                0.12
            )


        return all_pairs


    def best_pairs_by_token(
        self,
        pairs
    ):

        best = {}


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


            existing = best.get(
                address
            )


            if not existing:

                best[address] = pair

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

                best[address] = pair


        return best


    def best_pair(
        self,
        address
    ):

        pairs = self.token_pairs_batch(
            [address]
        )

        best = self.best_pairs_by_token(
            pairs
        )

        return best.get(
            address
        )


# ============================================================
# JARVIS X
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
        # TOTAL STATS
        # ====================================================

        self.total_profit = 0.0

        self.total_wins = 0

        self.total_losses = 0


        # ====================================================
        # DAILY
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

        self.last_stop_loss_time = 0


        # ====================================================
        # TOKEN MEMORY
        # ====================================================

        self.token_last_exit = {}

        self.candidate_alerts = {}


        # ====================================================
        # SCANNER STATS
        # ====================================================

        self.last_scan_time = None

        self.last_discovered = 0

        self.last_pairs = 0

        self.last_qualified = 0

        self.last_best_symbol = "NONE"

        self.last_best_score = 0


        self.last_source_counts = {
            "profile": 0,
            "latest_boost": 0,
            "top_boost": 0,
            "community": 0
        }


        self.last_rejections = {

            "liquidity": 0,
            "volume": 0,
            "market_cap": 0,
            "5m": 0,
            "1h": 0,
            "buys": 0,
            "pressure": 0,
            "age": 0,
            "score": 0
        }


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

                data = json.load(file)


            self.balance = safe_float(

                data.get(
                    "balance",
                    self.balance
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
                    "daily_profit",
                    0
                )
            )


            self.daily_entries = safe_int(

                data.get(
                    "daily_entries",
                    0
                )
            )


            self.day_key = data.get(
                "day_key",
                today_key()
            )


            self.day_start_equity = safe_float(

                data.get(
                    "day_start_equity",
                    CONFIG[
                        "starting_balance"
                    ]
                )
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


            self.candidate_alerts = data.get(
                "candidate_alerts",
                {}
            )


            print(
                "JARVIS X saved state loaded."
            )


        except Exception as error:

            print(
                "State load error:",
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
                    self.token_last_exit,

                "candidate_alerts":
                    self.candidate_alerts
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
                "Save error:",
                error
            )


    # ========================================================
    # STATS
    # ========================================================

    def rebuild_stats(self):

        self.total_profit = 0.0

        self.total_wins = 0

        self.total_losses = 0


        for trade in self.trade_history:

            profit = safe_float(
                trade.get(
                    "profit"
                )
            )


            self.total_profit += profit


            if profit > 0:

                self.total_wins += 1

            else:

                self.total_losses += 1


    # ========================================================
    # EQUITY
    # ========================================================

    def open_market_value(self):

        result = 0.0


        for position in self.positions.values():

            result += (

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


        return result


    def get_equity(self):

        return (
            self.balance
            +
            self.open_market_value()
        )


    def account_profit(self):

        return (
            self.get_equity()
            -
            CONFIG[
                "starting_balance"
            ]
        )


    def unrealized_profit(self):

        result = 0.0


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


            cost = safe_float(
                position.get(
                    "total_entry_cost"
                )
            )


            result += (
                current_value
                -
                cost
            )


        return result


    # ========================================================
    # NEW DAY
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


        self.save_state()


        self.telegram.send(

            "🌅 NEW JARVIS X DAY\n\n"

            f"Starting equity: "
            f"${self.day_start_equity:,.2f}\n\n"

            "Daily safety limits reset."
        )


    # ========================================================
    # TARGET
    # ========================================================

    def target_progress(self):

        target = CONFIG[
            "daily_profit_target"
        ]


        if target <= 0:

            return 0.0


        return max(

            0,

            min(

                self.daily_profit
                /
                target
                *
                100,

                100
            )
        )


    def daily_target_hit(self):

        return (
            self.daily_profit
            >=
            CONFIG[
                "daily_profit_target"
            ]
        )


    # ========================================================
    # DAILY DRAWDOWN
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


    def daily_loss_limit_hit(self):

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


        remaining = (

            CONFIG[
                "stop_loss_cooldown_seconds"
            ]

            -

            (
                time.time()

                -

                self.last_stop_loss_time
            )
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
                "token_reentry_seconds"
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
        pair,
        sources=None
    ):

        base = pair.get(
            "baseToken",
            {}
        )


        changes = pair.get(
            "priceChange",
            {}
        )


        txns = pair.get(
            "txns",
            {}
        )


        m5 = txns.get(
            "m5",
            {}
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


        total = (
            buys
            +
            sells
        )


        buy_pressure = (

            buys
            /
            total
            *
            100

            if total > 0

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

            "token_address":
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

            "volume24":
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

            "age_minutes":
                self.pair_age_minutes(
                    pair
                ),

            "sources":
                sources or []
        }


    # ========================================================
    # SCORE
    # ========================================================

    def score_token(
        self,
        token
    ):

        score = 0


        # ---------------- LIQUIDITY ----------------

        if token["liquidity"] >= 250000:

            score += 20

        elif token["liquidity"] >= 100000:

            score += 18

        elif token["liquidity"] >= 50000:

            score += 14

        elif token["liquidity"] >= 30000:

            score += 10


        # ---------------- VOLUME ----------------

        if token["volume24"] >= 1000000:

            score += 20

        elif token["volume24"] >= 500000:

            score += 18

        elif token["volume24"] >= 200000:

            score += 15

        elif token["volume24"] >= 60000:

            score += 10


        # ---------------- 5M MOMENTUM ----------------

        if 2.5 <= token["change5"] <= 7.5:

            score += 22

        elif 0.75 <= token["change5"] < 2.5:

            score += 14

        elif 7.5 < token["change5"] <= 11:

            score += 16

        elif 11 < token["change5"] <= 16:

            score += 7


        # ---------------- 1H MOMENTUM ----------------

        if 3 <= token["change1h"] <= 25:

            score += 15

        elif -5 <= token["change1h"] < 3:

            score += 8

        elif 25 < token["change1h"] <= 50:

            score += 10

        elif 50 < token["change1h"] <= 70:

            score += 4


        # ---------------- BUY PRESSURE ----------------

        if token["buy_pressure"] >= 72:

            score += 20

        elif token["buy_pressure"] >= 64:

            score += 17

        elif token["buy_pressure"] >= 58:

            score += 13

        elif token["buy_pressure"] >= 53:

            score += 8


        # ---------------- MARKET CAP ----------------

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
            3000000
        ):

            score += 7

        elif (
            3000000
            <
            token["market_cap"]
            <=
            6000000
        ):

            score += 4


        # ---------------- MULTI-SOURCE BONUS ----------------

        source_count = len(
            token.get(
                "sources",
                []
            )
        )


        if source_count >= 3:

            score += 4

        elif source_count == 2:

            score += 2


        return min(
            score,
            100
        )


    # ========================================================
    # FILTER
    # ========================================================

    def rejection_reason(
        self,
        token
    ):

        if (
            token["liquidity"]
            <
            CONFIG[
                "min_liquidity"
            ]
        ):

            return "liquidity"


        if (
            token["volume24"]
            <
            CONFIG[
                "min_volume_24h"
            ]
        ):

            return "volume"


        if (
            token["market_cap"]
            <
            CONFIG[
                "min_market_cap"
            ]

            or

            token["market_cap"]
            >
            CONFIG[
                "max_market_cap"
            ]
        ):

            return "market_cap"


        if (
            token["change5"]
            <
            CONFIG[
                "min_5m_change"
            ]

            or

            token["change5"]
            >
            CONFIG[
                "max_5m_change"
            ]
        ):

            return "5m"


        if (
            token["change1h"]
            <
            CONFIG[
                "min_1h_change"
            ]

            or

            token["change1h"]
            >
            CONFIG[
                "max_1h_change"
            ]
        ):

            return "1h"


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

            return "pressure"


        age = token[
            "age_minutes"
        ]


        if age is not None:

            if (
                age
                <
                CONFIG[
                    "min_pair_age_minutes"
                ]
            ):

                return "age"


            maximum_age = (

                CONFIG[
                    "max_pair_age_days"
                ]

                *

                24

                *

                60
            )


            if age > maximum_age:

                return "age"


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
    # MARKET SCAN
    # ========================================================

    def scan_market(self):

        (
            addresses,
            source_counts,
            address_sources

        ) = (
            self.dex
            .discover_solana_addresses()
        )


        self.last_discovered = (
            len(addresses)
        )


        self.last_source_counts = (
            source_counts
        )


        rejections = {

            "liquidity": 0,

            "volume": 0,

            "market_cap": 0,

            "5m": 0,

            "1h": 0,

            "buys": 0,

            "pressure": 0,

            "age": 0,

            "score": 0
        }


        if not addresses:

            self.last_pairs = 0

            self.last_qualified = 0

            self.last_rejections = (
                rejections
            )

            self.last_scan_time = (
                now_sydney()
            )

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


            token = self.pair_to_token(

                pair,

                sources=address_sources.get(
                    address,
                    []
                )
            )


            if token["price"] <= 0:
                continue


            reason = self.rejection_reason(
                token
            )


            if reason:

                if reason in rejections:

                    rejections[
                        reason
                    ] += 1

                continue


            candidates.append(
                token
            )


        candidates.sort(

            key=lambda item:
                item["score"],

            reverse=True
        )


        self.last_qualified = (
            len(candidates)
        )


        self.last_rejections = (
            rejections
        )


        self.last_scan_time = (
            now_sydney()
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


        print(

            "JARVIS X SCAN |",

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

        equity = self.get_equity()


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
    # ENTRY COST MODEL
    # ========================================================

    def simulated_buy_price(
        self,
        market_price
    ):

        return (

            market_price

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


    def simulated_sell_price(
        self,
        market_price
    ):

        return (

            market_price

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
    # CANDIDATE ALERT
    # ========================================================

    def candidate_alert(
        self,
        token
    ):

        address = token[
            "token_address"
        ]


        previous = safe_float(

            self.candidate_alerts.get(
                address,
                0
            )
        )


        if (
            time.time()
            -
            previous
            <
            CONFIG[
                "candidate_alert_cooldown"
            ]
        ):

            return


        self.candidate_alerts[
            address
        ] = time.time()


        self.telegram.send(

            "🔥 JARVIS X FOUND SETUP\n\n"

            f"{token['name']} "
            f"({token['symbol']})\n\n"

            f"Score: "
            f"{token['score']}/100\n"

            f"5m: "
            f"{token['change5']:+.2f}%\n"

            f"1h: "
            f"{token['change1h']:+.2f}%\n"

            f"Buy pressure: "
            f"{token['buy_pressure']:.1f}%\n\n"

            f"Buys 5m: "
            f"{token['buys5']}\n"

            f"Sells 5m: "
            f"{token['sells5']}\n\n"

            f"Liquidity: "
            f"${token['liquidity']:,.0f}\n"

            f"Volume: "
            f"${token['volume24']:,.0f}\n"

            f"Market cap: "
            f"${token['market_cap']:,.0f}"
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


        if self.daily_loss_limit_hit():
            return False


        if self.daily_target_hit():
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
            "token_address"
        ]


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


        execution_price = (
            self.simulated_buy_price(
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


        usable_amount = (
            amount
            -
            entry_fee
        )


        quantity = (

            usable_amount

            /

            execution_price
        )


        total_entry_cost = (
            amount
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

            "token_address":
                address,

            "market_entry_price":
                market_price,

            "entry_price":
                execution_price,

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

            "total_entry_cost":
                total_entry_cost,

            "score":
                token[
                    "score"
                ],

            "opened_at":
                now_sydney()
                .isoformat(),

            "opened_timestamp":
                time.time()
        }


        self.balance -= amount

        self.daily_entries += 1


        self.save_state()


        planned_stop = (

            amount

            *

            CONFIG[
                "stop_loss_pct"
            ]

            /

            100
        )


        self.telegram.send(

            "🚀 JARVIS X PAPER BUY\n\n"

            f"{token['name']} "
            f"({token['symbol']})\n\n"

            f"Market price: "
            f"${market_price:.10f}\n"

            f"Simulated fill: "
            f"${execution_price:.10f}\n"

            f"Position: "
            f"${amount:.2f}\n"

            f"Entry fee: "
            f"${entry_fee:.2f}\n\n"

            f"Score: "
            f"{token['score']}/100\n"

            f"Buy pressure: "
            f"{token['buy_pressure']:.1f}%\n\n"

            f"Hard stop: "
            f"-{CONFIG['stop_loss_pct']}%\n"

            f"Trailing activates: "
            f"+{CONFIG['trailing_activation_pct']}%\n"

            f"Hard TP: "
            f"+{CONFIG['take_profit_pct']}%\n\n"

            f"Approx planned risk: "
            f"${planned_stop:.2f}\n\n"

            "PAPER TRADING ONLY"
        )


        return True


    # ========================================================
    # UPDATE POSITIONS
    # ========================================================

    def update_positions(self):

        for address in list(
            self.positions.keys()
        ):

            if address not in self.positions:
                continue


            position = self.positions[
                address
            ]


            pair = self.dex.best_pair(
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


            position[
                "current_price"
            ] = market_price


            highest = safe_float(
                position.get(
                    "highest_price"
                )
            )


            if market_price > highest:

                highest = market_price

                position[
                    "highest_price"
                ] = highest


            entry_price = safe_float(
                position.get(
                    "entry_price"
                )
            )


            if entry_price <= 0:
                continue


            pnl_pct = (

                (
                    market_price
                    -
                    entry_price
                )

                /

                entry_price

                *

                100
            )


            highest_pct = (

                (
                    highest
                    -
                    entry_price
                )

                /

                entry_price

                *

                100
            )


            drop_from_high = (

                (
                    highest
                    -
                    market_price
                )

                /

                highest

                *

                100

                if highest > 0

                else 0
            )


            opened_timestamp = safe_float(

                position.get(
                    "opened_timestamp",
                    time.time()
                )
            )


            minutes_open = (

                time.time()

                -

                opened_timestamp

            ) / 60


            print(

                position[
                    "symbol"
                ],

                "|",

                f"{pnl_pct:+.2f}%",

                "| high",

                f"{highest_pct:+.2f}%",

                "|",

                f"{minutes_open:.1f}m"
            )


            # =================================================
            # STOP LOSS
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
            # HARD TAKE PROFIT
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


            # =================================================
            # TRAILING PROFIT
            # =================================================

            if (
                highest_pct
                >=
                CONFIG[
                    "trailing_activation_pct"
                ]

                and

                drop_from_high
                >=
                CONFIG[
                    "trailing_stop_pct"
                ]
            ):

                self.sell(
                    address,
                    "TRAILING PROFIT"
                )

                continue


            # =================================================
            # WEAK TRADE EXIT
            # =================================================

            if (
                minutes_open
                >=
                CONFIG[
                    "weak_trade_minutes"
                ]

                and

                pnl_pct
                <=
                CONFIG[
                    "weak_trade_exit_pct"
                ]
            ):

                self.sell(
                    address,
                    "WEAK TRADE"
                )

                continue


            # =================================================
            # TIME EXIT
            # =================================================

            if (
                minutes_open
                >=
                CONFIG[
                    "max_hold_minutes"
                ]
            ):

                self.sell(
                    address,
                    "TIME EXIT"
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


        position = self.positions[
            address
        ]


        market_exit_price = safe_float(
            position.get(
                "current_price"
            )
        )


        execution_price = (
            self.simulated_sell_price(
                market_exit_price
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

            execution_price
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


        total_entry_cost = safe_float(
            position.get(
                "total_entry_cost"
            )
        )


        profit = (

            net_value

            -

            total_entry_cost
        )


        pnl_pct = (

            profit

            /

            total_entry_cost

            *

            100

            if total_entry_cost > 0

            else 0
        )


        self.balance += net_value

        self.daily_profit += profit


        opened_timestamp = safe_float(
            position.get(
                "opened_timestamp",
                time.time()
            )
        )


        minutes_held = (

            time.time()

            -

            opened_timestamp

        ) / 60


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

            "minutes_held":
                minutes_held,

            "entry_fee":
                safe_float(
                    position.get(
                        "entry_fee"
                    )
                ),

            "exit_fee":
                exit_fee,

            "closed_at":
                now_sydney()
                .isoformat()
        })


        self.token_last_exit[
            address
        ] = time.time()


        if reason == "STOP LOSS":

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


        if reason == "STOP LOSS":

            cooldown_text = (

                "\n\n⏳ 5-MINUTE NEW-ENTRY COOLDOWN"

            )


        self.telegram.send(

            f"{icon} JARVIS X TRADE CLOSED\n\n"

            f"{position.get('name')} "
            f"({position.get('symbol')})\n\n"

            f"Reason: "
            f"{reason}\n"

            f"Return after simulated costs: "
            f"{pnl_pct:+.2f}%\n"

            f"Net trade P/L: "
            f"${profit:+.2f}\n"

            f"Held: "
            f"{minutes_held:.1f} minutes\n\n"

            f"Today's P/L: "
            f"${self.daily_profit:+.2f}\n"

            f"Account P/L: "
            f"${self.account_profit():+,.2f}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}"

            f"{cooldown_text}"
        )


    # ========================================================
    # ENTRY ENGINE
    # ========================================================

    def try_new_entries(self):

        if self.daily_loss_limit_hit():
            return


        if self.daily_target_hit():
            return


        if self.entries_paused:
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


        if not candidates:
            return


        self.candidate_alert(
            candidates[0]
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
                "token_address"
            ]


            if address in self.positions:
                continue


            if not self.token_reentry_allowed(
                address
            ):
                continue


            self.buy(
                token
            )


            time.sleep(
                0.25
            )


    # ========================================================
    # STATUS
    # ========================================================

    def status_message(self):

        total_trades = (

            self.total_wins

            +

            self.total_losses
        )


        win_rate = (

            self.total_wins

            /

            total_trades

            *

            100

            if total_trades > 0

            else 0
        )


        if self.daily_loss_limit_hit():

            status = "🛑 DAILY LOSS STOP"


        elif self.entries_paused:

            status = "⏸ PAUSED"


        elif self.stop_cooldown_active():

            status = (

                "⏳ COOLDOWN "
                f"{self.stop_cooldown_remaining() / 60:.1f} MIN"
            )


        elif self.daily_target_hit():

            status = "🎯 TARGET REACHED"


        elif (
            self.daily_entries
            >=
            CONFIG[
                "max_daily_entries"
            ]
        ):

            status = "🟡 ENTRY LIMIT"


        else:

            status = "🟢 ACTIVE"


        return (

            "⚡ JARVIS X STATUS\n\n"

            f"Starting balance: "
            f"${CONFIG['starting_balance']:,.2f}\n"

            f"Cash: "
            f"${self.balance:,.2f}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}\n\n"

            f"💰 Account P/L: "
            f"${self.account_profit():+,.2f}\n"

            f"Closed P/L: "
            f"${self.total_profit:+,.2f}\n"

            f"Open P/L: "
            f"${self.unrealized_profit():+,.2f}\n"

            f"Today's P/L: "
            f"${self.daily_profit:+,.2f}\n\n"

            f"🎯 Stretch target: "
            f"${CONFIG['daily_profit_target']:,.2f}\n"

            f"Progress: "
            f"{self.target_progress():.1f}%\n\n"

            f"Wins: "
            f"{self.total_wins}\n"

            f"Losses: "
            f"{self.total_losses}\n"

            f"Win rate: "
            f"{win_rate:.1f}%\n"

            f"Open trades: "
            f"{len(self.positions)}/"
            f"{CONFIG['max_open_positions']}\n"

            f"Today's entries: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n\n"

            "🔎 SCANNER\n"

            f"Discovered: "
            f"{self.last_discovered}\n"

            f"Pairs: "
            f"{self.last_pairs}\n"

            f"Qualified: "
            f"{self.last_qualified}\n"

            f"Best: "
            f"{self.last_best_symbol} "
            f"{self.last_best_score}/100\n\n"

            f"Daily drawdown: "
            f"{self.daily_loss_pct():.2f}%/"
            f"{CONFIG['max_daily_loss_pct']:.2f}%\n\n"

            f"Status: "
            f"{status}\n"

            "Market data: LIVE\n"

            "Mode: PAPER TRADING"
        )


    # ========================================================
    # SCAN DIAGNOSTIC
    # ========================================================

    def scan_message(self):

        scan_time = (

            self.last_scan_time
            .strftime(
                "%H:%M:%S"
            )

            if self.last_scan_time

            else "Not yet"
        )


        r = self.last_rejections


        return (

            "🔎 JARVIS X SCANNER\n\n"

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

            "REJECTED BY\n"

            f"Liquidity: "
            f"{r.get('liquidity', 0)}\n"

            f"Volume: "
            f"{r.get('volume', 0)}\n"

            f"Market cap: "
            f"{r.get('market_cap', 0)}\n"

            f"5m momentum: "
            f"{r.get('5m', 0)}\n"

            f"1h momentum: "
            f"{r.get('1h', 0)}\n"

            f"5m buys: "
            f"{r.get('buys', 0)}\n"

            f"Buy pressure: "
            f"{r.get('pressure', 0)}\n"

            f"Age: "
            f"{r.get('age', 0)}\n"

            f"Score: "
            f"{r.get('score', 0)}\n\n"

            "DISCOVERY\n"

            f"Profiles: "
            f"{self.last_source_counts.get('profile', 0)}\n"

            f"Latest boosts: "
            f"{self.last_source_counts.get('latest_boost', 0)}\n"

            f"Top boosts: "
            f"{self.last_source_counts.get('top_boost', 0)}\n"

            f"Community: "
            f"{self.last_source_counts.get('community', 0)}"
        )


    # ========================================================
    # LIVE
    # ========================================================

    def live_message(self):

        if not self.positions:

            return (

                "⚡ JARVIS X LIVE\n\n"

                "No open paper positions.\n\n"

                f"Equity: "
                f"${self.get_equity():,.2f}\n"

                f"Account P/L: "
                f"${self.account_profit():+,.2f}"
            )


        message = (

            "⚡ JARVIS X LIVE\n\n"
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


            high = safe_float(
                position.get(
                    "highest_price"
                )
            )


            pnl_pct = (

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


            market_value = (

                safe_float(
                    position.get(
                        "quantity"
                    )
                )

                *

                current
            )


            pnl_dollars = (

                market_value

                -

                safe_float(
                    position.get(
                        "total_entry_cost"
                    )
                )
            )


            message += (

                f"{position.get('symbol')}\n"

                f"P/L: "
                f"{pnl_pct:+.2f}%\n"

                f"P/L $: "
                f"${pnl_dollars:+.2f}\n"

                f"High: "
                f"{high_pct:+.2f}%\n"

                f"Size: "
                f"${safe_float(position.get('amount')):.2f}\n\n"
            )


        message += (

            f"Total open P/L: "
            f"${self.unrealized_profit():+,.2f}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}"
        )


        return message


    # ========================================================
    # POSITIONS
    # ========================================================

    def positions_message(self):

        if not self.positions:

            return (

                "📊 JARVIS X POSITIONS\n\n"

                "No open paper trades."
            )


        message = (

            "📊 JARVIS X POSITIONS\n\n"
        )


        for position in self.positions.values():

            message += (

                f"{position.get('symbol')}\n"

                f"Entry: "
                f"${safe_float(position.get('entry_price')):.10f}\n"

                f"Current: "
                f"${safe_float(position.get('current_price')):.10f}\n"

                f"Size: "
                f"${safe_float(position.get('amount')):.2f}\n"

                f"Score: "
                f"{safe_int(position.get('score'))}/100\n\n"
            )


        return message


    # ========================================================
    # RECENT TRADES
    # ========================================================

    def trades_message(self):

        if not self.trade_history:

            return (

                "📜 JARVIS X TRADES\n\n"

                "No closed trades yet."
            )


        message = (

            "📜 JARVIS X RECENT TRADES\n\n"
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

                f"${profit:+.2f}\n"

                f"{safe_float(trade.get('minutes_held')):.1f} min\n\n"
            )


        return message


    # ========================================================
    # MAX DRAWDOWN
    # ========================================================

    def calculate_max_drawdown(self):

        equity = CONFIG[
            "starting_balance"
        ]


        peak = equity

        maximum_drawdown = 0.0


        for trade in self.trade_history:

            equity += safe_float(
                trade.get(
                    "profit"
                )
            )


            if equity > peak:

                peak = equity


            if peak > 0:

                drawdown = (

                    (
                        peak
                        -
                        equity
                    )

                    /

                    peak

                    *

                    100
                )


                maximum_drawdown = max(
                    maximum_drawdown,
                    drawdown
                )


        return maximum_drawdown


    # ========================================================
    # STATS
    # ========================================================

    def stats_message(self):

        if not self.trade_history:

            return (

                "📈 JARVIS X STATISTICS\n\n"

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

            if total > 0

            else 0
        )


        gross_profit = sum(
            wins
        )


        gross_loss = abs(
            sum(
                losses
            )
        )


        average_win = (

            gross_profit

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
        )


        win_probability = (

            len(wins)

            /

            total
        )


        loss_probability = (

            len(losses)

            /

            total
        )


        expectancy = (

            win_probability
            *
            average_win

            +

            loss_probability
            *
            average_loss
        )


        if gross_loss > 0:

            profit_factor_text = (
                f"{gross_profit / gross_loss:.2f}"
            )

        elif gross_profit > 0:

            profit_factor_text = "∞"

        else:

            profit_factor_text = "0.00"


        max_drawdown = (
            self.calculate_max_drawdown()
        )


        return (

            "📈 JARVIS X STATISTICS\n\n"

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
            f"${average_loss:+,.2f}\n\n"

            f"Best trade: "
            f"${max(profits):+,.2f}\n"

            f"Worst trade: "
            f"${min(profits):+,.2f}\n\n"

            f"Profit factor: "
            f"{profit_factor_text}\n"

            f"Expectancy/trade: "
            f"${expectancy:+,.2f}\n"

            f"Max drawdown: "
            f"{max_drawdown:.2f}%\n\n"

            f"Current equity: "
            f"${self.get_equity():,.2f}\n"

            f"Account P/L: "
            f"${self.account_profit():+,.2f}"
        )


    # ========================================================
    # TODAY
    # ========================================================

    def today_message(self):

        remaining = max(

            0,

            CONFIG[
                "daily_profit_target"
            ]

            -

            self.daily_profit
        )


        return (

            "📅 JARVIS X TODAY\n\n"

            f"Net P/L: "
            f"${self.daily_profit:+,.2f}\n"

            f"Stretch target: "
            f"${CONFIG['daily_profit_target']:,.2f}\n"

            f"Progress: "
            f"{self.target_progress():.1f}%\n"

            f"Remaining: "
            f"${remaining:,.2f}\n\n"

            f"Entries: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n"

            f"Open trades: "
            f"{len(self.positions)}/"
            f"{CONFIG['max_open_positions']}\n\n"

            f"Daily drawdown: "
            f"{self.daily_loss_pct():.2f}%/"
            f"{CONFIG['max_daily_loss_pct']:.2f}%\n\n"

            f"Equity: "
            f"${self.get_equity():,.2f}"
        )


    # ========================================================
    # RUN
    # ========================================================

    def run(self):

        self.telegram.send(

            "⚡ JARVIS X - FAST PROFIT HUNTER\n\n"

            "ONLINE ✅\n\n"

            "Real Solana market data\n"

            "Execution: PAPER ONLY\n\n"

            f"Paper balance: "
            f"${self.get_equity():,.2f}\n\n"

            "⚡ Scan: every 12 sec\n"

            "⚡ Max positions: 3\n"

            "⚡ Max entries/day: 30\n"

            "📊 Simulated slippage + fees\n"

            "🛡 Hard stop + trailing profits\n"

            "🛡 Daily account-loss cutoff\n\n"

            "Send /scan first."
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
                # UPDATE OPEN POSITIONS
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
                # SCAN + TRADE
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
                    2
                )


            except KeyboardInterrupt:

                self.save_state()

                print(
                    "JARVIS X stopped."
                )

                break


            except Exception as error:

                print(
                    "BOT ERROR:",
                    error
                )


                self.telegram.send(

                    "⚠️ JARVIS X ERROR\n\n"

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
