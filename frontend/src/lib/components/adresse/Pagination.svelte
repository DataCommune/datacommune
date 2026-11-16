<script lang="ts">
	// Pagination of a list of the address page (UI-SPEC « /adresse/[id] » point 4, VN18), shared by
	// the declared documents and the sales (04-05). It renders the served range (« Documents {a} à
	// {b} sur {N} »), then « Page précédente » / « Page suivante » as GET links that work without
	// JavaScript: each link carries the generation of the list it pages, the caller builds it. With
	// JavaScript the caller handles the click (`onchanger`): navigation in place, announcement of the
	// new range and focus on its title. Hidden on screen for a single page, the range is still
	// printed. Links are preloaded on tap, not on hover (FRONT WR-03 of the D-15 review).
	interface Props {
		/** Served range of the page, or null when the page carries nothing. */
		plage: string | null;
		pages: number;
		lienPrecedent: string | null;
		lienSuivant: string | null;
		/** Accessible name of the navigation (« Pages des documents DPE »). */
		etiquette: string;
		/** Class prefix of the list (`doclist`, `ventes`…), kept for the existing selectors. */
		prefixe: string;
		onchanger?: (event: MouseEvent, href: string) => void;
		/**
		 * A page held by the caller, not by the URL (the panel of the parcel map, 04.10-07): given, its
		 * direction is a `button` with the same class and the same words, which calls it; the link of
		 * that direction is then not rendered.
		 */
		onprecedent?: () => void;
		onsuivant?: () => void;
	}

	let { plage, pages, lienPrecedent, lienSuivant, etiquette, prefixe, onchanger, onprecedent, onsuivant }: Props = $props();
</script>

{#if plage}
	<div class="pagination {prefixe}-pagination" class:pagination-seule={pages <= 1}>
		<p class="pagination-range {prefixe}-range">{plage}</p>
		{#if pages > 1}
			<nav aria-label={etiquette} data-sveltekit-preload-data="tap">
				{#if onprecedent}
					<button type="button" class="pagination-page {prefixe}-page" onclick={onprecedent}>Page précédente</button>
				{:else if lienPrecedent}
					{@const href = lienPrecedent}
					<a class="pagination-page {prefixe}-page" {href} onclick={(event) => onchanger?.(event, href)}>Page précédente</a>
				{/if}
				{#if onsuivant}
					<button type="button" class="pagination-page {prefixe}-page" onclick={onsuivant}>Page suivante</button>
				{:else if lienSuivant}
					{@const href = lienSuivant}
					<a class="pagination-page {prefixe}-page" {href} onclick={(event) => onchanger?.(event, href)}>Page suivante</a>
				{/if}
			</nav>
		{/if}
	</div>
{/if}

<style>
	.pagination {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: var(--s2) var(--s4);
		margin-top: var(--s4);
	}

	/* One page: no pagination on screen (VN18); the range is still printed. */
	.pagination-seule {
		display: none;
	}

	.pagination-range {
		margin: 0;
		font-size: 16px;
		line-height: 1.5;
	}

	.pagination nav {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
	}

	/* Secondary button (UI-SPEC v4 § 4.6): line-strong outline, ink text, control radius, 16 px / 600. */
	.pagination-page {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		box-sizing: border-box;
		min-height: 44px;
		padding: 0 var(--s4);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-style: inherit;
		line-height: inherit;
		font-size: 16px;
		font-weight: 600;
		text-decoration: none;
		cursor: pointer;
	}

	.pagination-page:hover {
		background: var(--color-soft);
	}

	@media print {
		.pagination-seule {
			display: flex;
		}

		.pagination nav {
			display: none;
		}
	}
</style>
