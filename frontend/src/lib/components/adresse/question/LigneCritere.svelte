<script lang="ts">
	// One criterion of the address fiche, as a row of a list (UI-SPEC v4 § 3.3, § 3.4, § 3.6, § 3.10;
	// D-08, D-11, D-33, D-37). It reads the theme object as the API serves it and writes nothing of its
	// own: title and verdict (French spacing at render time), the served alert of a link that is not
	// sure, the served headline unless it only repeats the badge, the badge of statutDe(), the served
	// reason where it stays on the row (the detail says it otherwise), the note of a partial outage. The detail is a native <details>, opened by its anchor `#critere-{id}`.
	// One form at every scale since the reading of 06/11: the scale word stays beside the value (CADRAGE R8),
	// and the columns line up from one row to the next inside a category.
	import { onMount, untrack, type Snippet } from 'svelte';
	import type { ThemeRapport } from '$lib/api/rapport';
	import DetailGenerique from '$lib/components/adresse/rapport/DetailGenerique.svelte';
	import BadgeEtat from '$lib/components/ds/BadgeEtat.svelte';
	import EchelleDpe, { type CaseDpe } from '$lib/components/ds/EchelleDpe.svelte';
	import IconInfo from '$lib/components/ds/icons/IconInfo.svelte';
	import RepereEchelle from '$lib/components/ds/RepereEchelle.svelte';
	import {
		accrocheDeLigne,
		alerteDe,
		aplatirDetails,
		dateFr,
		libelleCode,
		motConfiance,
		phraseMotif,
		raisonDeLigne,
		statutDe,
		valeurChiffre
	} from '$lib/data/adresse-rapport';
	import { niveauDe, zoneDe } from '$lib/data/echelles';
	import { enClairTheme } from '$lib/data/explications-simples';
	import { typographieFr } from '$lib/utils/typographie';

	interface Props {
		theme: ThemeRapport;
		/** `h5` under the title of a group, `h4` in a category, `h3` under « Autres critères ». */
		niveauTitre: 3 | 4 | 5;
		/** Lists the page reads beside the report (DPE documents, sales), shown after the details. */
		suite?: Snippet;
		/** State of the native <details>; the page sets it for the criteria it injects lists into. */
		ouvert?: boolean;
		/** The reader opened or closed the criterion. A page passes `ouvert` with this callback, never `bind:ouvert` (see Categorie). */
		onbascule?: (ouvert: boolean) => void;
		/** Prose of the page with its acronyms spelled out (04.8-08, D-31), keyed `titre:`, `verdict:`, `alerte:` + id. */
		textes?: ReadonlyMap<string, string>;
	}
	let { theme, niveauTitre, suite, ouvert = $bindable(false), onbascule, textes }: Props = $props();
	function basculer(valeur: boolean) {
		ouvert = valeur;
		onbascule?.(valeur);
	}
	// Opened on the server render before the page is hydrated: the reader's gesture wins over the default,
	// which `bind:open` would write on the element at the hydration (R-06, as in `Categorie.svelte`).
	if (typeof document !== 'undefined' && untrack(() => document.getElementById(`critere-${theme.id}`)?.querySelector<HTMLDetailsElement>(':scope > details.det')?.open)) basculer(true);

	const NBSP = String.fromCharCode(0xa0); // no-break space
	const MODE = { direct: 'lu en direct chez le producteur', import: 'copie importée par DataCommune' } as const;

	const statut = $derived(statutDe(theme));
	const repere = $derived(niveauDe(theme.granularite));
	// The served cut (D-32): a value wider than the address keeps its outlined vigilance and its ink (D-11).
	const zone = $derived(zoneDe(theme.granularite));
	const titre = $derived(typographieFr(theme.titre));
	const titreAffiche = $derived(textes?.get(`titre:${theme.id}`) ?? titre);
	const verdict = $derived(textes?.get(`verdict:${theme.id}`) ?? typographieFr(theme.verdict));
	const alerteServie = $derived(alerteDe(theme));
	const alerte = $derived(alerteServie === null ? null : (textes?.get(`alerte:${theme.id}`) ?? typographieFr(alerteServie)));
	const noeuds = $derived(aplatirDetails(theme.details, 0, false, theme.id));
	// The plainest explanation of the criterion opens its detail (owner review of 09/11).
	const enClair = $derived(enClairTheme(theme.id));
	const repartition = $derived(noeuds.find((n) => n.type === 'repartition_dpe'));
	// Calm rows (favourable, not applicable) stay in place, compact (§ 3.3).
	const calme = $derived(statut?.cle === 'rassurant' || statut?.cle === 'non_concerne');
	const accroche = $derived(accrocheDeLigne(theme));
	const raison = $derived(raisonDeLigne(theme));
	const sourcesPartielles = $derived(theme.pannes_partielles.map((p) => libelleCode(p).toLowerCase()).join(', '));
	// The served reason of a gap, or the code made readable, as a sentence; a figure that answers needs none.
	const motif = $derived(phraseMotif(theme, raison, alerteServie));
	let copie = $state<string | null>(null);

	// `aplatirDetails` serves the seven classes A to G in order.
	const casesDpe = (classes: { classe: string; n: number | null }[]): CaseDpe[] =>
		classes.map((c) => ({ classe: c.classe as CaseDpe['classe'], n: c.n }));

	// A source line after its name: producer, vintage (or its absence, said), licence, mode.
	function detailsSource(s: ThemeRapport['sources'][number]): string {
		return [s.producteur, s.millesime ? dateFr(s.millesime) : 'année des données non indiquée par la source', s.licence, MODE[s.mode]]
			.filter((x) => x !== '')
			.join(' · ');
	}

	// A link to this criterion (summary of the page, shared URL) opens it on arrival.
	function ouvrirSiCible() {
		if (location.hash === `#critere-${theme.id}`) basculer(true);
	}
	onMount(() => {
		ouvrirSiCible();
		window.addEventListener('hashchange', ouvrirSiCible);
		return () => window.removeEventListener('hashchange', ouvrirSiCible);
	});

	async function copier(valeur: string) {
		try {
			await navigator.clipboard.writeText(valeur);
			copie = valeur;
		} catch {
			copie = null;
		}
	}
