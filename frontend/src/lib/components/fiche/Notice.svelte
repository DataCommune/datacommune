<script lang="ts">
	// State panel of a page (UI-SPEC v4 § 3.13, « Les quatre états servis »): a `simple` panel, its title
	// on the sage band above a line. `warn` (out of coverage, outage) keeps the ink of the title and the
	// text, without an ochre fill: ochre belongs to the Vigilance. An outage is announced (`alert`); a
	// state the page simply is in is a `status`. The title keeps the heading outline of the page: an h2
	// by default, an h3 inside a section already titled by an h2 (D-110 notice, 2026-10-05).
	import type { Snippet } from 'svelte';

	interface Props {
		titre: string;
		variante?: 'info' | 'warn';
		role?: 'status' | 'alert';
		niveau?: 2 | 3;
		children?: Snippet;
	}

	let { titre, variante = 'info', role = 'status', niveau = 2, children }: Props = $props();
</script>

<div class="notice" class:notice-warn={variante === 'warn'} class:notice-h3={niveau === 3} {role}>
	<svelte:element this={niveau === 3 ? 'h3' : 'h2'} class="notice-title">{titre}</svelte:element>
	{#if children}
		<div class="notice-corps">{@render children()}</div>
	{/if}
</div>

<style>
	.notice {
		box-sizing: border-box;
		max-width: 100%;
		padding: 0 0 var(--s4);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-panneau);
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.6;
		overflow-wrap: anywhere;
	}

	.notice-title {
		margin: 0;
		padding: var(--s5) var(--s5) var(--s4);
		border-bottom: 1px solid var(--color-line);
		border-radius: var(--radius-bande) var(--radius-bande) 0 0;
		background: var(--color-soft);
		color: var(--color-ink);
		font-family: var(--font-display);
		font-size: 30px;
		font-weight: 500;
		line-height: 1.2;
		text-wrap: balance;
	}

	.notice-h3 .notice-title {
		font-size: 22px;
		line-height: 1.25;
	}

	.notice-corps {
		padding: 0 var(--s5);
	}

	.notice-corps > :global(*) {
		margin: var(--s4) 0 0;
	}

	.notice-corps > :global(p) {
		max-width: var(--mesure);
	}

	@media (max-width: 720px) {
		.notice-title {
			padding: var(--s4) var(--s4) var(--s3);
			font-size: 26px;
		}

		.notice-h3 .notice-title {
			font-size: 20px;
		}

		.notice-corps {
			padding: 0 var(--s4);
		}
	}
</style>
