<script lang="ts">
	// Legend « Lire une fiche » (UI-SPEC v4 § 3.12, § 7.6; D-14): a part panel that says what each scale and
	// each badge means. One line per served level of the scale table (`NIVEAUX`, in the order of the ranks)
	// and one per key of the badges; the words come from `NIVEAUX` and `LIBELLE_STATUT`, the definitions from
	// `lib/data/lire-une-fiche.ts`, whose test fails on a missing one. Mounted on the address fiche, before
	// its sources, and on /datasets.
	//
	// Compact since lot 8 of 4.9: the two lists side by side when the panel is wide enough, one row per
	// entry. `repliable` (A7, for the address fiche) folds them under the title: closed by default, opened
	// by the anchor `#lire`, and written out for paper, which does not open a fold.
	//
	// Review of 2026-11-11: on /datasets this panel is the one glossary of the page. `apresEchelles` is read
	// under the scales, `children` under the two lists, inside the same panel; `titreId` lets a link of the
	// page move the focus to its title.
	import { onMount, type Snippet } from 'svelte';
	import { lignesEchelles, lignesEtats } from '$lib/data/lire-une-fiche';
	import BadgeEtat from './BadgeEtat.svelte';
	import Panneau from './Panneau.svelte';
	import RepereEchelle from './RepereEchelle.svelte';

	interface Props {
		/** Folded under the title, opened by `#lire` (the address fiche, A7). */
		repliable?: boolean;
		/** Id of the title when a page links to it. */
		titreId?: string;
		/** Under the list of the scales, in its column. */
		apresEchelles?: Snippet;
		/** Under the two lists, in the panel. */
		children?: Snippet;
	}

	let { repliable = false, titreId, apresEchelles, children }: Props = $props();

	const echelles = lignesEchelles();
	const etats = lignesEtats();

	let ouvert = $state(false);

	// A link to the legend (summary of the page, shared URL) opens it on arrival.
	function ouvrirSiCible() {
		if (location.hash === '#lire') ouvert = true;
	}
	onMount(() => {
		if (!repliable) return;
		ouvrirSiCible();
		window.addEventListener('hashchange', ouvrirSiCible);
		return () => window.removeEventListener('hashchange', ouvrirSiCible);
	});
</script>

{#snippet legende()}
	<div class="groupes">
		<div>
			<h3 class="sous-theme">Les échelles</h3>
			<ul class="lignes">
				{#each echelles as ligne (ligne.niveau)}
					<li class="ligne">
						<div class="def">
							<RepereEchelle pictogramme={ligne.pictogramme} mot={ligne.mot} />
							<p>{ligne.definition}</p>
						</div>
					</li>
				{/each}
			</ul>
			{@render apresEchelles?.()}
		</div>

		<div>
			<h3 class="sous-theme">Les états</h3>
			<ul class="lignes">
				{#each etats as ligne (ligne.cle)}
					<li class="ligne">
						<div class="def">
							{#if ligne.statut}
								<!-- A wrapper: a grid item would stretch the badge over the whole column. -->
								<span><BadgeEtat statut={ligne.statut} zone={ligne.zone} /></span>
							{:else}
								<span class="def-sans">Sans badge</span>
							{/if}
							<p>{ligne.definition}</p>
						</div>
					</li>
				{/each}
			</ul>
		</div>
	</div>
{/snippet}

<Panneau titre="Lire une fiche" variante="partie" id="lire" {titreId} chapo="Chaque valeur porte son échelle et, s’il y a lieu, un état.">
	{#if repliable}
		<details class="repli" bind:open={ouvert}>
			<summary>Voir les échelles et les états</summary>
			{@render legende()}
		</details>
		<div class="lire-impression">{@render legende()}</div>
	{:else}
		{@render legende()}
	{/if}
	{@render children?.()}
</Panneau>

<style>
	/* Side by side as soon as the panel holds two readable columns, whatever the page around it. */
	.groupes {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr));
		gap: 0 var(--s6);
	}

	/* The group title of the part (§ 4.4), kept apart by space, no rule; smaller in this compact legend. */
	.sous-theme {
		margin: var(--s4) 0 0;
		color: var(--color-ink);
		font-family: var(--font-display);
		font-size: 20px;
		font-weight: 500;
		line-height: 1.25;
	}

	.lignes {
		margin: var(--s1) 0 0;
		padding: 0;
		list-style: none;
	}

	.ligne {
		padding: var(--s2) 0;
		border-bottom: 1px solid var(--color-filet-pale);
	}

	.ligne:last-child {
		border-bottom: 0;
	}

	.def {
		display: grid;
		grid-template-columns: 10rem minmax(0, 1fr);
		gap: var(--s3);
		align-items: start;
	}

	.def p {
		max-width: var(--mesure);
		margin: 0;
		font-size: 14px;
		line-height: 1.4;
	}

	.repli > summary {
		display: list-item;
		box-sizing: border-box;
		min-height: 44px;
		padding-block: var(--s2);
		color: var(--color-accent);
		font-weight: 600;
		cursor: pointer;
	}

	.lire-impression {
		display: none;
	}

	/* The scale word may wrap here: the first column is only as wide as the longest word. */
	.def :global(.ech) {
		padding-top: var(--s-fin);
		white-space: normal;
	}

	.def-sans {
		color: var(--color-muted);
		font-size: 14px;
	}

	@media (max-width: 720px) {
		.def {
			grid-template-columns: minmax(0, 1fr);
			gap: var(--s1);
		}
	}

	@media print {
		.repli {
			display: none;
		}

		.lire-impression {
			display: block;
		}
	}
</style>