</script>

<li
	class="ligne ligne-{statut?.cle ?? 'neutre'}"
	class:is-calme={calme}
	class:is-commune={zone === 'tuile'}
	id={`critere-${theme.id}`}
	data-theme={theme.id}
	data-etat={theme.etat}
>
	<div class="ligne-tete">
		<div class="ligne-txt">
			<svelte:element this={`h${niveauTitre}`} class="ligne-titre">{titreAffiche}</svelte:element>
			<p class="ligne-phrase">{verdict}</p>
			{#if alerte}<p class="ligne-rat">{alerte}</p>{/if}
		</div>
		<div class="ligne-val">
			{#if accroche}<span class="val">{accroche}</span>{/if}
			<BadgeEtat {statut} {zone} />
		</div>
		<div class="ligne-ech">
			<RepereEchelle pictogramme={repere.pictogramme} mot={repere.mot} />
		</div>
	</div>
	{#if theme.id === 'dpe' && repartition?.type === 'repartition_dpe'}
		<div class="ligne-dpe"><EchelleDpe cases={casesDpe(repartition.classes)} /></div>
	{/if}
	{#if raison}<p class="ligne-etat">{raison}</p>{/if}
	{#if theme.pannes_partielles.length > 0}
		<p class="ligne-etat">Une partie des sources n’a pas répondu&nbsp;: {sourcesPartielles}</p>
	{/if}
	<details class="det" bind:open={() => ouvert, basculer}>
		<summary><IconInfo class="det-icone" /><span class="sr">Détail, échelle et source&nbsp;: {titre}</span></summary>
		<div class="det-in">
			{#if enClair}<p class="en-clair">{enClair}</p>{/if}
			{#if theme.chiffres.length > 0}
				<dl class="chiffres">
					{#each theme.chiffres as c (c.id)}
						<div>
							<dt>{c.libelle}</dt>
							<dd class="valeur">
								{#if c.unite === 'classe_dpe' && /^[A-G]$/.test(String(c.valeur))}<span class="dpe-badge dpe-{c.valeur}">{c.valeur}</span
									>{:else}{valeurChiffre(c)}{/if}
							</dd>
							{#if c.millesime}<dd class="millesime">{dateFr(c.millesime)}</dd>{/if}
						</div>
					{/each}
				</dl>
			{/if}
			{#if motif}<p class="motif">{motif}</p>{/if}
			<DetailGenerique {noeuds} />
			{@render suite?.()}
			<p class="det-ech">Cette valeur vaut {repere.longue}.</p>
			{#if theme.rattachement}
				{@const r = theme.rattachement}
				<div class="rattachement" class:incertain={r.confiance !== 'sure'}>
					<p>
						<strong>Rattachement {motConfiance(r.confiance)}&nbsp;:</strong>
						{libelleCode(r.entite)}{#if r.candidats && r.candidats > 1}, {r.candidats} candidats{/if}.
					</p>
					{#if r.maillons && r.maillons.length > 0}
						<p>
							Maillons&nbsp;: {r.maillons.map((m) => `${libelleCode(m.lien)}${NBSP}(${motConfiance(m.confiance)})`).join(', ')}.
						</p>
					{/if}
					{#if r.cle}
						<p>
							Identifiant&nbsp;: <span class="chiffre">{r.cle}</span>
							<button type="button" class="copier" onclick={() => copier(r.cle!)} aria-label={`Copier l’identifiant ${r.cle}`}>Copier</button>
							{#if copie === r.cle}<span role="status">Copié.</span>{/if}
						</p>
					{/if}
				</div>
			{/if}
			{#if theme.sources.length > 0}
				<div class="det-sources">
					<p class="det-lib">{theme.sources.length > 1 ? 'Sources' : 'Source'}&nbsp;:</p>
					<ul class="dc-sources">
						{#each theme.sources as s, i (s.cle + i)}
							<li>
								{#if s.url}<a href={s.url} rel="noopener noreferrer">{s.nom}</a>{:else}{s.nom}{/if}
								· {detailsSource(s)}
							</li>
						{/each}
					</ul>
				</div>
			{/if}
		</div>
	</details>
</li>

<style>
	/* A row reads across: title, sentence, value and state, scale, then the « i ». The columns come from the width
	   of the list (its parent is the query container), not of the window: the detail is laid over the top
	   right corner of the row, and its body flows under the row, so that a native <details> keeps working
	   without JavaScript. Two widths: phone, and the column of the fiche. */
	.ligne {
		position: relative;
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		grid-template-areas:
			'titre titre'
			'phrase phrase'
			'ech val';
		gap: var(--s-demi) var(--s3);
		align-items: center;
		min-width: 0;
		padding: var(--s3) 0;
		border-top: 1px solid var(--color-filet-pale);
	}

	.ligne:first-child {
		border-top: 0;
	}

	/* Calm rows (favourable, not applicable) stay in place, compact. */
	.is-calme {
		padding: var(--s2) 0;
	}

	.ligne-tete,
	.ligne-txt {
		display: contents;
	}

	.ligne-titre {
		grid-area: titre;
		margin: 0;
		padding-right: var(--s7);
		color: var(--color-ink);
		font-family: var(--font-sans);
		font-size: 16px;
		font-weight: 600;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.is-calme .ligne-titre {
		font-weight: 400;
	}

	.ligne-phrase {
		grid-area: phrase;
		min-width: 0;
		max-width: var(--mesure);
		margin: 0;
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.45;
		overflow-wrap: anywhere;
	}

	.is-calme .ligne-phrase {
		color: var(--color-muted);
	}

	.ligne-rat,
	.ligne-dpe,
	.ligne-etat {
		grid-column: 1 / -1;
		max-width: var(--mesure);
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.ligne-dpe {
		max-width: none;
		margin-top: var(--s2);
		overflow-x: auto;
	}

	.ligne-val {
		grid-area: val;
		display: flex;
		flex-flow: row wrap;
		justify-content: flex-end;
		align-items: center;
		gap: var(--s-demi) var(--s2);
		min-width: 0;
	}

	.ligne-ech {
		grid-area: ech;
		min-width: 0;
		color: var(--color-muted);
		font-size: 14px;
	}

	/* A long word wraps instead of pushing the value (the mark is built with nowrap). */
	.ligne-ech :global(.ech) {
		overflow-wrap: anywhere;
		white-space: normal;
	}

	.val {
		color: var(--color-ink);
		font-family: var(--font-display);
		font-size: 18px;
		font-weight: 500;
		line-height: 1.2;
		font-variant-numeric: lining-nums tabular-nums;
		overflow-wrap: anywhere;
	}

	.ligne-vigilance:not(.is-commune) .val {
		color: var(--color-warn);
	}

	/* The class inherits Newsreader from the value: its allowed emphasis is 500. */
	.valeur .dpe-badge {
		font-weight: 500;
	}

	@container (min-width: 561px) {
		.ligne {
			grid-template-columns: 170px minmax(0, 1fr) auto;
			grid-template-areas:
				'titre phrase phrase'
				'titre ech val';
			padding-right: var(--s7);
		}

		.ligne-titre {
			align-self: start;
			padding-right: 0;
		}

		.ligne-rat,
		.ligne-dpe,
		.ligne-etat {
			grid-column: 2 / -1;
		}

		.ligne-val {
			justify-content: flex-start;
		}
	}

	/* Closed, the detail takes no height: only its « i » shows, in the corner of the row. */
	.det {
		grid-column: 1 / -1;
		min-width: 0;
		margin: 0;
	}

	.det > summary {
		position: absolute;
		top: var(--s2);
		right: 0;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		box-sizing: border-box;
		width: 32px;
		height: 32px;
		border-radius: var(--radius-control);
		color: var(--color-accent);
		list-style: none;
		cursor: pointer;
	}

	.det > summary :global(.det-icone) {
		width: 20px;
		height: 20px;
	}

	.det > summary:hover,
	.det[open] > summary {
		background: var(--color-accent-doux);
		color: var(--color-accent-hover);
	}

	.det > summary::-webkit-details-marker {
		display: none;
	}

	@container (min-width: 561px) {
		.det {
			margin-right: calc(-1 * var(--s7));
		}
	}

	.det-in {
		display: grid;
		gap: var(--s4);
		margin-top: var(--s3);
		padding: var(--s4);
		border-radius: var(--radius-ligne);
		background: var(--color-bg);
		font-size: 14px;
		line-height: 1.4;
	}

	.det-in p {
		margin: 0;
	}

	/* Read by screen readers, not shown (never display: none). */
	.sr {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}

	/* Served figures: label and value side by side, a pale rule under each pair (`dc-dl`). */
	.chiffres {
		display: grid;
		gap: var(--s-demi);
		margin: 0;
	}

	.chiffres div {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		gap: var(--s-fin) var(--s3);
		padding-bottom: var(--s-demi);
		border-bottom: 1px solid var(--color-filet-pale);
	}

	.chiffres dt {
		margin: 0;
		color: var(--color-muted);
	}

	.chiffres dd {
		margin: 0;
		overflow-wrap: anywhere;
	}

	.chiffres .valeur {
		color: var(--color-ink);
		font-family: var(--font-display);
		font-size: 20px;
		font-weight: 500;
		line-height: 1.3;
		font-variant-numeric: tabular-nums;
	}

	.chiffres .millesime {
		grid-column: 2;
		color: var(--color-muted);
	}

	.motif {
		color: var(--color-muted);
	}

	/* The plain explanation reads first, in the ink of the page and one step above the detail. */
	.en-clair {
		max-width: 60ch;
		margin: 0;
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.5;
	}

	.rattachement {
		display: grid;
		gap: var(--s-demi);
		color: var(--color-muted);
	}

	.rattachement strong {
		color: var(--color-ink);
		font-weight: 600;
	}

	.rattachement.incertain strong {
		color: var(--color-warn);
	}

	.copier {
		min-height: 32px;
		margin-left: var(--s-demi);
		padding: 0 var(--s2);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-size: 14px;
		font-weight: 600;
		line-height: inherit;
		cursor: pointer;
	}

	.det-lib {
		color: var(--color-ink);
		font-weight: 600;
	}

	.dc-sources {
		margin: var(--s-fin) 0 0;
		padding-left: var(--s5);
		color: var(--color-muted);
	}

	.dc-sources a {
		color: inherit;
		text-decoration: underline;
		text-underline-offset: 3px;
	}

	@media (max-width: 720px) {
		/* The drawn button stays 32 px high in its corner: its target reaches 44 px. */
		.det > summary::after {
			content: '';
			position: absolute;
			inset: -6px;
		}

		.copier {
			min-height: 44px;
			min-width: 44px;
		}
	}

	@media print {
		.ligne {
			padding-right: 0;
		}

		.det > summary {
			display: none;
		}

		.det {
			margin-right: 0;
		}

		.copier {
			display: none;
		}

		.ligne-titre {
			break-after: avoid;
			padding-right: 0;
		}

		.ligne-phrase {
			break-inside: avoid;
		}
	}

	/* R-07: a read in progress has its own form, apart from the outage (red) and the gap (dashed);
	   set here, the shared badge keeps the forms of the states it knows. */
	.ligne-en_cours :global(.badge-en_cours) {
		border: 1px dotted var(--color-line-strong);
		background: var(--color-paper);
		color: var(--color-neutre);
		font-weight: 400;
	}
</style>
