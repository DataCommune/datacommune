<script lang="ts">
	// Final sources/millésimes block of the printed document (UI-04, D-11/D-14, plan 50-07).
	//
	// WHY IT EXISTS: a PDF leaves the site. Once printed, the reader has no ℹ️ to open, so the
	// document must carry its own provenance — otherwise it states figures nobody can trace.
	//
	// NOTHING HERE IS WRITTEN BY HAND. The publication name comes from the LIVE provenance already
	// wired at plan 49-06 and is attributed by `sourcePourDataset` — the very same precedence the
	// source panels use (extracted from the former `Theme.svelte` at plan 50-07, so the two can never
	// disagree).
	// The vintage comes from the same maps, through `millesimePour` — the very same authority, for
	// the very same reason (NV-01, see the scope block below). No producer name, no year is a
	// literal of this file: a hand-copied list would go stale at the first ETL refresh, silently,
	// on a printed document.
	import type { DatasetProvenance } from '$lib/api/resolve';
	import type { Scope } from '$lib/types/fiche';
	import {
		COMMUNE_META_SOURCE_ORDER,
		millesimePour,
		sourcePourDataset,
		type SourceLive
	} from '$lib/data/explications';
	import { LIBELLES_ECARTS_HISTORIQUES } from '$lib/data/etats-libelles';

	interface Props {
		/**
		 * SAME contract as the grids of both fiches (`rowsToGrid`) — the maps are passed through, never
		 * reshaped into a third format:
		 *   - `provenances` : Fiche Bien, `provenanceParDataset(meta.source)` of /v1/resolve;
		 *   - `millesimes`  : both fiches, `millesimesFromCommune()` (only the 5 datasets exposing
		 *     an `annee_*` column have one);
		 *   - `sources`     : Fiche Commune, `sourcesCommuneParDataset(meta.source)`.
		 */
		provenances?: Record<string, DatasetProvenance>;
		millesimes?: Record<string, string | null>;
		sources?: Record<string, SourceLive>;
		/**
		 * Cell of a source without vintage. The phase 3 fiches keep their « non disponible »; the
		 * address page (phase 4, where that wording is refused) passes an empty cell until a wording
		 * is decided (final review FRONT WR-02).
		 */
		sansMillesime?: string;
	}

	let { provenances = {}, millesimes = {}, sources = {}, sansMillesime }: Props = $props();

	/**
	 * Reader-facing label of a DATASET — not of its publication. The publication name is the
	 * SOURCE column, and it is live; this column only says WHICH data the row is about, in the
	 * same vocabulary the fiche already uses on screen (the 6 bien entries below are the ones
	 * the former landing fiche preview rendered publicly). A dataset absent from this map falls back
	 * to its key: an unexpected key must stay visible in the table, not vanish from the audit.
	 */
	const LIBELLES_DATASET: Record<string, string> = {
		// 13 datasets agrégés de la Fiche Commune (COMMUNE_META_SOURCE_ORDER).
		demographie: 'Démographie',
		revenus: 'Revenus',
		zonage_abc: 'Zonage ABC',
		vacance: 'Vacance des logements',
		fiscalite: 'Fiscalité',
		loyers: 'Loyers',
		dvf: 'Transactions (DVF)',
		dpe: 'DPE & énergie',
		equipements: 'Équipements',
		scolaire: 'Secteur scolaire',
		monuments: 'Monuments historiques',
		foncier_pm: 'Foncier (personnes morales)',
		connectivite: 'Connectivité',
		// Datasets propres à la Fiche Bien (DATASETS_BIEN, resolve.ts).
		cadastre: 'Cadastre & parcelle',
		urbanisme: 'Urbanisme (PLU)',
		risques: 'Risques naturels',
		commune: 'Contexte commune',
		ban: 'Adresses (BAN)',
		// Datasets of the themes of the address page (04-06): a key never printed as such.
		batiments: 'Bâtiments (BD TOPO)',
		rnb: 'Référentiel national des bâtiments',
		copropriete: 'Copropriétés (registre national)',
		georisques: 'Risques (Géorisques)',
		transports: 'Arrêts de transport',
		gares: 'Gares'
	};

	/**
	 * SCOPE of a dataset in this table — DECLARED, never inferred from the shape of the key
	 * (patron of plan 50-12). It is what lets the vintage go through `millesimePour`, the single
	 * scoped precedence of D-21 #5, instead of a third private copy of the rule.
	 *
	 * NV-01, and it was MEASURED before it was fixed (`surfaces-coherence.test.ts` §2ter): this
	 * file used to apply `millesimes` first and the provenance period as the fallback — the same
	 * ingredients as the authority, in the REVERSE order and with no scope. On the Fiche Bien the
	 * page hands it the resolve provenance map, and /v1/resolve expands its `commune` alias into
	 * the thirteen communal sources, so a commune dataset with no `annee_*` column was dated on
	 * PAPER from the resolve period while the ℹ️ of its own row on SCREEN stated an absence. One
	 * document, one figure, two dates — and paper is the copy the reader keeps.
	 *
	 * THE TWO EXCEPTIONS ARE NAMED, not derived from a prefix: `dpe` and `dvf` are communal
	 * aggregates AND the datasets whose « sur ce bien » rows carry the property's own figures. This
	 * table has one row per DATASET, not per scope, so their row is the property's — bien scope,
	 * which is also the behaviour this file already had for them. Every other key outside the
	 * declared communal list (cadastre, urbanisme, risques…) is bien-scoped for the same reason,
	 * and an UNEXPECTED key stays bien-scoped rather than losing its provenance vintage.
	 */
	const DATASETS_AUSSI_RENDUS_SUR_LE_BIEN: ReadonlySet<string> = new Set(['dpe', 'dvf']);

	function porteePour(dataset: string): Scope {
		if (DATASETS_AUSSI_RENDUS_SUR_LE_BIEN.has(dataset)) return 'bien';
		return (COMMUNE_META_SOURCE_ORDER as readonly string[]).includes(dataset) ? 'commune' : 'bien';
	}

	/**
	 * One row per dataset that has SOMETHING to declare — a source, a vintage, or both.
	 *
	 * A dataset with neither gets NO row: `millesimesFromCommune` always returns its 5 keys, at
	 * `null` when the year is not published, so keying the table on the raw union would print rows
	 * saying « non disponible / non disponible », which informs nobody. A dataset that HAS a source
	 * but no vintage does get its row, with the absence stated in the vintage column — that is the
	 * honest case this table exists for.
	 */
	const lignes = $derived.by(() => {
		const cles = new Set([
			...Object.keys(provenances),
			...Object.keys(millesimes),
			...Object.keys(sources)
		]);
		return [...cles]
			.map((dataset) => ({
				dataset,
				libelle: LIBELLES_DATASET[dataset] ?? dataset,
				source: sourcePourDataset(dataset, provenances, sources),
				millesime: millesimePour(dataset, porteePour(dataset), provenances, millesimes)
			}))
			.filter((l) => l.source != null || l.millesime != null)
			.sort((a, b) => a.libelle.localeCompare(b.libelle, 'fr'));
	});

	// Absence wording READ from the shared glossary (D-21 #2 discipline): the inline, lower-case
	// entry of the historical wording (a missing source or vintage is not a served state of the
	// glossary: it says the table has nothing to declare). Inventing a third wording for
	// « we do not know » is exactly the divergence the glossary exists to prevent.
	const ABSENCE = LIBELLES_ECARTS_HISTORIQUES.out_of_coverage;
