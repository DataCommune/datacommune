<script lang="ts">
	// The arrival of the address fiche (4.9, reading of 06/11; CADRAGE R1, R3): the figures of the head, then
	// one discreet line that names what touches the address, each name a link to its criterion. Everything is
	// read from what the API serves: the line from `resume.pointsAdresse`, the value of a name from its served
	// `accroche`. The vigilances of the whole commune, the sentence and its counts live in « Risques »
	// (BilanRisques). Nothing is computed here: no count, no split between scales, no summary when none is
	// served.
	import type { Snippet } from 'svelte';
	import type { ChiffreCleRapport, ThemeRapport } from '$lib/api/rapport';
	import type { ResumeFiche } from '$lib/data/adresse-rapport';
	import { signauxArrivee } from '$lib/data/questions';
	import { typographieFr } from '$lib/utils/typographie';
	import ChiffresCles from '$lib/components/adresse/rapport/ChiffresCles.svelte';

	interface Props {
		/** The served summary brought back to the rendered rows (`lireResume`); null when it is not served in full. */
		resume: ResumeFiche | null;
		/** The key figures, `rapport.chiffresCles`. */
		chiffres: readonly ChiffreCleRapport[];
		/** The themes of the report: a key figure reads its source theme. */
		themes: readonly ThemeRapport[];
		/** Prose of the page with its acronyms spelled out (04.8-08, D-31): `cle:<id>` (key figures), `bref:bref-l-adresse:<id>`. */
		textes?: ReadonlyMap<string, string>;
		/** The fiche of the commune, where a served zero of the DPE sends the reader. */
		lienCommune?: string;
		/** The next step after the price, a link of the page, given to the figures. */
		suite?: Snippet;
	}
	let { resume, chiffres, themes, textes, lienCommune, suite }: Props = $props();

	const uid = $props.id();
	const titreId = `${uid}-titre`;
	const adresse = $derived(signauxArrivee(resume?.pointsAdresse ?? []));
</script>

<section class="bref" id="bref" aria-labelledby={titreId}>
	<h2 id={titreId} class="sr-only">En bref</h2>

	{#if chiffres.length}<ChiffresCles {chiffres} {themes} {textes} {lienCommune} suitePrix={suite} />{/if}

	{#if resume}
		<div class="arrivee" class:avec-points={adresse.length > 0}>
			<h3>Vigilances à l’adresse ou autour</h3>
			{#if adresse.length}
				<ul class="bref-l bref-l-adresse">
					{#each adresse as theme (theme.id)}
						<li>
							<!-- `bref-fin` keeps a value and its arrow in one piece of the line (phone layout below). -->
							<a href={`#critere-${theme.id}`}
								><span class="bref-t">{textes?.get(`bref:bref-l-adresse:${theme.id}`) ?? typographieFr(theme.titre)}</span
								>{#if theme.accroche}{' '}<span class="bref-fin"
										><span class="bref-v">{theme.accroche}</span><span class="fleche" aria-hidden="true">↓</span></span
									>{:else}<span class="fleche" aria-hidden="true">↓</span>{/if}</a
							>
						</li>
					{/each}
				</ul>
			{:else}
				<!-- Nothing was read at this scale: never « Rien à signaler » then (UI-SPEC § 5, rule 1). -->
				<p class="bref-vide">{resume.lusAdresse ? 'Rien à signaler au point, au bâtiment ni aux alentours.' : 'Aucun critère lu à cette échelle.'}</p>
			{/if}
		</div>
	{:else}
		<p class="bref-indisponible">Le résumé de cette fiche n’est pas disponible.</p>
	{/if}
</section>

<style>
	.arrivee {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--s-demi) var(--s3);
		margin-top: var(--s3);
		padding: var(--s2) var(--s4);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-tuile);
		background: var(--color-paper);
		font-size: 16px;
		line-height: 1.5;
	}

	.arrivee h3 {
		margin: 0;
		color: var(--color-muted);
		font-family: var(--font-sans);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.5;
	}

	.avec-points h3 {
		color: var(--color-warn);
	}

	.bref-l {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s-fin) var(--s4);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.bref-l a {
		color: var(--color-accent);
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.bref-l a:hover {
		color: var(--color-accent-hover);
		text-decoration-thickness: 2px;
	}

	.bref-t {
		font-weight: 600;
	}

	.bref-v {
		color: var(--color-warn);
	}

	.fleche {
		margin-left: var(--s-demi);
		color: var(--color-accent);
	}

	.bref-vide,
	.bref-indisponible {
		max-width: var(--mesure);
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.5;
	}

	.bref-indisponible {
		margin-top: var(--s3);
	}

	@media (max-width: 720px) {
		/* One running line on the short first screen: the padding of an inline link takes no room in the line
		   and brings its target to 44 px (§ 2.3). */
		.arrivee {
			display: block;
			margin-top: var(--s2);
			padding: var(--s2) var(--s3);
			font-size: 14px;
			line-height: 1.75;
		}

		.arrivee h3,
		.bref-l {
			display: inline;
		}

		.arrivee h3::after {
			content: '\00a0: ';
		}

		/* Each item is one box of the line: the items carry no space between them, so as plain inlines they ran
		   together into one unbreakable chunk and the line broke inside names (« Remontée de / nappe »). An item
		   that does not fit moves whole to the next line; only one longer than a line wraps, from the left edge. */
		.bref-l li {
			display: inline-block;
			max-width: 100%;
			margin-right: var(--s2);
		}

		/* An item longer than a line breaks between its name and its value, never inside the value
		   (« 1 / mouvement de terrain »). */
		.bref-fin {
			display: inline-block;
			max-width: 100%;
			/* An inline-block does not take the underline of its link: carried over, hover included. */
			text-decoration: inherit;
		}

		.bref-l a {
			padding-block: var(--s2);
		}
	}
</style>
