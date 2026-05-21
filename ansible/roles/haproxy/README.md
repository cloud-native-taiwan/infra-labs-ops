# HAProxy role

Deploys HAProxy as the deploy host edge reverse proxy. The default route sends
`registry.cloudnative.tw` to the local Harbor proxy on `127.0.0.1:8443`.

This role intentionally does not stop, disable, remove, or edit NGINX. If NGINX
is still bound to `80` or `443`, the role fails before starting HAProxy and
prints the conflicting listener.

## Apply

```bash
cd ansible
ansible-playbook playbooks/deploy-haproxy.yml --check --diff --limit deploy01
ansible-playbook playbooks/deploy-haproxy.yml --limit deploy01
```

To pre-stage the package, certificate bundle, and configuration without binding
ports:

```bash
ansible-playbook playbooks/deploy-haproxy.yml --limit deploy01 -e haproxy_service_state=stopped
```

## Important variables

| Variable | Default |
|---|---|
| `haproxy_tls_certificate` | `/etc/letsencrypt/live/cloudnative.tw/fullchain.pem` |
| `haproxy_tls_private_key` | `/etc/letsencrypt/live/cloudnative.tw/privkey.pem` |
| `haproxy_cert_bundle` | `/etc/haproxy/certs/cloudnative.tw.pem` |
| `haproxy_http_port` | `80` |
| `haproxy_https_port` | `443` |
| `haproxy_http_routes` | Harbor route for `registry.cloudnative.tw` |
| `haproxy_backends` | Harbor backend at `127.0.0.1:8443` |

Add more hostnames by extending `haproxy_http_routes` and `haproxy_backends` in
inventory variables rather than editing the template.
