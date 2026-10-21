"""Command line interface for email automation and suppression management."""

from __future__ import annotations

import argparse
import sys

from tools.email_automation.config import EmailConfig
from tools.email_automation.db import get_session, init_db
from tools.email_automation.orchestrator import EmailOrchestrationEngine
from tools.email_automation.reader import EmailReader
from tools.email_automation.suppression_service import (
    block_email,
    get_suppression,
    is_email_blocked,
    list_suppressions,
    unblock_email,
)
from tools.email_automation.validator import verify_email


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="email-automation",
        description=(
            "Connects to email, reads messages, sends automated replies, "
            "and blocks invalid/bounced emails."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run command
    run_parser = subparsers.add_parser("run", help="Run email processing cycle")
    run_parser.add_argument(
        "--dry-run", action="store_true", help="Stage replies without sending SMTP"
    )
    run_parser.add_argument("--limit", type=int, default=20, help="Max emails to process")
    run_parser.add_argument("--skip-domain-check", action="store_true", help="Skip DNS MX check")

    # read command
    read_parser = subparsers.add_parser("read", help="Fetch and print unread emails")
    read_parser.add_argument("--limit", type=int, default=10, help="Max emails to fetch")
    read_parser.add_argument(
        "--mark-as-read", action="store_true", help="Mark fetched emails as read"
    )

    # block command
    block_parser = subparsers.add_parser("block", help="Block a wrong or bounced email in database")
    block_parser.add_argument("email", type=str, help="Email address to block")
    block_parser.add_argument(
        "--reason",
        type=str,
        default="manual",
        choices=["bounced", "invalid_syntax", "mailbox_not_found", "domain_not_found", "manual"],
        help="Reason for suppression",
    )
    block_parser.add_argument("--detail", type=str, default="", help="Diagnostic or error details")

    # check command
    check_parser = subparsers.add_parser("check", help="Check if an email is blocked in database")
    check_parser.add_argument("email", type=str, help="Email address to check")

    # list-blocked command
    list_parser = subparsers.add_parser("list-blocked", help="List blocked/suppressed emails")
    list_parser.add_argument("--limit", type=int, default=50, help="Max rows to return")

    # unblock command
    unblock_parser = subparsers.add_parser("unblock", help="Unblock an email from database")
    unblock_parser.add_argument("email", type=str, help="Email address to unblock")
    unblock_parser.add_argument(
        "--force", action="store_true", help="Force unblocking even if bounced"
    )

    # verify command
    verify_parser = subparsers.add_parser("verify", help="Verify syntax and MX records of an email")
    verify_parser.add_argument("email", type=str, help="Email address to verify")

    return parser


def run_cli(args: argparse.Namespace) -> int:
    config = EmailConfig.from_env()
    if getattr(args, "dry_run", False):
        config.dry_run = True

    init_db()

    if args.command == "run":
        engine = EmailOrchestrationEngine(config)
        print(f"[*] Starting email processing cycle (dry_run={config.dry_run})...")
        result = engine.process_incoming_emails(
            limit=args.limit,
            validate_domains=not args.skip_domain_check,
            mark_as_read=True,
        )
        print("\n=== Email Processing Summary ===")
        print(f"Emails Read/Processed : {result.emails_processed}")
        print(f"Bounces Detected & Blocked : {len(result.bounces_detected)}")
        for b in result.bounces_detected:
            print(f"  - Blocked Bounced Recipient: {b['failed_recipient']} ({b['diagnostic']})")
        print(f"Wrong Emails Blocked  : {len(result.wrong_emails_blocked)}")
        for w in result.wrong_emails_blocked:
            print(f"  - Blocked Invalid Email: {w['email']} (reason: {w['reason']})")
        print(f"Automated Replies Sent: {len(result.replies_sent)}")
        for r in result.replies_sent:
            print(f"  - Replied to: {r['recipient']} | Subject: {r['subject']}")
        print(f"Replies Skipped       : {len(result.replies_skipped)}")
        for s in result.replies_skipped:
            print(f"  - Skipped: {s['sender']} (reason: {s['reason']})")
        return 0

    elif args.command == "read":
        reader = EmailReader(config)
        try:
            print(f"[*] Connecting to IMAP {config.imap_host}:{config.imap_port}...")
            reader.connect()
            messages = reader.fetch_unseen_emails(limit=args.limit, mark_as_read=args.mark_as_read)
            print(f"Found {len(messages)} unread messages:\n")
            for m in messages:
                print(f"UID: {m.uid}")
                print(f"From: {m.from_name} <{m.from_email}>")
                print(f"Subject: {m.subject}")
                print(f"Date: {m.date.isoformat()}")
                print(f"Is Bounce: {m.is_bounce}")
                print(f"Is Auto-Submitted: {m.is_auto_submitted}")
                snippet = m.body_text[:150].replace("\n", " ") if m.body_text else "(empty)"
                print(f"Snippet: {snippet}...")
                print("-" * 50)
            return 0
        finally:
            reader.close()

    elif args.command == "block":
        with get_session() as session:
            row = block_email(
                session,
                config.organization_id,
                args.email,
                reason=args.reason,
                diagnostic_detail=args.detail,
            )
            print(f"[+] Successfully blocked {args.email} in database:")
            print(f"    ID: {row.id}")
            print(f"    Reason: {row.reason}")
            print(f"    Masked: {row.email_masked}")
            print(f"    Detail: {row.diagnostic_detail}")
        return 0

    elif args.command == "check":
        with get_session() as session:
            blocked = is_email_blocked(session, config.organization_id, args.email)
            if blocked:
                row = get_suppression(session, config.organization_id, args.email)
                print(f"[!] BLOCKED: {args.email} is in suppression database.")
                if row:
                    print(f"    Reason: {row.reason}")
                    print(f"    Created At: {row.created_at}")
                    print(f"    Detail: {row.diagnostic_detail}")
            else:
                print(f"[-] NOT BLOCKED: {args.email} is NOT in suppression database.")
        return 0

    elif args.command == "list-blocked":
        with get_session() as session:
            items = list_suppressions(session, config.organization_id, limit=args.limit)
            print(f"Found {len(items)} blocked emails:\n")
            for item in items:
                print(f"• {item.email} (reason: {item.reason}, blocked at: {item.created_at})")
                if item.diagnostic_detail:
                    print(f"  Detail: {item.diagnostic_detail}")
        return 0

    elif args.command == "unblock":
        with get_session() as session:
            success = unblock_email(session, config.organization_id, args.email, force=args.force)
            if success:
                print(f"[+] Successfully unblocked {args.email}.")
            else:
                print(
                    f"[-] Could not unblock {args.email}. "
                    "(May be permanent bounce; use --force to override)"
                )
        return 0

    elif args.command == "verify":
        valid, reason = verify_email(args.email, check_dns=True)
        if valid:
            print(f"[+] {args.email} is VALID (syntax ok, domain MX ok).")
        else:
            print(f"[!] {args.email} is INVALID: {reason}")
        return 0

    return 1


def main() -> None:
    parser = make_parser()
    args = parser.parse_args()
    try:
        sys.exit(run_cli(args))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
