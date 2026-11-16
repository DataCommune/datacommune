# DataCommune API — Ansible IaC

**Phase 33 — Provisioning VPS + ingestion dept 85**

This directory is the single source of truth for the DataCommune API infrastructure.
All VPS mutations MUST go through this playbook. No direct SSH edits are allowed (D-06 / anti-drift).

---

## Directory Structure

```
infra/
├── ansible.cfg              — Ansible config (inventory, timeout, ssh args)
├── inventory/
│   └── vps-demo.yml         — host definition (set ansible_host to real IP)
├── group_vars/
│   └── all.yml              — Public vars (repo URL, branch dev, deploy_dir, domain)
├── vault.yml                — Secrets (Ansible Vault AES256 encrypted — D-11)
├── templates/
│   └── env.j2               — .env template interpolating vault_* vars
├── site.yml                 — One-shot idempotent playbook
└── README.md                — This file
```

---

## Prerequisites

Before running the playbook:

1. **VPS ordered** (D-01) — OVH VPS, ~2 vCPU / 4 GB RAM / 80 GB SSD, Ubuntu 22.04 LTS
2. **IP set outside the repository** — Copy `inventory/host_vars/vps-demo.yml.example` to `inventory/host_vars/vps-demo.yml` (git-ignored) and set `ansible_host`
3. **SSH key configured** — Either via ssh-agent or `ansible_ssh_private_key_file` in inventory
4. **Vault password** — Store in `~/.secrets/vault-pass` (hors-repo, NEVER commit)
5. **Vault secrets filled** — Edit vault and replace all `CHANGEME_*` values (see below)
6. **Cloudflare Tunnel created** — One-time manual step before running the playbook (see CF Tunnel section)

---

## Vault — 5 Required Secrets

The `vault.yml` is encrypted with Ansible Vault AES256. The file is committed in the repo
(encrypted). The vault-password is stored OUTSIDE the repo at `~/.secrets/vault-pass`.

### Required vault keys (all CHANGEME_ values must be replaced)

| Variable | Description |
|----------|-------------|
| `vault_postgres_password` | Strong random password for PostgreSQL (≥20 chars) |
| `vault_jwt_secret_key` | Strong random key for JWT signing (≥32 chars) |
| `vault_admin_password` | Password for the `/admin` endpoint (≥16 chars) |
| `vault_api_key_hmac_secret` | Strong random key for API key HMAC (≥32 chars) |
| `vault_cf_tunnel_token` | Cloudflare Tunnel token (see CF Tunnel section) |

### Editing the vault

```bash
# Edit secrets interactively (decrypts in-memory, never writes plaintext to disk)
ansible-vault edit infra/vault.yml --vault-password-file ~/.secrets/vault-pass

# View current contents (for verification)
ansible-vault view infra/vault.yml --vault-password-file ~/.secrets/vault-pass

# Re-key vault (change encryption password)
ansible-vault rekey infra/vault.yml --vault-password-file ~/.secrets/vault-pass
```

### Vault-password format

`~/.secrets/vault-pass` must contain the password as a single line (no trailing newline).

```bash
# Create vault-password file (example — use your own strong password)
echo -n "YOUR_STRONG_VAULT_PASSWORD" > ~/.secrets/vault-pass
chmod 600 ~/.secrets/vault-pass
```

---

## Cloudflare Tunnel — One-time Setup (D-03/D-04)

The tunnel must be created once before running the playbook. The token is then stored in vault.

```bash
# Authenticate cloudflared with your Cloudflare account (one-time)
cloudflared tunnel login

# Create the tunnel (saves credentials locally)
cloudflared tunnel create datacommune-demo

# Route the DNS record (requires Cloudflare account access)
cloudflared tunnel route dns datacommune-demo api.datacommune.fr
cloudflared tunnel route dns datacommune-demo dev-api.datacommune.fr

# Get the tunnel token (store this in vault as vault_cf_tunnel_token)
cloudflared tunnel token datacommune-demo
```

---

## Running the Playbook

### Full one-shot provisioning (plan 03 — live VPS)

```bash
# From the repo root, using the vault-password file
ansible-playbook infra/site.yml --vault-password-file ~/.secrets/vault-pass

# Or with a different inventory (override)
ansible-playbook infra/site.yml -i infra/inventory/vps-demo.yml \
  --vault-password-file ~/.secrets/vault-pass

# Dry-run (check mode) — validates without applying changes
# NOTE: Requires a live VPS target. Run this at plan 03 when the VPS is provisioned.
ansible-playbook infra/site.yml --check --vault-password-file ~/.secrets/vault-pass
```

### Validation only (no live VPS required)

```bash
# Syntax check (vault password required to decrypt vars_files)
ANSIBLE_VAULT_PASSWORD_FILE=~/.secrets/vault-pass \
  ansible-playbook --syntax-check infra/site.yml

# Lint (vault password required for the syntax-check sub-call)
ANSIBLE_VAULT_PASSWORD_FILE=~/.secrets/vault-pass \
  ansible-lint infra/

# Note: --check (dry-run) requires a live VPS target.
# This command is run at plan 03 when the VPS is provisioned and
# ansible_host is set to the real IP in the git-ignored inventory/host_vars/vps-demo.yml.
ANSIBLE_VAULT_PASSWORD_FILE=~/.secrets/vault-pass \
  ansible-playbook --check infra/site.yml
```

---

## Anti-drift Policy (D-06)

> Any change to the VPS configuration MUST go through this playbook.
> Direct SSH edits that diverge from the repo are forbidden.

Rationale: The DB wipe incident of 2026-04-03 was caused by a direct .env edit that
was not tracked in the repo. The playbook encodes a guard (`creates:` on the .env task)
that prevents overwriting an existing .env — preventing data loss.

**Allowed:** `ansible-playbook infra/site.yml` (versionné, tracé, idempotent)
**Forbidden:** `ssh root@VPS_IP && vim /opt/datacommune/api/.env`

---

## Deploy Sequence (phase 33)

1. Bootstrap (this playbook): Docker + cloudflared + clone dev + .env + compose up (ETL overlay)
2. Alembic migration: `docker exec api python -m alembic upgrade head` → must reach 066
3. ETL ingestion dept 85: follow `docs/etl-runbook.md` steps 3-6
4. Data quality certification: `docker exec api python -m app.data_quality.run` → exit 0 / 0 ERROR
5. Switch to permanent-mode overlay: compose up with `docker-compose.vps.yml`
6. External health check: `curl -sf https://api.datacommune.fr/v1/health/live`

---

## Commit History

This IaC tree was introduced in Phase 33, Plan 02 (2026-06-15).
