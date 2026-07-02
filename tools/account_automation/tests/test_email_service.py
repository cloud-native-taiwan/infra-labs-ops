from datetime import date

from account_automation.services.email_service import ResendEmailService


WELCOME_SUBJECT = "CNTUG Infra Labs 帳號開通通知"
ADMIN_SUBJECT = "CNTUG Infra Labs 帳號開通通知（管理員副本）"


def test_welcome_not_resent_when_admin_notification_fails(
    make_config, make_row, mocker
) -> None:
    # Regression for the retry-scoping fix: the admin notification is best-effort
    # and lives OUTSIDE the retried unit. A transient error there must not cause
    # the password-bearing welcome email to be re-sent.
    service = ResendEmailService(make_config())
    row = make_row()
    subjects: list[str] = []

    def fake_send(payload: dict) -> None:
        subjects.append(payload["subject"])
        if payload["subject"] == ADMIN_SUBJECT:
            raise ConnectionError("transient")

    mocker.patch(
        "account_automation.services.email_service.resend.Emails.send",
        side_effect=fake_send,
    )
    mocker.patch("time.sleep", lambda _seconds: None)

    # Must not raise despite the admin-copy failure.
    service.send_welcome_email(row, "pw", date(2026, 4, 25))

    assert subjects.count(WELCOME_SUBJECT) == 1
    assert subjects.count(ADMIN_SUBJECT) == 1


def test_welcome_user_send_retries_on_transient_error(
    make_config, make_row, mocker
) -> None:
    # The user-facing send is still retried on transient failures; the admin
    # copy is sent once after it eventually succeeds.
    service = ResendEmailService(make_config())
    row = make_row()
    subjects: list[str] = []
    attempts = {"welcome": 0}

    def fake_send(payload: dict) -> None:
        subjects.append(payload["subject"])
        if payload["subject"] == WELCOME_SUBJECT:
            attempts["welcome"] += 1
            if attempts["welcome"] < 2:
                raise ConnectionError("transient")

    mocker.patch(
        "account_automation.services.email_service.resend.Emails.send",
        side_effect=fake_send,
    )
    mocker.patch("time.sleep", lambda _seconds: None)

    service.send_welcome_email(row, "pw", date(2026, 4, 25))

    assert attempts["welcome"] == 2  # failed once, retried, then succeeded
    assert subjects.count(ADMIN_SUBJECT) == 1