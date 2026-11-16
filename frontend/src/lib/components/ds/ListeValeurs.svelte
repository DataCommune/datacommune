<script lang="ts" module>
	import type { Snippet } from 'svelte';
	import type { EntreeVolet, EtatKpi, SourceKpi, ValeurServie } from '$lib/data/commune-kpi';
	import type { Pictogramme } from './RepereEchelle.svelte';

	export interface ValeurDeListe {
		/** Stable key of the row within the list. */
		id: string;
		libelle: string;
		/** Served value or declared absence, rendered by StateValue (an absence is never a zero). */
		etat: Exclude<EtatKpi, { kind: 'absence-non-declaree' }>;
		/** Field of the semantic contract the value comes from (`data-field-id`). */
		fieldId?: string | null;
		/** Note of the value: its period, level 1 (A3). */
		note?: string | null;
		/** Caveats of the value: printed under it (the source panel says them on screen). */
		reserves?: readonly string[];
		/** Source panel of the row, opened in one action by « Source » (UI-01). */
		volet?: EntreeVolet[];
		brut?: ValeurServie[];
		sourcePanneau?: SourceKpi | null;
		/** The label is the neutral provisional one (a field without a decided label): marked. */
		provisoire?: boolean;
		/** A served number: its digits never break (UI-010); a text wraps. */
		numerique?: boolean;
		/** Shows the glossary sentence of a served state under its badge (true by default). */
		aide?: boolean;
		/**
		 * A value served at another scale than the page (R-09: a value of its commune on an arrondissement): the
		 * scale mark of `RepereEchelle` under the value, and `enClair` in its place on paper (no pictogram there).
		 */
		echelle?: { pictogramme?: Pictogramme; mot: string; enClair: string };
		/** Where the value is to be read when it is not here (R-09: the other mesh), a link under the value. */
		lien?: { href: string; texte: string };
		/**
		 * Content of the value given by the page, rendered in place of StateValue (a value that carries links:
		 * an address, a dataset, a replacing document; P7 of the DPE document, 2026-11-10). `etat` still says
		 * the value in plain text; term, field id, note and « i » are unchanged.
		 */
		contenu?: Snippet;
	}
</script>

<script lang="ts">
	// List of values, `dl.tab` of the mock-up (UI-SPEC v4 § 3.13, `style.css:244-250`): one `.tab-l` row
	// per value, a `1fr 1.2fr` grid (one column under 720 px), a pale line between two rows. The term is
	// Inter 400; the value is the served one in Newsreader 20 through StateValue, so that an absence keeps
	// its served word and is never read as a zero (D45-01), then the note in 14 px. Each row keeps the field
	// id of its value and, when it has one, the « i » that opens its panel, at the end of the value: definition,
	// source and period are read there, the period being printed under the value on paper (review of 06/11).
	// Renders the model: no figure is computed here.
	import SourceButton from '$lib/components/fiche/SourceButton.svelte';
	import SourcePanel from '$lib/components/fiche/SourcePanel.svelte';
	import StateValue from '$lib/components/fiche/StateValue.svelte';
	import RepereEchelle from './RepereEchelle.svelte';

	interface Props {
		valeurs: readonly ValeurDeListe[];
		/** Prose of the page with its acronyms spelled out (04.8-08, D-31): `ligne:<id>:libelle`. */
		textes?: ReadonlyMap<string, string>;
		/** Form of the button of each row (`SourceButton`). */
		forme?: 'source' | 'detail';
	}

	let { valeurs, textes, forme }: Props = $props();

	let panneaux = $state<Record<string, ReturnType<typeof SourcePanel> | undefined>>({});
	// The visible term; the button and its panel keep the raw label.
	const libelleDe = (valeur: ValeurDeListe) => textes?.get(`ligne:${valeur.id}:libelle`) ?? valeur.libelle;
</script>

