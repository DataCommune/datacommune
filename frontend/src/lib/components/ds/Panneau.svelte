<script lang="ts">
	// Panel, the container of a block (UI-SPEC v4 § 3.0, § 3.1): panel background, 1 px line, panel
	// radius, a section named by its h2.
	//   - `partie`: the title and its lead on a sage band as wide as the panel, a line under the band;
	//   - `simple`: the title alone on the band, then the line;
	//   - `essentiel`: padded, the title without a band.
	// The content keeps its own margins under the band (zero-specificity defaults: a child styles itself).
	import type { Snippet } from 'svelte';
	import type { IdPartie } from '$lib/data/questions';

	interface Props {
		titre: string;
		variante?: 'partie' | 'simple' | 'essentiel';
		/** The category the panel speaks of: the band and the title wear its tint. */
		teinte?: IdPartie;
		/** Lead sentence under the title. */
		chapo?: string;
		/** Anchor of the section. */
		id?: string;
		/** Id of the title when a page links to it (`#…`): the title then takes the focus the link moves. */
		titreId?: string;
		children?: Snippet;
	}

	let { titre, variante = 'simple', teinte, chapo, id, titreId: titreIdDonne, children }: Props = $props();

	const uid = $props.id();
	const titreId = $derived(titreIdDonne ?? `${uid}-titre`);
</script>

<section class="panneau panneau-{variante} {teinte ? `teinte-${teinte}` : ''}" class:avec-chapo={Boolean(chapo)} {id} aria-labelledby={titreId}>
	<!-- svelte-ignore a11y_no_noninteractive_tabindex (only -1, never in the tab order: the title linked by `#…` takes the focus the link moves) -->
	<h2 id={titreId} class="panneau-titre" tabindex={titreIdDonne ? -1 : undefined}>{titre}</h2>
	{#if chapo}<p class="panneau-chapo">{chapo}</p>{/if}
	{#if children}
		<div class="panneau-corps">{@render children()}</div>
	{/if}
</section>

<style>
	.panneau {
		margin-top: var(--s6);
		padding: 0 0 var(--s4);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-panneau);
	}

	.panneau-titre {
		margin: 0;
		color: var(--c-encre, var(--color-ink));
		font-family: var(--font-display);
		font-size: 30px;
		font-weight: 500;
		line-height: 1.2;
		letter-spacing: -0.01em;
		text-wrap: balance;
	}

	/* The band of a part or of a simple panel: the title, and its lead when there is one. Drawn on the
	   section, behind their two rows: the lead keeps the reading measure, the band the width of the panel. */
	.panneau-partie,
	.panneau-simple {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
	}

	.panneau-partie::before,
	.panneau-simple::before {
		content: '';
		grid-area: 1 / 1 / 3 / 2;
		border-bottom: 1px solid var(--color-line);
		border-radius: var(--radius-bande) var(--radius-bande) 0 0;
		background: var(--c-fond, var(--color-soft));
	}

	.panneau-partie .panneau-titre,
	.panneau-simple .panneau-titre {
		grid-area: 1 / 1;
		padding: var(--s5) var(--s5) 0;
	}

	.panneau-partie:not(.avec-chapo) .panneau-titre,
	.panneau-simple:not(.avec-chapo) .panneau-titre {
		padding-bottom: var(--s4);
	}

	.panneau-chapo {
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.panneau-partie .panneau-chapo,
	.panneau-simple .panneau-chapo {
		grid-area: 2 / 1;
		max-width: var(--mesure);
		padding: var(--s1) var(--s5) var(--s4);
		color: var(--color-ink);
	}

	.panneau-corps {
		padding: 0 var(--s5);
	}

	:where(.panneau-corps) > :global(*) {
		margin-top: var(--s4);
	}

	:where(.panneau-corps) > :global(p) {
		max-width: var(--mesure);
	}

	.panneau-essentiel {
		padding: var(--s5);
	}

	.panneau-essentiel .panneau-chapo {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s2);
		margin-top: var(--s-demi);
	}

	.panneau-essentiel .panneau-corps {
		padding: 0;
	}

	@media (max-width: 720px) {
		.panneau-titre {
			font-size: 26px;
		}

		.panneau-partie .panneau-titre,
		.panneau-simple .panneau-titre {
			padding: var(--s4) var(--s4) 0;
		}

		.panneau-partie:not(.avec-chapo) .panneau-titre,
		.panneau-simple:not(.avec-chapo) .panneau-titre {
			padding-bottom: var(--s3);
		}

		.panneau-partie .panneau-chapo,
		.panneau-simple .panneau-chapo {
			padding: var(--s1) var(--s4) var(--s3);
		}

		.panneau-corps {
			padding: 0 var(--s4);
		}

		.panneau-essentiel {
			padding: var(--s4);
		}

		.panneau-essentiel .panneau-corps {
			padding: 0;
		}
	}

	@media print {
		.panneau-titre {
			break-after: avoid;
		}
	}
</style>
