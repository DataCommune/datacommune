<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title: string;
		badge?: { label: string; className: string };
		children?: Snippet;
	}
	let { title, badge, children }: Props = $props();
</script>

<section class="rounded border border-stone-border bg-parchment p-4">
	<header class="mb-3 flex items-center justify-between">
		<h3 class="text-base font-semibold text-ink">{title}</h3>
		{#if badge}
			<span class="rounded px-2 py-0.5 text-xs {badge.className}">{badge.label}</span>
		{/if}
	</header>
	{#if children}{@render children()}{/if}
</section>
