# shellcheck shell=bash
# What the private working repository holds and the public one does not (D-135: arbre du privé
# moins les chemins privés = arbre du public). Sourced, never run, by scripts/export-public.sh and
# scripts/check-public-parity.sh: one list, so the export and the parity check cannot disagree.

# Paths left out of the public tree (git glob pathspecs), the motive above each one.
public_excludes=(
  # Private project tracking (D-07, audit PUB-01): plans, research, captures, reviews,
  # journals.
  '.planning/**'
  # Instructions of the private agent workflow: paths of the maintainer's machine, private
  # hosts. AGENTS.md stays (docs/INVENTORY-3394395a.md keeps it).
  'CLAUDE.md'
  # Scripts and settings of the maintainer's own Proxmox host: private (D-140).
  'infra/pve02/**'
  # Ansible inventories of the maintainer's hosts (container ids, host names): private (D-140).
  'infra/inventory/**'
  # Backend-suite wrapper of infra/pve02/tests/test-workbase-deploy.sh: it goes with the
  # script it runs.
  'api/backend/tests/test_workbase_deploy_doubles.py'
  # Raw pytest journals (JUnit XML) kept as evidence under docs/: they carry the paths of
  # the machine that ran them. The README and manifests of their directories stay.
  'docs/**/*.xml'
  # Table of fingerprints of the 2026-09-10 audit, keyed by the private tracking paths it
  # proves: made generic, it would no longer prove anything. VERIFICATION.md stays.
  'docs/audit-recentrage-2026-09-10/socle/verification.json'
)

# Paths whose presence in a public tree is refused outright, whatever the list above says.
public_forbidden_paths=(.planning commercial infra/pve02 infra/inventory CLAUDE.md)

# public_pathspecs: the exclusions as git pathspecs, in the array `pathspecs`.
public_pathspecs() {
  pathspecs=()
  local pattern
  for pattern in "${public_excludes[@]}"; do pathspecs+=(":(exclude,glob)$pattern"); done
}