<dl class="tab">
	{#each valeurs as valeur (valeur.id)}
		<div class="tab-l" data-field-id={valeur.fieldId ?? undefined} data-ligne={valeur.id}>
			<dt>{#if valeur.provisoire}<span data-provisoire>{libelleDe(valeur)}</span>{:else}{libelleDe(valeur)}{/if}</dt>
			<dd>
				<div class="tab-v" class:tab-v-nombre={valeur.numerique}
					>{#if valeur.contenu}{@render valeur.contenu()}{:else}<StateValue etat={valeur.etat} aide={valeur.aide ?? true} />{/if}</div
				>
				{#if valeur.echelle}
					<span class="tab-echelle"
						><span class="tab-echelle-ecran"><RepereEchelle pictogramme={valeur.echelle.pictogramme} mot={valeur.echelle.mot} /></span
						><span class="tab-echelle-clair">{valeur.echelle.enClair}</span></span
					>
				{/if}
				{#if valeur.lien}
					<a class="tab-lien" href={valeur.lien.href}>{valeur.lien.texte}</a>
				{/if}
				{#if valeur.note}
					<span class="tab-n" class:tab-n-au-volet={valeur.volet}><span class="tab-note"><span class="tab-note-texte">{valeur.note}</span></span></span>
				{/if}
				{#if valeur.volet}
					<SourceButton {forme} libelle={valeur.libelle} onouvrir={(declencheur) => panneaux[valeur.id]?.ouvrir(declencheur)} />
				{/if}
				{#each valeur.reserves ?? [] as reserve, index (index)}
					<span class="tab-reserve">{reserve}</span>
				{/each}
				{#if valeur.volet}
					<SourcePanel
						bind:this={panneaux[valeur.id]}
						titre={valeur.libelle}
						entrees={valeur.volet}
						brut={valeur.brut ?? []}
						source={valeur.sourcePanneau ?? null}
					/>
				{/if}
			</dd>
		</div>
	{/each}
</dl>

<style>
	.tab {
		margin: 0;
	}

	.tab-l {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
		gap: var(--s4);
		padding: var(--s3) 0;
	}

	.tab-l + .tab-l {
		border-top: 1px solid var(--color-filet-pale);
	}

	dt {
		min-width: 0;
		color: var(--color-ink);
		font-family: var(--font-sans);
		font-size: 16px;
		font-weight: 400;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	/* The value, its « i » at the end of the row, the rest under them. */
	dd {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: var(--s-fin) var(--s2);
		min-width: 0;
		margin: 0;
	}

	dd > :global(*) {
		grid-column: 1 / -1;
	}

	.tab-v {
		grid-column: 1;
		grid-row: 1;
	}

	dd > :global(.source-button) {
		grid-column: 2;
		grid-row: 1;
	}

	/* The value of a row, Newsreader 20 with tabular figures (§ 2.2); a badge keeps its own size. */
	.tab-v :global(.state-value) {
		font-size: 20px;
		line-height: 1.3;
	}

	.tab-v-nombre :global(.state-value .value) {
		white-space: nowrap;
	}

	.tab-v :global(p) {
		max-width: var(--mesure);
	}

	/* A period the panel says stays out of the screen: the value reads alone. */
	@media screen {
		.tab-n-au-volet {
			display: none;
		}
	}

	.tab-n {
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.tab-note {
		max-width: var(--mesure);
		overflow-wrap: anywhere;
	}

	/* A value of another scale (R-09): its mark on screen, the scale in words on paper. */
	.tab-echelle-clair {
		display: none;
	}

	/* Where to read a value absent here: a link of the site, a 44 px target under the value. */
	.tab-lien {
		display: inline-flex;
		align-items: center;
		justify-self: start;
		min-height: 44px;
		color: var(--color-accent);
		font-size: 16px;
		text-decoration: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
		overflow-wrap: anywhere;
	}

	.tab-lien:hover {
		color: var(--color-accent-hover);
		text-decoration-thickness: 2px;
	}

	/* On screen the source panel says the caveats; on paper they stay under the value. */
	.tab-reserve {
		display: none;
	}

	@media (max-width: 720px) {
		.tab-l {
			grid-template-columns: minmax(0, 1fr);
			gap: var(--s1);
		}
	}

	@media print {
		.tab-l {
			break-inside: avoid;
		}

		.tab-echelle-ecran {
			display: none;
		}

		.tab-echelle-clair {
			display: inline;
			color: var(--color-ink);
			font-size: 14px;
		}

		.tab-reserve {
			display: block;
			color: var(--color-ink);
			font-size: 14px;
			line-height: 1.4;
			overflow-wrap: anywhere;
		}
	}
</style>