</script>

<!-- `hidden print:block` — invisible on screen (the on-screen sheet footer keeps its own short
     sources line), restored under @media print. `.bloc-impression` is the hook of the
     `break-inside: avoid` rules of app.css, which also cover its `tr` (D-14). No `section.theme`
     class here: the e2e suite counts those, and this is not a theme. -->
{#if lignes.length}
	<div class="bloc-impression hidden print:block">
		<table class="sources">
			<caption>Sources et millésimes des données de cette fiche</caption>
			<thead>
				<tr>
					<th scope="col">Donnée</th>
					<th scope="col">Source</th>
					<th scope="col">Millésime</th>
				</tr>
			</thead>
			<tbody>
				{#each lignes as ligne (ligne.dataset)}
					<tr>
						<td>{ligne.libelle}</td>
						<td>{ligne.source ? ligne.source.nom : ABSENCE}</td>
						<td>{ligne.millesime ?? sansMillesime ?? ABSENCE}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

<style>
	/* v4 form (UI-SPEC v4 § 2.5, § 3.13 `dc-table`), style scoped to the component — nothing is added to
	   app.css. This table is only ever rendered on paper, so it has no scroll wrapper: a fixed layout,
	   cells that wrap anywhere, the header row repeated on each page and rows that never split
	   (`.bloc-impression tr` in app.css). 14 px cells, the header under a --color-line rule, a pale rule
	   between rows. */
	.bloc-impression {
		margin-top: var(--s5);
		padding-top: var(--s2);
		border-top: 1px solid var(--color-line);
	}
	table.sources {
		width: 100%;
		border-collapse: collapse;
		table-layout: fixed;
		font-size: 14px;
		line-height: 1.4;
	}
	table.sources thead {
		display: table-header-group;
	}
	table.sources caption {
		padding-bottom: var(--s-demi);
		font-size: 14px;
		font-weight: 600;
		text-align: left;
		color: var(--color-ink);
	}
	table.sources th,
	table.sources td {
		padding: var(--s-demi) var(--s3) var(--s-demi) 0;
		text-align: left;
		vertical-align: top;
		overflow-wrap: anywhere;
		border-bottom: 1px solid var(--color-filet-pale);
	}
	table.sources thead th {
		font-weight: 600;
		color: var(--color-muted);
		border-bottom-color: var(--color-line);
	}
	table.sources td {
		color: var(--color-ink);
	}
</style>
