"""Report orchestrator -- wires CloudKitty, OpenStack, and Resend services."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from infra_labs_common.persistence import atomic_write_json

from usage_reports.config import AppConfig
from usage_reports.models import (
    ProjectUsage,
    ReportData,
    ReportPeriod,
    ResourceCost,
    ResourceIndex,
)
from usage_reports.services.cloudkitty_service import (
    CloudKittyService,
    CloudKittyServiceImpl,
)
from usage_reports.services.email_service import EmailService, ResendEmailService
from usage_reports.services.openstack_service import (
    OpenStackService,
    OpenStackServiceImpl,
)


LOGGER = logging.getLogger(__name__)


def run_report(
    config: AppConfig,
    period: ReportPeriod,
    force: bool = False,
    only_project: str | None = None,
    only_email: str | None = None,
    record_deliveries: bool = False,
) -> int:
    """Default entry point: instantiate real services and run."""
    cloudkitty = CloudKittyServiceImpl(config)
    openstack = OpenStackServiceImpl(config)
    email = ResendEmailService(config)
    return run(
        config=config,
        period=period,
        force=force,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
        only_project=only_project,
        only_email=only_email,
        record_deliveries=record_deliveries,
    )


def run(
    *,
    config: AppConfig,
    period: ReportPeriod,
    force: bool,
    cloudkitty: CloudKittyService,
    openstack: OpenStackService,
    email: EmailService,
    only_project: str | None = None,
    only_email: str | None = None,
    record_deliveries: bool = False,
) -> int:
    """Run the report flow with injected services (for tests).

    only_project / only_email scope the run to a single project_id and/or a
    single recipient email -- for testing one user's report (dry-run or real)
    without touching every other project member.

    A scoped run does NOT record deliveries to the manifest by default: a real
    test send would otherwise poison the production manifest and cause the next
    scheduled run to silently skip that recipient (potentially leaving them with
    an incomplete report if the test bypassed the freshness gate with --force).
    Pass record_deliveries=True to opt back in to manifest writes.
    """
    if not force and not _data_is_fresh(cloudkitty, openstack, period):
        LOGGER.error(
            "CloudKitty has not finished processing %s; aborting. Pass --force to override.",
            period.label,
        )
        return 2

    # Push --only-project down to CloudKitty as a server-side tenant filter so
    # a scoped run fetches only that project's rows instead of the whole fleet.
    projects = cloudkitty.get_summary(period, project_id=only_project)

    # Check for a mistyped --only-project before the empty-summary check so it
    # reports exit 2 even in a month with no billable usage (otherwise the
    # generic "no usage -> 0" path would mask the typo).
    if only_project and not projects:
        LOGGER.error(
            "--only-project %s matched no billable project for %s",
            only_project,
            period.label,
        )
        return 2

    if not projects:
        LOGGER.info("No billable usage found for %s", period.label)
        return 0

    resource_index = _build_resource_index(openstack)

    scoped = bool(only_project or only_email)
    persist_manifest = record_deliveries or not scoped
    if scoped and not persist_manifest and not config.dry_run:
        LOGGER.warning(
            "Scoped run (only_project/only_email): deliveries will NOT be "
            "recorded to the manifest, so a future scheduled run can still "
            "send to these recipients. Pass --record-deliveries to override."
        )

    manifest = _load_manifest(config.delivery_manifest_path)
    failed_projects = 0

    for project in projects:
        try:
            project_failed = _process_project(
                config=config,
                period=period,
                project=project,
                manifest=manifest,
                force=force,
                openstack=openstack,
                email=email,
                only_email=only_email,
                persist_manifest=persist_manifest,
                resource_index=resource_index,
            )
        except Exception as exc:
            failed_projects += 1
            LOGGER.error(
                "Project failed project_id=%s err=%s; continuing",
                project.project_id,
                exc,
            )
            continue

        if project_failed:
            failed_projects += 1

    return 1 if failed_projects > 0 else 0


def _build_resource_index(openstack: OpenStackService) -> ResourceIndex | None:
    """Build the once-per-run server index, or None if the bulk listing
    fails -- enrichment then falls back to per-resource lookups."""
    try:
        return openstack.build_resource_index()
    except Exception as exc:
        LOGGER.warning(
            "Could not build OpenStack resource index err=%s; falling back to "
            "per-resource lookups",
            exc,
        )
        return None


def _data_is_fresh(
    cloudkitty: CloudKittyService,
    openstack: OpenStackService,
    period: ReportPeriod,
) -> bool:
    try:
        scopes = cloudkitty.get_scope_last_processed()
    except Exception as exc:
        LOGGER.warning("Could not check CloudKitty freshness: %s", exc)
        return False
    if not scopes:
        return False

    # Every scope must be processed past the period end. CloudKitty rates
    # scopes asynchronously; if even one lags, that project's report would
    # under-bill the user, so we wait for the slowest.
    #
    # Exception: a deleted project. CloudKitty's fetcher stops discovering a
    # deleted project's scope, so its last_processed freezes below period_end
    # forever and would wedge the gate indefinitely. A deleted project has no
    # recipients to under-bill, so a lagging scope whose project is provably
    # gone (404) is skipped. We only confirm existence for a *lagging* scope,
    # and only skip on a definitive 404 -- a transient lookup error raises and
    # is treated as "still live" so we block rather than risk under-billing.
    for scope_id, ts in scopes.items():
        if ts is not None and ts >= period.end_utc:
            continue
        if not _project_is_gone(openstack, scope_id):
            LOGGER.info(
                "Scope %s is lagging (last_processed=%s, period_end=%s)",
                scope_id,
                ts,
                period.end_utc,
            )
            return False
        # WARNING, not INFO: excusing a scope from the gate is billing-
        # relevant, and it also fires if scope_id stops matching a project id
        # (fetcher/key change), so surface it for monitoring.
        LOGGER.warning(
            "Ignoring lagging scope %s (last_processed=%s): project no longer "
            "exists, treating as deleted",
            scope_id,
            ts,
        )
    return True


def _project_is_gone(openstack: OpenStackService, project_id: str) -> bool:
    """True only if the project is provably deleted (404).

    Any error determining existence is treated as 'not gone' so the caller
    keeps the scope in the gate -- never excuse a lagging scope on a blip."""
    try:
        return not openstack.project_exists(project_id)
    except Exception as exc:
        LOGGER.warning(
            "Could not confirm project %s still exists: %s; keeping its scope "
            "in the freshness gate",
            project_id,
            exc,
        )
        return False


def _process_project(
    *,
    config: AppConfig,
    period: ReportPeriod,
    project: ProjectUsage,
    manifest: dict[str, str],
    force: bool,
    openstack: OpenStackService,
    email: EmailService,
    only_email: str | None = None,
    persist_manifest: bool = True,
    resource_index: ResourceIndex | None = None,
) -> bool:
    """Send the report for one project. Returns True if delivery failed for at
    least one recipient, or if a recipient could not be resolved at all (the
    caller treats either as a project-level failure for exit-code purposes)."""
    project_name = openstack.get_project_name(project.project_id)
    enriched_project = _enrich_project(project, project_name, openstack, resource_index)

    if enriched_project.total_cost == 0:
        LOGGER.info(
            "Project %s has zero rated usage; skipping email", enriched_project.project_id
        )
        return False

    membership = openstack.list_project_members(project.project_id)
    members = membership.members
    # A recipient we could not resolve (transient Keystone error past retries)
    # is a delivery gap, not a silent drop: it fails the project so the run
    # exits non-zero and an operator investigates.
    unresolved = membership.unresolved_user_ids
    if unresolved:
        LOGGER.error(
            "Project %s: %d recipient(s) unresolved after retries user_ids=%s; "
            "marking run failed",
            enriched_project.project_id,
            len(unresolved),
            list(unresolved),
        )

    if only_email:
        target = only_email.strip().lower()
        filtered = tuple(m for m in members if m.email.strip().lower() == target)
        if not filtered:
            LOGGER.warning(
                "--only-email %s matched no member of project %s; skipping",
                only_email,
                enriched_project.project_id,
            )
            return bool(unresolved)
        members = filtered
    if not members:
        LOGGER.info(
            "Project %s has no members with email; skipping",
            enriched_project.project_id,
        )
        return bool(unresolved)

    report = ReportData(period=period, project=enriched_project)
    delivered = 0
    failed = 0
    eligible = 0
    for member in members:
        key = _manifest_key(period, project.project_id, member.email)
        if not force and key in manifest:
            LOGGER.info(
                "Already delivered key=%s on %s; skipping (use --force to resend)",
                key,
                manifest[key],
            )
            continue
        eligible += 1
        try:
            email.send_cost_report(report, member)
        except Exception as exc:
            failed += 1
            LOGGER.error(
                "Email send failed project=%s recipient=%s err=%s",
                project.project_id,
                member.email,
                exc,
            )
            continue
        delivered += 1
        if not config.dry_run and persist_manifest:
            manifest[key] = datetime.now(timezone.utc).isoformat()
            # Persist after every successful send so a mid-run crash
            # does not cause the next run to re-deliver this email.
            try:
                _save_manifest(config.delivery_manifest_path, manifest)
            except Exception as exc:
                # The email WAS sent but this persist failed. Do not abort the
                # remaining members (the generic project handler would hide this);
                # count it as a failure and flag the duplicate risk distinctly.
                # Keep the key in the in-memory manifest: a later member's
                # successful persist then records this recipient durably too. Only
                # if every subsequent persist also fails (e.g. the last member, or
                # a persistent disk fault) does the next run re-deliver this one.
                failed += 1
                LOGGER.error(
                    "Delivered but manifest persist failed project=%s recipient=%s "
                    "err=%s; recipient may be re-emailed on the next run",
                    project.project_id,
                    member.email,
                    exc,
                )

    LOGGER.info(
        "Project complete project=%s eligible=%s delivered=%s failed=%s total_cost=%s",
        project.project_id,
        eligible,
        delivered,
        failed,
        enriched_project.total_cost,
    )
    # Any recipient failure (send error or an unresolved lookup) on this project
    # counts as a project failure so the cron run exits non-zero. Other
    # recipients still receive their emails because we caught per-recipient
    # exceptions above.
    return failed > 0 or bool(unresolved)


def _enrich_project(
    project: ProjectUsage,
    project_name: str,
    openstack: OpenStackService,
    resource_index: ResourceIndex | None = None,
) -> ProjectUsage:
    enriched_resources: list[ResourceCost] = []
    for resource in project.resources:
        try:
            enriched_resources.append(openstack.enrich_resource(resource, resource_index))
        except Exception as exc:
            LOGGER.warning(
                "Resource enrichment failed uuid=%s err=%s; using original",
                resource.resource_id,
                exc,
            )
            enriched_resources.append(resource)
    return ProjectUsage(
        project_id=project.project_id,
        project_name=project_name,
        resources=tuple(enriched_resources),
    )


def _manifest_key(period: ReportPeriod, project_id: str, email_addr: str) -> str:
    return f"{period.label}/{project_id}/{email_addr}"


def _load_manifest(path: str) -> dict[str, str]:
    """Load the delivery manifest, refusing to silently treat a corrupt
    file as 'no deliveries yet' -- that would cause duplicate sends."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Delivery manifest at {path} is corrupt ({exc}); refusing to "
            "continue. Inspect/repair the file manually -- treating a corrupt "
            "manifest as empty would re-email every recipient."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Cannot read delivery manifest at {path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Delivery manifest at {path} is not a JSON object; refusing to continue."
        )
    return {str(k): str(v) for k, v in raw.items()}


def _save_manifest(path: str, manifest: dict[str, str]) -> None:
    """Write the manifest atomically (tmp file + rename + fsync) so a crash
    mid write cannot leave a half-written JSON behind.

    Raises on write failure: silently swallowing here would let the
    caller believe the send is durably recorded when it isn't, causing
    a duplicate email on the next run.
    """
    atomic_write_json(path, manifest, tmp_prefix=".manifest-")
