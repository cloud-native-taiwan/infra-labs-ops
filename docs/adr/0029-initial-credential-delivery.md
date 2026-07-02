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

Keystone `[security_compliance] change_password_upon_first_use`, which would
make the emailed password single-use, is recommended but deferred: it applies
to every admin-set password, so all service accounts must carry
`ignore_change_password_upon_first_use` first, or the next kolla password
rotation breaks the control plane. Adopt only after verifying every service
user carries the ignore option.

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
`openstack user password set` instead. The forced-change hardening remains
an open follow-up tracked by this ADR.

## References

- ADR-0021 (account lifecycle automation)
- 2026-07-02 whole-repo/fleet audit, section 2 P2
