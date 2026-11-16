<script lang="ts">
	// Nature switch of `/comparer` (UI-SPEC « /comparer — même nature seulement », D-102, 04-10):
	// three links of 44 px, « Communes ({n}) », « Adresses ({n}) », « Documents DPE ({n}) ». Each
	// nature keeps its own selection; a link opens it (its link, or its empty comparator). The active
	// one carries `aria-current="page"`, underlined in `ink` like the active link of the header.
	// Nothing here reads or writes a selection: the page gives the counts and the links.
	//
	// T5 (direction A, proposed on 2026-11-10, to be reread by Ianis): an empty nature shows its name
	// alone, its link kept (« (0) » three times was the first figure read on the empty comparator); a
	// count is written as before, « Communes (3) ».
	import type { NatureSelection } from '$lib/data/commune-comparison-selection';

	interface Props {
		active: NatureSelection;
		comptes: Record<NatureSelection, number>;
		liens: Record<NatureSelection, string>;
	}

	let { active, comptes, liens }: Props = $props();

	const LIBELLES: { nature: NatureSelection; libelle: string }[] = [
		{ nature: 'commune', libelle: 'Communes' },
		{ nature: 'adresse', libelle: 'Adresses' },
		{ nature: 'dpe', libelle: 'Documents DPE' }
	];
</script>

<nav class="nature-nav" aria-label="Nature des objets comparés">
	{#each LIBELLES as { nature, libelle } (nature)}
		<a
			class="nature-nav-lien"
			href={liens[nature]}
			data-nature={nature}
			aria-current={nature === active ? 'page' : undefined}
			><!-- One flex item: the name and its count read as one accessible name. --><span
				>{libelle}{#if comptes[nature] > 0}{' '}(<span class="chiffre">{comptes[nature]}</span>){/if}</span
			></a
		>
	{/each}
</nav>

<style>
	.nature-nav {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s1) var(--s2);
		margin-top: var(--s4);
		border-bottom: 1px solid var(--color-line);
	}

	.nature-nav-lien {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		padding: 0 var(--s2);
		border-bottom: 2px solid transparent;
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.2;
		text-decoration: none;
	}

	.nature-nav-lien:hover {
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	.nature-nav-lien[aria-current='page'] {
		border-bottom-color: var(--color-ink);
		font-weight: 600;
	}

	/* Up to 720 px the three links hold on one line (375 px, 13 px text) right under the page header
	   (its title alone once a table is shown; surtitle and method sentence only without a table), so
	   that the switch costs one line of 44 px above the table (the commune comparator keeps its
	   header in the first screen of 375 x 800, commune-comparison.spec.ts); the 44 px targets are
	   kept. */
	@media (max-width: 720px) {
		.nature-nav {
			gap: 0;
			margin-top: 0;
		}

		.nature-nav-lien {
			padding: 0 var(--s1);
			font-size: 14px;
		}
	}

	@media print {
		.nature-nav {
			display: none;
		}
	}
	@media screen {
		:global(.public-site) .nature-nav-lien[aria-current='page'] { border-bottom-color: var(--color-accent); color: var(--color-accent); }
	}
</style>
