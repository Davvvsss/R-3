import asyncio
import os
from typing import Set, List, Dict

import aiohttp
import base58
from tronpy import Tron
from tronpy.providers import HTTPProvider
from telegram import Bot
from dotenv import load_dotenv

# -------------------------
#   Load configuration
# -------------------------
load_dotenv()

TG_TOKEN: str = os.getenv("TG_TOKEN")
CHAT_ID: int = int(os.getenv("CHAT_ID"))
TRON_ADDR: str = os.getenv("TRON_ADDR")
TRONGRID_KEY: str = os.getenv("TRONGRID_KEY", "")

# Пороги для уведомлений
MIN_TRX: float = float(os.getenv("MIN_TRX", 1.0))     # ≥ 1 TRX
MIN_USDT: float = float(os.getenv("MIN_USDT", 1.0))   # ≥ 1 USDT (TRC‑20)

# USDT (TRC‑20) contract address в hex без 0x
USDT_HEX = "a614f803b6fd780986a42c78ec9c7f77e6ded13c"

if not all([TG_TOKEN, CHAT_ID, TRON_ADDR]):
    raise RuntimeError("TG_TOKEN, CHAT_ID и TRON_ADDR должны быть заданы в .env")

# -------------------------
#   Initialise clients
# -------------------------

bot = Bot(TG_TOKEN)
client = Tron(provider=HTTPProvider(api_key=TRONGRID_KEY))

MY_HEX: str = base58.b58decode_check(TRON_ADDR).hex()
API_URL = f"https://api.trongrid.io/v1/accounts/{TRON_ADDR}/transactions?limit=20"
seen_tx: Set[str] = set()

# -------------------------
#   Helper functions
# -------------------------

def trx_link(txid: str) -> str:
    return f"https://tronscan.org/#/transaction/{txid}"


async def get_balance() -> float:
    return round(client.get_account_balance(TRON_ADDR), 2)


async def fetch_transactions() -> List[Dict]:
    headers = {"TRON-PRO-API-KEY": TRONGRID_KEY} if TRONGRID_KEY else {}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(API_URL, headers=headers) as resp:
            data = await resp.json()
            return data.get("data", [])


async def notify_trx(tx: Dict, amount: float, direction: str):
    if amount < MIN_TRX:
        return
    balance = await get_balance()
    text = (
        f"{direction} **{amount:.2f} TRX**\n"
        f"[TronScan]({trx_link(tx['txID'])})\n"
        f"💰 Balance: `{balance} TRX`"
    )
    await bot.send_message(CHAT_ID, text, parse_mode="Markdown")


async def notify_usdt(txid: str, amount: float, in_out: str):
    if amount < MIN_USDT:
        return
    text = (
        f"{in_out} **{amount:.2f} USDT**\n"
        f"[TronScan]({trx_link(txid)})"
    )
    await bot.send_message(CHAT_ID, text, parse_mode="Markdown")


async def handle_tx(tx: Dict):
    txid = tx["txID"]
    if txid in seen_tx:
        return

    # Native TRX transfer
    if tx["raw_data"]["contract"][0]["type"] == "TransferContract":
        value = tx["raw_data"]["contract"][0]["parameter"]["value"]
        amount = int(value.get("amount", 0)) / 1_000_000
        to_hex = value.get("to_address", "")
        direction = "⬇️ IN" if to_hex == MY_HEX else "⬆️ OUT"
        await notify_trx(tx, amount, direction)

    # TRC‑20 transfers
    if "trc20TransferInfo" in tx and tx["trc20TransferInfo"]:
        for info in tx["trc20TransferInfo"]:
            if info.get("contract_address", "").lower() == USDT_HEX:
                amount = int(info.get("amount", 0)) / 1_000_000
                in_out = "⬇️ IN" if info.get("to", "").lower() == MY_HEX else "⬆️ OUT"
                await notify_usdt(txid, amount, in_out)

    seen_tx.add(txid)


# -------------------------
#   Main loop
# -------------------------

async def main() -> None:
    while True:
        try:
            for tx in await fetch_transactions():
                await handle_tx(tx)
            await asyncio.sleep(15)
        except (aiohttp.ClientError, asyncio.TimeoutError) as net_err:
            await bot.send_message(CHAT_ID, f"⚠️ Network error: {net_err}")
            await asyncio.sleep(30)
        except Exception as exc:
            await bot.send_message(CHAT_ID, f"⚠️ Bot error: {exc}")
            await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
