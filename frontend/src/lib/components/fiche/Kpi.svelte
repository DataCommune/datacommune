<script lang="ts">
	// One key figure of the commune or of a DPE document, as a tile of the direction A (04.8-04, UI-SPEC v4
	// § 3.2, § 6.2, § 6.7): `div.cle` in the `dl.cles` of KpiStrip, its label as the `dt`; the `dd` holds
	// each served line with its value (or the badge of its served state, SEM-01) and ONE short line, then
	// the « i » in the corner of the tile, which opens the panel in one action (definition, source, period,
	// qualification, sentence of the state, caveats; 4.9, lot 3, review of 06/11). On a commune the short line
	// shows the count, the period being in the panel and on paper. No tile writes its source on its face (a
	// DPE document landmark did until P4 of the document page, 2026-11-10). DPE classes are shown as tags in the served A→G order. A line whose absence
	// the API did not declare is not in the model at all.
	import { fragmentsDeDates, type EtatKpi, type KpiCommune, type KpiLigne } from '$lib/data/commune-kpi';
	import type { KpiDocument } from '$lib/data/document-dpe';
	import CourbeAnnuelle from './CourbeAnnuelle.svelte';
	import DpeTag from './DpeTag.svelte';
	import SourceButton from './SourceButton.svelte';
	import SourcePanel from './SourcePanel.svelte';
	import StateValue from './StateValue.svelte';

	// A landmark of the document page (04-04) is rendered by the same card: its energy and climate
	// landmarks carry the served class as a tag (`etiquetteEnTete`).
	// `textes`: prose of the page with its acronyms spelled out (04.8-08, D-31), `kpi:<id>:libelle`,
	// `kpi:<id>:sous:<ligne>`; the raw text without it. The button and the panel keep the raw label (an
	// attribute, a dialog title). `forme`: form of the button (`SourceButton`).
	let { kpi, textes, forme }: { kpi: KpiCommune | KpiDocument; textes?: ReadonlyMap<string, string>; forme?: 'source' | 'detail' } = $props();

	let panneau = $state<ReturnType<typeof SourcePanel>>();
	const brut = $derived(kpi.lignes.map((ligne) => ligne.brut));

	type LigneAffichable = KpiLigne & { etat: Exclude<EtatKpi, { kind: 'absence-non-declaree' }> };
	const affichable = (ligne: KpiLigne): ligne is LigneAffichable => ligne.etat.kind !== 'absence-non-declaree';

	const affichees = $derived(kpi.lignes.filter(affichable));

	// ① and ② of a DPE document: class and Source first, the served total on its own full line. Beside
	// a class and a Source target, a five-digit total was cut inside its number on phones (UI-010).
	const ETIQUETTES = new Set(['energie', 'climat']);
	const etiquetteEnTete = $derived(ETIQUETTES.has(kpi.id));

	// A commune marker reads « value + base »: its definition and its source are in the panel (4.9, lot 3).

	// The tiles of the commune wear the tint of the category they answer on the address fiche.
	const TEINTES: Readonly<Record<string, string>> = {
		'prix-maisons': 'teinte-q-prix',
		'prix-appartements': 'teinte-q-prix',
		tendance: 'teinte-q-prix',
		'dpe-dominant': 'teinte-q-logement'
	};
	const meta = (ligne: KpiLigne) => [ligne.effectif, ligne.fenetre].filter(Boolean).join(' · ');
	// Keep each date legible on phones, served or spelled; wrapping may occur between dates, never within one.
	// A commune tile says its period in the panel: the value reads with its count alone.
	const periodeAuVolet = $derived('definition' in kpi);
</script>

