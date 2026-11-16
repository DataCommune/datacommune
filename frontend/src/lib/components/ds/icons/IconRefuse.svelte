<script lang="ts">
	// Failure cross — replaces the cross emoji of BulkActionModal.svelte:164. Shares its tracé with
	// IconFermer today (semantic distinction only: closing a panel vs marking a failed outcome), the
	// two components are free to diverge without breaking either usage.
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
