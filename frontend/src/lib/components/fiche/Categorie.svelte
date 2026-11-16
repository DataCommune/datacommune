<script lang="ts">
	// One category of a fiche (4.9, decisions of 06/11), shared by the address and the commune fiches. Above the
	// threshold of two columns it is a card that is always open: its rows are read without a click and its title
	// controls nothing. Under it, a native <details> closed by default, whose closed row announces a served
	// result and its counts. The stylesheet shows a large card before the page is hydrated. The page renders
	// what the category holds.
	import { untrack, type Snippet } from 'svelte';
	import IconeCategorie from '$lib/components/ds/IconeCategorie.svelte';
	import type { IdPartie, SignalPartie } from '$lib/data/questions';

	interface Props {
		id: IdPartie;
		titre: string;
		/** What the closed row announces, served values only. */
		annonce: readonly string[];
		/** What the closed row counts; none when nothing is served to count. */
		signaux?: readonly SignalPartie[];
		/** Above the threshold: a card always open. */
		deuxColonnes?: boolean;
		/** State of the category under the threshold, set by the page for its anchors. */
		ouverte?: boolean;
		/**
		 * Tells the page the reader opened or closed the category. A page passes `ouverte` with this callback,
		 * never `bind:ouverte`: a binding on a child component makes Svelte render the whole page again on a
		 * copied renderer at the server, whose <title> then loses to the default of the root layout (production,
		 * 2026-11-12; scripts/served-titles.test.mjs and src/routes/titre-serveur.test.ts).
		 */
		onbascule?: (ouverte: boolean) => void;
		children: Snippet;
	}
	let { id, titre, annonce, signaux = [], deuxColonnes = false, ouverte = $bindable(false), onbascule, children }: Props = $props();

	function basculer(valeur: boolean) {
		ouverte = valeur;
		onbascule?.(valeur);
	}

	// Opened on the server render before the page is hydrated: the reader's gesture wins over the default.
	if (typeof document !== 'undefined' && untrack(() => (document.getElementById(id) as HTMLDetailsElement | null)?.open)) basculer(true);
</script>

<details class="cat teinte-{id}" {id} bind:open={() => deuxColonnes || ouverte, (valeur) => { if (!deuxColonnes) basculer(valeur); }}>
	<summary tabindex={deuxColonnes ? -1 : undefined} onclick={(event) => deuxColonnes && event.preventDefault()}>
		<h3 class="cat-titre"><IconeCategorie partie={id} />{titre}</h3>
		<span class="cat-annonce">{annonce.join(' · ')}</span>
		{#if signaux.length}
			<span class="cat-signaux">
				{#each signaux as s (s.texte)}
					<span class="signal signal-{s.ton}">{s.texte}</span>
				{/each}
			</span>
		{/if}
		<span class="cat-plus" aria-hidden="true"></span>
	</summary>
	<div class="cat-corps">
		{@render children()}
	</div>
</details>

<style>
	.cat + :global(.cat) {
		border-top: 1px solid var(--color-filet-pale);
	}

	/* The closed row: title, served result, counts, and the sign that it opens. */
	.cat > summary {
		display: grid;
		grid-template-columns: 170px minmax(0, 1fr) 32px;
		align-items: center;
		gap: var(--s-fin) var(--s4);
		min-height: 56px;
		padding: var(--s2) var(--s4) var(--s2) var(--s5);
		list-style: none;
		cursor: pointer;
	}

	.cat > summary::-webkit-details-marker {
		display: none;
	}

	.cat > summary:hover,
	.cat[open] > summary {
		background: var(--c-fond);
	}

	.cat > summary:focus-visible {
		outline-offset: -3px;
	}

	.cat-titre {
		display: flex;
		align-items: center;
		gap: var(--s2);
		grid-row: 1 / span 2;
		margin: 0;
		color: var(--c-encre);
		font-family: var(--font-display);
		font-size: 20px;
		font-weight: 500;
		line-height: 1.2;
		font-variant-numeric: lining-nums;
	}

	.cat-annonce {
		min-width: 0;
		color: var(--color-ink);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.cat-signaux {
		display: grid;
		grid-column: 2;
		min-width: 0;
		font-size: 14px;
		line-height: 1.4;
	}

	.signal {
		min-width: 0;
		color: var(--color-muted);
		overflow-wrap: anywhere;
	}

	.signal-vigilance {
		color: var(--color-warn);
		font-weight: 600;
	}

	.signal-panne {
		color: var(--color-danger);
	}

	/* The sign is drawn: a plus that loses its vertical bar. */
	.cat-plus {
		position: relative;
		grid-column: 3;
		grid-row: 1 / span 2;
		justify-self: end;
		width: 32px;
		height: 32px;
		border: 1px solid var(--color-accent);
		border-radius: var(--radius-control);
		background: var(--color-panneau);
	}

	.cat-plus::before,
	.cat-plus::after {
		content: '';
		position: absolute;
		left: 50%;
		top: 50%;
		width: 12px;
		height: 2px;
		background: var(--color-accent);
		transform: translate(-50%, -50%);
		transition: transform 0.15s ease-out;
	}

	.cat-plus::after {
		transform: translate(-50%, -50%) rotate(90deg);
	}

	.cat[open] .cat-plus::after {
		transform: translate(-50%, -50%);
	}

	.cat-corps {
		container-type: inline-size;
		padding: var(--s1) var(--s5) var(--s4);
	}

	/* Two columns: one card per category. The stylesheet opens it, so that nothing moves at the hydration and
	   a page without JavaScript reads the same; the result of the closed row would repeat the rows under it. */
	@media screen and (min-width: 1081px) {
		.cat {
			overflow: clip;
			border: 1px solid var(--color-line);
			border-radius: var(--radius-panneau);
			background: var(--color-panneau);
		}

		.cat + :global(.cat) {
			border-top: 1px solid var(--color-line);
		}

		.cat::details-content {
			content-visibility: visible;
		}

		.cat > summary,
		.cat > summary:hover,
		.cat[open] > summary {
			grid-template-columns: minmax(0, 1fr) minmax(0, 340px);
			border-bottom: 1px solid var(--color-line);
			background: var(--c-fond);
			cursor: default;
		}

		.cat-titre {
			grid-row: auto;
			font-size: 24px;
		}

		.cat-annonce,
		.cat-plus {
			display: none;
		}
	}

	@media (max-width: 720px) {
		.cat > summary {
			grid-template-columns: minmax(0, 1fr) 32px;
			padding: var(--s3) var(--s3) var(--s3) var(--s4);
		}

		.cat-titre {
			grid-row: auto;
		}

		.cat-signaux {
			grid-column: 1;
		}

		.cat-plus {
			grid-column: 2;
			grid-row: 1 / span 3;
		}

		.cat-corps {
			padding: 0 var(--s4) var(--s3);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.cat-plus::before,
		.cat-plus::after {
			transition: none;
		}
	}

	@media print {
		.cat-plus {
			display: none;
		}

		.cat > summary {
			display: block;
			min-height: 0;
			padding: var(--s2) 0;
		}

		.cat-corps {
			padding: 0;
		}
	}
</style>