{#snippet dates(texte: string)}
	{#each fragmentsDeDates(texte) as fragment}{#if fragment.date}<span class="kpi-date">{fragment.texte}</span>{:else}{fragment.texte}{/if}{/each}
{/snippet}

<div class="cle kpi {TEINTES[kpi.id] ?? ''}" class:cle-document={!('definition' in kpi)} id={`kpi-${kpi.id}`} data-kpi={kpi.id}>
	<dt class="kpi-label">{textes?.get(`kpi:${kpi.id}:libelle`) ?? kpi.libelle}</dt>
	<dd class="kpi-dd">
		{#each affichees as ligne (ligne.id)}
			<!-- `data-libelle`: the label of the field, compared with its theme row (one field, one label). -->
			<div class="kpi-line" data-field-id={ligne.fieldId} data-kpi-line={ligne.id} data-libelle={ligne.libelleChamp ?? ligne.libelle ?? kpi.libelle}>
				{#if ligne.libelle}<p class="kpi-sub">{textes?.get(`kpi:${kpi.id}:sous:${ligne.id}`) ?? ligne.libelle}</p>{/if}
				<div class="kpi-value" class:kpi-value-absence={ligne.etat.kind === 'absence'}>
					{#if etiquetteEnTete}
						<div class="kpi-etiquette">
							{#if ligne.etiquette}<DpeTag classe={ligne.etiquette} principal={kpi.id === 'energie'} />{/if}
							<StateValue etat={ligne.etat} taille="kpi" aide={false} />
						</div>
					{:else if ligne.classes?.length}
						<p class="kpi-classes" data-state="value">
							{#each ligne.classes as classe (classe)}<DpeTag {classe} principal />{/each}
						</p>
					{:else}
						<StateValue etat={ligne.etat} taille="kpi" aide={false} />
					{/if}
				</div>
					{#if ligne.serie?.length}<CourbeAnnuelle points={ligne.serie} />{/if}
					{#if ligne.periodeVisible}<p class="kpi-meta" data-periode>{ligne.periodeVisible}</p>{/if}
					{#if meta(ligne)}
						<p class="kpi-meta" class:kpi-au-volet={periodeAuVolet && !ligne.effectif}
							>{#if periodeAuVolet && ligne.effectif && ligne.fenetre}{ligne.effectif}<span class="kpi-au-volet">{' · '}{@render dates(ligne.fenetre)}</span
								>{:else}{@render dates(meta(ligne))}{/if}</p
						>
					{/if}
			</div>
		{/each}
		<!-- Last in the tile: the one source action of the tile (C-21, UI-01). -->
		<span class="cle-n"
			><SourceButton
				{forme}
				libelle={kpi.libelle}
				onouvrir={(declencheur) => panneau?.ouvrir(declencheur)}
			/></span
		>
		<SourcePanel bind:this={panneau} titre={kpi.libelle} entrees={kpi.volet} {brut} source={kpi.source} />
	</dd>
</div>

<style>
	/* The tile (UI-SPEC v4 § 3.0, § 3.2): background, 1 px line, tile radius. */
	.cle {
		position: relative;
		min-width: 0;
		padding: var(--s3) var(--s4);
		border: 1px solid var(--c-fond, var(--color-line));
		border-radius: var(--radius-tuile);
		background: var(--c-fond, var(--color-bg));
		color: var(--color-ink);
	}

	@media screen {
		.kpi-au-volet {
			display: none;
		}
	}

	/* Clear of the « i », in the corner of the tile. */
	.kpi-label {
		padding-right: var(--s5);
		color: var(--c-encre, var(--color-muted));
		font-family: var(--font-sans);
		font-size: 14px;
		font-weight: 400;
		line-height: 1.4;
		overflow-wrap: break-word;
	}

	.kpi-dd {
		display: grid;
		gap: var(--s-demi);
		margin: var(--s-demi) 0 0;
	}

	.kpi-line {
		display: grid;
		gap: var(--s1);
		min-width: 0;
	}

	.kpi-line + .kpi-line {
		padding-top: var(--s-demi);
		border-top: 1px solid var(--color-filet-pale);
	}

	.kpi-value {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--s2);
		min-width: 0;
	}

	.kpi-value :global(.state-value) {
		min-width: 0;
		overflow-wrap: break-word;
		word-break: normal;
	}

	.kpi-etiquette {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s-demi) var(--s2);
		min-width: 0;
	}

	/* Number and unit are two whole pieces: on a narrow tile the unit takes the next line instead of
	   the digits being split (UI-010). */
	.kpi :global(.state-value-kpi) {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		column-gap: var(--s1);
		overflow-wrap: normal;
	}

	/* The per-m² measure of an energy or climate label keeps its digits together (UI-010); a composed
	   text (« Appartement · 81,9 m² ») wraps between its pieces. */
	.kpi-etiquette :global(.state-value-kpi .value) {
		white-space: nowrap;
	}

	/* The state of a key figure is a word, not a badge (UI-SPEC v4 § 3.2): Newsreader 20, « Source
	   indisponible » (an outage) in danger, the others muted. Label and reason are unchanged. */
	.kpi-value :global(.state-absence .state-badge) {
		padding: 0;
		border: 0;
		background: transparent;
		color: var(--color-muted);
		font-family: var(--font-display);
		font-size: 20px;
		font-weight: 500;
		line-height: 1.3;
		overflow-wrap: anywhere;
	}

	.kpi-value :global(.state-absence[data-state='temporary_error'] .state-badge) {
		color: var(--color-danger);
	}

	.kpi-classes {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s1);
		margin: 0;
	}

	.kpi-sub,
	.kpi-meta {
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: break-word;
	}

	.kpi-sub {
		color: var(--color-ink);
		font-weight: 600;
	}

	.kpi-date {
		white-space: nowrap;
	}

	/* A month in full may wrap inside its day rather than overflow a narrow tile (04.7-15 review). */
	@media (max-width: 359px) {
		.kpi-date {
			white-space: normal;
		}
	}

	/* The line holds nothing but the « i » (no tile writes a note since P4 of the DPE document, 2026-11-10): it
	   takes no height and the « i » goes to the corner. */
	.cle-n {
		display: contents;
	}

	.cle .cle-n :global(.source-button) {
		position: absolute;
		top: 0;
		right: 0;
		margin: 0;
	}

	/* A document landmark reads a long first line (class, number, unit) right under its label: the label
	   keeps the height of the « i » target, so that line never runs under it on a narrow tile. */
	.cle-document .kpi-label {
		min-height: calc(44px - var(--s3));
	}

	@media print {
		.cle {
			break-inside: avoid;
		}
	}
</style>
