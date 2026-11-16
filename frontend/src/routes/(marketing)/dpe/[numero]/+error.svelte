<script lang="ts">
	// Error states of the DPE document page (UI-SPEC « Contrat de rédaction » ; SEM-01, D-102). The
	// server loader throws a constant message per state, so that the page answers with its HTTP
	// status (04-DECISIONS-TEXTES, 2026-10-05, 04-04 rows 13 and 14) and this boundary renders the
	// same content as before inside the site layout:
	//   - 404 « DPE inconnu »: the served coverage of the instance, never the input as a fact;
	//   - 404 malformed number: h1 « Numéro DPE non valide », the input quoted in the sentence only;
	//   - 503: the document could not be read; one warn notice, no value, « Recharger le document ».
	// A 409, 429, 401 or 403 never reaches this page: it is typed by `panne.ts` on the page itself.
	// Since the shared `PageErreur` (T5, 2026-11-10) the body is the one of the other boundaries; the h1 is
	// plain text (the tabular digits of the number are gone), the wording stays today's. `developper` is off:
	// the line under the title already says « Document DPE ».
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/stores'; // Project convention: stores, never the state module.
	import PageErreur from '$lib/components/ds/PageErreur.svelte';
	import {
		ERREUR_DPE_INCONNU,
		MESSAGE_PANNE_DOCUMENT,
		SURTITRE_DOCUMENT,
		TITRE_NUMERO_NON_VALIDE,
		TITRE_PANNE_DOCUMENT,
		phraseInconnu,
		phraseMalForme,
		titreDocument
	} from '$lib/data/document-dpe';

	const numero = $derived($page.params?.numero ?? '');
	const etat = $derived(
		$page.status === 404 ? ($page.error?.message === ERREUR_DPE_INCONNU ? 'inconnu' : 'mal_forme') : 'panne'
	);
	// The input is never a title: a malformed number is named by its state (row 14).
	const titre = $derived(etat === 'mal_forme' ? TITRE_NUMERO_NON_VALIDE : titreDocument(numero));
	const inconnu = $derived(etat === 'inconnu' ? phraseInconnu($page.error?.departements ?? []) : null);
	const message = $derived(
		etat === 'panne' ? MESSAGE_PANNE_DOCUMENT : etat === 'inconnu' ? (inconnu?.texte ?? '') : phraseMalForme(numero)
	);
	const SORTIES_INCONNU = [
		{ href: '/', titre: 'Revenir à la recherche' },
		{ href: '/datasets', titre: 'Voir la couverture des données' }
	];
	const SORTIES_MAL_FORME = [{ href: '/', titre: 'Revenir à la recherche' }];
</script>

<svelte:head>
	<title>{titre} — DataCommune</title>
	<meta name="robots" content="noindex, follow" />
</svelte:head>

{#if etat === 'panne'}
	<PageErreur
		{titre}
		sousTitre={SURTITRE_DOCUMENT}
		ariane={titre}
		etat={TITRE_PANNE_DOCUMENT}
		panne
		developper={false}
		{message}
		relance={{ libelle: 'Recharger le document', action: () => invalidateAll() }}
	/>
{:else if etat === 'inconnu'}
	<PageErreur
		{titre}
		sousTitre={SURTITRE_DOCUMENT}
		ariane={titre}
		etat={ERREUR_DPE_INCONNU}
		developper={false}
		{message}
		liens={SORTIES_INCONNU}
	/>
{:else}
	<PageErreur
		{titre}
		sousTitre={SURTITRE_DOCUMENT}
		ariane={titre}
		etat={TITRE_NUMERO_NON_VALIDE}
		developper={false}
		{message}
		liens={SORTIES_MAL_FORME}
	/>
{/if}
