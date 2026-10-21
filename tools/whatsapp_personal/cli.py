"""CLI interface for managing personal WhatsApp QR code pairing and AI auto-replies."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from tools.email_automation.ai_engine import AIReplyEngine

WHATSAPP_DIR = Path(__file__).parent


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whatsapp-personal",
        description=(
            "Connect personal WhatsApp via QR code and automate AI replies "
            "(no Meta Business Account needed)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    subparsers.add_parser("status", help="Check if personal WhatsApp is linked")

    # connect (generates QR code)
    subparsers.add_parser("connect", help="Display QR code in terminal to link personal WhatsApp")

    # simulate
    sim_parser = subparsers.add_parser(
        "simulate", help="Test AI response for an incoming WhatsApp message"
    )
    sim_parser.add_argument("phone", type=str, help="Customer phone number (e.g. 919876543210)")
    sim_parser.add_argument("message", type=str, help="Customer incoming message text")

    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()

    if args.command == "status":
        proc = subprocess.run(
            ["node", str(WHATSAPP_DIR / "index.js"), "--status"],
            cwd=str(WHATSAPP_DIR),
        )
        return proc.returncode

    elif args.command == "connect":
        print("\n[*] Starting Personal WhatsApp Web client...")
        print("[*] Generating QR Code. Open WhatsApp on your phone:")
        print("    Settings -> Linked Devices -> Link a Device -> Scan QR Code\n")
        proc = subprocess.run(
            ["node", str(WHATSAPP_DIR / "index.js")],
            cwd=str(WHATSAPP_DIR),
        )
        return proc.returncode

    elif args.command == "simulate":
        print(f"\n[Incoming WhatsApp] From: +{args.phone}")
        print(f'Message: "{args.message}"')
        engine = AIReplyEngine()
        print(f"\n[AI Engine Active Provider: {engine._select_provider().upper()}]")
        reply = engine.generate_reply(
            incoming_message=args.message,
            sender_name=f"Customer +{args.phone[-4:]}",
            channel="whatsapp",
        )
        print("\n=== AI Generated WhatsApp Reply ===")
        print(reply)
        print("===================================\n")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
