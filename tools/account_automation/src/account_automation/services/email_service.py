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

HORIZON_URL = "https://openstack.cloudnative.tw"
SKYLINE_URL = "https://console.cloudnative.tw"
DOCS_URL = "https://docs.cloudnative.tw/docs/intro"
TELEGRAM_CHANNEL_URL = "https://t.me/cntug_infra_labs"
SYSTEM_STATS_URL = "https://stats.cloudnative.tw"
UPPTIME_STATUS_URL = "https://status.cloudnative.tw"
ADMIN_EMAIL = "infra@cloudnative.tw"
AUP_URL_ZH = "https://drive.google.com/file/d/1hMHciO1rsXem7EIHiVryiZxeyAq-NwbN/view?usp=sharing"
AUP_URL_EN = "https://drive.google.com/file/d/1lQClZOOOdLFMBPvqCDFFlzPEHXdF_ppb/view?usp=sharing"
DONATION_URL = "https://ocf.neticrm.tw/civicrm/contribute/transact?&id=29"

_FOOTER_HTML = f"""
<hr style="border:none;border-top:1px solid #ddd;margin:16px 0;" />
<p style="font-size:12px;color:#555;">
  <strong>實用連結 / Useful links</strong><br />
  Horizon Dashboard: <a href="{HORIZON_URL}">{HORIZON_URL}</a><br />
  Skyline Console (替代介面): <a href="{SKYLINE_URL}">{SKYLINE_URL}</a><br />
  Getting Started 文件: <a href="{DOCS_URL}">{DOCS_URL}</a><br />
  維護公告 Telegram Channel: <a href="{TELEGRAM_CHANNEL_URL}">{TELEGRAM_CHANNEL_URL}</a><br />
  System Status (Grafana): <a href="{SYSTEM_STATS_URL}">{SYSTEM_STATS_URL}</a><br />
  Upptime Status Page: <a href="{UPPTIME_STATUS_URL}">{UPPTIME_STATUS_URL}</a><br />
  Acceptable Use Policy: <a href="{AUP_URL_ZH}">中文版</a> / <a href="{AUP_URL_EN}">English</a><br />
  支持我們 / Support us (捐款 Donate): <a href="{DONATION_URL}">{DONATION_URL}</a>
</p>
<p style="font-size:12px;color:#888;">
  如有問題請回覆本信至 <a href="mailto:{ADMIN_EMAIL}">{ADMIN_EMAIL}</a>，
  或透過 Telegram Channel 取得最新公告。
</p>
""".strip()


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
                "cc": [ADMIN_EMAIL],
                "reply_to": ADMIN_EMAIL,
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
                "cc": [ADMIN_EMAIL],
                "reply_to": ADMIN_EMAIL,
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
                "reply_to": ADMIN_EMAIL,
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
            <p>歡迎加入 CNTUG Infra Labs！您的帳號已開通，以下為登入資訊：</p>
            <ul>
              <li>Horizon Dashboard：<a href="{HORIZON_URL}">{HORIZON_URL}</a></li>
              <li>Skyline Console (替代介面)：<a href="{SKYLINE_URL}">{SKYLINE_URL}</a></li>
              <li>使用者名稱 (Username)：{username}</li>
              <li>初始密碼 (Password)：{safe_password}</li>
              <li>到期日 (Expiry)：{expiry}</li>
            </ul>
            <p>資源配額 (Resource quota)：</p>
            <ul>
              <li>vCPUs：{_format_quota_value(row.quota.vcpus)}</li>
              <li>RAM：{_format_quota_value(row.quota.ram_gb, suffix=" GB")}</li>
              <li>Storage：{_format_quota_value(row.quota.storage_gb, suffix=" GB")}</li>
              <li>額外資源 (Extras)：{extras}</li>
            </ul>
            <p>新手上路 (Getting started) 文件：<a href="{DOCS_URL}">{DOCS_URL}</a> — 內含登入、建立 SSH key、啟動 instance 等操作說明，建議首次使用前先閱讀。</p>
            <p><strong>初次登入須知：</strong></p>
            <ul>
              <li>請於首次登入後立即變更密碼並妥善保管。</li>
              <li>請確認可正常建立 instance、volume 與 network，若有異常請於 7 日內回報。</li>
              <li>到期前 7 天系統會自動寄出續期提醒；如需提前續期，請聯絡管理員。</li>
              <li>本平台為社群實驗環境，請勿存放正式營運或敏感資料；維護時段以 Telegram 公告為準。</li>
              <li>請遵守申請時同意之使用規範 (Acceptable Use Policy)：禁止挖礦、違法行為、攻擊性流量等；違反者帳號將被立即停權。AUP 全文：<a href="{AUP_URL_ZH}">中文版</a> / <a href="{AUP_URL_EN}">English</a>。</li>
            </ul>
            {_FOOTER_HTML}
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
            <p>提醒您：CNTUG Infra Labs 帳號 <strong>{username}</strong> 將於 <strong>{expiry}</strong> 到期。</p>
            <p><strong>若仍有使用需求</strong>：請於到期前聯絡管理員提出續期申請，以避免帳號與資源被自動回收。</p>
            <p><strong>若無續期需求</strong>：請於到期前自行備份所需資料 (instances、volumes、images、object storage)。到期後帳號與相關資源將依流程停用並刪除，刪除後資料將無法復原。</p>
            <p>登入備份用介面：Horizon <a href="{HORIZON_URL}">{HORIZON_URL}</a> 或 Skyline <a href="{SKYLINE_URL}">{SKYLINE_URL}</a></p>
            <p>如需續期或有其他問題，請直接回覆本信至 <a href="mailto:{ADMIN_EMAIL}">{ADMIN_EMAIL}</a>。</p>
            {_FOOTER_HTML}
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
