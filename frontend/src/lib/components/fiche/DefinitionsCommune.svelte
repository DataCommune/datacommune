<script lang="ts">
	// The block « Définitions » of a commune fiche (A9-9): the texts a reader finds in the panels, written
	// out where there is no panel to open, on paper and on `/commune/{code}/definitions` (no JavaScript).
	import type { EntreeDefinition } from '$lib/data/commune-grid';

	let { definitions }: { definitions: EntreeDefinition[] } = $props();
</script>

<dl class="definitions">
	{#each definitions as entree (entree.id)}
		<dt>{entree.libelle}</dt>
		{#each entree.textes as texte (texte)}<dd>{texte}</dd>{/each}
	{/each}
</dl>

<style>
	.definitions {
		margin: 0;
	}

	.definitions dt {
		margin-top: var(--s2);
		font-weight: 600;
	}

	.definitions dd {
		margin: 0;
	}
</style>
