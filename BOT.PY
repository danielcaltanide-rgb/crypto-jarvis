import os
import time
import random
import json
import urllib.parse
import urllib.request
from datetime import datetime


# ============================================================
# JARVIS CRYPTO PAPER TRADING BOT + TELEGRAM
# ============================================================

CONFIG = {

    # Starting paper balance
    "starting_balance": 1000.00,

    # Risk controls
    "risk_per_trade_pct": 1.0,
    "max_position_pct": 10.0,
    "max_open_trades": 3,

    # Exit rules
    "stop_loss_pct": 8.0,
    "take_profit_pct": 18.0,
    "trailing_stop_pct": 7.0,

    # Scanner filters
    "min_liquidity": 20_000,
    "min_volume_24h": 30_000,
    "min_market_cap": 30_000,
    "max_market_cap": 5_000_000,

    # Momentum
    "min_price_change_5m": 2.0,
    "min_score": 65,

    # Safety
    "max_daily_loss_pct": 5.0,

    # Bot loop
    "cooldown_seconds": 10,

    # Send Telegram dashboard every X cycles
    "telegram_dashboard_every": 6,

    # PAPER MODE
    "paper_mode": True
}


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

        self.update_offset = None

        if not self.token:
            print(
                "WARNING: TELEGRAM_BOT_TOKEN "
                "not found."
            )
        else:
            print("Telegram token loaded.")

    def api_request(self, method, data=None):

        if not self.token:
            return None

        url = (
            f"https://api.telegram.org/"
            f"bot{self.token}/{method}"
        )

        try:

            if data is not None:

                encoded = urllib.parse.urlencode(
                    data
                ).encode("utf-8")

                request = urllib.request.Request(
                    url,
                    data=encoded
                )

            else:

                request = urllib.request.Request(
                    url
                )

            with urllib.request.urlopen(
                request,
                timeout=15
            ) as response:

                content = response.read().decode(
                    "utf-8"
                )

                return json.loads(content)

        except Exception as error:

            print(
                "Telegram error:",
                error
            )

            return None

    def send(self, message):

        if not self.token:
            return

        if not self.chat_id:
            print(
                "Telegram waiting for chat."
            )
            return

        self.api_request(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": message
            }
        )

    def get_updates(self):

        if not self.token:
            return []

        data = {}

        if self.update_offset is not None:
            data["offset"] = self.update_offset

        result = self.api_request(
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

            self.update_offset = (
                updates[-1]["update_id"] + 1
            )

        return updates

    def check_messages(self, bot):

        updates = self.get_updates()

        for update in updates:

            message = update.get(
                "message",
                {}
            )

            chat = message.get(
                "chat",
                {}
            )

            incoming_chat_id = str(
                chat.get("id", "")
            )

            text = message.get(
                "text",
                ""
            ).strip()

            # Automatically connect first chat
            if (
                not self.chat_id
                and incoming_chat_id
            ):

                self.chat_id = incoming_chat_id

                print(
                    "Telegram connected:",
                    self.chat_id
                )

                self.send(
                    "🤖 JARVIS CONNECTED\n\n"
                    "Telegram connection successful.\n"
                    "Paper trading mode is ACTIVE.\n\n"
                    "Commands:\n"
                    "/status\n"
                    "/positions\n"
                    "/trades\n"
                    "/help"
                )

            # Ignore messages from other chats
            if (
                self.chat_id
                and incoming_chat_id
                != self.chat_id
            ):
                continue

            command = text.lower()

            if command in [
                "/start",
                "start"
            ]:

                self.send(
                    "🤖 JARVIS CRYPTO BOT\n\n"
                    "Connected successfully.\n"
                    "Mode: PAPER TRADING\n\n"
                    "Commands:\n"
                    "/status - account dashboard\n"
                    "/positions - open trades\n"
                    "/trades - recent trades\n"
                    "/help - commands"
                )

            elif command == "/status":

                self.send(
                    bot.telegram_dashboard()
                )

            elif command == "/positions":

                self.send(
                    bot.telegram_positions()
                )

            elif command == "/trades":

                self.send(
                    bot.telegram_trades()
                )

            elif command == "/help":

                self.send(
                    "🤖 JARVIS COMMANDS\n\n"
                    "/status\n"
                    "Show balance and P/L\n\n"
                    "/positions\n"
                    "Show open trades\n\n"
                    "/trades\n"
                    "Show recent closed trades\n\n"
                    "/help\n"
                    "Show this menu"
                )


# ============================================================
# JARVIS BOT
# ============================================================

class JarvisBot:

    def __init__(self):

        self.balance = CONFIG[
            "starting_balance"
        ]

        self.starting_day_balance = (
            self.balance
        )

        self.positions = {}

        self.trade_history = []

        self.total_profit = 0.0

        self.total_wins = 0
        self.total_losses = 0

        self.cycle = 0

        self.telegram = TelegramManager()

        print("=" * 55)
        print("JARVIS CRYPTO PAPER TRADING BOT")
        print("=" * 55)
        print("MODE: PAPER TRADING")
        print(
            f"STARTING BALANCE: "
            f"${self.balance:,.2f}"
        )
        print("=" * 55)


    # ========================================================
    # SCANNER
    # ========================================================

    def scan_market(self):

        # SIMULATED market data.
        # This is NOT yet live Solana data.

        symbols = [
            "BONK",
            "WIF",
            "POPCAT",
            "MEW",
            "MOON",
            "PEPE2",
            "DOGGO",
            "CAT",
            "MEME",
            "ROCKET"
        ]

        opportunities = []

        for symbol in symbols:

            token = {

                "symbol":
                    symbol,

                "price":
                    random.uniform(
                        0.00001,
                        0.05
                    ),

                "liquidity":
                    random.uniform(
                        5_000,
                        500_000
                    ),

                "volume":
                    random.uniform(
                        5_000,
                        1_000_000
                    ),

                "market_cap":
                    random.uniform(
                        10_000,
                        8_000_000
                    ),

                "change_5m":
                    random.uniform(
                        -10,
                        20
                    ),

                "change_1h":
                    random.uniform(
                        -20,
                        50
                    ),

                "buys":
                    random.randint(
                        10,
                        500
                    ),

                "sells":
                    random.randint(
                        5,
                        400
                    )
            }

            token["score"] = (
                self.calculate_score(
                    token
                )
            )

            if self.valid_token(token):

                opportunities.append(
                    token
                )

        opportunities.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        return opportunities


    # ========================================================
    # SCORE
    # ========================================================

    def calculate_score(
        self,
        token
    ):

        score = 0

        liquidity = token[
            "liquidity"
        ]

        volume = token[
            "volume"
        ]

        change_5m = token[
            "change_5m"
        ]

        change_1h = token[
            "change_1h"
        ]

        market_cap = token[
            "market_cap"
        ]

        buys = token[
            "buys"
        ]

        sells = token[
            "sells"
        ]


        # Liquidity score

        if liquidity > 100_000:
            score += 20

        elif liquidity > 50_000:
            score += 15

        elif liquidity > 20_000:
            score += 10


        # Volume score

        if volume > 500_000:
            score += 20

        elif volume > 100_000:
            score += 15

        elif volume > 30_000:
            score += 10


        # 5 minute momentum

        if change_5m > 10:
            score += 20

        elif change_5m > 5:
            score += 15

        elif change_5m > 2:
            score += 10


        # 1 hour momentum

        if change_1h > 20:
            score += 15

        elif change_1h > 5:
            score += 10


        # Buy pressure

        if buys > sells * 1.5:
            score += 20

        elif buys > sells:
            score += 10


        # Market cap

        if (
            50_000
            <= market_cap
            <= 1_000_000
        ):

            score += 10

        elif market_cap <= 5_000_000:

            score += 5


        return min(
            score,
            100
        )


    # ========================================================
    # TOKEN FILTER
    # ========================================================

    def valid_token(
        self,
        token
    ):

        if (
            token["liquidity"]
            < CONFIG[
                "min_liquidity"
            ]
        ):
            return False

        if (
            token["volume"]
            < CONFIG[
                "min_volume_24h"
            ]
        ):
            return False

        if (
            token["market_cap"]
            < CONFIG[
                "min_market_cap"
            ]
        ):
            return False

        if (
            token["market_cap"]
            > CONFIG[
                "max_market_cap"
            ]
        ):
            return False

        if (
            token["change_5m"]
            < CONFIG[
                "min_price_change_5m"
            ]
        ):
            return False

        if (
            token["score"]
            < CONFIG[
                "min_score"
            ]
        ):
            return False

        return True


    # ========================================================
    # POSITION SIZE
    # ========================================================

    def calculate_position_size(
        self
    ):

        risk_amount = (
            self.get_equity()
            *
            CONFIG[
                "risk_per_trade_pct"
            ]
            / 100
        )

        stop_fraction = (
            CONFIG[
                "stop_loss_pct"
            ]
            / 100
        )

        position_size = (
            risk_amount
            / stop_fraction
        )

        maximum_position = (
            self.get_equity()
            *
            CONFIG[
                "max_position_pct"
            ]
            / 100
        )

        position_size = min(
            position_size,
            maximum_position,
            self.balance
        )

        return max(
            position_size,
            0
        )


    # ========================================================
    # BUY
    # ========================================================

    def buy(
        self,
        token
    ):

        symbol = token[
            "symbol"
        ]

        if symbol in self.positions:
            return

        if (
            len(self.positions)
            >= CONFIG[
                "max_open_trades"
            ]
        ):
            return

        amount = (
            self.calculate_position_size()
        )

        if amount <= 0:
            return

        price = token[
            "price"
        ]

        quantity = (
            amount / price
        )

        position = {

            "symbol":
                symbol,

            "entry_price":
                price,

            "current_price":
                price,

            "quantity":
                quantity,

            "amount":
                amount,

            "highest_price":
                price,

            "score":
                token["score"],

            "opened":
                datetime.now().isoformat()
        }

        self.balance -= amount

        self.positions[
            symbol
        ] = position


        print()
        print("PAPER BUY")
        print(
            f"Token: {symbol}"
        )
        print(
            f"Score: "
            f"{token['score']}/100"
        )
        print(
            f"Entry: "
            f"${price:.8f}"
        )
        print(
            f"Position: "
            f"${amount:.2f}"
        )


        # TELEGRAM BUY MESSAGE

        self.telegram.send(

            "🚀 JARVIS PAPER BUY\n\n"

            f"Coin: {symbol}\n"

            f"Score: "
            f"{token['score']}/100\n"

            f"Entry: "
            f"${price:.8f}\n"

            f"Position: "
            f"${amount:.2f}\n"

            f"5m momentum: "
            f"{token['change_5m']:+.2f}%\n"

            f"Liquidity: "
            f"${token['liquidity']:,.0f}\n"

            f"Volume: "
            f"${token['volume']:,.0f}\n\n"

            f"Take profit: "
            f"+{CONFIG['take_profit_pct']}%\n"

            f"Stop loss: "
            f"-{CONFIG['stop_loss_pct']}%\n\n"

            "Mode: PAPER"
        )


    # ========================================================
    # UPDATE POSITIONS
    # ========================================================

    def update_positions(
        self
    ):

        for symbol in list(
            self.positions.keys()
        ):

            position = (
                self.positions[
                    symbol
                ]
            )

            movement = (
                random.uniform(
                    -0.08,
                    0.10
                )
            )

            new_price = (
                position[
                    "current_price"
                ]
                *
                (1 + movement)
            )

            position[
                "current_price"
            ] = new_price


            if (
                new_price
                >
                position[
                    "highest_price"
                ]
            ):

                position[
                    "highest_price"
                ] = new_price


            entry = position[
                "entry_price"
            ]

            pnl_pct = (
                (
                    new_price
                    -
                    entry
                )
                /
                entry
                *
                100
            )


            trailing_drop = (
                (
                    position[
                        "highest_price"
                    ]
                    -
                    new_price
                )
                /
                position[
                    "highest_price"
                ]
                *
                100
            )


            print(
                f"{symbol} | "
                f"PnL "
                f"{pnl_pct:+.2f}% | "
                f"${new_price:.8f}"
            )


            # TAKE PROFIT

            if (
                pnl_pct
                >=
                CONFIG[
                    "take_profit_pct"
                ]
            ):

                self.sell(
                    symbol,
                    "TAKE PROFIT"
                )

                continue


            # STOP LOSS

            if (
                pnl_pct
                <=
                -CONFIG[
                    "stop_loss_pct"
                ]
            ):

                self.sell(
                    symbol,
                    "STOP LOSS"
                )

                continue


            # TRAILING STOP

            if (
                pnl_pct > 5
                and
                trailing_drop
                >=
                CONFIG[
                    "trailing_stop_pct"
                ]
            ):

                self.sell(
                    symbol,
                    "TRAILING STOP"
                )


    # ========================================================
    # SELL
    # ========================================================

    def sell(
        self,
        symbol,
        reason
    ):

        position = (
            self.positions[
                symbol
            ]
        )

        exit_price = (
            position[
                "current_price"
            ]
        )

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

        self.total_profit += (
            profit
        )


        if profit >= 0:

            self.total_wins += 1

            icon = "💰"

        else:

            self.total_losses += 1

            icon = "🔻"


        trade = {

            "symbol":
                symbol,

            "entry":
                position[
                    "entry_price"
                ],

            "exit":
                exit_price,

            "profit":
                profit,

            "pnl_pct":
                pnl_pct,

            "reason":
                reason,

            "closed":
                datetime.now().isoformat()
        }


        self.trade_history.append(
            trade
        )

        del self.positions[
            symbol
        ]


        print()
        print("PAPER SELL")
        print(
            f"Token: {symbol}"
        )
        print(
            f"Reason: {reason}"
        )
        print(
            f"PnL: "
            f"{pnl_pct:+.2f}%"
        )
        print(
            f"Profit: "
            f"${profit:+.2f}"
        )
        print()


        self.telegram.send(

            f"{icon} JARVIS PAPER SELL\n\n"

            f"Coin: {symbol}\n"

            f"Reason: {reason}\n"

            f"Entry: "
            f"${position['entry_price']:.8f}\n"

            f"Exit: "
            f"${exit_price:.8f}\n\n"

            f"P/L: "
            f"{pnl_pct:+.2f}%\n"

            f"Profit: "
            f"${profit:+.2f}\n\n"

            f"Account equity: "
            f"${self.get_equity():,.2f}"
        )


        self.save_results()


    # ========================================================
    # ACCOUNT EQUITY
    # ========================================================

    def get_equity(
        self
    ):

        position_value = 0.0

        for position in (
            self.positions.values()
        ):

            position_value += (
                position[
                    "quantity"
                ]
                *
                position[
                    "current_price"
                ]
            )

        return (
            self.balance
            +
            position_value
        )


    # ========================================================
    # DAILY LOSS
    # ========================================================

    def daily_loss_limit_hit(
        self
    ):

        equity = (
            self.get_equity()
        )

        loss_pct = (
            (
                self.starting_day_balance
                -
                equity
            )
            /
            self.starting_day_balance
            *
            100
        )

        return (
            loss_pct
            >=
            CONFIG[
                "max_daily_loss_pct"
            ]
        )


    # ========================================================
    # TELEGRAM DASHBOARD
    # ========================================================

    def telegram_dashboard(
        self
    ):

        equity = (
            self.get_equity()
        )

        trades = (
            self.total_wins
            +
            self.total_losses
        )

        if trades > 0:

            win_rate = (
                self.total_wins
                /
                trades
                *
                100
            )

        else:

            win_rate = 0


        account_change = (
            equity
            -
            CONFIG[
                "starting_balance"
            ]
        )


        return (

            "🤖 JARVIS DASHBOARD\n\n"

            f"Cash: "
            f"${self.balance:,.2f}\n"

            f"Equity: "
            f"${equity:,.2f}\n"

            f"Account change: "
            f"${account_change:+,.2f}\n"

            f"Closed P/L: "
            f"${self.total_profit:+,.2f}\n\n"

            f"Wins: "
            f"{self.total_wins}\n"

            f"Losses: "
            f"{self.total_losses}\n"

            f"Win rate: "
            f"{win_rate:.1f}%\n"

            f"Open trades: "
            f"{len(self.positions)}\n\n"

            "Mode: PAPER TRADING"
        )


    # ========================================================
    # TELEGRAM POSITIONS
    # ========================================================

    def telegram_positions(
        self
    ):

        if not self.positions:

            return (
                "📊 OPEN POSITIONS\n\n"
                "No open trades."
            )


        message = (
            "📊 OPEN POSITIONS\n\n"
        )


        for symbol, position in (
            self.positions.items()
        ):

            pnl_pct = (
                (
                    position[
                        "current_price"
                    ]
                    -
                    position[
                        "entry_price"
                    ]
                )
                /
                position[
                    "entry_price"
                ]
                *
                100
            )

            message += (
                f"{symbol}\n"
                f"Entry: "
                f"${position['entry_price']:.8f}\n"
                f"Current: "
                f"${position['current_price']:.8f}\n"
                f"P/L: "
                f"{pnl_pct:+.2f}%\n"
                f"Position: "
                f"${position['amount']:.2f}\n\n"
            )


        return message


    # ========================================================
    # TELEGRAM TRADE HISTORY
    # ========================================================

    def telegram_trades(
        self
    ):

        if not self.trade_history:

            return (
                "📜 RECENT TRADES\n\n"
                "No closed trades yet."
            )


        message = (
            "📜 RECENT TRADES\n\n"
        )


        recent = (
            self.trade_history[-5:]
        )


        for trade in reversed(
            recent
        ):

            message += (

                f"{trade['symbol']} | "
                f"{trade['reason']}\n"

                f"P/L: "
                f"{trade['pnl_pct']:+.2f}% | "

                f"${trade['profit']:+.2f}\n\n"
            )


        return message


    # ========================================================
    # CONSOLE DASHBOARD
    # ========================================================

    def dashboard(
        self
    ):

        equity = (
            self.get_equity()
        )

        trades = (
            self.total_wins
            +
            self.total_losses
        )


        if trades > 0:

            win_rate = (
                self.total_wins
                /
                trades
                *
                100
            )

        else:

            win_rate = 0


        print()
        print("=" * 55)
        print("JARVIS DASHBOARD")
        print("=" * 55)

        print(
            f"Cash:          "
            f"${self.balance:,.2f}"
        )

        print(
            f"Equity:        "
            f"${equity:,.2f}"
        )

        print(
            f"Total P/L:     "
            f"${self.total_profit:+,.2f}"
        )

        print(
            f"Wins/Losses:   "
            f"{self.total_wins}/"
            f"{self.total_losses}"
        )

        print(
            f"Win Rate:      "
            f"{win_rate:.1f}%"
        )

        print(
            f"Open Trades:   "
            f"{len(self.positions)}"
        )

        print("=" * 55)


    # ========================================================
    # SAVE RESULTS
    # ========================================================

    def save_results(
        self
    ):

        data = {

            "balance":
                self.balance,

            "equity":
                self.get_equity(),

            "total_profit":
                self.total_profit,

            "wins":
                self.total_wins,

            "losses":
                self.total_losses,

            "trades":
                self.trade_history
        }


        try:

            with open(
                "jarvis_results.json",
                "w"
            ) as file:

                json.dump(
                    data,
                    file,
                    indent=4
                )

        except Exception as error:

            print(
                "Save error:",
                error
            )


    # ========================================================
    # START BOT
    # ========================================================

    def run(
        self
    ):

        print()
        print("Jarvis started.")
        print(
            "Waiting for Telegram..."
        )
        print()


        # Check Telegram immediately
        self.telegram.check_messages(
            self
        )


        if self.telegram.chat_id:

            self.telegram.send(

                "🟢 JARVIS IS ONLINE\n\n"

                f"Starting paper balance: "
                f"${self.balance:,.2f}\n"

                "Scanner: ACTIVE\n"

                "Risk controls: ACTIVE\n"

                "Telegram: CONNECTED\n\n"

                "Send /status anytime."
            )


        while True:

            try:

                self.cycle += 1


                # Read Telegram commands

                self.telegram.check_messages(
                    self
                )


                # Daily loss protection

                if (
                    self.daily_loss_limit_hit()
                ):

                    print(
                        "DAILY LOSS LIMIT HIT"
                    )

                    self.telegram.send(

                        "🛑 JARVIS SAFETY STOP\n\n"

                        "Daily loss limit reached.\n"

                        "No new paper trades "
                        "will be opened."
                    )


                    if self.positions:

                        self.update_positions()


                    self.dashboard()

                    time.sleep(
                        CONFIG[
                            "cooldown_seconds"
                        ]
                    )

                    continue


                # Update existing positions

                if self.positions:

                    self.update_positions()


                # Scan market

                opportunities = (
                    self.scan_market()
                )


                if opportunities:

                    print()
                    print(
                        f"Found "
                        f"{len(opportunities)} "
                        f"potential setups"
                    )


                    for token in (
                        opportunities[:3]
                    ):

                        print(

                            f"{token['symbol']} | "

                            f"Score "
                            f"{token['score']} | "

                            f"5m "
                            f"{token['change_5m']:+.1f}% | "

                            f"Liq "
                            f"${token['liquidity']:,.0f}"
                        )


                    best = (
                        opportunities[0]
                    )


                    self.buy(
                        best
                    )


                else:

                    print(
                        "No setup meets rules."
                    )


                self.dashboard()


                # Send periodic dashboard
                # to Telegram

                if (
                    self.cycle
                    %
                    CONFIG[
                        "telegram_dashboard_every"
                    ]
                    ==
                    0
                ):

                    self.telegram.send(
                        self.telegram_dashboard()
                    )


                time.sleep(
                    CONFIG[
                        "cooldown_seconds"
                    ]
                )


            except KeyboardInterrupt:

                print(
                    "Jarvis stopped."
                )

                self.telegram.send(
                    "🔴 JARVIS STOPPED"
                )

                self.save_results()

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

                time.sleep(5)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    bot = JarvisBot()

    bot.run()
