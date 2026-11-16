<script lang="ts">
	// Checkmark, harmonised tracé (d="M4.5 12.75l6 6 9-13.5") — replaces the checkmark emoji of BulkActionModal.svelte:160.
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
	<path d="M4.5 12.75l6 6 9-13.5" />
</svg>
