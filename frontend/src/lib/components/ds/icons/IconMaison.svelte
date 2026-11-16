<script lang="ts">
	// House, new tracé (roof + body) — replaces the house emoji of mon-espace/+page.svelte:87.
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
	<path d="M3 11.5 12 4l9 7.5" />
	<path d="M5.5 10v9a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1v-9" />
	<path d="M9.5 20v-6h5v6" />
</svg>
