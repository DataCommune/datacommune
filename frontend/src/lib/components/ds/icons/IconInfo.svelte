<script lang="ts">
	// Info glyph — trigger of the metric disclosure (SourceButton).
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
	<circle cx="12" cy="12" r="10" />
	<path d="M12 16v-4" />
	<path d="M12 8h.01" />
</svg>
