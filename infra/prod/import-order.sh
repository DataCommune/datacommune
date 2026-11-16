#!/usr/bin/env bash
# Official national import order (plan 04.2-03), sourced by infra/prod/import.sh and
# the hypervisor's national-in-ct.sh (private). Defines SMALL, BIG, ORDER and REPLAYABLE; runs nothing.
# SMALL then BIG. BAN comes before DPE (orphans attached by raw address) and DPE early, so that
# its transient happens on a nearly empty disk. The names are the dataset_name of the pipelines
# (run_etl), not the scope catalogue keys.
readonly SMALL=(monuments loyers transports scolaire zonage_abc fiscalite revenus demographie vacance copropriete permis antennes equipements)
readonly BIG=(ban dpe batiments foncier_pm cadastre connectivite rnb_liens dvf)
readonly ORDER=("${SMALL[@]}" "${BIG[@]}")
readonly REPLAYABLE=(vacance dpe batiments dvf)
