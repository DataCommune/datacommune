<script lang="ts">
	// Three-storey building, new tracé — replaces the building emoji of mon-espace/+page.svelte:65.
	let { class: classe = 'h-4 w-4' }: { class?: string } = $props();
</script>

<svg
	class={classe}
	aria-hidden="true"
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width="1.5"
	stroke-linecap="round"
	stroke-linejoin="round"
>
	<path d="M6 21V5a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v16" />
	<path d="M14 10h4a1 1 0 0 1 1 1v10" />
	<path d="M9 8h1" />
	<path d="M9 12h1" />
	<path d="M9 16h1" />
	<path d="M17 14h1" />
	<path d="M17 18h1" />
	<path d="M3 21h18" />
</svg>
