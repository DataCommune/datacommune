<script lang="ts">
	// « Ajouter à la comparaison » of one commune of the directory (04.6-02): the same tab selection
	// and the same confirmation as the search results (`confirmationAjout`), announced by a live
	// region. A JavaScript enhancement: the button is rendered once the page is hydrated, so the
	// served HTML offers no control that does nothing without JavaScript.
	import { onMount } from 'svelte';
	import {
		comparisonSelection,
		confirmationAjout,
		type ConfirmationAjout
	} from '$lib/stores/comparison-selection.svelte';

	let { code, nom }: { code: string; nom: string } = $props();

	let hydrate = $state(false);
	let confirmation = $state<ConfirmationAjout>(null);

	onMount(() => {
		hydrate = true;
	});

	function ajouter() {
		const ajout = comparisonSelection.add(code);
		confirmation = confirmationAjout(ajout, comparisonSelection.count) ?? confirmation;
	}
</script>

{#if hydrate}
	<div class="ajout">
		<!-- D7, D11 (2026-11-10): an action written as a link, lighter than the row it follows, on one line. -->
		<button type="button" class="bouton-lien ajout-bouton" onclick={ajouter}>
			Ajouter à la comparaison<span class="ajout-sr"> : {nom}</span>
		</button>
		<p class="ajout-confirmation" class:ajout-confirmation-vide={confirmation === null} role="status">
			{#if confirmation}
				{confirmation.texte}{#if confirmation.lien}&nbsp;·
					<a href={comparisonSelection.href}>Ouvrir le comparateur</a>{/if}
			{/if}
		</p>
	</div>
{/if}

<style>
	.ajout {
		position: relative;
		display: flex;
		flex-direction: column;
		gap: var(--s1);
	}

	/* The socle's `.bouton-lien`; it keeps to its text, never stretched by the column of the flex. */
	.ajout-bouton {
		align-self: flex-start;
		white-space: nowrap;
	}

	.ajout-confirmation {
		margin: 0;
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.ajout-confirmation a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	/* Empty, the live region stays in the tree (its first message is announced) but takes no room. */
	.ajout-confirmation-vide,
	.ajout-sr {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}

	@media print {
		.ajout {
			display: none;
		}
	}
</style>
