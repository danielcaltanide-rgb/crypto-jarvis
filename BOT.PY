import os
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from openai import OpenAI

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)


def get_coin(coin):
    url = "https://api.coingecko.com/api/v3/coins/markets"

    params = {
        "vs_currency": "usd",
        "ids": coin.lower()
    }

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()

    data = response.json()

    if not data:
        return None

    return data[0]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Crypto Jarvis online.\n\n"
        "Try:\n"
        "/price bitcoin\n"
        "/analyse bitcoin"
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Try: /price bitcoin")
        return

    coin = context.args[0]
    data = get_coin(coin)

    if not data:
        await update.message.reply_text("Coin not found.")
        return

    text = (
        f"{data['name']} ({data['symbol'].upper()})\n\n"
        f"Price: ${data['current_price']:,}\n"
        f"24h change: {data['price_change_percentage_24h']:.2f}%\n"
        f"Market cap: ${data['market_cap']:,}\n"
        f"24h volume: ${data['total_volume']:,}"
    )

    await update.message.reply_text(text)


async def analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Try: /analyse bitcoin")
        return

    coin = context.args[0]
    data = get_coin(coin)

    if not data:
        await update.message.reply_text("Coin not found.")
        return

    prompt = f"""
You are Crypto Jarvis, a cautious crypto analyst.

Coin: {data['name']}
Price: ${data['current_price']}
24h change: {data['price_change_percentage_24h']}%
Market cap: ${data['market_cap']}
24h volume: ${data['total_volume']}

Give a short analysis of:
- momentum
- volume
- risk
- what to watch next

Never guarantee profits.
"""

    response = client.responses.create(
        model="gpt-5-mini",
        input=prompt
    )

    await update.message.reply_text(
        "JARVIS ANALYSIS\n\n" + response.output_text
    )


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("analyse", analyse))

    print("Crypto Jarvis online")
    app.run_polling()


if __name__ == "__main__":
    main()
