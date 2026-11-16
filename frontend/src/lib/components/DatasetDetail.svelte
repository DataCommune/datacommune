<script lang="ts" module>
	import { etatDuJeu, type DatasetPublic } from '$lib/api/datasets';
	import { libelleEtat } from '$lib/data/etats-libelles';

	const LIBELLES_HEBERGES: Record<string, string> = {
		disponible: 'Importé',
		proxy_amont: 'Lu en direct',
		non_importe: 'Pas encore importé'
	};

	/** The whole wording of a badge whose row says it short (lot 8 of 4.9): level 2, in the detail. */
	const PRECISIONS_HEBERGES: Record<string, string> = {
		proxy_amont: 'Lu en direct chez le producteur, sans import'
	};

	/**
	 * Badge of a dataset, shared by its row (DatasetCard) and its detail: the quarantine first, then
	 * the hosting reason, then the access mode. Null for a state the page cannot word (« Non servi »).
	 */
	export function badgeDuJeu(jeu: DatasetPublic): { texte: string; alerte: boolean } | null {
		const etat = etatDuJeu(jeu);
		if (etat.kind === 'non_qualifie') return { texte: libelleEtat('not_qualified', ''), alerte: true };
		if (etat.kind === 'hors_couverture') return { texte: libelleEtat('out_of_coverage', etat.motif), alerte: true };
		if (etat.kind === 'heberge') return { texte: LIBELLES_HEBERGES[etat.statut], alerte: false };
		return null;
	}
</script>

<script lang="ts">
	// Metadata of one dataset of « Données & méthode » (UI-SPEC « /datasets », D-40; phase 04.5 plan
	// 11, B7, UI-029), rendered from one entry of `GET /v1/datasets` and nothing else: name, producer,
	// state and reason, published licence and its link (read in `reutilisation`, never in
	// `provenance.licence`), producer page, period, last successful import, cadence, coverage, reuse
	// conditions, then the reuse restrictions of the producer (DVF, LIC-02) and the limits of the
	// source, two blocks apart: one says what a reuser may do, the other what the source covers. A
	// quarantined dataset carries the « Non qualifié » badge and its served reason; it is never
	// presented as available. The one rendering of these metadata: the row of the catalogue
	// (DatasetCard) mounts it when its dataset is chosen. Its inventory is frozen at the starting SHA
	// of the plan (`catalogue-inventaire-reference.json`).
	import { DEPARTEMENTS } from '$lib/data/departements';
	import { LIBELLES_MOTIFS, PHRASES_ETATS } from '$lib/data/etats-libelles';

	interface Props {
		jeu: DatasetPublic;
		/** Departements hosted by the instance (union served by `/v1/datasets`). */
		departementsInstance: string[];
		/**
		 * Prose of the page with its acronyms spelled out (04.8-08, D-31), keyed `<key>:motif`,
		 * `<key>:conditions`, `<key>:restriction:<i>`, `<key>:chapeau`, `<key>:puce:<i>`; without it, the
		 * served text as it is.
		 */
		textes?: ReadonlyMap<string, string>;
		/**
		 * Id of the heading of the row that shows this dataset (DatasetCard). Given, the detail names the
		 * dataset by it and drops its own name and producer, which the row above already shows. Its badge
		 * stays when it warns (« Non qualifié », « Hors couverture… »: an honesty fact the detail must carry
		 * on its own, when the row has scrolled away or on print) or when it says more than the row's
		 * (« Lu en direct… »); a plain « Importé » is said once, in the row.
		 */
		libelleId?: string;
		/** Heading level of the blocks: one under the row that shows the dataset (5 under a row in h4). */
		niveauBlocs?: 4 | 5;
	}

	let { jeu, departementsInstance, textes, libelleId, niveauBlocs = 4 }: Props = $props();
	const hBloc = $derived(`h${niveauBlocs}`);
	const texte = (cle: string, brut: string) => textes?.get(`${jeu.nom}:${cle}`) ?? brut;

	const etat = $derived(etatDuJeu(jeu));
	const badge = $derived(badgeDuJeu(jeu));
	const libelleBadge = $derived((etat.kind === 'heberge' && PRECISIONS_HEBERGES[etat.statut]) || badge?.texte);
	const provenance = $derived(jeu.provenance);
	const reutilisation = $derived(jeu.reutilisation);
	const titreId = $derived(`jeu-${jeu.nom}-detail`);

	const dateLongue = new Intl.DateTimeFormat('fr-FR', {
		day: 'numeric',
		month: 'long',
		year: 'numeric',
		timeZone: 'UTC'
	});

	function dateFr(iso: string | null | undefined): string | null {
		if (!iso) return null;
		const date = new Date(iso);
		return Number.isNaN(date.getTime()) ? null : dateLongue.format(date);
	}

	function nomDepartement(code: string): string {
		const nom = DEPARTEMENTS.find((d) => d.code === code)?.nom;
		return nom ? `${nom} (${code})` : `Département ${code}`;
	}

	const periode = $derived.by(() => {
		const p = provenance?.periode;
		if (p?.statut === 'connue') {
			if (p.label) return p.label;
			if (p.debut && p.fin) return `${p.debut} à ${p.fin}`;
		}
		if (p?.statut === 'glissante') {
			return 'Glissante : les données sont lues en direct chez le producteur';
		}
		return 'Non déclarée';
	});

	const cadence = $derived.by(() => {
		const c = provenance?.cadence;
		if (c?.statut === 'official' && c.label) return `${c.label}, selon le producteur`;
		return 'Non renseignée dans le catalogue';
	});

	const verifieeLe = $derived(dateFr(reutilisation?.verifiee_le));

	const importation = $derived.by(() => {
		const date = dateFr(provenance?.derniere_importation_reussie);
		if (date) return date;
		if (etat.kind === 'heberge' && etat.statut === 'proxy_amont') return 'Sans objet : lu en direct';
		return 'Aucune';
	});

	// Departements served, read from `hebergement.departements` — also for a quarantined dataset
	// the instance hosts: the quarantine says it is not qualified, not that it is not served. When
	// they are every hosted departement of the instance, the detail says so instead of listing them.
	const servis = $derived.by(() => {
		const hebergement = jeu.hebergement;
		if (hebergement?.etat !== 'heberge') return null;
		const departements = hebergement.departements;
		if (departements.length === 0) return 'Aucun département';
		const sauf = departementsInstance.filter((code) => !departements.includes(code));
		if (sauf.length === 0) return 'Tous les départements hébergés';
		return `Tous les départements hébergés sauf ${sauf.map(nomDepartement).join(', ')}`;
	});

	const motifHebergement = $derived(
		jeu.hebergement?.etat === 'hors_couverture' && jeu.hebergement.motif
			? LIBELLES_MOTIFS[jeu.hebergement.motif]
			: null
	);
