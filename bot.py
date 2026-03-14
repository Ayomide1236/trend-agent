"""
TREND AGENT TELEGRAM BOT
#AgentTalentShow - Bitget Wallet Track

Commands:
  /start   - Welcome + ask for wallet
  /wallet  - Update your wallet address
  /scan    - Scan top Solana gainers
  /pnl     - Show PnL summary
  /help    - Show all commands
"""

import sys
import json
import os
import logging
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)

sys.path.insert(0, 'scripts')
import bitget_agent_api as bgw

BOT_TOKEN            = "8258813509:AAHATQBeAxVP73hBCpKcfzu1dHS465wARjQ"
CHAIN                = "sol"
SOL_CONTRACT         = "So11111111111111111111111111111111111111112"
SOL_SYMBOL           = "SOL"
TRADE_AMOUNT_SOL     = 0.005
PNL_LOG_FILE         = "pnl_log.json"
WALLETS_FILE         = "wallets.json"

MIN_VOLUME_24H       = 100000
MIN_PRICE_CHANGE_24H = 20.0
ALLOWED_RISK         = ["low"]
ALLOWED_CHAINS       = ["sol"]

WAITING_FOR_WALLET   = 1

logging.basicConfig(level=logging.INFO)


# ── Wallet store ──────────────────────────────────────────────────────────────
def load_wallets():
    if os.path.exists(WALLETS_FILE):
        with open(WALLETS_FILE) as f:
            return json.load(f)
    return {}

def save_wallets(wallets):
    with open(WALLETS_FILE, "w") as f:
        json.dump(wallets, f, indent=2)

def get_wallet(user_id):
    wallets = load_wallets()
    return wallets.get(str(user_id))

def set_wallet(user_id, address):
    wallets = load_wallets()
    wallets[str(user_id)] = address
    save_wallets(wallets)

