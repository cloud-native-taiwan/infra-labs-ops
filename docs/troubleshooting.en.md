# Troubleshooting

[中文](troubleshooting.md)

The most common problems a new operator will hit, ordered to match the steps in [quickstart.en.md](quickstart.en.md).

## Setup

### `ansible-playbook: command not found`

`.venv` is not active. Use the explicit path or activate:

```bash
.venv/bin/ansible-playbook ...
# or
source .venv/bin/activate
```

### `pip install ansible-core` hangs in a PEP 517 build

Usually Python too old (< 3.10) or `python3-dev` missing.

```bash
python3 --version
```

Need 3.10+. On Debian/Ubuntu: `sudo apt install python3-dev`.

## Step 4: validate.sh fails

### `yamllint` reports indentation / trailing-spaces

Fix as the message says. Repo style: 2-space indent, no trailing whitespace. `.yamllint` is the authority.

### `ansible-lint` flags a new rule

Don't blanket `# noqa` it. Check whether it's a real design issue first. Approved deviations already carry `# noqa:` with a rationale comment at the top of the file.

### Syntax check: `ERROR! Syntax Error while loading YAML.`

Usually a task `name:` line is malformed or indentation drifted. The error names the file and line.

### Inventory target: `Could not match supplied host pattern, ignoring: ...`

A group named in a playbook's `hosts:` is missing from `ansible/hosts`, or it has no members. Inspect `ansible/hosts`.

## SSH / sudo

### `unreachable=1` or `Permission denied (publickey)`

Walk this list:

```bash
# 1. Can you SSH to debian directly?
ssh debian@<host-ip>

# 2. Does debian have passwordless sudo?
ssh debian@<host-ip> 'sudo -n true && echo OK'

# 3. Is Ansible using the right user?
grep ansible_user ansible/ansible.cfg ansible/hosts
```

If plain `ssh` fails, your SSH key is not on that host. Ask a maintainer to add it, or use `ssh-copy-id` if another login path exists.

### `sudo: a password is required`

`debian` sudoers has no `NOPASSWD`. On the host:

```bash
sudo visudo
# add: debian ALL=(ALL) NOPASSWD:ALL
```

### Bond does not come up at boot (known issue, openstack01)

The bond interface does not auto-up after reboot. Manual fix:

```bash
ssh debian@openstack01 'sudo systemctl restart networking'
```

Root cause is the Mellanox DKMS module load order vs the bonding module.

## Ansible Vault

### `ERROR! Attempting to decrypt but no vault secrets found`

`kolla/passwords.yml` is Vault-encrypted but you have no vault password locally. Create `kolla/ansible_vault_pass`:

```bash
echo '<ask a maintainer>' > kolla/ansible_vault_pass
chmod 600 kolla/ansible_vault_pass
```

Kolla-Ansible reads it via `--configdir kolla`.

### `Failed to find any cipher mapping for vault id ...`

Wrong content in `kolla/ansible_vault_pass`, or a trailing newline/space. Prefer `printf` over `echo`:

```bash
printf '%s' '<vault password>' > kolla/ansible_vault_pass
```

## Playbook runtime

### Why does the network apply pause 15 seconds between hosts?

By design. `roles/network` does a rolling restart with a 15s gap to avoid simultaneously disrupting Ceph / OVN / VM traffic across the cluster. Be patient.

### `--check --diff` shows no change for command tasks

Expected. `ansible.builtin.command` cannot predict results in check mode, so it shows as skipped or unchanged. Template tasks (GRUB, sysctl, network, mail) still show the full diff in check mode.

### Ceph apply playbook does nothing

```
TASK [ceph-config : ...] ******
skipping: ...
```

`ceph-apply.yml` is no-op by default. You must opt in:

```bash
ansible-playbook playbooks/ceph-apply.yml --limit ceph_bootstrap -e ceph_iac_apply=true
```

## Cephadm packaging

### `apt: Unable to locate package cephadm`

`roles/ceph-bootstrap` deliberately points Debian 13 (`trixie`) hosts at Ceph upstream's `bookworm` apt repo, because upstream does not yet publish a `trixie` suite. This is intentional, not a bug. If a brand-new host still cannot find the package, confirm `bootstrap.yml` has run there first (it installs the apt source).

## Tools

### `command not found` when adding a cron entry

If you add an entry to `tools/<name>/deploy/crontab` using a relative command (e.g. `account-automation`) instead of an absolute path, Supercronic cannot find the binary -- it does not inherit the container shell's PATH. **The existing crontabs already use absolute paths** (e.g. `/usr/local/bin/account-automation`); keep that convention.

### Supercronic skipped a scheduled run (container down)

The container was down at the scheduled time. Supercronic does not catch up (unlike `systemd Persistent=true`). Check `docker compose ps` and host reboot history.

**Period-anchored jobs already have a fix:** `usage_reports` now uses the
period-job integrity contract in `tools/period_reconcile/` -- instead of a
single timed fire, it reconciles hourly and on container start toward "a
successful report exists for every closed month". If you suspect a month was
missed, inspect the watermark:

```bash
docker compose exec usage-reports cat /var/lib/usage-reports/reconcile-watermark.json
```

`last_success_label` is the most recent successfully-run month (`YYYY-MM`).
See `tools/period_reconcile/README.en.md` for the watermark format, catch-up
semantics, and how to reset it.

`account_automation` (which reconciles current spreadsheet state daily) is not
anchored on *closed periods* -- re-running an old date is meaningless -- so it
has not adopted this contract. `keystone-totp` could not be assessed here (not
yet tracked in git). Their adoption status is detailed in
`tools/period_reconcile/README.en.md`.

## Cannot identify the cause?

1. Read the full stderr; share it on Slack/TG.
2. Skim the runbooks and reports linked from [docs/README.en.md](README.en.md) to see if it has happened before.
3. Reach the maintainer directly.
