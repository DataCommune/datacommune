<script lang="ts">
	// Error states of the address page (UI-SPEC « Contrat de rédaction » ; SEM-01, D-107). The server
	// loader throws a constant message per served outcome, so that the page answers with its HTTP
	// status (404, 503) and this boundary renders the contract inside the site layout:
	//   - 404 « Adresse inconnue »: no address carries this key in the instance BAN;
	//   - 404 « Précisez le numéro »: a street key opened by a link; a page is about a number;
	//   - 503: the identity could not be read; one warn notice, no value, « Recharger la fiche »;
	//   - 429: the weekly allowance of fiches of the session is spent (H-3); the loader's message
	//     carries the threshold and the date the allowance comes back.
	// A 409, 429, 401 or 403 of the API never reaches this page: it is typed by `panne.ts` on the page itself.
	// Since the shared `PageErreur` (T5, 2026-11-10) the body is the one of the other boundaries; the wording
	// stays today's: the line « Adresse » above the title, the breadcrumb from « Accueil » (T6, settled 2026-11-10).
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/stores'; // Project convention: stores, never the state module.
	import PageErreur from '$lib/components/ds/PageErreur.svelte';
	import {
		MESSAGE_ADRESSE_INCONNUE,
		MESSAGE_PANNE_ADRESSE,
		MESSAGE_VOIE,
		TITRE_ADRESSE_INCONNUE,
		TITRE_PANNE_ADRESSE,
		TITRE_QUOTA_FICHES,
		TITRE_VOIE
	} from '$lib/data/recherche-lieux';

	const issue = $derived(
		$page.status === 404
			? $page.error?.message === TITRE_VOIE
				? 'voie'
				: 'inconnue'
			: $page.status === 429
				? 'quota'
				: 'panne'
	);
	const titre = $derived(
		issue === 'voie'
			? TITRE_VOIE
			: issue === 'inconnue'
				? TITRE_ADRESSE_INCONNUE
				: issue === 'quota'
					? TITRE_QUOTA_FICHES
					: TITRE_PANNE_ADRESSE
	);
	const message = $derived(
		issue === 'panne'
			? MESSAGE_PANNE_ADRESSE
			: issue === 'quota'
				? ($page.error?.message ?? '')
				: issue === 'voie'
					? MESSAGE_VOIE
					: MESSAGE_ADRESSE_INCONNUE
	);
	const SORTIES = [{ href: '/', titre: 'Revenir à la recherche' }];
</script>

<svelte:head>
	<title>{titre} — DataCommune</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

{#if issue === 'panne'}
	<PageErreur
		{titre}
		surtitre="Adresse"
		ariane="Adresse"
		etat="Les données de cette adresse sont momentanément indisponibles"
		panne
		{message}
		relance={{ libelle: 'Recharger la fiche', action: () => invalidateAll() }}
	/>
{:else}
	<PageErreur {titre} surtitre="Adresse" ariane="Adresse" {message} liens={SORTIES} />
{/if}
