# Keystone service-user exemptions

`kolla/config/keystone.conf` enables `change_password_upon_first_use` and
lockout after 5 failed logins for 30 minutes. Those rules also apply to
Kolla's service users. Without exemptions:

- A kolla password change (reconfigure or rotation) marks the service
  password as expired. The service then fails to authenticate.
- A service that retries a stale password locks itself out for 30 minutes.

Every service user therefore needs two Keystone user options:
`ignore_change_password_upon_first_use` and `ignore_lockout_failure_attempts`.
Kolla sets the first on the users it creates. It does not set the second on
all of them: on 2026-11-09, `cloudkitty` and `heat_domain_admin` were
missing the lockout exemption.

## When to run

- After enabling a new Kolla service, since it creates a new service user.
- Before any password rotation.

## Check

```sh
for u in admin $(openstack user list --project service -f value -c Name); do
  printf '%-14s %s\n' "$u" "$(openstack user show "$u" -f value -c options)"
done
openstack user show --domain heat_user_domain heat_domain_admin -f value -c options
```

Every line must show both options set to `True`.

## Fix

```sh
openstack user set --ignore-change-password-upon-first-use \
  --ignore-lockout-failure-attempts <user>
# Heat's domain admin lives in its own domain:
openstack user set --domain heat_user_domain --ignore-change-password-upon-first-use \
  --ignore-lockout-failure-attempts heat_domain_admin
```

`password_expires_days` is not set, so `ignore_password_expiry` is not
needed. If password expiry is ever enabled, add `--ignore-password-expiry`
to every service user first.

`admin` is exempt from lockout so that a password-guessing attack can't lock
operators out. The trade-off is that guessing attempts against `admin` are
never throttled. Keep the admin password long and random.
