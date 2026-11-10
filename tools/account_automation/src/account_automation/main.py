from __future__ import annotations

import argparse
import fcntl
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import replace

import openstack

from account_automation.backfill import backfill_project_tags
from account_automation.config import AppConfig, load_config
from account_automation.logging_config import (
    configure_logging,
    describe_exception,
    format_traceback,
)
from account_automation.models import RESOURCE_FIELDS, DeletePreview, ResourceItem
from account_automation.orchestrator import run
from account_automation.repositories import GoogleSheetsRepository
from account_automation.services import OpenStackServiceImpl, ResendEmailService
from account_automation.services.keystone_accounts import UnmanagedAccountError


LOGGER = logging.getLogger(__name__)
LOCK_PATH = "/tmp/account-automation.lock"

# `run` exit codes (sysexits.h values where one fits; 2 is argparse's).
EXIT_OK = 0
EXIT_FAILED = 1  # a row failed to process, or the run aborted
EXIT_LOCK_HELD = 75  # EX_TEMPFAIL: another run holds the lock; nothing was done


def main() -> int:
    configure_logging("INFO")
    args = _parse_args()
    try:
        return args.func(args)
    except UnmanagedAccountError as exc:
        print(exc, file=sys.stderr)
        return 1


def _handle_run(args: argparse.Namespace) -> int:
    # O_NOFOLLOW refuses to open the lock path if it is a symlink, so a
    # pre-planted /tmp symlink cannot redirect (or truncate) an arbitrary file.
    # No O_TRUNC: the lock's contents are irrelevant; flock() is what matters.
    lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # flock is released by the kernel when its holder exits, so a held
            # lock means a live run (stale only if that process hangs). Not
            # success: the skipped pass must not exit 0 or count as a success.
            LOGGER.warning(
                "Another account automation run holds %s; skipping this run", LOCK_PATH
            )
            return EXIT_LOCK_HELD

        try:
            return _run_pass(args)
        except Exception as exc:
            # Log through the message text, not exc_info, so the redaction
            # filter sees the exception text and traceback.
            LOGGER.error("Run aborted: %s\n%s", describe_exception(exc), format_traceback(exc))
            return EXIT_FAILED


def _run_pass(args: argparse.Namespace) -> int:
    config = _load_handler_config(require_all=True, dry_run=args.dry_run)
    repo = GoogleSheetsRepository(config)
    openstack = OpenStackServiceImpl(config)
    email = ResendEmailService(config)

    results = run(config, repo, openstack, email)
    succeeded = sum(1 for result in results if result.success)
    failed = sum(1 for result in results if not result.success)

    LOGGER.info("Processing summary: succeeded=%s failed=%s", succeeded, failed)
    return EXIT_FAILED if failed else EXIT_OK


def _handle_delete(args: argparse.Namespace) -> int:
    config = _load_handler_config(require_all=False, dry_run=args.dry_run)
    openstack = OpenStackServiceImpl(config)

    if not args.force:
        if not sys.stdin.isatty():
            print(
                "Refusing deletion without --force when stdin is not a TTY.",
                file=sys.stderr,
            )
            return 1

        preview = openstack.preview_delete(args.username)
        _print_delete_preview(preview)
        confirmation = input("Proceed with deletion? [y/N] ").strip().lower()
        if confirmation != "y":
            LOGGER.info("Deletion cancelled for username=%s", args.username)
            return 0

    openstack.log_project_resources(args.username)
    LOGGER.info("Deleting OpenStack user and project for username=%s", args.username)
    openstack.delete_user_and_project(args.username)
    LOGGER.info("Finished deleting OpenStack user and project for username=%s", args.username)
    return 0


def _handle_preview(args: argparse.Namespace) -> int:
    config = _load_handler_config(require_all=False, dry_run=False)
    openstack = OpenStackServiceImpl(config)
    preview = openstack.preview_delete(args.username)
    _print_delete_preview(preview)
    return 0


def _handle_backfill_tags(args: argparse.Namespace) -> int:
    # Tagging makes a project deletable by the tool, and the fingerprint cannot
    # rule out a hand-made project, so --apply only acts on reviewed usernames.
    if args.apply and not args.usernames:
        print(
            "--apply needs the usernames to tag; review the dry run and pass the "
            "would-tag usernames you have confirmed.",
            file=sys.stderr,
        )
        return 2
    config = _load_handler_config(require_all=True, dry_run=False)
    rows = GoogleSheetsRepository(config).read_all_rows()
    conn = openstack.connect(cloud=config.openstack_cloud)
    outcomes = backfill_project_tags(
        conn,
        config.openstack_domain_id,
        rows,
        apply=args.apply,
        only=frozenset(args.usernames) or None,
    )
    for outcome in outcomes:
        reason = f" ({outcome.reason})" if outcome.reason else ""
        print(f"{outcome.username}: {outcome.action}{reason}")
    if not args.apply:
        print("Dry run: nothing was tagged. Re-run with --apply <username>... to tag.")
    return 0


def _load_handler_config(*, require_all: bool, dry_run: bool) -> AppConfig:
    config = load_config(require_all=require_all)
    configure_logging(config.log_level)
    if dry_run:
        return replace(config, dry_run=True)
    return config


def _print_delete_preview(preview: DeletePreview) -> None:
    print(f"Username: {preview.username}")
    print(f"User found: {_format_bool(preview.user_found)}")
    if preview.user_found and preview.user_has_other_roles:
        print("  User has roles on other projects -- will be RETAINED (only target project roles removed)")
    elif preview.user_found:
        print("  User has no other project roles -- will be DELETED")
    print(f"Project found: {_format_bool(preview.project_found)}")
    print(f"Group found: {_format_bool(preview.group_found)}")
    if preview.group_members:
        print(f"Group Members: {len(preview.group_members)}")
        for member in preview.group_members:
            print(f"  - {_format_resource_item(member)}")
    for attr, label in RESOURCE_FIELDS:
        items: tuple[ResourceItem, ...] = getattr(preview, attr)
        print(f"{label}: {len(items)}")
        for item in items:
            print(f"  - {_format_resource_item(item)}")


def _format_resource_item(item: ResourceItem) -> str:
    extra = f" ({item.extra})" if item.extra else ""
    return f"{item.name or '(unnamed)'} [{item.id}]{extra}"


def _format_bool(value: bool) -> str:
    return "yes" if value else "no"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(func=_handle_run)

    delete_parser = subparsers.add_parser("delete")
    delete_parser.add_argument("username")
    delete_parser.add_argument("--dry-run", action="store_true")
    delete_parser.add_argument("--force", action="store_true")
    delete_parser.set_defaults(func=_handle_delete)

    preview_parser = subparsers.add_parser("preview")
    preview_parser.add_argument("username")
    preview_parser.set_defaults(func=_handle_preview)

    backfill_parser = subparsers.add_parser(
        "backfill-tags",
        help="Tag pre-existing projects this tool provisioned (dry run unless --apply)",
    )
    backfill_parser.add_argument("usernames", nargs="*")
    backfill_parser.add_argument("--apply", action="store_true")
    backfill_parser.set_defaults(func=_handle_backfill_tags)

    parsed_argv = list(sys.argv[1:] if argv is None else argv)
    if not parsed_argv or parsed_argv[0].startswith("-"):
        parsed_argv.insert(0, "run")

    return parser.parse_args(parsed_argv)


if __name__ == "__main__":
    sys.exit(main())