</script>

<section class="dataset-detail" aria-labelledby={libelleId ?? titreId}>
	{#if libelleId}
		{#if badge && (badge.alerte || libelleBadge !== badge.texte)}
			<p class="dataset-state">
				<span class="dataset-badge" class:dataset-badge-warn={badge.alerte}>{libelleBadge}</span>
			</p>
		{/if}
	{:else}
		<header class="dataset-head">
			<h3 id={titreId} class="dataset-title">{provenance?.nom ?? jeu.nom}</h3>
			{#if provenance}<p class="dataset-producer">{provenance.producteur}</p>{/if}
			<p class="dataset-state">
				{#if badge}
					<span class="dataset-badge" class:dataset-badge-warn={badge.alerte}>{libelleBadge}</span>
				{:else}
					<span class="dataset-unserved">Non servi</span>
				{/if}
			</p>
		</header>
	{/if}

	{#if etat.kind === 'non_qualifie'}
		<div class="dataset-quarantine">
			<p>{PHRASES_ETATS.not_qualified}</p>
			{#if etat.motif}<p><span class="dataset-label">Motif :</span> {texte('motif', etat.motif)}</p>{/if}
		</div>
	{/if}

	<dl class="dataset-facts">
		<div>
			<dt>Licence</dt>
			<dd>
				{#if reutilisation}
					<a href={reutilisation.licence_url} rel="noopener noreferrer">{reutilisation.licence}</a
					>{#if verifieeLe}<span class="dataset-muted">&nbsp;· vérifiée le {verifieeLe}</span>{/if}
				{:else}
					<span class="dataset-unserved">Non servie</span>
				{/if}
			</dd>
		</div>
		{#if provenance}
			<div>
				<dt>Source</dt>
				<dd><a href={provenance.url} rel="noopener noreferrer">Page du producteur</a></dd>
			</div>
		{/if}
		<div>
			<dt>Période amont</dt>
			<dd>{periode}</dd>
		</div>
		<div>
			<dt>Dernière importation réussie</dt>
			<dd>{importation}</dd>
		</div>
		<div>
			<dt>Cadence</dt>
			<dd>{cadence}</dd>
		</div>
		<div>
			<dt>Couverture</dt>
			<dd>
				{#if motifHebergement}
					{motifHebergement.libelle}<span class="dataset-muted">&nbsp;: {motifHebergement.phrase}</span>
				{:else if servis}
					{servis}
				{:else}
					<span class="dataset-unserved">Non servie</span>
				{/if}
			</dd>
		</div>
	</dl>

	<!-- Review of 2026-11-11: what the source does not cover is read before the terms of its reuse. -->
	{#if jeu.limites_source}
		<section class="dataset-block dataset-limits" aria-labelledby="{titreId}-limites">
			<svelte:element this={hBloc} id="{titreId}-limites" class="dataset-block-title">Limites de la source</svelte:element>
			<p>{texte('chapeau', jeu.limites_source.chapeau)}</p>
			<ul>
				{#each jeu.limites_source.puces as puce, i (puce)}
					<li>{texte(`puce:${i}`, puce)}</li>
				{/each}
			</ul>
		</section>
	{/if}

	{#if reutilisation}
		<section class="dataset-block dataset-conditions" aria-labelledby="{titreId}-conditions">
			<svelte:element this={hBloc} id="{titreId}-conditions" class="dataset-block-title">Conditions de réutilisation</svelte:element>
			<p>{texte('conditions', reutilisation.conditions)}</p>
		</section>
	{/if}

	{#if reutilisation && reutilisation.restrictions.length > 0}
		<section class="dataset-block dataset-restrictions" aria-labelledby="{titreId}-restrictions">
			<svelte:element this={hBloc} id="{titreId}-restrictions" class="dataset-block-title">Restrictions de réutilisation</svelte:element>
			<ul>
				{#each reutilisation.restrictions as restriction, i (restriction)}
					<li>{texte(`restriction:${i}`, restriction)}</li>
				{/each}
			</ul>
		</section>
	{/if}
</section>

<style>
	/* The detail of the source read is a panel (UI-SPEC v4 § 3.0, § 6.8). */
	.dataset-detail {
		box-sizing: border-box;
		max-width: 100%;
		padding: var(--s5);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-panneau);
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.dataset-head {
		display: flex;
		flex-direction: column;
		gap: var(--s1);
	}

	.dataset-title {
		margin: 0;
		font-size: 20px;
		font-weight: 600;
		line-height: 1.2;
	}

	.dataset-producer,
	.dataset-muted {
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.dataset-state {
		margin: var(--s1) 0 0;
	}

	/* Under its row (`libelleId`), the detail starts with a fact, a quarantine or a badge: no gap above it. */
	.dataset-detail > :first-child {
		margin-top: 0;
	}

	.dataset-badge {
		display: inline-block;
		max-width: 100%;
		box-sizing: border-box;
		padding: var(--s1) var(--s2);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-badge);
		background: var(--color-soft);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.dataset-badge-warn {
		border-color: var(--color-warn);
		background: var(--color-warn-bg);
		color: var(--color-warn);
	}

	.dataset-unserved {
		color: var(--color-muted);
		font-size: 14px;
	}

	.dataset-quarantine {
		margin-top: var(--s4);
		padding: var(--s4);
		border: 1px solid var(--color-warn);
		border-radius: var(--radius-tuile);
		background: var(--color-warn-bg);
	}

	.dataset-quarantine p {
		margin: 0;
	}

	.dataset-quarantine p + p {
		margin-top: var(--s2);
	}

	.dataset-label {
		font-weight: 600;
	}

	.dataset-facts {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: var(--s3) var(--s5);
		margin: var(--s4) 0 0;
	}

	.dataset-facts dt {
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.dataset-facts dd {
		margin: 0;
	}

	.dataset-detail a {
		color: var(--color-ink);
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	.dataset-block {
		margin-top: var(--s4);
		padding-top: var(--s4);
		border-top: 1px solid var(--color-line);
	}

	.dataset-block-title {
		margin: 0 0 var(--s2);
		font-size: 16px;
		font-weight: 600;
		line-height: 1.5;
	}

	.dataset-block p {
		margin: 0 0 var(--s2);
	}

	.dataset-conditions p {
		margin: 0;
	}

	.dataset-block ul {
		margin: 0;
		padding-left: var(--s5);
	}

	.dataset-block li + li {
		margin-top: var(--s1);
	}

	@media (max-width: 720px) {
		.dataset-detail {
			padding: var(--s4);
		}

		.dataset-facts {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
