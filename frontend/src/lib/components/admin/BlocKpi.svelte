<script lang="ts">
	// Motif « étiquette au-dessus, valeur en dessous » — répété au moins deux fois
	// (l'ancien aperçu de fiche de la landing, admin/OverviewCards.svelte) et extrait ici (56-05, D-07).
	//
	// Les deux variantes divergent volontairement : `filet` (registre document, contenu non
	// actionnable — landing, Fiche Commune/Bien) sépare par un trait, `bordee` (écrans
	// applicatifs où une carte est réellement un objet posé) garde la carte bordée. Le
	// composant expose la divergence en prop plutôt que d'imposer une forme unique.
	//
	// La valeur porte `.chiffre` (chasse fixe) et une graisse Inter franche — jamais l'utilitaire
	// réservé au logo et au H1 de page (D-01) : un chiffre de KPI n'est ni l'un ni l'autre.
	import type { Snippet } from 'svelte';

	interface Props {
		libelle: string;
		valeur: string;
		variante?: 'filet' | 'bordee';
		/** Affordance optionnelle (déclencheur d'explication) — le parent fournit InfoPopover,
		 *  ce composant ne le connaît pas. */
		affordance?: Snippet;
	}

	let { libelle, valeur, variante = 'filet', affordance }: Props = $props();
</script>

<div class="ds-bloc-kpi ds-bloc-kpi--{variante}">
	<span class="ds-bloc-kpi__libelle">
		{libelle}
		{#if affordance}
			{@render affordance()}
		{/if}
	</span>
	<span class="chiffre ds-bloc-kpi__valeur">{valeur}</span>
</div>

<style>
	.ds-bloc-kpi__libelle {
		display: flex;
		align-items: center;
		gap: 4px;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-ink-muted);
	}
	.ds-bloc-kpi__valeur {
		display: block;
		margin-top: 4px;
		font-weight: 600;
		font-size: 24px;
		line-height: 1.1;
		color: var(--color-ink);
	}

	/* Variante « filet » — registre document, contenu non actionnable. */
	.ds-bloc-kpi--filet {
		padding: 16px 20px;
		border-bottom: 1px solid var(--color-hairline);
	}

	/* Variante « bordee » — carte applicative, un objet réellement posé (OverviewCards.svelte). */
	.ds-bloc-kpi--bordee {
		padding: 16px;
		border: 1px solid var(--color-stone-border);
		border-radius: 8px;
		background: var(--color-parchment);
	}
</style>
