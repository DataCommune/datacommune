<script lang="ts">
	// Close cross, harmonised from ExportDialog.svelte:114 (d="M18 6 6 18M6 6l12 12") — closes a panel or dialog.
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
	<path d="M18 6 6 18M6 6l12 12" />
</svg>
