import os
import time
import random
import json
import urllib.parse
import urllib.request
from datetime import datetime


# ============================================================
# CONFIG
# ============================================================

CONFIG = {
    "starting_balance": 1000.00,

    "risk_per_trade_pct": 1.0,
    "max_position_pct": 10.0,
    "max_open_trades": 3,

    "stop_loss_pct": 8.0,
    "take_profit_pct": 18.0,
    "trailing_stop_pct": 7.0,

    "min_liquidity": 20_000,
    "min_volume_24h": 30_000,
    "min_market_cap": 30_000,
    "max_market_cap": 5_000_000,

    "min_price_change_5m": 2.0,
    "min_score": 65,

    "max_daily_loss_pct": 5.0,
    "max_daily_trades": 30,

    "cooldown_seconds": 10,
    "telegram_dashboard_every": 12,

    "paper_mode": True
}


# ============================================================
# TELEGRAM
# ============================================================

class TelegramManager:

    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.offset = None

        if not self.token:
            print("WARNING: TELEGRAM_BOT_TOKEN missing.")
        else:
            print("Telegram token loaded.")

    def request(self, method, data=None):
        if not self.token:
            return None

        url = f"https://api.telegram.org/bot{self.token}/{method}"

        try:
            if data:
                encoded = urllib.parse.urlencode(data).encode("utf-8")
                req = urllib.request.Request(url, data=encoded)
            else:
                req = urllib.request.Request(url)

            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))

        except Exception as error:
            print("Telegram request error:", error)
            return None

    def send(self, text):
        if not self.token:
            return

        if not self.chat_id:
            print("Telegram chat ID not connected yet.")
            return

        self.request(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text
            }
        )

    def get_updates(self):
        data = {
            "timeout": 1
        }

        if self.offset is not None:
            data["offset"] = self.offset

        result = self.request("getUpdates", data)

        if not result:
            return []

        if not result.get("ok"):
            return []

        updates = result.get("result", [])

        if updates:
            self.offset = updates[-1]["update_id"] + 1

        return updates

    def handle_commands(self, bot):
        updates = self.get_updates()

        for update in updates:

            msg = update.get("message", {})
            chat = msg.get("chat", {})
            text = msg.get("text", "").strip()

            incoming_chat_id = str(chat.get("id", ""))

            if not incoming_chat_id:
                continue

            # Automatically connect first Telegram chat
            if not self.chat_id:
                self.chat_id = incoming_chat_id

                print("Telegram connected to:", self.chat_id)

                self.send(
                    "🤖 JARVIS CONNECTED\n\n"
                    "Paper trading mode is active.\n\n"
                    "Commands:\n"
                    "/status\n"
                    "/positions\n"
                    "/trades\n"
                    "/today\n"
                    "/help"
                )

            if incoming_chat_id != self.chat_id:
                continue

            command = text.lower()

            if command in ["/start", "start"]:
                self.send(
                    "🤖 JARVIS CRYPTO BOT\n\n"
                    "Status: ONLINE\n"
                    "Mode: PAPER TRADING\n\n"
                    "Commands:\n"
                    "/status\n"
                    "/positions\n"
                    "/trades\n"
                    "/today\n"
                    "/help"
                )

            elif command == "/status":
                self.send(bot.status_message())

            elif command == "/positions":
                self.send(bot.positions_message())

            elif command == "/trades":
                self.send(bot.trades_message())

            elif command == "/today":
                self.send(bot.today_message())

            elif command == "/help":
                self.send(
                    "🤖 JARVIS COMMANDS\n\n"
                    "/status - account dashboard\n"
                    "/positions - open trades\n"
                    "/trades - recent closed trades\n"
                    "/today - today's performance\n"
                    "/help - show commands"
                )


# ============================================================
# JARVIS
# ============================================================

