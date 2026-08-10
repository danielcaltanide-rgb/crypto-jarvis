import os
import time
import json
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# JARVIS CLASSIC FAST
# BUILT TO RECREATE THE ACTIVE PAPER-TRADING STYLE
# ============================================================

SYDNEY = ZoneInfo("Australia/Sydney")

DEX_BASE = "https://api.dexscreener.com"

# New clean state for this version
STATE_FILE = "jarvis_classic_state.json"


CONFIG = {

    # --------------------------------------------------------
    # PAPER ACCOUNT
    # --------------------------------------------------------

    "starting_balance": 1000.00,

    "daily_profit_target": 500.00,

    # --------------------------------------------------------
    # ACTIVE POSITION SETTINGS
    # --------------------------------------------------------

    "max_position_pct": 10.0,

    "max_open_positions": 3,

    "max_daily_entries": 30,

    # --------------------------------------------------------
    # EXITS - CLASSIC SETTINGS
    # --------------------------------------------------------

    "stop_loss_pct": 8.0,

    "take_profit_pct": 18.0,

    "trailing_activation_pct": 8.0,

    "trailing_stop_pct": 6.0,

    # --------------------------------------------------------
    # DAILY SAFETY
    # --------------------------------------------------------

    "max_daily_loss_pct": 5.0,

    # --------------------------------------------------------
    # TOKEN FILTERS
    # --------------------------------------------------------

    "min_liquidity": 25000,

    "min_volume_24h": 50000,

    "min_market_cap": 40000,

    "max_market_cap": 5000000,

    "min_5m_change": 1.5,

    "max_5m_change": 35.0,

    "min_1h_change": -5.0,

    "max_1h_change": 120.0,

    "min_5m_buys": 5,

    "min_buy_pressure_pct": 50.0,

    "min_score": 62,

    "min_pair_age_minutes": 3,

    "max_pair_age_days": 30,

    # --------------------------------------------------------
    # DISCOVERY / SPEED
    # --------------------------------------------------------

    "max_discovered_tokens": 90,

    "batch_size": 30,

    "scan_every_seconds": 15,

    "price_update_seconds": 8,

    "status_every_seconds": 300,

    # --------------------------------------------------------
    # MODE
    # --------------------------------------------------------

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


def http_json(
    url,
    method="GET",
    data=None,
    timeout=15
):

    try:

        headers = {
            "User-Agent": "JarvisClassicPaperBot/1.0",
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
                response.read().decode("utf-8")
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
                "description": "Today's P/L"
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
                "command": "scan",
                "description": "Scanner activity"
            },

            {
                "command": "pause",
                "description": "Pause new trades"
            },

            {
                "command": "resume",
                "description": "Resume trading"
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

            if not self.chat_id:
                self.chat_id = incoming_chat_id

            if incoming_chat_id != self.chat_id:
                continue

            command = (
                text.lower().split()[0]
                if text
                else ""
            )


            if command in [
                "/start",
                "start"
            ]:

                self.send(

                    "🤖 JARVIS CLASSIC FAST ONLINE\n\n"

                    "Market data: LIVE\n"
                    "Mode: PAPER TRADING\n\n"

                    "Fast scanner enabled.\n"
                    "Maximum 3 open trades.\n"
                    "Maximum 30 entries/day.\n\n"

                    "/status\n"
                    "/today\n"
                    "/positions\n"
                    "/trades\n"
                    "/scan\n"
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


            elif command == "/scan":

                self.send(
                    bot.scan_message()
                )


            elif command == "/pause":

                bot.entries_paused = True

                bot.save_state()

                self.send(
                    "⏸ JARVIS PAUSED\n\n"
                    "No new positions will open."
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

                    "REAL MARKET DATA\n"
                    "PAPER TRADING ONLY\n\n"

                    "No wallet connected.\n"
                    "No real funds are being traded."
                )


            elif command == "/help":

                self.send(

                    "🤖 JARVIS COMMANDS\n\n"

                    "/status\n"
                    "/today\n"
                    "/positions\n"
                    "/trades\n"
                    "/scan\n"
                    "/pause\n"
                    "/resume\n"
                    "/paper"
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


        # Round robin so one feed does not dominate
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


                seen.add(address)

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
    # BATCH PAIRS
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
                0.15
            )


        return all_pairs


    def best_pairs_by_token(
        self,
        pairs
    ):

        result = {}


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


            current = result.get(
                address
            )


            if current is None:

                result[address] = pair

                continue


            old_liquidity = safe_float(

                current.get(
                    "liquidity",
                    {}
                ).get(
                    "usd"
                )
            )


            if liquidity > old_liquidity:

                result[address] = pair


        return result


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
# JARVIS
# ============================================================

class JarvisBot:

    def __init__(self):

        self.dex = DexScreener()

        self.telegram = TelegramManager()


        self.balance = CONFIG[
            "starting_balance"
        ]


        self.positions = {}

        self.trade_history = []


        self.total_wins = 0

        self.total_losses = 0


        self.daily_profit = 0.0

        self.daily_entries = 0


        self.day_key = today_key()

        self.day_start_equity = CONFIG[
            "starting_balance"
        ]


        self.entries_paused = False


        self.last_scan = 0

        self.last_status = 0


        self.last_discovered = 0

        self.last_pairs = 0

        self.last_qualified = 0

        self.last_best_symbol = "NONE"

        self.last_best_score = 0

        self.last_scan_time = None


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


            print(
                "Jarvis saved state loaded."
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
                    self.entries_paused
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
    # REBUILD WINS / LOSSES
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


            if profit >= 0:

                self.total_wins += 1

            else:

                self.total_losses += 1


    # ========================================================
    # EQUITY
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


        self.save_state()


        self.telegram.send(

            "🌅 NEW JARVIS TRADING DAY\n\n"

            f"Starting equity: "
            f"${self.day_start_equity:,.2f}\n"

            f"Maximum entries: "
            f"{CONFIG['max_daily_entries']}"
        )


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


        change = pair.get(
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


        total = buys + sells


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
                    change.get(
                        "m5"
                    )
                ),

            "change1h":
                safe_float(
                    change.get(
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

        elif token["liquidity"] >= 50000:
            score += 14

        elif token["liquidity"] >= 25000:
            score += 10


        # VOLUME

        if token["volume"] >= 1000000:
            score += 20

        elif token["volume"] >= 500000:
            score += 18

        elif token["volume"] >= 150000:
            score += 14

        elif token["volume"] >= 50000:
            score += 10


        # 5M MOMENTUM

        if 4 <= token["change5"] <= 15:
            score += 20

        elif 1.5 <= token["change5"] < 4:
            score += 14

        elif 15 < token["change5"] <= 30:
            score += 10


        # 1H MOMENTUM

        if 5 <= token["change1h"] <= 40:
            score += 15

        elif -5 <= token["change1h"] < 5:
            score += 8


        # BUY PRESSURE

        if token["buy_pressure"] >= 70:
            score += 20

        elif token["buy_pressure"] >= 60:
            score += 15

        elif token["buy_pressure"] >= 50:
            score += 8


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
            5000000
        ):

            score += 6


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
            CONFIG[
                "min_liquidity"
            ]
        ):
            return False


        if (
            token["volume"]
            <
            CONFIG[
                "min_volume_24h"
            ]
        ):
            return False


        if (
            token["market_cap"]
            <
            CONFIG[
                "min_market_cap"
            ]
        ):
            return False


        if (
            token["market_cap"]
            >
            CONFIG[
                "max_market_cap"
            ]
        ):
            return False


        if (
            token["change5"]
            <
            CONFIG[
                "min_5m_change"
            ]
        ):
            return False


        if (
            token["change5"]
            >
            CONFIG[
                "max_5m_change"
            ]
        ):
            return False


        if (
            token["change1h"]
            <
            CONFIG[
                "min_1h_change"
            ]
        ):
            return False


        if (
            token["change1h"]
            >
            CONFIG[
                "max_1h_change"
            ]
        ):
            return False


        if (
            token["buys5"]
            <
            CONFIG[
                "min_5m_buys"
            ]
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
                return False


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
                return False


        token["score"] = (
            self.score_token(
                token
            )
        )


        return (

            token["score"]

            >=

            CONFIG[
                "min_score"
            ]
        )


    # ========================================================
    # SCAN
    # ========================================================

    def scan_market(self):

        addresses = (
            self.dex
            .discover_addresses()
        )


        self.last_discovered = len(
            addresses
        )


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

            key=lambda x:
                x["score"],

            reverse=True
        )


        self.last_qualified = len(
            candidates
        )


        self.last_scan_time = (
            now_sydney()
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

            "SCAN",

            "| discovered",
            self.last_discovered,

            "| pairs",
            self.last_pairs,

            "| qualified",
            self.last_qualified
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


        return min(
            maximum,
            self.balance
        )


    # ========================================================
    # BUY
    # ========================================================

    def buy(
        self,
        token
    ):

        # IMPORTANT:
        # The entry limit is checked again HERE.
        # This fixes the old 32/30 bug.

        if (
            self.daily_entries
            >=
            CONFIG[
                "max_daily_entries"
            ]
        ):

            return False


        if self.entries_paused:
            return False


        if self.daily_loss_hit():
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


        if address in self.positions:
            return False


        amount = (
            self.position_size()
        )


        if amount <= 0:
            return False


        price = token[
            "price"
        ]


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
                token[
                    "symbol"
                ],

            "name":
                token[
                    "name"
                ],

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
                token[
                    "score"
                ],

            "opened_at":
                now_sydney()
                .isoformat()
        }


        self.balance -= amount

        self.daily_entries += 1


        self.save_state()


        self.telegram.send(

            "🟢 PAPER SNIPE OPENED\n\n"

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

            f"Target: "
            f"+{CONFIG['take_profit_pct']}%\n"

            f"Stop: "
            f"-{CONFIG['stop_loss_pct']}%\n\n"

            f"Trades today: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}"
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


            price = safe_float(
                pair.get(
                    "priceUsd"
                )
            )


            if price <= 0:
                continue


            position[
                "current_price"
            ] = price


            if (
                price
                >
                position[
                    "highest_price"
                ]
            ):

                position[
                    "highest_price"
                ] = price


            entry = position[
                "entry_price"
            ]


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


            highest = position[
                "highest_price"
            ]


            highest_profit = (

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


            drop_from_high = (

                (
                    highest
                    -
                    price
                )

                /

                highest

                *

                100

                if highest > 0

                else 0
            )


            print(

                position[
                    "symbol"
                ],

                f"{pnl_pct:+.2f}%"
            )


            # STOP LOSS

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


            # TAKE PROFIT

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


            # TRAILING STOP

            if (

                highest_profit
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
                    "TRAILING STOP"
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


        exit_price = position[
            "current_price"
        ]


        value = (

            position[
                "quantity"
            ]

            *

            exit_price
        )


        profit = (

            value

            -

            position[
                "amount"
            ]
        )


        pnl_pct = (

            profit

            /

            position[
                "amount"
            ]

            *

            100
        )


        self.balance += value

        self.daily_profit += profit


        self.trade_history.append({

            "symbol":
                position[
                    "symbol"
                ],

            "profit":
                profit,

            "pnl_pct":
                pnl_pct,

            "reason":
                reason,

            "closed_at":
                now_sydney()
                .isoformat()
        })


        del self.positions[
            address
        ]


        self.rebuild_stats()

        self.save_state()


        icon = (
            "🟢"
            if profit >= 0
            else "🔴"
        )


        self.telegram.send(

            f"{icon} PAPER TRADE CLOSED\n\n"

            f"{position['name']} "
            f"({position['symbol']})\n\n"

            f"Reason: "
            f"{reason}\n"

            f"Return: "
            f"{pnl_pct:+.2f}%\n"

            f"P/L: "
            f"${profit:+.2f}\n\n"

            f"Today's P/L: "
            f"${self.daily_profit:+.2f}\n"

            f"Total P/L: "
            f"${self.total_profit():+,.2f}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}"
        )


    # ========================================================
    # FAST ENTRY ENGINE
    # ========================================================

    def try_new_entries(self):

        if self.entries_paused:
            return


        if self.daily_loss_hit():
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


        if not candidates:
            return


        # Fill available slots with strongest candidates.

        for token in candidates:

            if (
                len(self.positions)
                >=
                CONFIG[
                    "max_open_positions"
                ]
            ):

                break


            # Check limit on EVERY iteration.
            # Prevents 31/30 or 32/30.

            if (
                self.daily_entries
                >=
                CONFIG[
                    "max_daily_entries"
                ]
            ):

                break


            if (
                token[
                    "address"
                ]
                in
                self.positions
            ):

                continue


            opened = self.buy(
                token
            )


            if opened:

                time.sleep(
                    0.25
                )


    # ========================================================
    # STATUS - MATCHES THE SCREENSHOT STYLE
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

            else 0
        )


        return (

            "🤖 JARVIS STATUS\n\n"

            f"Cash: "
            f"${self.balance:,.2f}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}\n"

            f"Total P/L: "
            f"${self.total_profit():+,.2f}\n"

            f"Today's P/L: "
            f"${self.daily_profit:+,.2f}\n\n"

            f"Wins: "
            f"{self.total_wins}\n"

            f"Losses: "
            f"{self.total_losses}\n"

            f"Win rate: "
            f"{win_rate:.1f}%\n"

            f"Open trades: "
            f"{len(self.positions)}\n"

            f"Today's trades: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n\n"

            "Mode: PAPER TRADING"
        )


    # ========================================================
    # TODAY
    # ========================================================

    def today_message(self):

        return (

            "📅 TODAY\n\n"

            f"P/L: "
            f"${self.daily_profit:+,.2f}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}\n"

            f"Trades: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n\n"

            f"Daily loss used: "
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

            entry = position[
                "entry_price"
            ]


            current = position[
                "current_price"
            ]


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
            )


            message += (

                f"{position['symbol']}\n"

                f"Entry: "
                f"${entry:.10f}\n"

                f"Current: "
                f"${current:.10f}\n"

                f"P/L: "
                f"{pnl:+.2f}%\n"

                f"Size: "
                f"${position['amount']:.2f}\n\n"
            )


        return message


    # ========================================================
    # TRADES
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
                if profit >= 0
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
    # SCAN
    # ========================================================

    def scan_message(self):

        scan_time = (

            self.last_scan_time.strftime(
                "%H:%M:%S"
            )

            if self.last_scan_time

            else "Waiting for first scan"
        )


        return (

            "🔎 JARVIS SCANNER\n\n"

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

            "Scanner mode: FAST"
        )


    # ========================================================
    # RUN
    # ========================================================

    def run(self):

        self.telegram.send(

            "🤖 JARVIS CLASSIC FAST ONLINE\n\n"

            "Real Solana market data\n"

            "PAPER TRADING ONLY\n\n"

            f"Starting equity: "
            f"${self.get_equity():,.2f}\n"

            f"Max positions: "
            f"{CONFIG['max_open_positions']}\n"

            f"Max trades/day: "
            f"{CONFIG['max_daily_entries']}\n"

            f"Stop: "
            f"-{CONFIG['stop_loss_pct']}%\n"

            f"Take profit: "
            f"+{CONFIG['take_profit_pct']}%\n\n"

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


                # UPDATE OPEN TRADES

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


                # FAST MARKET SCAN

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


                # AUTO STATUS

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
                    "Jarvis stopped."
                )

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
