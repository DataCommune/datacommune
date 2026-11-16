<script lang="ts">
	// Downward chevron, harmonised at stroke-width 1.5 from SiteHeader.svelte:220 (d="M19 9l-7 7-7-7"); the FAQ component that shared it was removed in 03-05.
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
	<path d="M19 9l-7 7-7-7" />
</svg>
