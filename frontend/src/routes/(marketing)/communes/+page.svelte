<script lang="ts">
	// `/communes` (UI-SPEC « /communes », D-43, D-44, H5). Server-rendered: every state lives in
	// the URL (`q`, `departement`, `limite`, `page`) and is read back by +page.ts.
	//   - search: one row per commune (04.8-07), sorted by served population; homonyms
	//     stay distinct; a commune out of coverage is listed with its reason;
	//   - directory (`q` empty, 04.6-02): the communes of the served departements by default, 50 a
	//     page, filters in a GET form (name, departement, population range, coverage, sort), a
	//     `<table>` on desktop and one card per commune up to 720 px, with the served median prices
	//     of houses and flats (`annuaire-communes.ts` words each state). A failed price read keeps
	//     the list with a notice; a failed list is the page outage.
	// « Aucun résultat » never says the commune does not exist, and an outage is never shown as
	// an empty result. A postal-code search out of coverage is rendered as such, never as a zero,
	// and a zero names the departements served, as read in the answer (CR-01, D-34).
	// Since 04-17 a search shows two typed groups, « Adresses » and « Communes », in the order served
	// by the address search (D-106); an empty group is not shown, both empty is the « Aucun résultat »
	// contract, the failure of one search is the typed state of its group (SEM-01), addresses out of
	// coverage are a warn notice naming the served territory (D-105). An address card opens
	// `/adresse/{ban_id}`; a street card asks for the number and opens nothing (D-107).
	// Anchor of the screen: the h2 of the first group.
	// 04.8-07 (UI-SPEC § 6.5): the v4 form. Breadcrumb « Accueil › Recherche » (« Annuaire des communes »
	// for the directory, D4, 4.9 lot 7), the h1, the large field,
	// the state in a `p role="status"` with the served texts, the results in simple panels « Adresses »
	// then « Communes » as whole-row links (→); a commune row keeps « Ajouter à la comparaison » under it
	// and its out-of-coverage reason. The directory filters fold in « Filtrer »; the directory is a table
	// in a panel on desktop, rows up to 720 px. No territory is written here.
	// 4.9 lot 7 (C5): a directory of 699 pages is not walked one by one. First, last, the pages within two
	// and a GET form for a number: every one works without JavaScript and keeps the filters.
	import { invalidateAll } from '$app/navigation';
	import { navigating, page } from '$app/stores';
	import CommuneSearch from '$lib/components/CommuneSearch.svelte';
	import ListeLiens from '$lib/components/ds/ListeLiens.svelte';
	import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
	import { titrePage } from '$lib/seo/titres';
	import { rechercheSansCanal } from '$lib/mesure-audience';
	import PageSimple from '$lib/components/ds/PageSimple.svelte';
	import Panneau from '$lib/components/ds/Panneau.svelte';
	import Notice from '$lib/components/fiche/Notice.svelte';
	import StateValue from '$lib/components/fiche/StateValue.svelte';
	import { DEPARTEMENTS } from '$lib/data/departements';
	import { LIBELLE_HORS_COUVERTURE } from '$lib/data/etats-libelles';
	import {
		CONSEIL_RECHERCHE_PAR_NOM,
		LIEN_COUVERTURE,
		TITRE_CODE_POSTAL_HORS_COUVERTURE,
		phraseRechercheBornee,
		conseilCodePostal
	} from '$lib/data/recherche-code-postal';
	import type { CommuneAnnuaireRow } from '$lib/api/communes';
	import type { ResultatRechercheAdresse } from '$lib/api/adresses';
	import {
		TRANCHES_POPULATION,
		TRIS,
		celluleDePrix,
		periodeDesPrix,
		periodeDUnPrix,
		champsAnnuaire,
		filtresActifs,
		hrefAnnuaire,
		pagesVoisines,
		populationServie,
		appartenance,
		nomSousCommune,
		type CellulePrix,
		type FiltresAnnuaire
	} from '$lib/data/annuaire-communes';
	import { communeParente, grouperParCommune, mentionArrondissement } from '$lib/data/arrondissements';
	import AjoutComparaison from './AjoutComparaison.svelte';
	import {
		CORPS_AUCUN_RESULTAT,
		EXEMPLES_ACCUEIL,
		PANNE_ADRESSES,
		PANNE_COMMUNES,
		PAS_ZERO,
		RECHERCHE_TROP_LARGE,
		PRECISEZ_LE_NUMERO,
		RELANCER,
		annonceResultats,
		libelleGroupeAdresses,
		libelleVoie,
		lieuAdresse,
		messageCorrespondanceNumero,
		titreAdressesHorsCouverture,
		titreAucunResultat
	} from '$lib/data/recherche-lieux';
	import { LIBELLES_MOTIFS } from '$lib/data/etats-libelles';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const nombre = new Intl.NumberFormat('fr-FR');
	const SEARCH_LIMIT_STEP = 20;
	const SEARCH_LIMIT_MAX = 50;

	function nomDepartement(code: string): string {
		const nom = DEPARTEMENTS.find((d) => d.code === code)?.nom;
		return nom ? `${nom} (${code})` : `Département ${code}`;
	}

	function communesHref(params: Record<string, string | number | undefined>): string {
		const sp = new URLSearchParams();
		for (const [key, value] of Object.entries(params)) {
			if (value !== undefined && value !== '') sp.set(key, String(value));
		}
		const qs = sp.toString();
		return `/communes${qs ? '?' + qs : ''}`;
	}

	const departementLibelle = $derived(data.departement ? nomDepartement(data.departement) : '');
	// The typed results page (04-17) when the addresses were searched; the phase 3 title otherwise.
	const typee = $derived(data.mode === 'recherche' && data.adresses !== null);
	// D4 (4.9 lot 7): the h1 says what the page is; the directory has no query.
	const titre = $derived(
		data.mode === 'annuaire'
			? 'Annuaire des communes'
			: data.mode === 'recherche' && data.q
				? `Résultats pour « ${data.q} »`
				: 'Rechercher une adresse ou une commune'
	);
	// The search may answer arrondissements of Paris, Lyon and Marseille (04.7-09): each kind is
	// counted apart, never an arrondissement called a commune.
	const compte = $derived.by(() => {
		const arrondissements = data.results.filter((r) => r.type_zone === 'arrondissement').length;
		const communes = data.results.length - arrondissements;
		const parties = [
			communes ? (communes === 1 ? '1 commune' : `${nombre.format(communes)} communes`) : '',
			arrondissements
				? arrondissements === 1
					? '1 arrondissement'
					: `${nombre.format(arrondissements)} arrondissements`
				: ''
		].filter(Boolean);
		if (!parties.length) return '0 commune trouvée';
		const accord = data.results.length === 1 ? 'trouvé' : 'trouvés';
		return `${parties.join(' et ')} ${arrondissements ? accord : data.results.length === 1 ? 'trouvée' : 'trouvées'}`;
	});
	const plusHref = $derived(
		data.results.length >= data.limite && data.limite < SEARCH_LIMIT_MAX
			? communesHref({
					q: data.q,
					departement: data.departement,
					limite: Math.min(SEARCH_LIMIT_MAX, data.limite + SEARCH_LIMIT_STEP)
				})
			: null
	);
	const annuaire = $derived(data.annuaire);
	const reponse = $derived(annuaire?.reponse ?? null);
	const pageCount = $derived(
		reponse ? Math.max(1, Math.ceil(reponse.total_count / reponse.per_page)) : 1
	);
	// Arrondissements sit under their commune, which keeps its own row and prices (04.7-09).
	const rangees = $derived(reponse ? grouperParCommune(reponse.results) : []);
	const fenetre = $derived(reponse?.prix.fenetre_mediane_dvf ?? null);
	const periode = $derived(periodeDesPrix(fenetre));
	const servis = $derived(reponse?.departements_servis ?? []);
	// The directory asks for the arrondissements of Paris, Lyon and Marseille (04.7-09): when one of
	// their departements is in play, the total counts communes and arrondissements together.
	const DEPARTEMENTS_PLM = ['75', '69', '13'];
	const avecArrondissements = $derived(
		data.departement ? DEPARTEMENTS_PLM.includes(data.departement) : servis.some((d) => DEPARTEMENTS_PLM.includes(d))
	);
	const totalLieux = (n: number): string =>
		avecArrondissements
			? n === 1
				? '1 commune ou arrondissement'
				: `${nombre.format(n)} communes et arrondissements`
			: n === 1
				? '1 commune'
				: `${nombre.format(n)} communes`;
	// The served departements are named from the static referential only while few enough to read.
	const SERVIS_NOMMES_MAX = 6;
	// A departement chosen outside the served ones, with the default coverage: say why it is empty.
	const departementNonServi = $derived(
		annuaire !== null &&
			reponse !== null &&
			annuaire.filtres.couverture === 'servies' &&
			annuaire.filtres.departement !== '' &&
			!servis.includes(annuaire.filtres.departement)
	);

	function cellules(commune: CommuneAnnuaireRow): { libelle: string; cellule: CellulePrix }[] {
		return [
			{ libelle: 'Maisons', cellule: celluleDePrix(commune.prix?.maison ?? null) },
			{ libelle: 'Appartements', cellule: celluleDePrix(commune.prix?.appartement ?? null) }
		];
	}
	const adresses = $derived(data.adresses);
	const adressesServies = $derived(adresses?.etat === 'ok' ? adresses.resultats : []);
	const libelleAdresses = $derived(libelleGroupeAdresses(adressesServies));
	// The served results in their served order (D-106), cut into runs: a run of addresses is a list of
	// whole-row links (→ their page); a street has no page of its own (D-107) and stays a plain row at its
	// rank, between two runs.
	type SegmentAdresses =
		| { type: 'liens'; liens: { href: string; titre: string; note: string }[] }
		| { type: 'voie'; voie: ResultatRechercheAdresse };
	const segmentsAdresses = $derived.by(() => {
		const segments: SegmentAdresses[] = [];
		for (const adresse of adressesServies) {
			if (adresse.type_resultat === 'adresse' && adresse.ban_id) {
				const lien = {
					href: `/adresse/${encodeURIComponent(adresse.ban_id)}`,
					titre: libelleVoie(adresse.numero, adresse.rep, adresse.nom_voie),
					note: `Adresse · ${lieuAdresse(adresse)}`
				};
				const dernier = segments.at(-1);
				if (dernier?.type === 'liens') dernier.liens.push(lien);
				else segments.push({ type: 'liens', liens: [lien] });
			} else {
				segments.push({ type: 'voie', voie: adresse });
			}
		}
		return segments;
	});
	// The state of a typed search, served counts only (`annonceResultats`, the announcement of the
	// field). A half that failed or is out of coverage is not counted (`null`), and with nothing listed
	// the line is left out: the notices of the groups say the outage or the coverage, never a zero.
	const etatRecherche = $derived(
		data.results.length + adressesServies.length > 0
			? annonceResultats(
					data.panne ? null : data.results.length,
					adresses?.etat === 'ok' ? adressesServies.length : null,
					data.q,
					adressesServies,
					data.results.filter((r) => r.type_zone === 'arrondissement').length
				)
			: null
	);

	/**
	 * Second line of a commune row (UI-SPEC § 6.5), from the served fields only: « Commune » (or the
	 * arrondissement), the postal code when served, « Département (code) » and the INSEE code that tells
	 * homonyms apart (D-43); then the population, dated and sourced only when the API serves its year
	 * and the INSEE source (H6).
	 */
	function identiteCommune(result: (typeof data.results)[number]): string {
		// D11 (2026-11-10): the INSEE code is written when another result of the list bears the same name, the
		// case it tells apart (D-43); a name found once reads without it.
		const homonyme = data.results.some((autre) => autre !== result && autre.nom.toLocaleLowerCase('fr') === result.nom.toLocaleLowerCase('fr'));
		return [mentionArrondissement(result) ?? 'Commune', result.code_postal ?? '', nomDepartement(result.departement), homonyme ? `INSEE ${result.code_insee}` : '']
			.filter(Boolean)
			.join(' · ');
	}

	function populationCommune(result: (typeof data.results)[number]): string | null {
		if (result.population === null) return null;
		const unite = result.population === 1 ? 'habitant' : 'habitants';
		const suite = result.population_source === 'demographie' && result.population_annee !== null ? ` · ${result.population_annee} · INSEE` : '';
		return `${nombre.format(result.population)} ${unite}${suite}`;
	}
	// A postal code out of coverage with no commune keeps the phase 3 contract (C22): the communes
	// group says it with its own title, the address group adds no second notice.
	const codePostalHorsCouverture = $derived(data.results.length === 0 && data.couverture?.kind === 'hors-couverture');
	const adressesHorsCouverture = $derived(
		!codePostalHorsCouverture && adresses?.etat === 'ok' && adresses.hebergement.etat === 'hors_couverture'
			? adresses.hebergement
			: null
	);
	const messageNumero = $derived(
		adresses?.etat === 'ok' && adresses.hebergement.etat !== 'hors_couverture'
			? messageCorrespondanceNumero(adresses.correspondanceNumero)
			: null
	);
	// Both searches failed: one page-level outage, as for a communes-only search.
	const panneGlobale = $derived(data.panne !== null && (adresses === null || adresses.etat === 'panne'));
	// Nothing to list in either group, and neither out of coverage nor failed: « Aucun résultat ».
	const aucunResultat = $derived(
		typee &&
			!data.panne &&
			data.results.length === 0 &&
			data.couverture?.kind !== 'hors-couverture' &&
			adresses?.etat === 'ok' &&
			adressesServies.length === 0 &&
			!adressesHorsCouverture
	);

	function phraseMotif(motif: string | null): string {
		return motif && Object.hasOwn(LIBELLES_MOTIFS, motif)
			? LIBELLES_MOTIFS[motif as keyof typeof LIBELLES_MOTIFS].phrase
			: `${LIBELLE_HORS_COUVERTURE}.`;
	}

	// Client navigation towards this page: the results region is busy and shows skeletons.
	const chargement = $derived($navigating?.to?.url.pathname === '/communes');
	const partage = $derived({
		titre: titrePage(titre),
		description: 'Rechercher une commune française par son nom ou son code postal, distinguer les homonymes et ouvrir sa fiche.',
		// The channel of a spread link (`?src=`) is no part of the address of the page.
		chemin: $page.url.pathname + rechercheSansCanal($page.url)
	});
