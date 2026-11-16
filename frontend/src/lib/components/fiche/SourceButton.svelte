<script lang="ts">
	// The one information button of the site (review of 06/11): an « i » alone, that opens the panel of a value
	// in one action. It carries the source, the period and the definition, so that the page shows the value
	// first. The name says what it opens; it never carries a native hover tooltip. The button box is the
	// 44 × 44 hit area (WCAG 2.5.5), pulled back by negative margins so the row keeps the size of the icon.
	// `forme` only words that name.
	import IconInfo from '$lib/components/ds/icons/IconInfo.svelte';

	interface Props {
		libelle: string;
		forme?: 'source' | 'detail';
		onouvrir: (declencheur: HTMLButtonElement) => void;
	}

	let { libelle, forme = 'source', onouvrir }: Props = $props();
	let bouton = $state<HTMLButtonElement>();
</script>

<button
	bind:this={bouton}
	type="button"
	class="source-button"
	aria-label={forme === 'detail' ? `Détail : ${libelle}` : `Source et définition : ${libelle}`}
	aria-haspopup="dialog"
	onclick={() => bouton && onouvrir(bouton)}
>
	<IconInfo class="source-button-icone" />
</button>

<style>
	.source-button {
		display: inline-flex;
		flex: none;
		align-items: center;
		justify-content: center;
		box-sizing: border-box;
		min-width: 44px;
		min-height: 44px;
		margin: calc(-1 * var(--s3));
		padding: 0 var(--s3);
		border: 0;
		border-radius: var(--radius-control);
		background: transparent;
		color: var(--color-accent);
		line-height: 1;
		vertical-align: middle;
		cursor: pointer;
	}

	.source-button:hover {
		background: var(--color-accent-doux);
		color: var(--color-accent-hover);
	}

	/* The 44 px target overflows its row by 12 px on each side, onto the line above: the contract ring
	   (3 px, same colour) is drawn inside that box, 2 px around the icon, instead of 4 px outside it. */
	.source-button:focus-visible {
		outline-offset: -10px;
	}

	.source-button :global(.source-button-icone) {
		flex: none;
		width: 20px;
		height: 20px;
	}

	@media print {
		.source-button {
			display: none;
		}
	}
</style>
