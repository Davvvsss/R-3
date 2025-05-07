import asyncio
import os
from typing import List, Dict, Set

import aiohttp
import base58
from tronpy import Tron
from tronpy.providers import HTTPProvider
from telegram import Bot
from dotenv import load_dotenv

# ──────────────────────────────── 1. Конфигурация ─────────────────────────────
load_dotenv()                                    # читаем .env

TG_TOKEN: str = os.getenv("TG_TOKEN")
CHAT_ID: int = int(os.getenv("CHAT_ID"))
TRON_ADDR: str = os.getenv("TRON_ADDR")
TRONGRID_KEY: str = os.getenv("TRONGRID_KEY", "")

# пороги уведомлений
MIN_TRX:   float = float(os.getenv("MIN_TRX",   1.0))   # ≥ 1 TRX
MIN_USDT:  float = float(os.getenv("MIN_USDT",  1.0))   # ≥ 1 USDT (TRC‑20)

# TRC‑20 USDT контракт в hex (без 0x)
USDT_HEX = "a614f803b6fd780986a42c78ec9c7f77e6ded13c"

if not all([TG_TOKEN, CHAT_ID, TRON_ADDR]):
    raise RuntimeError("TG_TOKEN, CHAT_ID и TRON_ADDR обязательны в .env")

# ──────────────────────────────── 2. Инициализация ────────────────────────────
bot = Bot(TG_TOKEN)
client = Tron(provider=HTTPProvider(api_key=TRONGRID_KEY))

MY_HEX: str = base58.b58decode_check(TRON_ADDR).hex()      # адрес в hex
API_URL = f"https://api.trongrid.io/v1/accounts/{TRON_ADDR}/transactions?limit=20"

seen_tx: Set[str] = set()                                  # уже обработанные

# ──────────────────────────────── 3. Вспом. функции ───────────────────────────
def trx_link(txid: str) -> str:
    return f"https://tronscan.org/#/transaction/{txid}"

async def get_balance() -> float:
    return round(client.get_account_balance(TRON_ADDR), 2)

async def fetch_transactions() -> List[Dict]:
    headers = {"TRON-PRO-API-KEY": TRONGRID_KEY} if TRONGRID_KEY else {}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=aiohttp.TCPConnector(ssl=False)          # Railway уже с CA
    ) as session:
        async with session.get(API_URL, headers=headers) as resp:
            data = await resp.json()
            return data.get("data", [])

# ───────────────────────── Уведомления: TRX и USDT ────────────────────────────
async def notify_trx(tx: Dict, amount: float, direction: str) -> None:
    if amount < MIN_TRX:
        return
    balance = await get_balance()
    url = trx_link(tx["txID"])
    text = (
        f"{direction} **{amount:.2f} TRX**\n"
        f"{url}\n"
        f"💰 Balance: `{balance} TRX`"
    )
    await bot.send_message(
        CHAT_ID, text, parse_mode="Markdown", disable_web_page_preview=True
    )

async def notify_usdt(txid: str, amount: float, in_out: str) -> None:
    if amount < MIN_USDT:
        return
    url = trx_link(txid)
    text = f"{in_out} **{amount:.2f} USDT**\n{url}"
    await bot.send_message(
        CHAT_ID, text, parse_mode="Markdown", disable_web_page_preview=True
    )

# ────────────────────────── Обработка каждой транзакции ───────────────────────
async def handle_tx(tx: Dict) -> None:
    txid = tx["txID"]
    if txid in seen_tx:        # уже отправляли
        return

    ctype = tx["raw_data"]["contract"][0]["type"]

    # --- нативные TRX ---
    if ctype == "TransferContract":
        val = tx["raw_data"]["contract"][0]["parameter"]["value"]
        amount = int(val.get("amount", 0)) / 1_000_000
        to_hex = val.get("to_address", "")
        direction = "⬇️ IN" if to_hex == MY_HEX else "⬆️ OUT"
        await notify_trx(tx, amount, direction)

    # --- TRC‑20 USDT ---
    if "trc20TransferInfo" in tx and tx["trc20TransferInfo"]:
        for info in tx["trc20TransferInfo"]:
            if info.get("contract_address", "").lower() == USDT_HEX:
                amount = int(info.get("amount", 0)) / 1_000_000
                in_out = "⬇️ IN" if info.get("to", "").lower() == MY_HEX else "⬆️ OUT"
                await notify_usdt(txid, amount, in_out)

    seen_tx.add(txid)

# ───────────────────────────────── Основной цикл ──────────────────────────────
async def main() -> None:
    # при запуске просто запоминаем последние 20 tx, но не шлём их
    try:
        for t in await fetch_transactions():
            seen_tx.add(t["txID"])
    except Exception:
        pass  # если сеть недоступна — игнорируем и ждём следующей итерации

    while True:
        try:
            for t in await fetch_transactions():
                await handle_tx(t)
            await asyncio.sleep(15)
        except (aiohttp.ClientError, asyncio.TimeoutError) as net_err:
            await bot.send_message(CHAT_ID, f"⚠️ Network error: {net_err}")
            await asyncio.sleep(30)
        except Exception as exc:
            await bot.send_message(CHAT_ID, f"⚠️ Bot error: {exc}")
            await asyncio.sleep(30)

# ───────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(main())