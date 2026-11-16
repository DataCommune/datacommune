<script lang="ts">
	// The block « Définitions » of a commune fiche on a page of its own (A9-9): the fiche links it from its
	// <noscript>, so a reader without JavaScript finds in one click what the panels say, while the server
	// HTML of the fiche no longer repeats it on every commune (Search Console of 2026-11-16). Not indexed:
	// it says nothing a fiche does not.
	import DefinitionsCommune from '$lib/components/fiche/DefinitionsCommune.svelte';
	import Notice from '$lib/components/fiche/Notice.svelte';
	import { blocsPublies } from '$lib/api/commune';
	import { definitionsCommune, mapCommuneToGrid, themesCommune } from '$lib/data/commune-grid';
	import { communeKpis, reperesConnus } from '$lib/data/commune-kpi';
	import { libelleArrondissement } from '$lib/data/arrondissements';
	import { titrePage } from '$lib/seo/titres';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const nom = $derived.by(() => {
		const servi = data.etat === 'fiche' ? data.synthese.nom_commune : data.identite?.nom;
		return (data.parent ? libelleArrondissement(data.code) : null) ?? servi ?? `Commune INSEE ${data.code}`;
	});
	// The same rows as the fiche, in the same order: the reading of the fiche, nothing recomputed.
	const definitions = $derived.by(() => {
		if (data.etat !== 'fiche') return [];
		const synthese = blocsPublies(data.synthese);
		const grids = mapCommuneToGrid(synthese, data.sources);
		const themes = themesCommune(synthese, data.sources, grids).filter((theme) => theme.lignes.length > 0);
		return definitionsCommune(reperesConnus(communeKpis(synthese, data.sources)), themes);
	});
	const fiche = $derived(`/commune/${data.code}`);
</script>

<svelte:head>
	<title>{titrePage(`Définitions de la fiche ${nom}`)}</title>
	<meta name="robots" content="noindex, follow" />
</svelte:head>

<div class="definitions-page gabarit">
	<p class="retour"><a href={fiche}>Revenir à la fiche {nom}</a></p>
	<h1>Définitions de la fiche {nom}</h1>
	{#if definitions.length}
		<DefinitionsCommune {definitions} />
	{:else}
		<Notice titre="Aucune définition à afficher" variante="warn">
			<p>Cette fiche n’affiche aucune valeur : <a href={fiche}>sa page</a> dit pourquoi.</p>
		</Notice>
	{/if}
</div>

<style>
	.definitions-page {
		padding-block: var(--s6) var(--s8);
		color: var(--color-ink);
	}

	.definitions-page h1 {
		margin: var(--s4) 0 var(--s4);
		font-family: var(--font-display);
		font-size: 32px;
		font-weight: 500;
		line-height: 1.15;
		overflow-wrap: anywhere;
	}

	.definitions-page :global(.definitions) {
		max-width: var(--mesure);
	}

	.retour a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-accent);
		text-decoration: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}
</style>
