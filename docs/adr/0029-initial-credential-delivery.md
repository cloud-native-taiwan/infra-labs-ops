# ADR-0029: Initial credential delivery for tenant accounts

- **Status:** Accepted
- **Date:** 2026-07-03
- **Deciders:** CNTUG ops

## Context

Account automation emails each new tenant an initial password. The welcome
email was CC'd to the admin mailbox, so live credentials accumulated in
infra@; email addresses were validated only as `"@" in value`. Keystone has
no self-service password reset upstream, so some form of out-of-band initial
secret delivery is unavoidable.

## Decision

The credential email goes to the user only. Admins receive a separate,
credential-free account notification whose delivery failure cannot affect
the user-facing send. Recipient addresses pass real syntactic validation
(RFC5322-lite plus length limits, stdlib only). The welcome email instructs
an immediate password change, and the user-facing send is the only part of
the flow under retry, so a transient error cannot re-send the password.

Keystone `[security_compliance] change_password_upon_first_use` makes the
emailed password single-use. It applies to every admin-set password, so all
service accounts must carry `ignore_change_password_upon_first_use`, or the
next kolla password rotation breaks the control plane.

Update 2026-11-09: the setting is live (`kolla/config/keystone.conf`). Every
service user was verified to carry the ignore option. Lockout
(`lockout_failure_attempts`) is also live, and two service users lacked
`ignore_lockout_failure_attempts`; both were fixed. The check and fix are in
[docs/runbooks/keystone-service-user-options.md](../runbooks/keystone-service-user-options.md).

## Alternatives considered

- One-time-secret links: rejected. Extra service to run for a small
  community cloud; the link email is the same trust channel anyway.
- change_password_upon_first_use now: rejected until the service-account
  precondition above is verified.
- Keep admin CC: rejected. A mailbox of live credentials is a single point
  of compromise with no operational benefit over a credential-free notice.

## Consequences

infra@ no longer accumulates passwords. Operators lose the ability to look
up a user's initial password from the CC copy — by design; reset via
`openstack user password set` instead. Forced change on first use is in
effect. Run the service-user runbook after enabling any new Kolla service.

## References

- ADR-0021 (account lifecycle automation)
- 2026-07-02 whole-repo/fleet audit, section 2 P2
