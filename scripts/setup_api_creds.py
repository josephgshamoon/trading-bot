#!/usr/bin/env python3
"""Derive Polymarket CLOB API credentials from an Ethereum private key.

Usage:
    python scripts/setup_api_creds.py

Reads POLYMARKET_PRIVATE_KEY from .env file, derives API credentials,
and writes them back to .env for the trading bot to use.

Signature types:
    0 = EOA (regular Ethereum wallet, e.g. MetaMask)
    1 = POLY_PROXY (Polymarket email/social login wallet)
    2 = GNOSIS_SAFE (multisig)
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_clob_client.client import ClobClient


CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet


def load_env():
    """Load .env file into dict."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env_vars[key.strip()] = value.strip()
    return env_vars, env_path


def save_env(env_vars, env_path):
    """Write env vars back to .env file."""
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")


def main():
    env_vars, env_path = load_env()

    private_key = env_vars.get("POLYMARKET_PRIVATE_KEY", "")
    if not private_key:
        print("ERROR: POLYMARKET_PRIVATE_KEY not found in .env")
        print()
        print("To set up your private key:")
        print("  1. If you have a Polymarket account: Settings > Export Private Key")
        print("  2. If starting fresh: use any Ethereum wallet private key")
        print("     (fund it with USDCe on Polygon chain)")
        print()
        print("Add to .env:")
        print("  POLYMARKET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE")
        sys.exit(1)

    # Ensure key has 0x prefix
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    # Determine signature type
    sig_type_str = env_vars.get("POLYMARKET_SIG_TYPE", "0")
    sig_type = int(sig_type_str)
    sig_names = {0: "EOA", 1: "POLY_PROXY", 2: "GNOSIS_SAFE"}
    print(f"Using signature type: {sig_type} ({sig_names.get(sig_type, 'unknown')})")

    # Create L1 client to derive credentials
    print(f"Connecting to CLOB at {CLOB_HOST} (chain {CHAIN_ID})...")
    client = ClobClient(
        host=CLOB_HOST,
        chain_id=CHAIN_ID,
        key=private_key,
        signature_type=sig_type,
    )

    address = client.get_address()
    print(f"Wallet address: {address}")

    # Derive or create API credentials
    print("Deriving API credentials...")
    try:
        creds = client.create_or_derive_api_creds()
    except Exception as e:
        print(f"ERROR deriving credentials: {e}")
        print()
        print("If this is a new wallet, make sure it has been used on Polymarket.")
        print("You may need to make at least one trade on the Polymarket website first.")
        sys.exit(1)

    print(f"API Key:        {creds.api_key}")
    print(f"API Secret:     {creds.api_secret[:8]}...")
    print(f"API Passphrase: {creds.api_passphrase[:8]}...")

    # Save to .env
    env_vars["POLYMARKET_API_KEY"] = creds.api_key
    env_vars["POLYMARKET_API_SECRET"] = creds.api_secret
    env_vars["POLYMARKET_API_PASSPHRASE"] = creds.api_passphrase
    env_vars["POLYMARKET_WALLET_ADDRESS"] = address
    save_env(env_vars, env_path)

    print()
    print(f"Credentials saved to {env_path}")
    print("The trading bot can now use authenticated CLOB endpoints.")

    # Quick connectivity test
    print()
    print("Testing API connectivity...")
    try:
        l2_client = ClobClient(
            host=CLOB_HOST,
            chain_id=CHAIN_ID,
            key=private_key,
            creds=creds,
            signature_type=sig_type,
        )
        ok = l2_client.get_ok()
        print(f"CLOB health check: {ok}")

        keys = l2_client.get_api_keys()
        print(f"Active API keys: {len(keys) if isinstance(keys, list) else keys}")

        print()
        print("Setup complete! You're ready for live trading.")
    except Exception as e:
        print(f"WARNING: Connectivity test failed: {e}")
        print("Credentials were saved but may need to be re-derived.")


if __name__ == "__main__":
    main()
