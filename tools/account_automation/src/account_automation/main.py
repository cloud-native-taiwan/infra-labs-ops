from __future__ import annotations

import argparse
import fcntl
import logging
import sys
from dataclasses import replace

from account_automation.config import load_config
from account_automation.logging_config import configure_logging
from account_automation.orchestrator import run
from account_automation.repositories import GoogleSheetsRepository
from account_automation.services import OpenStackServiceImpl, ResendEmailService


LOGGER = logging.getLogger(__name__)
LOCK_PATH = "/tmp/account-automation.lock"


def main() -> int:
    configure_logging("INFO")
    args = _parse_args()

    with open(LOCK_PATH, "w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOGGER.warning("Another account automation process is already running")
            return 0

        config = load_config()
        configure_logging(config.log_level)

        if args.dry_run:
            config = replace(config, dry_run=True)

        repo = GoogleSheetsRepository(config)
        openstack = OpenStackServiceImpl(config)
        email = ResendEmailService(config)

        results = run(config, repo, openstack, email)
        succeeded = sum(1 for result in results if result.success)
        failed = sum(1 for result in results if not result.success)

        LOGGER.info("Processing summary: succeeded=%s failed=%s", succeeded, failed)
        return 1 if failed else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
