<script lang="ts">
	// Cards of DVF sales (UI-SPEC « /adresse/[id] » point 5, « Espacement » : same template as the
	// card of a document, 12 / 16 px, ≤ 160 px at 375 px; D-114, D-115, LIEU-02). One ordered list in
	// the served order. Line 1 « {mois année} · {nature} » (and « à {d} m (à vol d'oiseau) » for a
	// similar sale); line 2 the served composition and the declared built surface; line 3 the price
	// and the price per m², or the neutral badge « Prix non affiché » when the API withholds it (its
	// reason is read in the panel of the section). A sale is attached to no dwelling; nothing here
	// filters, sorts or counts (D-51): the API decided the order and the withheld prices. A VEFA served
	// in a list (C4, plan 04.5-07) carries the neutral badge « VEFA, hors statistiques » after its price,
	// on the same template: listed, never counted in any statistic. An optional snippet `pied` (04.10-07,
	// the panel of the parcel map) ends each card, given the card and its rank; without it, nothing changes.
	import type { Snippet } from 'svelte';
	import type { CarteVente } from '$lib/api/adresses';
	import { LIBELLES_ETATS } from '$lib/data/etats-libelles';
	import {
		BADGE_PRIX_NON_AFFICHE,
		BADGE_VEFA,
		ligneDateNature,
		ligneDistance,
		lignePrix,
		ligneSurface,
		partiesComposition
	} from '$lib/data/adresse-ventes';

	interface Props {
		ventes: CarteVente[];
		/** What ends a card: the card and its rank in the served order. */
		pied?: Snippet<[CarteVente, number]>;
	}

	let { ventes, pied }: Props = $props();

	const NON_RENSEIGNE = LIBELLES_ETATS.not_reported;
</script>

<ol class="ventes-cartes">
	<!-- Keyed by position: the served order is the order; a sale carries no served identifier. -->
	{#each ventes as carte, rang (rang)}
		{@const prix = lignePrix(carte)}
		{@const distance = ligneDistance(carte.distance_m)}
		<li class="ventecard">
			<p class="ventecard-l1">
				<span>{ligneDateNature(carte)}</span>
				{#if distance}{' '}<span aria-hidden="true">·</span>{' '}<span class="ventecard-distance">{distance}</span>{/if}
			</p>
			<p class="ventecard-l2">
				<span
					>{#each partiesComposition(carte.composition) as partie, index (index)}{#if index > 0}{', '}{/if}<span
							data-provisoire={partie.provisoire ? 'true' : undefined}>{partie.texte}</span
						>{/each}</span
				>
				<span aria-hidden="true">·</span>
				<span>{ligneSurface(carte.surface_batie)}</span>
			</p>
			<p class="ventecard-l3">
				{#if prix}
					<span class="chiffre">{prix}</span>
				{:else if !carte.prix_affiche}
					<span class="ventecard-badge" data-minimisation={carte.minimisation?.motif}>{BADGE_PRIX_NON_AFFICHE}</span>
				{:else}
					<span class="ventecard-badge">{NON_RENSEIGNE}</span>
				{/if}
				{#if carte.hors_stats_vefa}{' '}<span class="ventecard-badge" data-vefa="true">{BADGE_VEFA}</span>{/if}
			</p>
			{#if pied}{@render pied(carte, rang)}{/if}
		</li>
	{/each}
</ol>

<style>
	.ventes-cartes {
		display: flex;
		flex-direction: column;
		gap: var(--s2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* Same template as the card of a document (UI-SPEC « Espacement »). v4 form: a sale is one datum on
	   its own, so a tile (UI-SPEC v4 § 3.0): page background, line outline, tile radius. */
	.ventecard {
		display: flex;
		flex-direction: column;
		gap: var(--s2);
		box-sizing: border-box;
		padding: var(--s3) var(--s4);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-tuile);
		background: var(--color-bg);
	}

	.ventecard p {
		margin: 0;
	}

	.ventecard-l1,
	.ventecard-l2 {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s2);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.ventecard-l3 {
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.chiffre {
		font-variant-numeric: tabular-nums;
	}

	/* Neutral badge: a withheld price is a fact on the publication, never a judgement. */
	.ventecard-badge {
		display: inline-block;
		padding: 0 var(--s2);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-badge);
		background: var(--color-soft);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}
</style>
