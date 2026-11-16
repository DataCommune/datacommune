<script lang="ts">
	// List of the departements (04.7-10, decision 7; form of the direction A, UI-SPEC v4 § 6.3, plan
	// 04.8-05): one simple panel per region, each departement a page link (→) of the list. A
	// departement whose prices are not served says why on its line: the reason comes from the
	// hosting of DVF that the API gives for it (`prix`), never from a code written here. The page is
	// indexable, canonical to itself, with a BreadcrumbList. An outage is rendered whole and answered
	// 503 by hooks.server.ts (the load marks it).
	import { invalidateAll } from '$app/navigation';
	import ListeLiens, { type LienDeListe } from '$lib/components/ds/ListeLiens.svelte';
	import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
	import Panneau from '$lib/components/ds/Panneau.svelte';
	import Notice from '$lib/components/fiche/Notice.svelte';
	import type { DepartementRow } from '$lib/api/communes';
	import { SITE_URL } from '$lib/config';
	import { donneesStructureesListe, etatLigneDepartement, filArianeListe } from '$lib/data/departement-presentation';
	import { jsonLdScript } from '$lib/seo/json-ld';
	import { developperSigles } from '$lib/utils/sigles';
	import type { PageData } from './$types';
	import FilAriane from '$lib/components/ds/FilAriane.svelte';

	let { data }: { data: PageData } = $props();

	const TITLE = 'Départements — DataCommune';
	const DESCRIPTION =
		'Les départements français et leurs chiffres clés : population, prix médians au m², part des DPE classés F ou G et communes les plus peuplées.';
	// The lead is the only prose of the main with an acronym of the list (04.8-08, D-17, D-31): DPE is
	// spelled out there; the meta description above keeps the raw text.
	const [CHAPO] = developperSigles([
		'Choisissez un département pour lire sa population, ses prix médians au m², la part de ses DPE classés F ou G et ses communes les plus peuplées. Un département dont les prix ne sont pas servis le dit sur sa ligne.'
	]);
	const etapes = filArianeListe();
	const donnees = $derived(data.etat === 'liste' ? donneesStructureesListe(SITE_URL) : []);

	/** The departements of a region as page links, the state of the row (served) as its note. */
	function liensDe(departements: readonly DepartementRow[]): LienDeListe[] {
		return departements.map((departement) => ({
			href: `/departement/${departement.code}`,
			titre: `${departement.nom}\u00a0(${departement.code})`,
			note: etatLigneDepartement(departement) ?? undefined
		}));
	}

	const messagePanne = $derived(data.etat === 'panne' ? (data.detail.message ?? null) : null);
	const titrePanne = $derived(data.etat === 'panne' ? (data.detail.titre ?? null) : null);

	// « Recharger la liste »: one reload in flight at most, announced by aria-busy.
	let rechargement = $state(false);
	async function recharger() {
		if (rechargement) return;
		rechargement = true;
		try {
			await invalidateAll();
		} finally {
			rechargement = false;
		}
	}
</script>

<!-- Title, description, canonical and the card of a shared link (T1), twitter included. -->
<MetaPartage titre={TITLE} description={DESCRIPTION} chemin="/departements" />

<svelte:head>
	{#each donnees as objet, index (index)}
		{@html jsonLdScript(objet)}
	{/each}
</svelte:head>

<div class="deps gabarit">
	<FilAriane {etapes} />

	<h1 class="deps-title">Départements</h1>
	{#if data.etat === 'liste'}
		<p class="fiche-sous">{CHAPO}</p>
	{/if}

	{#if data.etat === 'liste'}
		<!-- The regions flow in columns, each panel whole (D12, form A): two from 720 px, three from 1081 px. -->
		<div class="deps-regions">
			{#each data.groupes as groupe (groupe.region ?? '')}
				<Panneau titre={groupe.region ?? 'Région non renseignée'}>
					<ListeLiens liens={liensDe(groupe.departements)} />
				</Panneau>
			{/each}
		</div>
	{:else}
		<div class="deps-panne">
			<Notice titre={titrePanne ?? 'La liste des départements est momentanément indisponible'} variante="warn" role="alert">
				<p>{messagePanne ?? 'Le service n’a pas répondu. Aucune liste n’est affichée plutôt qu’une liste incomplète.'}</p>
				<button type="button" class="bouton-secondaire" onclick={recharger} disabled={rechargement} aria-busy={rechargement}>Recharger la liste</button>
			</Notice>
		</div>
	{/if}
</div>

<style>
	/* A simple page of the direction A (UI-SPEC v4 § 6.0, § 6.3): `.gabarit`, h1 44/32, one
	   simple panel per region. */
	.deps {
		padding-block: var(--s6) var(--s8);
		color: var(--color-ink);
	}

	.deps-title {
		margin: 0;
		font-family: var(--font-display);
		font-size: 44px;
		font-weight: 500;
		line-height: 1.12;
	}

	.fiche-sous {
		max-width: var(--mesure);
		margin: var(--s3) 0 0;
		color: var(--color-muted);
		font-size: 16px;
		line-height: 1.5;
	}

	/* A column break truncates the top margin of the panel it falls before: the room is given under each panel
	   instead, so every column starts at the same height. */
	.deps-regions {
		margin-top: var(--s6);
		column-gap: var(--s4);
	}

	.deps-regions > :global(.panneau) {
		margin: 0 0 var(--s4);
		break-inside: avoid;
	}

	@media screen and (min-width: 721px) {
		.deps-regions {
			columns: 2;
		}
	}

	@media screen and (min-width: 1081px) {
		.deps-regions {
			columns: 3;
		}
	}

	.deps-panne {
		margin-top: var(--s6);
	}

	@media (max-width: 720px) {
		.deps {
			padding-block: var(--s5) var(--s7);
		}

		.deps-title {
			font-size: 32px;
		}
	}

	@media print {
		.deps {
			padding: 0;
		}

		.deps-panne .bouton-secondaire {
			display: none;
		}
	}
</style>
