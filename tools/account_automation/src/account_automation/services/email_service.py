# pragma: exclude file from coverage
from __future__ import annotations

import html
import logging
from datetime import date
from typing import Protocol

import resend

from account_automation.config import AppConfig
from account_automation.models import SheetRow
from account_automation.retry import STANDARD_RETRY


LOGGER = logging.getLogger(__name__)
REDACTED_PASSWORD = "[REDACTED]"


class EmailService(Protocol):
    def send_welcome_email(self, row: SheetRow, password: str, expiry_date: date) -> None:
        ...

    def send_expiry_warning(self, row: SheetRow, expiry_date: date) -> None:
        ...


class ResendEmailService:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        resend.api_key = config.resend_api_key

    @STANDARD_RETRY
    def send_welcome_email(self, row: SheetRow, password: str, expiry_date: date) -> None:
        if self._config.dry_run:
            LOGGER.info(
                "Dry run enabled; skipping welcome email for username=%s password=%s",
                row.username,
                REDACTED_PASSWORD,
            )
            return

        LOGGER.info(
            "Sending welcome email to email=%s for username=%s password=%s",
            row.email,
            row.username,
            REDACTED_PASSWORD,
        )
        resend.Emails.send(
            {
                "from": self._config.resend_from_email,
                "to": row.email,
                "subject": "CNTUG Infra Labs 帳號開通通知",
                "html": self._build_welcome_html(row, password, expiry_date),
            }
        )

    @STANDARD_RETRY
    def send_expiry_warning(self, row: SheetRow, expiry_date: date) -> None:
        if self._config.dry_run:
            LOGGER.info(
                "Dry run enabled; skipping expiry warning email for username=%s",
                row.username,
            )
            return

        LOGGER.info(
            "Sending expiry warning email to email=%s for username=%s",
            row.email,
            row.username,
        )
        resend.Emails.send(
            {
                "from": self._config.resend_from_email,
                "to": row.email,
                "subject": "CNTUG Infra Labs 帳號即將到期通知",
                "html": self._build_expiry_warning_html(row, expiry_date),
            }
        )

    def _build_welcome_html(self, row: SheetRow, password: str, expiry_date: date) -> str:
        name = html.escape(row.name)
        username = html.escape(row.username)
        safe_password = html.escape(password)
        expiry = html.escape(expiry_date.isoformat())
        extras = _format_extras(row)

        return f"""
        <html>
          <body>
            <p>{name} 您好，</p>
            <p>您的 CNTUG Infra Labs 帳號已開通，以下為登入資訊：</p>
            <ul>
              <li>使用者名稱：{username}</li>
              <li>密碼：{safe_password}</li>
              <li>到期日：{expiry}</li>
            </ul>
            <p>資源配額如下：</p>
            <ul>
              <li>vCPUs：{row.quota.vcpus}</li>
              <li>RAM：{row.quota.ram_gb} GB</li>
              <li>Storage：{row.quota.storage_gb} GB</li>
              <li>額外資源：{extras}</li>
            </ul>
            <p>請使用上述帳號登入 CNTUG Infra Labs 的 OpenStack Horizon 或 CLI。</p>
            <p>首次登入後，請盡快確認環境可正常使用並妥善保管密碼。</p>
          </body>
        </html>
        """.strip()

    def _build_expiry_warning_html(self, row: SheetRow, expiry_date: date) -> str:
        name = html.escape(row.name)
        username = html.escape(row.username)
        expiry = html.escape(expiry_date.isoformat())

        return f"""
        <html>
          <body>
            <p>{name} 您好，</p>
            <p>您的 CNTUG Infra Labs 帳號（{username}）將於 {expiry} 到期。</p>
            <p>若您仍有使用需求，請於到期前聯絡管理員提出續期申請。</p>
            <p>若無續期需求，帳號與相關資源將依流程停用或刪除。</p>
          </body>
        </html>
        """.strip()


def _format_extras(row: SheetRow) -> str:
    if not row.quota.extras:
        return "無"
    return html.escape(", ".join(sorted(row.quota.extras)))
