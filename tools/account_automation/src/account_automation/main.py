from __future__ import annotations

import argparse
import fcntl
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace

from account_automation.config import AppConfig, load_config
from account_automation.logging_config import configure_logging
from account_automation.models import RESOURCE_FIELDS, DeletePreview, ResourceItem
from account_automation.orchestrator import run
from account_automation.repositories import GoogleSheetsRepository
from account_automation.services import OpenStackServiceImpl, ResendEmailService


LOGGER = logging.getLogger(__name__)
LOCK_PATH = "/tmp/account-automation.lock"


def main() -> int:
    configure_logging("INFO")
    args = _parse_args()
    return args.func(args)


def _handle_run(args: argparse.Namespace) -> int:
    with open(LOCK_PATH, "w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOGGER.warning("Another account automation process is already running")
            return 0

        config = _load_handler_config(require_all=True, dry_run=args.dry_run)
        repo = GoogleSheetsRepository(config)
        openstack = OpenStackServiceImpl(config)
        email = ResendEmailService(config)

        results = run(config, repo, openstack, email)
        succeeded = sum(1 for result in results if result.success)
        failed = sum(1 for result in results if not result.success)

        LOGGER.info("Processing summary: succeeded=%s failed=%s", succeeded, failed)
        return 1 if failed else 0


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

    parsed_argv = list(sys.argv[1:] if argv is None else argv)
    if not parsed_argv or parsed_argv[0].startswith("-"):
        parsed_argv.insert(0, "run")

    return parser.parse_args(parsed_argv)


if __name__ == "__main__":
    sys.exit(main())
