"""
TREND MOMENTUM AGENT - Bitget Wallet Skill
#AgentTalentShow submission
Strategy: Hot tokens on Solana -> Security filter -> Swap quote -> Log PnL

USAGE:
  python agent.py            - single scan
  python agent.py --loop     - continuous (every 5 mins)
  python agent.py --dry-run  - scan only, no trade prompts
  python agent.py --pnl      - show PnL summary
"""

import sys
import time
import json
import os
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch

# Import the real bitget script directly
sys.path.insert(0, 'scripts')
import bitget_agent_api as bgw

CHAIN              = "sol"
SOL_CONTRACT       = "So11111111111111111111111111111111111111112"
SOL_SYMBOL         = "SOL"
WALLET_ADDRESS     = "5BRhsW2bLv4F3aG1ZpccNBKf9t1zm9AtV8rGMtubSTmf"
TRADE_AMOUNT_SOL   = 0.005
TOP_N_TOKENS       = 10
SCAN_INTERVAL      = 300
PNL_LOG_FILE       = "pnl_log.json"

MIN_VOLUME_24H       = 100000
MIN_PRICE_CHANGE_24H = 20.0
ALLOWED_RISK         = ["low"]
ALLOWED_CHAINS       = ["sol"]

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def green(t):  return GREEN + str(t) + RESET
def red(t):    return RED + str(t) + RESET
def yellow(t): return YELLOW + str(t) + RESET
def cyan(t):   return CYAN + str(t) + RESET
def bold(t):   return BOLD + str(t) + RESET


def call_bgw(args_list):
    """Call bitget_agent_api with a list of CLI args, capture JSON output."""
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


def load_pnl_log():
    if os.path.exists(PNL_LOG_FILE):
        with open(PNL_LOG_FILE) as f:
            return json.load(f)
    return []


