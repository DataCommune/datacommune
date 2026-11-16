<script lang="ts">
	// Mark of a category of the fiche (charter of 06/11): one drawing per category, in the ink of its tint. It
	// comes beside the name of the category, which says it: the drawing is decoration, never read.
	import type { IdPartie } from '$lib/data/questions';

	interface Props {
		partie: IdPartie;
		taille?: 18 | 20 | 24;
	}

	let { partie, taille = 24 }: Props = $props();
</script>

<svg
	class="icone-categorie"
	width={taille}
	height={taille}
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width="1.6"
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
	focusable="false"
>
	{#if partie === 'q-prix'}
		<path d="M12.6 3H5a2 2 0 0 0-2 2v7.6c0 .5.2 1 .6 1.4l6.4 6.4a2 2 0 0 0 2.8 0l7.6-7.6a2 2 0 0 0 0-2.8L14 3.6a2 2 0 0 0-1.4-.6Z" />
		<circle cx="8" cy="8" r="1.4" />
	{:else if partie === 'q-logement'}
		<rect x="3" y="5" width="18" height="14" rx="1.6" />
		<path d="M3 9.7h18M3 14.3h18M9 5v4.7M15 5v4.7M6.5 9.7v4.6M12 9.7v4.6M17.5 9.7v4.6M9 14.3V19M15 14.3V19" />
	{:else if partie === 'q-quotidien'}
		<path d="M12 2.5v19M9 21.5h6M6.5 4.5h11l3 2.75-3 2.75h-11zM17.5 12.5h-11l-3 2.75 3 2.75h11z" />
	{:else}
		<path d="M12 3.5a9 9 0 0 1 9 9H3a9 9 0 0 1 9-9ZM12 2v1.5M12 12.5v6a2.5 2.5 0 0 0 5 0" />
	{/if}
</svg>

<style>
	.icone-categorie {
		flex: none;
		color: var(--c-encre, currentColor);
	}
</style>
