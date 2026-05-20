# Quickstart: first time operating this repo

[中文](quickstart.md)

Goal: from zero to "I just ran a dry-run against the fleet" in 10-20 minutes.

> Unfamiliar terms? See [glossary.en.md](glossary.en.md).  
> Curious about which host runs what? See [fleet-topology.en.md](fleet-topology.en.md).  
> Stuck on a step? See [troubleshooting.en.md](troubleshooting.en.md).

## What you need

- Internet on your control machine (laptop or jump host).
- SSH access to the fleet as `debian`, with passwordless sudo.
- The vault password for `kolla/passwords.yml` (ask a maintainer).
- The secret files under `ansible/private/` (ask a maintainer or generate yourself).

If you just want to validate the repo (no host access), the SSH and vault password can wait. Steps 1-4 are enough.

## Step 1: Get the code, build a venv

```bash
git clone <repo-url>
cd infra-labs-ops
python3 -m venv .venv
.venv/bin/pip install ansible-core ansible-lint yamllint jinja2 pyyaml
```

> Why `.venv/`: `ansible/scripts/validate.sh` looks for `./.venv/bin/python`. Other locations work but need path tweaks.

## Step 2: Place secret inputs

Create these files at the repo root. **They are already excluded from git.**

```text
ansible/private/
  authorized_keys             # roles/base writes this to /home/debian/.ssh/authorized_keys
  passwd.client               # roles/mail writes this to /etc/exim4/passwd.client
```

`authorized_keys` is one SSH public key per line (the keys that should be pushed to `debian` on each host).  
`passwd.client` is the exim4 smarthost credential, format `mail.example.com:username:password`.

Only needed if you also intend to deploy the tools:

```text
ansible/private/tools/account_automation/
  .env                  # copy from tools/account_automation/.env.example and fill values
  service-account.json  # Google service account key
  clouds.yaml           # OpenStack credentials
```

## Step 3: Place the Kolla vault password

```bash
echo '<ask a maintainer>' > kolla/ansible_vault_pass
chmod 600 kolla/ansible_vault_pass
```

Only needed if you intend to drive Kolla-Ansible. Plain `ansible/playbooks/*` runs do not need it.

## Step 4: Run local static validation

```bash
./ansible/scripts/validate.sh
```

Expected result: `yamllint`, `ansible-lint`, playbook syntax, inventory target, and template render all green. Any red output -> see [troubleshooting.en.md](troubleshooting.en.md#step-4-validatesh-fails).

> This step **does not touch any host**. A failure means the repo or your local environment is misconfigured.

## Step 5: Dry-run against the lowest-blast-radius host

`openstack06` is the Ceph-only node (no OpenStack control plane runs on it), so the OpenStack control plane is unaffected. It is still **production**: it runs OSDs in the live Ceph cluster, and the step 6 apply will actually change sysctl / GRUB / network / mail settings. Review the step 5 diff carefully, confirm with a maintainer, and pick a maintenance window if needed before continuing to step 6.

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --check --diff --limit openstack06
```

What to expect:

- A list of tasks, mostly `ok`, with some `changed` ("would change" in check mode).
- `--diff` prints proposed changes to GRUB, sysctl, network, and mail templates.
- The final PLAY RECAP shows `failed=0`, `unreachable=0`.

If `unreachable=1`, solve SSH/sudo before continuing -- see [troubleshooting.en.md](troubleshooting.en.md#unreachable1-or-permission-denied-publickey).

## Step 6: Once the diff looks right, apply

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --limit openstack06
```

If `bond0` changes, the network role applies host by host with a 15-second pause; the host briefly loses connectivity during its restart. For `ceph_cluster` members (openstack06 included) that means the OSD is briefly cut from the cluster -- confirm `ceph -s` shows `HEALTH_OK` with no backfill/recovery in progress before applying.

## What to read next

- What each playbook does: [`ansible/README.en.md`](../ansible/README.en.md)
- Adjusting Ceph day-2 config: [`ansible/roles/ceph-config/README.en.md`](../ansible/roles/ceph-config/README.en.md)
- Adjusting host performance: [`ansible/roles/tuning/README.en.md`](../ansible/roles/tuning/README.en.md)
- Deploying/operating tools: [`tools/account_automation/README.en.md`](../tools/account_automation/README.en.md)
- Adding a host: [Root README -- First-Time Host Setup](../README.en.md#first-time-host-setup)
- Index of everything: [`README.en.md`](README.en.md)