def save_pnl_log(log):
    with open(PNL_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def log_trade(symbol, contract, entry_price, sol_spent, tokens_received):
    log = load_pnl_log()
    log.append({
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
    print(green("  Trade logged to " + PNL_LOG_FILE))


def show_pnl_summary():
    log = load_pnl_log()
    if not log:
        print("No trades logged yet.")
        return
    open_trades   = [t for t in log if t["status"] == "open"]
    closed_trades = [t for t in log if t["status"] == "closed"]
    total_pnl     = sum(t["pnl_usd"] for t in closed_trades if t["pnl_usd"])
    print(bold("\n-- PnL Summary --"))
    print("  Open trades:   " + str(len(open_trades)))
    print("  Closed trades: " + str(len(closed_trades)))
    print("  Realised PnL:  $" + str(round(total_pnl, 2)))
    for t in open_trades:
        print("  [OPEN]  " + t["token"] + " | entry $" + str(t["entry_price_usd"]) + " | " + str(t["sol_spent"]) + " SOL")
    print()


def is_safe(contract):
    data = call_bgw(["security", "--chain", CHAIN, "--contract", contract])
    if not data:
        return False, "No security data returned"
    if data.get("isHoneypot"):
        return False, "HONEYPOT detected"
    if data.get("isBlacklist"):
        return False, "Blacklisted"
    return True, "Passed security checks"


def get_quote(to_contract, symbol):
    return call_bgw([
        "quote",
        "--from-chain",    CHAIN,
        "--from-symbol",   SOL_SYMBOL,
        "--from-contract", SOL_CONTRACT,
        "--from-amount",   str(TRADE_AMOUNT_SOL),
        "--from-address",  WALLET_ADDRESS,
        "--to-chain",      CHAIN,
        "--to-symbol",     symbol,
        "--to-contract",   to_contract,
        "--slippage",      "0.5"
    ])


def scan(dry_run=False):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(bold("\n" + "="*58))
    print(bold("  TREND AGENT SCAN -- " + timestamp))
    print(bold("="*58))

    print("\n" + cyan("Step 1") + " Fetching top gainers...")
    resp = call_bgw(["rankings", "--name", "topGainers"])

    if not resp or "data" not in resp:
        print(red("  Rankings API failed."))
        return

    all_tokens = resp.get("data", {}).get("list", [])

    tokens = [
        t for t in all_tokens
        if t.get("chain") in ALLOWED_CHAINS
        and float(t.get("change_24h", 0)) >= MIN_PRICE_CHANGE_24H
        and float(t.get("turnover_24h", 0)) >= MIN_VOLUME_24H
        and t.get("risk_level") in ALLOWED_RISK
    ][:TOP_N_TOKENS]

    sol_total = len([t for t in all_tokens if t.get("chain") == "sol"])
    print("  Total returned: " + str(len(all_tokens)) + " | Solana: " + str(sol_total) + " | After filters: " + str(len(tokens)))
    print()

    if not tokens:
        print(yellow("  No Solana tokens passed filters this scan."))
        return

    passed = []

    for i, token in enumerate(tokens, 1):
        symbol   = token.get("symbol", "UNKNOWN")
        contract = token.get("contract", "")
        price    = float(token.get("price", 0))
        change   = float(token.get("change_24h", 0))
        volume   = float(token.get("turnover_24h", 0))
        risk     = token.get("risk_level", "?")

        print("[" + str(i).zfill(2) + "] " + bold(symbol) + "  $" + str(round(price, 8)) + "  24h: " + green("+" + str(round(change, 1)) + "%") + "  Vol: $" + str(round(volume)) + "  Risk: " + risk)

        if not contract:
            print(red("      No contract -- skipping"))
            continue

        safe, reason = is_safe(contract)
        if not safe:
            print(red("      Security: " + reason))
            continue
        print(green("      Security: " + reason))

        passed.append({"symbol": symbol, "contract": contract, "price": price})

    print(bold("\n" + "-"*58))
    print(bold("  TOKENS PASSING ALL CHECKS: " + str(len(passed))))
    print(bold("-"*58))

    if not passed:
        print(yellow("  No tokens passed. Try next cycle."))
        show_pnl_summary()
        return

    for token in passed:
        symbol   = token["symbol"]
        contract = token["contract"]
        price    = token["price"]

        print("\n  " + bold(green("-> " + symbol)) + "  $" + str(round(price, 8)))

        quote = get_quote(contract, symbol)
        to_amount = "N/A"

        if quote and "data" in quote:
            qdata     = quote.get("data", {})
            to_amount = qdata.get("toAmount") or qdata.get("outAmount", "N/A")
            route     = qdata.get("dex") or qdata.get("router", "N/A")
            slippage  = qdata.get("priceImpact") or qdata.get("slippage", "N/A")
            print("     Spend:    " + str(TRADE_AMOUNT_SOL) + " SOL")
            print("     Receive:  " + str(to_amount) + " " + symbol)
            print("     Route:    " + str(route))
            print("     Slippage: " + str(slippage) + "%")
        else:
            print(yellow("     Quote unavailable"))

        if dry_run:
            print(yellow("     [DRY RUN] Not executed"))
            continue

        print()
        confirm = input("     Execute swap? (" + str(TRADE_AMOUNT_SOL) + " SOL -> " + symbol + ") [y/N]: ").strip().lower()
        if confirm == "y":
            log_trade(symbol, contract, price, TRADE_AMOUNT_SOL,
                      float(to_amount) if to_amount != "N/A" else 0)
            print(green("     Logged! Open Bitget Wallet to sign."))
        else:
            print("     Skipped.")

    show_pnl_summary()


if __name__ == "__main__":
    loop    = "--loop"    in sys.argv
    dry_run = "--dry-run" in sys.argv
    pnl     = "--pnl"     in sys.argv

    if pnl:
        show_pnl_summary()
        sys.exit(0)

    if dry_run:
        print(yellow("DRY RUN MODE -- scans only, no trade execution"))

    if loop:
        print(cyan("LOOP MODE -- scanning every " + str(SCAN_INTERVAL//60) + " mins. Ctrl+C to stop."))
        try:
            while True:
                scan(dry_run=dry_run)
                print(cyan("\n  Next scan in " + str(SCAN_INTERVAL//60) + " minutes..."))
                time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            print("\n  Agent stopped.")
    else:
        scan(dry_run=dry_run)