class JarvisBot:

    def __init__(self):
        self.balance = CONFIG["starting_balance"]
        self.starting_day_balance = self.balance

        self.positions = {}
        self.trade_history = []

        self.total_profit = 0.0
        self.total_wins = 0
        self.total_losses = 0

        self.daily_profit = 0.0
        self.daily_trades = 0

        self.cycle = 0

        self.telegram = TelegramManager()

        print("=" * 55)
        print("JARVIS PAPER TRADING BOT")
        print("=" * 55)
        print(f"Starting balance: ${self.balance:.2f}")
        print("=" * 55)

    # ========================================================
    # MARKET SCANNER
    # ========================================================

    def scan_market(self):

        symbols = [
            "BONK",
            "WIF",
            "POPCAT",
            "MEW",
            "DOGGO",
            "MEME",
            "CAT",
            "MOON",
            "ROCKET",
            "SCREM"
        ]

        opportunities = []

        for symbol in symbols:

            token = {
                "symbol": symbol,
                "price": random.uniform(0.00001, 0.05),
                "liquidity": random.uniform(5_000, 500_000),
                "volume": random.uniform(5_000, 1_000_000),
                "market_cap": random.uniform(10_000, 8_000_000),
                "change_5m": random.uniform(-10, 20),
                "change_1h": random.uniform(-20, 50),
                "buys": random.randint(10, 500),
                "sells": random.randint(5, 400)
            }

            token["score"] = self.calculate_score(token)

            if self.valid_token(token):
                opportunities.append(token)

        opportunities.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        return opportunities

    def calculate_score(self, token):
        score = 0

        if token["liquidity"] > 100_000:
            score += 20
        elif token["liquidity"] > 50_000:
            score += 15
        elif token["liquidity"] > 20_000:
            score += 10

        if token["volume"] > 500_000:
            score += 20
        elif token["volume"] > 100_000:
            score += 15
        elif token["volume"] > 30_000:
            score += 10

        if token["change_5m"] > 10:
            score += 20
        elif token["change_5m"] > 5:
            score += 15
        elif token["change_5m"] > 2:
            score += 10

        if token["change_1h"] > 20:
            score += 15
        elif token["change_1h"] > 5:
            score += 10

        if token["buys"] > token["sells"] * 1.5:
            score += 20
        elif token["buys"] > token["sells"]:
            score += 10

        if 50_000 <= token["market_cap"] <= 1_000_000:
            score += 10
        elif token["market_cap"] <= 5_000_000:
            score += 5

        return min(score, 100)

    def valid_token(self, token):

        if token["liquidity"] < CONFIG["min_liquidity"]:
            return False

        if token["volume"] < CONFIG["min_volume_24h"]:
            return False

        if token["market_cap"] < CONFIG["min_market_cap"]:
            return False

        if token["market_cap"] > CONFIG["max_market_cap"]:
            return False

        if token["change_5m"] < CONFIG["min_price_change_5m"]:
            return False

        if token["score"] < CONFIG["min_score"]:
            return False

        return True

    # ========================================================
    # MONEY MANAGEMENT
    # ========================================================

    def get_equity(self):

        open_value = 0.0

        for position in self.positions.values():
            open_value += (
                position["quantity"]
                * position["current_price"]
            )

        return self.balance + open_value

    def calculate_position_size(self):

        equity = self.get_equity()

        risk_amount = (
            equity
            * CONFIG["risk_per_trade_pct"]
            / 100
        )

        stop_fraction = (
            CONFIG["stop_loss_pct"]
            / 100
        )

        size = risk_amount / stop_fraction

        max_size = (
            equity
            * CONFIG["max_position_pct"]
            / 100
        )

        return min(
            size,
            max_size,
            self.balance
        )

    def daily_loss_limit_hit(self):

        loss = (
            self.starting_day_balance
            - self.get_equity()
        )

        loss_pct = (
            loss
            / self.starting_day_balance
            * 100
        )

        return loss_pct >= CONFIG["max_daily_loss_pct"]

    # ========================================================
    # BUY
    # ========================================================

    def buy(self, token):

        if self.daily_trades >= CONFIG["max_daily_trades"]:
            return

        if len(self.positions) >= CONFIG["max_open_trades"]:
            return

        symbol = token["symbol"]

        if symbol in self.positions:
            return

        amount = self.calculate_position_size()

        if amount <= 0:
            return

        price = token["price"]
        quantity = amount / price

        self.positions[symbol] = {
            "symbol": symbol,
            "entry_price": price,
            "current_price": price,
            "highest_price": price,
            "quantity": quantity,
            "amount": amount,
            "score": token["score"]
        }

        self.balance -= amount

        self.telegram.send(
            "🟢 PAPER SNIPE OPENED\n\n"
            f"{symbol}\n"
            f"Score: {token['score']}/100\n"
            f"Entry: ${price:.8f}\n"
            f"Position: ${amount:.2f}\n"
            f"5m: {token['change_5m']:+.2f}%\n"
            f"Liquidity: ${token['liquidity']:,.0f}\n\n"
            f"Target: +{CONFIG['take_profit_pct']}%\n"
            f"Stop: -{CONFIG['stop_loss_pct']}%"
        )

    # ========================================================
    # POSITION MANAGEMENT
    # ========================================================

    def update_positions(self):

        for symbol in list(self.positions.keys()):

            pos = self.positions[symbol]

            movement = random.uniform(-0.08, 0.10)

            new_price = (
                pos["current_price"]
                * (1 + movement)
            )

            pos["current_price"] = new_price

            if new_price > pos["highest_price"]:
                pos["highest_price"] = new_price

            pnl_pct = (
                (
                    new_price
                    - pos["entry_price"]
                )
                / pos["entry_price"]
                * 100
            )

            trailing_drop = (
                (
                    pos["highest_price"]
                    - new_price
                )
                / pos["highest_price"]
                * 100
            )

            print(
                f"{symbol} | "
                f"PnL {pnl_pct:+.2f}%"
            )

            if pnl_pct >= CONFIG["take_profit_pct"]:
                self.sell(symbol, "TAKE PROFIT")
                continue

            if pnl_pct <= -CONFIG["stop_loss_pct"]:
                self.sell(symbol, "STOP LOSS")
                continue

            if (
                pnl_pct > 5
                and trailing_drop
                >= CONFIG["trailing_stop_pct"]
            ):
                self.sell(symbol, "TRAILING STOP")

    # ========================================================
    # SELL
    # ========================================================

    def sell(self, symbol, reason):

        pos = self.positions[symbol]

        exit_price = pos["current_price"]

        value = (
            pos["quantity"]
            * exit_price
        )

        profit = (
            value
            - pos["amount"]
        )

        pnl_pct = (
            profit
            / pos["amount"]
            * 100
        )

        self.balance += value

        self.total_profit += profit
        self.daily_profit += profit
        self.daily_trades += 1

        if profit >= 0:
            self.total_wins += 1
            icon = "🟢"
        else:
            self.total_losses += 1
            icon = "🔴"

        self.trade_history.append({
            "symbol": symbol,
            "profit": profit,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "time": datetime.now().isoformat()
        })

        del self.positions[symbol]

        self.telegram.send(
            f"{icon} PAPER SNIPE CLOSED\n\n"
            f"{symbol}\n"
            f"Reason: {reason}\n"
            f"Return: {pnl_pct:+.2f}%\n"
            f"P/L: ${profit:+.2f}\n\n"
            f"Today's P/L: ${self.daily_profit:+.2f}\n"
            f"Today's trades: "
            f"{self.daily_trades}/"
            f"{CONFIG['max_daily_trades']}\n"
            "Status: 🟢 ACTIVE"
        )

        self.save_results()

    # ========================================================
    # TELEGRAM MESSAGES
    # ========================================================

    def status_message(self):

        total_trades = (
            self.total_wins
            + self.total_losses
        )

        win_rate = (
            self.total_wins
            / total_trades
            * 100
            if total_trades
            else 0
        )

        return (
            "🤖 JARVIS STATUS\n\n"
            f"Cash: ${self.balance:,.2f}\n"
            f"Equity: ${self.get_equity():,.2f}\n"
            f"Total P/L: ${self.total_profit:+,.2f}\n"
            f"Today's P/L: ${self.daily_profit:+,.2f}\n\n"
            f"Wins: {self.total_wins}\n"
            f"Losses: {self.total_losses}\n"
            f"Win rate: {win_rate:.1f}%\n"
            f"Open trades: {len(self.positions)}\n"
            f"Today's trades: "
            f"{self.daily_trades}/"
            f"{CONFIG['max_daily_trades']}\n\n"
            "Mode: PAPER TRADING"
        )

    def today_message(self):

        return (
            "📅 TODAY'S PERFORMANCE\n\n"
            f"P/L: ${self.daily_profit:+.2f}\n"
            f"Trades: "
            f"{self.daily_trades}/"
            f"{CONFIG['max_daily_trades']}\n"
            f"Current equity: "
            f"${self.get_equity():,.2f}"
        )

    def positions_message(self):

        if not self.positions:
            return (
                "📊 OPEN POSITIONS\n\n"
                "No open positions."
            )

        message = "📊 OPEN POSITIONS\n\n"

        for symbol, pos in self.positions.items():

            pnl_pct = (
                (
                    pos["current_price"]
                    - pos["entry_price"]
                )
                / pos["entry_price"]
                * 100
            )

            message += (
                f"{symbol}\n"
                f"Entry: ${pos['entry_price']:.8f}\n"
                f"Current: ${pos['current_price']:.8f}\n"
                f"P/L: {pnl_pct:+.2f}%\n\n"
            )

        return message

    def trades_message(self):

        if not self.trade_history:
            return (
                "📜 RECENT TRADES\n\n"
                "No closed trades yet."
            )

        message = "📜 RECENT TRADES\n\n"

        for trade in reversed(
            self.trade_history[-5:]
        ):
            message += (
                f"{trade['symbol']} | "
                f"{trade['reason']}\n"
                f"{trade['pnl_pct']:+.2f}% | "
                f"${trade['profit']:+.2f}\n\n"
            )

        return message

    # ========================================================
    # SAVE
    # ========================================================

    def save_results(self):

        try:
            with open(
                "jarvis_results.json",
                "w"
            ) as file:

                json.dump(
                    {
                        "balance": self.balance,
                        "equity": self.get_equity(),
                        "profit": self.total_profit,
                        "daily_profit": self.daily_profit,
                        "wins": self.total_wins,
                        "losses": self.total_losses,
                        "trades": self.trade_history
                    },
                    file,
                    indent=4
                )

        except Exception as error:
            print("Save error:", error)

    # ========================================================
    # RUN
    # ========================================================

    def run(self):

        print("Jarvis started.")

        while True:

            try:

                # Check Telegram EVERY cycle
                self.telegram.handle_commands(self)

                if self.daily_loss_limit_hit():

                    self.telegram.send(
                        "🛑 JARVIS SAFETY STOP\n\n"
                        "Daily loss limit reached.\n"
                        "No new trades will be opened."
                    )

                    if self.positions:
                        self.update_positions()

                    time.sleep(
                        CONFIG["cooldown_seconds"]
                    )

                    continue

                if self.positions:
                    self.update_positions()

                if (
                    self.daily_trades
                    < CONFIG["max_daily_trades"]
                ):

                    opportunities = self.scan_market()

                    if opportunities:
                        self.buy(opportunities[0])

                self.cycle += 1

                if (
                    self.cycle
                    % CONFIG["telegram_dashboard_every"]
                    == 0
                ):
                    self.telegram.send(
                        self.status_message()
                    )

                time.sleep(
                    CONFIG["cooldown_seconds"]
                )

            except Exception as error:

                print("BOT ERROR:", error)

                self.telegram.send(
                    "⚠️ JARVIS ERROR\n\n"
                    f"{error}"
                )

                time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    bot = JarvisBot()
    bot.run()
