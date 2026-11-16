"""CRON schedule and freshness threshold definitions for all 22 datasets.

CRON expressions stagger ETL runs between 02:00-05:00 (off-hours); they are kept
for the scheduler code, which no longer runs (D-12: manual updates in V1).

Freshness thresholds are the promise of a MANUAL refresh, sized on the upstream
cadence: refresh interval we commit to + 50% grace. They are served by /v1/health,
/v1/datasets and check_dataset_freshness (app.datasets.freshness
.manual_update_threshold), over the value stored in dataset_metadata.
Upstream cadences read on 2026-11-13: research note
05-SEUILS-FRAICHEUR (internal tracking).
"""

# Persisted jobs can survive removal from SCHEDULES. This policy also guards
# scheduler entrypoints; the explicitly invoked local ETL CLI is separate.
QUARANTINED_DATASETS: frozenset[str] = frozenset({"permis", "encadrement_loyers"})

# Dataset name -> CRON expression (5-field, UTC)
SCHEDULES: dict[str, str] = {
    # --- Core datasets (v1.0-v4.0) ---
    "ban": "0 2 * * 0",  # Sunday 02:00 (weekly)
    "dpe": "0 3 1,15 * *",  # 1st and 15th 03:00 (bimonthly)
    "transports": "0 4 1 * *",  # 1st of month 04:00
    "cadastre": "0 2 15 1,4,7,10 *",  # Quarterly (Jan/Apr/Jul/Oct 15th)
    "batiments": "0 2 1 1,4,7,10 *",  # Quarterly
    "connectivite": "0 3 1 1,4,7,10 *",  # Quarterly
    "dvf": "0 2 1 5,11 *",  # Semi-annual (May/Nov)
    "loyers": "0 2 15 3 *",  # Annual (March)
    "scolaire": "0 3 15 10 *",  # Annual (October)
    "monuments": "0 2 15 1 *",  # Annual (January)
    # --- v5.0 datasets: monthly ---
    # COR-07: permis suspended until its local source is qualified.
    "antennes": "0 3 5 * *",  # Monthly, 5th 03:00
    # --- v5.0 datasets: quarterly ---
    "copropriete": "0 4 1 1,4,7,10 *",  # Quarterly, 1st 04:00
    # --- v5.0 datasets: annual ---
    # COR-15: encadrement unavailable; historical annual Feb 1st 02:00 retired.
    "fiscalite": "0 3 1 2 *",  # Annual, Feb 1st 03:00
    "foncier_pm": "0 3 1 7 *",  # Annual, Jul 1st 03:00
    "zonage_abc": "0 4 1 1 *",  # Annual, Jan 1st 04:00
    "revenus": "0 2 15 6 *",  # Annual, Jun 15th 02:00
    "demographie": "0 3 15 1 *",  # Annual, Jan 15th 03:00
    "equipements": "0 4 15 2 *",  # Annual, Feb 15th 04:00
    "vacance": "0 2 15 2 *",  # Annual, Feb 15th 02:00
    # --- Lot B (rattachement DPE): RNB link table ---
    # Semi-annual (Feb/Aug 1st 05:00) — the low end of the RNB-recommended
    # 6-12 month refresh window. 05:00 keeps it last in the ETL window.
    "rnb_liens": "0 5 1 2,8 *",
}

# Dataset name -> days after which data is considered stale
FRESHNESS_THRESHOLDS: dict[str, int] = {
    # --- Core datasets (v1.0-v4.0) ---
    "ban": 45,  # upstream daily (data.gouv); manual monthly refresh
    "dpe": 45,  # upstream weekly (ADEME); manual monthly refresh
    "transports": 545,  # upstream irregular: 2023-10, 2024-02, 2025-09, 2026-01
    "cadastre": 135,
    "batiments": 135,
    "connectivite": 135,
    "dvf": 270,  # upstream semi-annual (April, October)
    "loyers": 545,
    "scolaire": 545,
    "monuments": 545,
    # --- v5.0 datasets: monthly ---
    # No freshness promise for the quarantined permis source.
    "antennes": 135,  # upstream monthly (ANFR, 6th); manual quarterly refresh
    # --- v5.0 datasets: quarterly ---
    "copropriete": 135,
    # --- v5.0 datasets: annual ---
    # No freshness promise for quarantined encadrement.
    "fiscalite": 545,
    "foncier_pm": 545,
    "zonage_abc": 545,
    "revenus": 545,
    "demographie": 545,
    "equipements": 545,
    "vacance": 545,
    # --- Lot B: 183-day cadence + 50% grace ---
    "rnb_liens": 270,
}
