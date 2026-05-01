# pragma: exclude file from coverage
from __future__ import annotations

import html
import logging
from datetime import date
from typing import Protocol

import resend

from account_automation.config import AppConfig
from account_automation.models import RESOURCE_FIELDS, DeletePreview, ResourceItem, SheetRow
from account_automation.retry import STANDARD_RETRY


LOGGER = logging.getLogger(__name__)
REDACTED_PASSWORD = "[REDACTED]"


class EmailService(Protocol):
    def send_welcome_email(self, row: SheetRow, password: str, expiry_date: date) -> None:
        ...

    def send_expiry_warning(self, row: SheetRow, expiry_date: date) -> None:
        ...

    def send_delete_preview_email(self, row: "SheetRow", preview: "DeletePreview", admin_email: str) -> None:
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

    @STANDARD_RETRY
    def send_delete_preview_email(
        self, row: SheetRow, preview: DeletePreview, admin_email: str
    ) -> None:
        if self._config.dry_run:
            LOGGER.info(
                "Dry run enabled; skipping delete preview email for username=%s",
                row.username,
            )
            return

        recipients = [addr.strip() for addr in admin_email.split(",") if addr.strip()]

        LOGGER.info(
            "Sending delete preview email for username=%s to %s",
            row.username,
            recipients,
        )
        resend.Emails.send(
            {
                "from": self._config.resend_from_email,
                "to": recipients,
                "subject": "CNTUG Infra Labs 帳號刪除預覽通知",
                "html": self._build_delete_preview_html(row, preview),
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
              <li>vCPUs：{_format_quota_value(row.quota.vcpus)}</li>
              <li>RAM：{_format_quota_value(row.quota.ram_gb, suffix=" GB")}</li>
              <li>Storage：{_format_quota_value(row.quota.storage_gb, suffix=" GB")}</li>
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

    def _build_delete_preview_html(self, row: SheetRow, preview: DeletePreview) -> str:
        name = html.escape(row.name)
        username = html.escape(row.username)
        email = html.escape(row.email)
        group_section = _build_resource_section("Group Members", preview.group_members)
        resource_sections = "\n".join(
            _build_resource_section(label, getattr(preview, attr))
            for attr, label in RESOURCE_FIELDS
        )

        user_status = "否"
        if preview.user_found and preview.user_has_other_roles:
            user_status = "是 (有其他專案角色，將保留使用者帳號)"
        elif preview.user_found:
            user_status = "是"

        return f"""
        <html>
          <body>
            <p>管理員您好，</p>
            <p>以下帳號已進入刪除預覽階段：</p>
            <ul>
              <li>使用者名稱：{username}</li>
              <li>姓名：{name}</li>
              <li>Email：{email}</li>
              <li>OpenStack 使用者存在：{user_status}</li>
              <li>OpenStack 專案存在：{"是" if preview.project_found else "否"}</li>
              <li>OpenStack Group: {"是" if preview.group_found else "否"}</li>
            </ul>
            <p>Group membership:</p>
            <ul>
              {group_section}
            </ul>
            <p>專案資源清單：</p>
            <ul>
              {resource_sections}
            </ul>
            <p>若無異議，請於 Google Sheet 中將狀態設為 ready_to_delete 以確認刪除。</p>
          </body>
        </html>
        """.strip()


def _format_extras(row: SheetRow) -> str:
    if not row.quota.extras:
        return "無"
    return html.escape(", ".join(sorted(row.quota.extras)))


def _format_quota_value(value: int | None, *, suffix: str = "") -> str:
    if value is None:
        return "預設"
    return f"{value}{suffix}"


def _build_resource_section(label: str, items: tuple[ResourceItem, ...]) -> str:
    count = len(items)
    if count == 0:
        return f"<li>{html.escape(label)}: 0</li>"
    rows = "".join(
        f"<li>{html.escape(item.name or '(unnamed)')} [{html.escape(item.id)}]"
        + (f" ({html.escape(item.extra)})" if item.extra else "")
        + "</li>"
        for item in items
    )
    return (
        f"<li><details><summary>{html.escape(label)}: {count}</summary>"
        f"<ul>{rows}</ul></details></li>"
    )