def is_valid_solana_address(address):
    # Solana addresses are base58, 32-44 chars
    import re
    return bool(re.match(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$', address))


# ── BGW API caller ────────────────────────────────────────────────────────────
def call_bgw(args_list):
    buf = StringIO()
    try:
        with patch('sys.argv', ['bitget_agent_api.py'] + args_list):
            with patch('sys.stdout', buf):
                try:
                    bgw.main()
                except SystemExit:
                    pass
        output = buf.getvalue().strip()
        if not output:
            return None
        for line in output.splitlines():
            if line.strip().startswith("{"):
                output = output[output.index(line):]
                break
        return json.loads(output)
    except Exception:
        return None


# ── PnL helpers ───────────────────────────────────────────────────────────────
def load_pnl_log():
    if os.path.exists(PNL_LOG_FILE):
        with open(PNL_LOG_FILE) as f:
            return json.load(f)
    return []

def save_pnl_log(log):
    with open(PNL_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)

def log_trade(user_id, symbol, contract, entry_price, sol_spent, tokens_received):
    log = load_pnl_log()
    log.append({
        "user_id":         str(user_id),
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "token":           symbol,
        "contract":        contract,
        "entry_price_usd": entry_price,
        "sol_spent":       sol_spent,
        "tokens_received": tokens_received,
        "exit_price_usd":  None,
        "pnl_usd":         None,
        "status":          "open"
    })
    save_pnl_log(log)


# ── Security check ────────────────────────────────────────────────────────────
def is_safe(contract):
    data = call_bgw(["security", "--chain", CHAIN, "--contract", contract])
    if not data:
        return False, "No security data"
    if data.get("isHoneypot"):
        return False, "HONEYPOT"
    if data.get("isBlacklist"):
        return False, "Blacklisted"
    return True, "Passed"


# ── Quote ─────────────────────────────────────────────────────────────────────
def get_quote(to_contract, symbol, wallet_address):
    return call_bgw([
        "quote",
        "--from-chain",    CHAIN,
        "--from-symbol",   SOL_SYMBOL,
        "--from-contract", SOL_CONTRACT,
        "--from-amount",   str(TRADE_AMOUNT_SOL),
        "--from-address",  wallet_address,
        "--to-chain",      CHAIN,
        "--to-symbol",     symbol,
        "--to-contract",   to_contract,
        "--slippage",      "0.5"
    ])


# ── Scan logic ────────────────────────────────────────────────────────────────
def run_scan():
    resp = call_bgw(["rankings", "--name", "topGainers"])
    if not resp or "data" not in resp:
        return None, "Rankings API failed."
    all_tokens = resp.get("data", {}).get("list", [])
    tokens = [
        t for t in all_tokens
        if t.get("chain") in ALLOWED_CHAINS
        and float(t.get("change_24h", 0)) >= MIN_PRICE_CHANGE_24H
        and float(t.get("turnover_24h", 0)) >= MIN_VOLUME_24H
        and t.get("risk_level") in ALLOWED_RISK
    ][:10]
    return tokens, None


# ── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallet  = get_wallet(user_id)

    if wallet:
        msg = (
            "👋 Welcome back to *Trend Agent*!\n\n"
            "Your wallet: `" + wallet + "`\n\n"
            "/scan — Scan top Solana gainers\n"
            "/wallet — Update wallet address\n"
            "/pnl — View PnL summary\n"
            "/help — All commands"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        msg = (
            "🤖 Welcome to *Trend Agent*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Built with `bitget-wallet-skill`\n"
            "Strategy: Hot Solana tokens → Security filter → Swap\n\n"
            "To get started, please send me your *Solana wallet address*.\n\n"
            "_Example: 5BRhsW2bLv4F3aG1ZpccNBKf9t1zm9AtV8rGMtubSTmf_"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return WAITING_FOR_WALLET

    return ConversationHandler.END


# ── /wallet ───────────────────────────────────────────────────────────────────
async def cmd_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me your Solana wallet address to update it.\n\n"
        "_Example: 5BRhsW2bLv4F3aG1ZpccNBKf9t1zm9AtV8rGMtubSTmf_",
        parse_mode="Markdown"
    )
    return WAITING_FOR_WALLET


# ── Receive wallet address ────────────────────────────────────────────────────
async def receive_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = update.message.text.strip()

    if not is_valid_solana_address(address):
        await update.message.reply_text(
            "❌ That doesn't look like a valid Solana address.\n\n"
            "Solana addresses are 32-44 characters, like:\n"
            "`5BRhsW2bLv4F3aG1ZpccNBKf9t1zm9AtV8rGMtubSTmf`\n\n"
            "Please try again.",
            parse_mode="Markdown"
        )
        return WAITING_FOR_WALLET

    set_wallet(user_id, address)

    # Check balance
    balance_resp = call_bgw([
        "get-processed-balance",
        "--chain", CHAIN,
        "--address", address
    ])

    balance = "Unknown"
    try:
        data = balance_resp.get("data", [])
        if data:
            token_list = data[0].get("list", {})
            native = token_list.get("", {})
            balance = native.get("balance", "0") + " SOL"
    except Exception:
        pass

    await update.message.reply_text(
        "✅ Wallet saved!\n\n"
        "Address: `" + address + "`\n"
        "Balance: " + balance + "\n\n"
        "You're all set. Use /scan to find hot tokens.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ── /scan ─────────────────────────────────────────────────────────────────────
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallet  = get_wallet(user_id)

    if not wallet:
        await update.message.reply_text(
            "⚠️ No wallet set. Use /start or /wallet to add your Solana address first."
        )
        return

    await update.message.reply_text("🔍 Scanning top Solana gainers...")

    tokens, error = run_scan()

    if error:
        await update.message.reply_text("❌ " + error)
        return

    if not tokens:
        await update.message.reply_text("😴 No tokens passed filters right now. Try again soon.")
        return

    timestamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = ["📊 *Scan Results* — " + timestamp + "\n"]
    passed = []

    for i, token in enumerate(tokens, 1):
        symbol   = token.get("symbol", "?")
        contract = token.get("contract", "")
        price    = float(token.get("price", 0))
        change   = float(token.get("change_24h", 0))
        volume   = float(token.get("turnover_24h", 0))

        safe, reason = is_safe(contract)
        status = "✅" if safe else "❌"

        lines.append(
            str(i) + ". *" + symbol + "* " + status + "\n"
            "   Price: $" + str(round(price, 8)) + "\n"
            "   24h: +" + str(round(change, 1)) + "% | Vol: $" + str(round(volume)) + "\n"
            "   Security: " + reason
        )

        if safe and contract:
            passed.append({"symbol": symbol, "contract": contract, "price": price})

    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")

    if not passed:
        await update.message.reply_text("⚠️ No tokens passed security checks.")
        return

    await update.message.reply_text("💱 Getting swap quotes for safe tokens...")

    for token in passed:
        symbol   = token["symbol"]
        contract = token["contract"]
        price    = token["price"]

        quote = get_quote(contract, symbol, wallet)

        if quote and "data" in quote:
            qdata     = quote.get("data", {})
            to_amount = qdata.get("toAmount") or qdata.get("outAmount", "N/A")
            route     = qdata.get("dex") or qdata.get("router", "N/A")
            slippage  = qdata.get("priceImpact") or qdata.get("slippage", "N/A")
            quote_msg = (
                "🟢 *" + symbol + "* quote\n"
                "━━━━━━━━━━━━━━━━\n"
                "Spend: " + str(TRADE_AMOUNT_SOL) + " SOL\n"
                "Receive: " + str(to_amount) + " " + symbol + "\n"
                "Route: " + str(route) + "\n"
                "Slippage: " + str(slippage) + "%\n\n"
                "Open Bitget Wallet to sign and execute."
            )
        else:
            quote_msg = (
                "🟡 *" + symbol + "* — Quote unavailable\n"
                "Price: $" + str(round(price, 8)) + "\n"
                "Token may be too new for routing."
            )

        await update.message.reply_text(quote_msg, parse_mode="Markdown")


# ── /pnl ──────────────────────────────────────────────────────────────────────
async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    log     = [t for t in load_pnl_log() if t.get("user_id") == user_id]

    if not log:
        await update.message.reply_text(
            "📭 No trades logged yet.\n\nRun /scan to find opportunities."
        )
        return

    open_trades   = [t for t in log if t["status"] == "open"]
    closed_trades = [t for t in log if t["status"] == "closed"]
    total_pnl     = sum(t["pnl_usd"] for t in closed_trades if t["pnl_usd"])

    lines = [
        "📈 *PnL Summary*",
        "━━━━━━━━━━━━━━━━",
        "Open:    " + str(len(open_trades)) + " trades",
        "Closed:  " + str(len(closed_trades)) + " trades",
        "PnL:     $" + str(round(total_pnl, 2)),
        ""
    ]

    if open_trades:
        lines.append("*Open Positions:*")
        for t in open_trades:
            lines.append(
                "• " + t["token"] +
                " | $" + str(t["entry_price_usd"]) +
                " | " + str(t["sol_spent"]) + " SOL"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /help ─────────────────────────────────────────────────────────────────────
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *Trend Agent — Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/start — Welcome + wallet setup\n"
        "/wallet — Update your Solana wallet\n"
        "/scan — Scan top Solana gainers\n"
        "/pnl — View your PnL summary\n"
        "/help — This message\n\n"
        "_Built with bitget-wallet-skill_\n"
        "_#AgentTalentShow_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# ── Cancel handler ────────────────────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Starting Trend Agent Bot...")
    print("Press Ctrl+C to stop.\n")

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start",  cmd_start),
            CommandHandler("wallet", cmd_wallet),
        ],
        states={
            WAITING_FOR_WALLET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_wallet)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("pnl",  cmd_pnl))
    app.add_handler(CommandHandler("help", cmd_help))

    print("Bot running. Open Telegram and message your bot.")
    app.run_polling()


if __name__ == "__main__":
    main()