</script>

<!-- The directory is the indexable view of this page: its document keeps its own name. A search query
     produces an arbitrary number of near-identical URLs; only the directory (departement + pagination) is
     worth an index slot. Title, description, canonical and the card of a shared link (T1). -->
<!-- The robots meta stays inside `{#if data.q}`: sitemap-source.test.ts reads the page for it there only. -->
{#if data.q}
	<MetaPartage {...partage} robots="noindex, follow" />
{:else}
	<MetaPartage {...partage} />
{/if}

<PageSimple {titre} ariane={data.mode === 'annuaire' ? 'Annuaire des communes' : 'Recherche'}>

	<!-- Keyed on the URL state: a back/forward navigation realigns the field with the page. -->
	{#key `${data.q}|${data.departement}`}
		<div class="communes-champ">
			<CommuneSearch variante="grand" enLigne initialQuery={data.q} initialDepartement={data.departement} />
		</div>
	{/key}

	{#if data.mode === 'annuaire' && annuaire}
		{@render filtresAnnuaire(annuaire.filtres)}
	{/if}

	<section
		class="communes-results"
		aria-label="Résultats"
		aria-busy={chargement ? 'true' : undefined}
	>
		{#if chargement}
			<ul class="communes-list" aria-hidden="true">
				{#each [0, 1, 2] as index (index)}
					<li class="communes-skeleton"></li>
				{/each}
			</ul>
		{:else if typee && !panneGlobale}
			{#if aucunResultat}
				<!-- D8: the Notice of the fiche; its status role announces the title, the served words unchanged. -->
				<div class="communes-notice">
					<Notice titre={titreAucunResultat(0, 0, data.q)}>
					{#if data.couverture?.kind === 'bornee'}
						<p>
							Cela ne veut pas dire qu'elle n'existe pas. {phraseRechercheBornee(
								data.couverture.departements,
								nomDepartement
							)}
							{conseilCodePostal(data.q)}
						</p>
					{:else}
						<p>{CORPS_AUCUN_RESULTAT}</p>
					{/if}
					{@render suiteRecherche()}
					{#if data.departement}
						<form action="/communes" method="get">
							<input type="hidden" name="q" value={data.q} />
							<button type="submit" class="bouton-secondaire">Retirer le filtre département</button>
						</form>
					{/if}
					</Notice>
				</div>
			{:else}
				{#if etatRecherche}<p class="r-etat" role="status">{etatRecherche}</p>{/if}
				{#each data.ordre as type (type)}
					{#if type === 'adresse'}
						{@render groupeAdresses()}
					{:else}
						{@render groupeCommunes()}
					{/if}
				{/each}
			{/if}
		{:else if data.mode === 'annuaire' && annuaire}
			{@render annuaireCommunes(annuaire.filtres)}
		{:else if data.panne}
			<!-- D8: the state panel of the fiche; `communes-notice(-warn)` stays a hook of the specs, without a style. -->
			<div class="communes-notice communes-notice-warn">
				<Notice titre={data.panne?.titre ?? data.panne?.message ?? 'La recherche est momentanément indisponible. Réessayez dans un instant.'} variante="warn" role="alert">
				{#if data.panne?.titre}<p>{data.panne?.message ?? 'La recherche est momentanément indisponible. Réessayez dans un instant.'}</p>{/if}
				<!-- Same URL: without JavaScript the GET form reloads it; with it, the load is re-run
				     (a navigation to the current URL would not re-run it). -->
				<form
					action="/communes"
					method="get"
					onsubmit={(event) => {
						event.preventDefault();
						invalidateAll();
					}}
				>
					{#if data.q}<input type="hidden" name="q" value={data.q} />{/if}
					{#if data.departement}
						<input type="hidden" name="departement" value={data.departement} />
					{/if}
					<button type="submit" class="bouton-secondaire">Relancer la recherche</button>
				</form>
				</Notice>
			</div>
		{:else if data.mode === 'trop-court'}
			<p class="communes-hint">Saisissez au moins deux caractères pour lancer la recherche.</p>
		{:else if data.mode === 'recherche' && data.results.length === 0 && data.couverture?.kind === 'hors-couverture'}
			<div class="communes-notice communes-notice-warn">
				<Notice titre={TITRE_CODE_POSTAL_HORS_COUVERTURE} variante="warn">
				<p>
					{data.couverture.phrase ?? `${LIBELLE_HORS_COUVERTURE}.`}
					{CONSEIL_RECHERCHE_PAR_NOM}
				</p>
				<p class="communes-liens"><a href="/datasets">{LIEN_COUVERTURE}</a> <a href="/couverture">Limites connues des données</a></p>
				{#if data.departement}
					<form action="/communes" method="get">
						<input type="hidden" name="q" value={data.q} />
						<button type="submit" class="bouton-secondaire">Retirer le filtre département</button>
					</form>
				{/if}
				</Notice>
			</div>
		{:else if data.mode === 'recherche' && data.results.length === 0}
			<div class="communes-notice">
				<Notice titre={`Aucune commune trouvée pour « ${data.q} »`}>
				{#if data.couverture?.kind === 'bornee'}
					<p>
						Cela ne veut pas dire qu'elle n'existe pas. {phraseRechercheBornee(
							data.couverture.departements,
							nomDepartement
						)}
						{conseilCodePostal(data.q)}
					</p>
				{:else}
					<p>
						Cela ne veut pas dire qu'elle n'existe pas. Vérifiez l'orthographe, essayez le code
						postal ou retirez le filtre département.
					</p>
				{/if}
				{@render suiteRecherche()}
				{#if data.departement}
					<form action="/communes" method="get">
						<input type="hidden" name="q" value={data.q} />
						<button type="submit" class="bouton-secondaire">Retirer le filtre département</button>
					</form>
				{/if}
				</Notice>
			</div>
		{:else if data.mode === 'recherche'}
			<p class="r-etat" role="status">
				{compte}{#if data.departement}&nbsp;· {departementLibelle}{/if}
			</p>
			{@render listeCommunes()}
		{/if}
	</section>
</PageSimple>

<!-- Directory filters (04.6-02): a plain GET form, so a filtered page is a shareable URL and works
     without JavaScript. Keyed on the URL state: back/forward realigns the fields with the page. -->
<!-- Next step of an empty search (04.7-14, R16): other spellings, communes au nom proche, examples. -->
{#snippet suiteRecherche()}
	<div class="communes-suite">
		{#if data.suite?.orthographes.length}
			<p>
				Essayer avec une autre orthographe :
				{#each data.suite.orthographes as variante, i (variante)}{#if i > 0}, {/if}<a
						href={communesHref({ q: variante, departement: data.departement })}>{variante}</a
					>{/each}
			</p>
		{/if}
		{#if data.suite?.proches}
			<p>Communes dont le nom commence par « {data.suite.proches.prefixe} » :</p>
			<ul class="communes-suite-liste">
				{#each data.suite.proches.communes as commune (commune.code_insee)}
					<li><a href={`/commune/${commune.code_insee}`}>{commune.nom}</a> · {nomDepartement(commune.departement)}</li>
				{/each}
			</ul>
		{/if}
		<p>Ou repartir d’une page du site :</p>
		<ul class="communes-suite-liste">
			{#each EXEMPLES_ACCUEIL as exemple (exemple.href)}
				<li><a href={exemple.href}>{exemple.label}</a></li>
			{/each}
		</ul>
	</div>
{/snippet}

{#snippet filtresAnnuaire(filtres: FiltresAnnuaire)}
	{#key hrefAnnuaire(filtres)}
		<!-- `det` stays a hook of forme-publique.spec and recherche-accueil-404.spec, without a style (T3). -->
		<details class="det depliant depliant-cadre" open={filtresActifs(filtres)}>
		<summary>Filtrer</summary>
		<form class="annuaire-filtres" action="/communes" method="get" aria-labelledby="annuaire-filtres-titre">
			<h2 id="annuaire-filtres-titre" class="sr-only">Filtrer l’annuaire</h2>
			<div class="annuaire-champs">
				<div class="annuaire-champ">
					<label for="annuaire-nom">Nom de la commune</label>
					<input id="annuaire-nom" name="nom" type="text" value={filtres.nom} maxlength="100" autocomplete="off" />
				</div>
				<div class="annuaire-champ">
					<label for="annuaire-departement">Département de la commune</label>
					<select id="annuaire-departement" name="departement" value={filtres.departement}>
						<option value="">Tous les départements</option>
						{#each DEPARTEMENTS as d (d.code)}
							<option value={d.code}>{d.nom} ({d.code})</option>
						{/each}
					</select>
				</div>
				<div class="annuaire-champ">
					<label for="annuaire-population">Population</label>
					<select id="annuaire-population" name="population" value={filtres.population}>
						<option value="">Toutes les tailles</option>
						{#each TRANCHES_POPULATION as tranche (tranche.valeur)}
							<option value={tranche.valeur}>{tranche.libelle}</option>
						{/each}
					</select>
				</div>
				<div class="annuaire-champ">
					<label for="annuaire-couverture">Couverture</label>
					<select
						id="annuaire-couverture"
						name="couverture"
						value={filtres.couverture}
						aria-describedby="annuaire-couverture-aide"
					>
						<option value="servies">Départements servis</option>
						<option value="toutes">Toute la France</option>
					</select>
				</div>
				<div class="annuaire-champ">
					<label for="annuaire-tri">Trier par</label>
					<select id="annuaire-tri" name="tri" value={filtres.tri}>
						{#each TRIS as tri (tri.valeur)}
							<option value={tri.valeur}>{tri.libelle}</option>
						{/each}
					</select>
				</div>
			</div>
			<p id="annuaire-couverture-aide" class="annuaire-aide">
				{#if reponse === null}
					Les communes hors des départements servis sont listées sans prix.
				{:else if servis.length === 0}
					Ce site ne sert encore aucun département : choisissez « Toute la France ».
				{:else if servis.length <= SERVIS_NOMMES_MAX}
					Départements servis : {servis.map(nomDepartement).join(', ')}. Les autres communes sont listées sans prix.
				{:else}
					Les communes hors des départements servis sont listées sans prix.
					<a href="/datasets">Voir la couverture des données</a>
				{/if}
			</p>
			<div class="annuaire-actions">
				<button type="submit" class="bouton-primaire">Afficher</button>
				{#if filtresActifs(filtres)}
					<a class="bouton-secondaire" href="/communes">Effacer les filtres</a>
				{/if}
			</div>
		</form>
		</details>
	{/key}
{/snippet}

<!-- The same URL, re-run: without JavaScript the GET form reloads it; with it, the load runs again. -->
{#snippet relancerAnnuaire(filtres: FiltresAnnuaire, libelle: string)}
	<form
		action="/communes"
		method="get"
		onsubmit={(event) => {
			event.preventDefault();
			invalidateAll();
		}}
	>
		{#each champsAnnuaire(filtres) as [nom, valeur] (nom)}
			<input type="hidden" name={nom} value={valeur} />
		{/each}
		<button type="submit" class="bouton-secondaire">{libelle}</button>
	</form>
{/snippet}

{#snippet prixCellule(cellule: CellulePrix)}
	<span class="prix prix-{cellule.kind}"
		><span class="prix-texte" class:chiffre={cellule.kind === 'valeur' || cellule.kind === 'nd'}>{cellule.texte}</span
		>{#if cellule.detail}<span class="prix-detail">{cellule.detail}</span>{/if}{#if periodeDUnPrix(cellule, fenetre)}<span
				class="annuaire-sr">, {periodeDUnPrix(cellule, fenetre)}</span
			>{/if}</span
	>
{/snippet}

<!-- The year and the source on their own line (T8): the « · » before them is read, not shown. -->
{#snippet populationCellule(commune: CommuneAnnuaireRow)}
	{@const servie = populationServie(commune)}
	{#if servie}<span class="chiffre">{servie.nombre}</span> {servie.unite}{#if servie.millesime}<span class="pop-millesime"
				><span class="annuaire-sr">{' · '}</span>{servie.millesime}</span
			>{/if}{:else}Non renseignée{/if}
{/snippet}

{#snippet annuaireCommunes(filtres: FiltresAnnuaire)}
	{#if reponse === null}
		<div class="communes-notice communes-notice-warn">
			<Notice titre={data.panne?.titre ?? data.panne?.message ?? 'L’annuaire est momentanément indisponible. Réessayez dans un instant.'} variante="warn" role="alert">
				{#if data.panne?.titre}<p>{data.panne?.message ?? 'L’annuaire est momentanément indisponible. Réessayez dans un instant.'}</p>{/if}
				{@render relancerAnnuaire(filtres, 'Relancer l’annuaire')}
			</Notice>
		</div>
	{:else}
		<p class="communes-count" role="status">
			{totalLieux(reponse.total_count)}{#if pageCount > 1}&nbsp;· page {reponse.page} sur {pageCount}{/if}
		</p>
		{#if reponse.prix.etat === 'indisponible'}
			<div class="communes-notice communes-notice-warn annuaire-panne-prix">
				<Notice titre="Prix momentanément indisponibles" variante="warn" role="alert">
					<p>
						La liste des communes reste consultable, mais les prix médians n’ont pas pu être lus. Ce n’est
						pas une absence de ventes.
					</p>
					{@render relancerAnnuaire(filtres, 'Relancer la lecture des prix')}
				</Notice>
			</div>
		{/if}
		{#if reponse.results.length === 0}
			<div class="communes-notice">
				{#if reponse.total_count > 0}
					<Notice titre="Cette page est au-delà de la sélection">
						<p><a class="bouton-secondaire" href={hrefAnnuaire(filtres, { page: 1 })}>Revenir à la première page</a></p>
					</Notice>
				{:else}
					<Notice titre="Aucune commune ne correspond à ces filtres">
					{#if departementNonServi}
						<p>
							Ce département n’est pas servi par ce site : ses communes figurent dans « Toute la France », sans prix.
						</p>
						<p>
							<a class="bouton-secondaire" href={hrefAnnuaire(filtres, { couverture: 'toutes', page: 1 })}
								>Afficher les communes de ce département</a
							>
						</p>
					{:else if filtresActifs(filtres)}
						<p><a class="bouton-secondaire" href="/communes">Effacer les filtres</a></p>
					{/if}
					</Notice>
				{/if}
			</div>
		{:else}
			<Panneau titre="Liste des communes">
			<!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access is required for the scroll region, UI-SPEC § 3.13) -->
			<div class="annuaire-tableau" role="region" tabindex="0" aria-label="Tableau de l’annuaire des communes">
				<table class="annuaire-table">
					<caption>
						Communes de la sélection{#if pageCount > 1}, page {reponse.page} sur {pageCount}{/if}. {periode ??
							'Prix médians au m² des ventes retenues (DVF)'}, avec leur nombre.
					</caption>
					<thead>
						<tr>
							<!-- D7: the prices first, in the tint of the prices (the tiles of the commune fiche). -->
							<th scope="col">Commune</th>
							<th scope="col" class="num teinte-q-prix">Maisons, prix médian</th>
							<th scope="col" class="num teinte-q-prix">Appartements, prix médian</th>
							<th scope="col" class="num">Population</th>
							<th scope="col">Département</th>
							<th scope="col"><span class="annuaire-sr">Comparaison</span></th>
						</tr>
					</thead>
					<tbody>
						{#each rangees as { ligne: commune, sous, communeAbsente } (commune.code_insee)}
							<tr data-code-insee={commune.code_insee} class:annuaire-sous={sous}>
								<th scope="row">
									<a href="/commune/{encodeURIComponent(commune.code_insee)}">{sous ? nomSousCommune(commune) : commune.nom}{#if sous}<span class="annuaire-sr">{" "}de {communeParente(commune.code_insee)?.nom ?? ''}</span>{/if}</a>
									{#if communeAbsente}<span class="annuaire-appartenance">{appartenance(communeAbsente)}</span>{/if}
								</th>
								{#each cellules(commune) as { libelle, cellule } (libelle)}
									<td class="num">{@render prixCellule(cellule)}</td>
								{/each}
								<td class="num annuaire-population">{@render populationCellule(commune)}</td>
								<td>{nomDepartement(commune.departement)}</td>
								<td><AjoutComparaison code={commune.code_insee} nom={commune.nom} /></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<ul class="annuaire-cartes">
				{#each rangees as { ligne: commune, sous, communeAbsente } (commune.code_insee)}
					<li class="annuaire-carte" class:annuaire-sous={sous} data-code-insee={commune.code_insee}>
						<p class="annuaire-carte-nom">
							<a href="/commune/{encodeURIComponent(commune.code_insee)}">{sous ? nomSousCommune(commune) : commune.nom}{#if sous}<span class="annuaire-sr">{" "}de {communeParente(commune.code_insee)?.nom ?? ''}</span>{/if}</a>
							{#if communeAbsente}<span class="annuaire-appartenance">{appartenance(communeAbsente)}</span>{/if}
						</p>
						<dl class="annuaire-carte-prix teinte-q-prix">
							{#each cellules(commune) as { libelle, cellule } (libelle)}
								<div>
									<dt>{libelle}</dt>
									<dd>{@render prixCellule(cellule)}</dd>
								</div>
							{/each}
						</dl>
						<p class="annuaire-carte-meta">
							{nomDepartement(commune.departement)} · {@render populationCellule(commune)}
						</p>
						<AjoutComparaison code={commune.code_insee} nom={commune.nom} />
					</li>
				{/each}
			</ul>
			</Panneau>
			{#if pageCount > 1}
				<!-- Preloaded on tap: a hover over a dozen links would read as many pages of the API. -->
				<nav class="communes-pages" aria-label="Pages de l'annuaire" data-sveltekit-preload-data="tap">
					<span class="communes-count">Page {reponse.page} sur {pageCount}</span>
					<ul class="communes-pages-liens">
						{#if reponse.page > 1}
							<li><a class="bouton-secondaire" href={hrefAnnuaire(filtres, { page: 1 })}>Première page</a></li>
							<li><a class="bouton-secondaire" href={hrefAnnuaire(filtres, { page: reponse.page - 1 })}>Page précédente</a></li>
						{/if}
						{#each pagesVoisines(reponse.page, pageCount) as numero (numero)}
							<li>
								{#if numero === reponse.page}
									<span class="communes-page-courante" aria-current="page"><span class="annuaire-sr">{'Page '}</span>{numero}</span>
								{:else}
									<a class="bouton-secondaire communes-page-numero" href={hrefAnnuaire(filtres, { page: numero })} aria-label="Page {numero}">{numero}</a>
								{/if}
							</li>
						{/each}
						{#if reponse.page < pageCount}
							<li><a class="bouton-secondaire" href={hrefAnnuaire(filtres, { page: reponse.page + 1 })}>Page suivante</a></li>
							<li><a class="bouton-secondaire" href={hrefAnnuaire(filtres, { page: pageCount })}>Dernière page</a></li>
						{/if}
					</ul>
					<form class="communes-pages-saut" action="/communes" method="get">
						{#each champsAnnuaire(filtres, { page: 1 }) as [nom, valeur] (nom)}
							<input type="hidden" name={nom} value={valeur} />
						{/each}
						<label for="annuaire-page">Aller à la page</label>
						<input id="annuaire-page" name="page" type="number" inputmode="numeric" min="1" max={pageCount} value={reponse.page} required />
						<button type="submit" class="bouton-secondaire">Aller</button>
					</form>
				</nav>
			{/if}
		{/if}
	{/if}
{/snippet}

<!-- A failed search in its own group: typed by `panne.ts`, the generic outage line otherwise. -->
{#snippet panneDeGroupe(panne: { titre: string | null; message: string | null }, phrase: string)}
	<div class="communes-notice communes-notice-warn">
		<Notice titre={panne.titre ?? panne.message ?? phrase} variante="warn" role="alert">
		{#if panne.titre}<p>{panne.message ?? phrase}</p>{/if}
		<form
			action="/communes"
			method="get"
			onsubmit={(event) => {
				event.preventDefault();
				invalidateAll();
			}}
		>
			<input type="hidden" name="q" value={data.q} />
			{#if data.departement}<input type="hidden" name="departement" value={data.departement} />{/if}
			<button type="submit" class="bouton-secondaire">{RELANCER}</button>
		</form>
		</Notice>
	</div>
{/snippet}

{#snippet groupeAdresses()}
	{#if adresses?.etat === 'panne'}
		<div class="communes-group">{@render panneDeGroupe(adresses.panne, PANNE_ADRESSES)}</div>
	{:else if adresses?.etat === 'trop_large'}
		<!-- R-08: too broad for the national read, served as such: no outage, no « Relancer » (the same
		     input meets the same budget), no zero. With no commune either, the next steps of an empty
		     search follow. -->
		<div class="communes-group">
			<div class="communes-notice">
				<Notice titre={RECHERCHE_TROP_LARGE}>
					{#if data.results.length === 0 && !data.panne}{@render suiteRecherche()}{/if}
				</Notice>
			</div>
		</div>
	{:else if adressesHorsCouverture}
		<div class="communes-group">
			<div class="communes-notice communes-notice-warn">
				<Notice titre={titreAdressesHorsCouverture(adressesHorsCouverture)} variante="warn">
					<p>{phraseMotif(adressesHorsCouverture.motif)} {PAS_ZERO}</p>
					<p class="communes-liens"><a href="/datasets">{LIEN_COUVERTURE}</a></p>
				</Notice>
			</div>
		</div>
	{:else if adressesServies.length > 0}
		<Panneau titre={libelleAdresses}>
			{#if messageNumero}
				<p class="communes-number-match" data-number-match="proches">{messageNumero}</p>
			{/if}
			{#each segmentsAdresses as segment, rang (rang)}
				{#if segment.type === 'liens'}
					<ListeLiens liens={segment.liens} />
				{:else}
					<ul class="voies">
						<li class="voie">
							<span class="lien-t">{libelleVoie(null, null, segment.voie.nom_voie)}</span>
							<span class="lien-n">Voie · {lieuAdresse(segment.voie)} · {PRECISEZ_LE_NUMERO}</span>
						</li>
					</ul>
				{/if}
			{/each}
		</Panneau>
	{/if}
{/snippet}

<!-- One commune of a search: the whole row opens its fiche (→); out of coverage, its reason under it;
     « Ajouter à la comparaison » under the row (a JavaScript enhancement, AjoutComparaison). -->
{#snippet listeCommunes()}
	<ul class="resultats">
		{#each data.results as result (result.code_insee)}
			{@const hors = result.hebergement.etat === 'hors_couverture'}
			<li class="resultat" data-code-insee={result.code_insee}>
				<a class="resultat-lien" href={`/commune/${encodeURIComponent(result.code_insee)}`}
					><span class="lien-t">{result.nom}</span><span class="lien-n"
						><span>{identiteCommune(result)}</span>{#if populationCommune(result)}{' · '}<span
								>{populationCommune(result)}</span
							>{/if}</span
					><span
						class="fleche"
						aria-hidden="true">→</span
					></a
				>
				{#if hors}
					<!-- D8: the badge of the fiche; label and sentence from the glossary, as before (the sentence of a known reason only). -->
					<div class="resultat-etat">
						<StateValue etat={{ kind: 'absence', etat: 'out_of_coverage', motif: result.hebergement.motif ?? '', effectif: null }} />
					</div>
				{/if}
				<AjoutComparaison code={result.code_insee} nom={result.nom} />
			</li>
		{/each}
	</ul>
	{#if plusHref}
		<p class="communes-more"><a href={plusHref} class="bouton-secondaire">Afficher plus</a></p>
	{/if}
{/snippet}

{#snippet groupeCommunes()}
	{#if data.panne}
		<div class="communes-group">{@render panneDeGroupe(data.panne, PANNE_COMMUNES)}</div>
	{:else if data.results.length > 0}
		<!-- D11: the state line above the groups says the count; the panel does not say it again. -->
		<Panneau titre="Communes">
			{@render listeCommunes()}
		</Panneau>
	{:else if data.couverture?.kind === 'hors-couverture' && !adressesHorsCouverture}
		<div class="communes-group">
			<div class="communes-notice communes-notice-warn">
				<Notice titre={TITRE_CODE_POSTAL_HORS_COUVERTURE} variante="warn">
					<p>
						{data.couverture.phrase ?? `${LIBELLE_HORS_COUVERTURE}.`}
						{CONSEIL_RECHERCHE_PAR_NOM}
					</p>
					<p class="communes-liens"><a href="/datasets">{LIEN_COUVERTURE}</a> <a href="/couverture">Limites connues des données</a></p>
				</Notice>
			</div>
		</div>
	{/if}
{/snippet}

<style>
	.communes-results {
		margin-top: var(--s6);
	}

	.communes-hint,
	.communes-count {
		max-width: var(--mesure);
		margin: 0;
		color: var(--color-muted);
		font-size: 16px;
		line-height: 1.5;
	}

	.communes-list {
		display: flex;
		flex-direction: column;
		gap: var(--s4);
		margin: var(--s4) 0 0;
		padding: 0;
		list-style: none;
	}

	.communes-number-match {
		margin: var(--s2) 0 0;
		color: var(--color-muted);
		font-size: 16px;
		line-height: 1.5;
	}

	.communes-skeleton {
		height: 120px;
		border-radius: var(--radius-tuile);
		background: var(--color-line);
	}

	/* Two links of a state panel on one line, 44 px targets. */
	.communes-liens {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2) var(--s5);
	}

	.communes-liens a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		text-decoration: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.communes-suite {
		margin-top: var(--s4);
		padding-top: var(--s2);
		border-top: 1px solid var(--color-line);
	}

	.communes-suite a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	.communes-suite-liste {
		margin: 0;
		padding-left: var(--s5);
		list-style: disc;
	}

	.communes-more {
		margin: var(--s5) 0 0;
	}

	/* Directory (04.6-02): filters, then a table on desktop and one card per commune up to 720 px. */
	.annuaire-filtres {
		margin-top: var(--s3);
	}

	.annuaire-champs {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
		gap: var(--s4);
		margin-top: var(--s4);
	}

	.annuaire-champ {
		display: flex;
		flex-direction: column;
		gap: var(--s1);
		min-width: 0;
	}

	.annuaire-champ label {
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.annuaire-champ input,
	.annuaire-champ select {
		box-sizing: border-box;
		width: 100%;
		min-height: 44px;
		padding: 0 var(--s3);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-weight: inherit;
		font-style: inherit;
		line-height: inherit;
		font-size: 16px;
	}

	.annuaire-aide {
		margin: var(--s4) 0 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.annuaire-aide a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	.annuaire-actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
		margin-top: var(--s4);
	}

	.annuaire-panne-prix {
		margin-top: var(--s4);
	}

	/* One frame, the panel's: the table draws no border of its own (D7). */
	.annuaire-tableau {
		overflow-x: auto;
		margin-top: var(--s4);
	}

	/* The headers of the prices in the tint of the prices, as the price tiles of the commune fiche. */
	.annuaire-table thead th.teinte-q-prix {
		background: var(--c-fond);
		color: var(--c-encre);
	}

	.annuaire-carte-prix.teinte-q-prix dt {
		color: var(--c-encre);
	}

	.pop-millesime {
		display: block;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		white-space: nowrap;
	}

	.annuaire-carte-meta .pop-millesime {
		display: inline;
	}

	.annuaire-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 16px;
		line-height: 1.5;
	}

	.annuaire-table caption {
		padding: var(--s3) var(--s4);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		text-align: left;
	}

	.annuaire-table th,
	.annuaire-table td {
		padding: var(--s3) var(--s4);
		border-top: 1px solid var(--color-line);
		text-align: left;
		vertical-align: top;
		/* `break-word`, not `anywhere`: a long word may still break, but the column keeps its min-content
		   width, so « Commune » or « Ille-et-Vilaine » is not cut inside the narrower simple template. */
		overflow-wrap: break-word;
	}

	.annuaire-table thead th {
		color: var(--color-muted);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.annuaire-table .num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}

	/* Names keep proportional glyphs: the tabular setting of tables widens the hyphen of Inter
	   (« Saint - Nazaire »). */
	.annuaire-table tbody th,
	.annuaire-table td:not(.num) {
		font-variant-numeric: normal;
	}

	.annuaire-table tbody th a,
	.annuaire-carte-nom a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		font-weight: 600;
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	.prix {
		display: flex;
		flex-direction: column;
		gap: var(--s-fin);
	}

	.prix-valeur .prix-texte {
		font-weight: 600;
	}

	.prix-detail,
	.prix-nd .prix-texte,
	.prix-aucune-vente .prix-texte,
	.prix-etat .prix-texte {
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.prix-indisponible .prix-texte {
		color: var(--color-warn);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.annuaire-sr {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}

	.annuaire-cartes {
		display: none;
		margin: var(--s4) 0 0;
		padding: 0;
		list-style: none;
	}

	/* An arrondissement is read as a part of the commune above it, never as one more commune. */
	.annuaire-table tr.annuaire-sous > th {
		padding-left: var(--s6);
		font-weight: 400;
	}
	.annuaire-carte.annuaire-sous {
		margin-left: var(--s4);
	}
	.annuaire-appartenance {
		display: block;
		font-size: 14px;
		font-weight: 400;
		color: var(--color-muted);
	}
	.annuaire-carte {
		padding: var(--s4);
		border: 1px solid var(--color-line);
		background: var(--color-paper);
	}

	.annuaire-carte + .annuaire-carte {
		margin-top: var(--s2);
	}

	.annuaire-carte-nom {
		margin: 0;
		font-size: 16px;
		line-height: 1.2;
		overflow-wrap: anywhere;
	}

	.annuaire-carte-meta {
		margin: var(--s1) 0 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.annuaire-carte-prix {
		margin: var(--s3) 0;
	}

	.annuaire-carte-prix div {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		gap: var(--s1) var(--s4);
		padding: var(--s2) 0;
		border-top: 1px solid var(--color-line);
	}

	.annuaire-carte-prix dt {
		font-size: 16px;
		font-weight: 600;
	}

	.annuaire-carte-prix dd {
		margin: 0;
		min-width: 0;
		font-size: 16px;
		text-align: right;
		font-variant-numeric: tabular-nums;
	}

	.annuaire-carte-prix .prix {
		align-items: flex-end;
	}

	/* Tighter cells before the region has to scroll: the last column stayed cut at 768 px. */
	@media (max-width: 1040px) {
		.annuaire-table th,
		.annuaire-table td {
			padding-inline: var(--s2);
		}
	}

	@media (max-width: 720px) {
		.annuaire-filtres {
			padding: var(--s4);
		}

		.annuaire-tableau {
			display: none;
		}

		.annuaire-cartes {
			display: block;
		}
	}

	.communes-pages {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s3) var(--s4);
		margin-top: var(--s5);
	}

	.communes-pages-liens {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.communes-page-numero {
		min-width: 44px;
		padding: 0 var(--s2);
		font-variant-numeric: tabular-nums;
	}

	/* The current page is a place, not a control: no border, the weight of a current link (§ 3.11). */
	.communes-page-courante {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 44px;
		min-height: 44px;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
		border-bottom: 2px solid var(--color-ink);
	}

	.communes-pages-saut {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s2);
	}

	.communes-pages-saut input[type='number'] {
		box-sizing: border-box;
		width: 88px;
		min-height: 44px;
		padding: 0 var(--s2);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-size: 16px;
		font-variant-numeric: tabular-nums;
	}

	@media print {
		.annuaire-filtres,
		.communes-pages {
			display: none;
		}
	}

	.communes-champ {
		margin-top: var(--s5);
	}

	/* The state of the search (UI-SPEC § 6.5): served texts, at the measure, wrapped whatever the query. */
	.r-etat {
		max-width: var(--mesure);
		margin: var(--s4) 0 0;
		overflow-wrap: anywhere;
	}

	/* « Filtrer »: the framed fold of the socle (`depliant-cadre`, T3); the page keeps its margin. */
	.det {
		margin-top: var(--s5);
	}

	/* Rows of the results: the whole row is the link, in ink, the arrow the only accent (§ 3.0, § 3.10). */
	.resultats,
	.voies {
		margin: var(--s3) 0 0;
		padding: 0;
		list-style: none;
	}

	.resultat + .resultat {
		border-top: 1px solid var(--color-filet-pale);
	}

	/* A street row sits at its served rank between two runs of links (D-106): one row per list, the same
	   pale line between rows as inside a list of links. */
	.voies {
		margin-top: 0;
		border-top: 1px solid var(--color-filet-pale);
	}

	.resultat {
		padding-bottom: var(--s2);
	}

	.resultat-lien,
	.voie {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--s-fin) var(--s3);
		padding: var(--s3) var(--s2);
	}

	.resultat-lien {
		margin: 0 calc(-1 * var(--s2));
		border-radius: var(--radius-ligne);
		color: var(--color-ink);
		text-decoration: none;
	}

	.resultat-lien:hover {
		background: var(--color-soft);
	}

	.voie {
		padding-right: 0;
		padding-left: 0;
	}

	.lien-t {
		flex: 1 1 14rem;
		min-width: 0;
		font-weight: 600;
		overflow-wrap: anywhere;
	}

	.lien-n {
		flex: 0 1 auto;
		min-width: 0;
		color: var(--color-muted);
		font-size: 14px;
		text-align: right;
		overflow-wrap: anywhere;
	}

	.fleche {
		flex: none;
		color: var(--color-accent);
	}

	.resultat-etat {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--s1) var(--s2);
		max-width: var(--mesure);
		margin: 0 0 var(--s2);
		font-size: 14px;
	}


	@media (max-width: 720px) {
		.lien-n {
			flex-basis: 100%;
			text-align: left;
		}

	}
</style>
