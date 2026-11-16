<script lang="ts">
	// Address fiche on the report format (04.7-03, UI-SPEC v3; reading of 06/11, CADRAGE R1 to R10). The layout
	// loader reads `GET /v1/adresse/{ban_id}/rapport` once per visit; the page renders, in reading order, the
	// head (breadcrumb, h1, commune and day of reading), the answer (price, rent, DPE) and the line of what
	// touches the address, the summary and the four categories, then the attachment sentence, the actions and
	// the map, then the legend and the sources. The DOM is that order at every width. Above 1080 px a grid lifts
	// the actions and the map beside the identity and sets the summary in a sticky margin, the categories open
	// as cards; under it they are closed rows. Every row, count and sentence reads its own theme object (A1); a
	// theme the front does not know renders generically from its title, verdict, figures, scale, attachment and
	// sources.
	// The lists the report does not carry stay on the page: the declared DPE documents in the detail
	// of the DPE criterion, the sales of the parcel and those declared at the address (C37, two
	// relations never merged) in the detail of the sales criterion, the similar sales (D-114, C42) in
	// a modal panel. « Télécharger (CSV) » exports one row per criterion; printing opens every
	// criterion and the sources.
	// States (SEM-01): out of coverage is a 200 page with its notice and no criterion (D-105); a
	// refused request (409, 429, 401, 403) is titled by `panne.ts` and shows no value; unknown key,
	// street and outage are thrown by the layout loader and rendered by `+error.svelte`.
	import { onMount, tick, untrack } from 'svelte';
	import { goto, invalidate, invalidateAll } from '$app/navigation';
	import { navigating, page } from '$app/stores'; // Project convention: stores, never the state module.
	import DpeDocumentList from '$lib/components/adresse/DpeDocumentList.svelte';
	import ComparablesForm from '$lib/components/adresse/ComparablesForm.svelte';
	import Pagination from '$lib/components/adresse/Pagination.svelte';
	import VenteList from '$lib/components/adresse/VenteList.svelte';
	import CarteAdresse from '$lib/components/adresse/rapport/CarteAdresse.svelte';
	import BilanRisques from '$lib/components/adresse/question/BilanRisques.svelte';
	import EnBref from '$lib/components/adresse/question/EnBref.svelte';
	import LigneCritere from '$lib/components/adresse/question/LigneCritere.svelte';
	import PartieQuestion from '$lib/components/adresse/question/PartieQuestion.svelte';
	import LireUneFiche from '$lib/components/ds/LireUneFiche.svelte';
	import Panneau from '$lib/components/ds/Panneau.svelte';
	import Sommaire, { type EntreeSommaire } from '$lib/components/adresse/rapport/Sommaire.svelte';
	import IconFermer from '$lib/components/ds/icons/IconFermer.svelte';
	import ExportDialog from '$lib/components/fiche/ExportDialog.svelte';
	import EnTetePrint from '$lib/components/fiche/EnTetePrint.svelte';
	import LienLimites from '$lib/components/fiche/LienLimites.svelte';
	import Notice from '$lib/components/fiche/Notice.svelte';
	import SourceButton from '$lib/components/fiche/SourceButton.svelte';
	import SourcePanel from '$lib/components/fiche/SourcePanel.svelte';
	import StateValue from '$lib/components/fiche/StateValue.svelte';
	import type { EntreeVolet } from '$lib/data/commune-kpi';
	import { phraseIdentiteIndeterminee } from '$lib/data/adresse-fiche';
	import { LIEN_RECHARGER_FICHE, alerteDe, dateFr, lireResume, mentionLectures, phraseEnBref, sourcesDuRapport } from '$lib/data/adresse-rapport';
	import { QUESTIONS, cibleDeAncre, placerThemes, type ArgsCritere, type IdPartie } from '$lib/data/questions';
	import { developperParCle } from '$lib/utils/sigles';
	import { typographieFr } from '$lib/utils/typographie';
	import { voieLisible } from '$lib/utils/voie-lisible';
	import { baseFichierExport, lignesExportRapport } from '$lib/data/adresse-export';
	import { dateConsultationFr } from '$lib/utils/date-consultation';
	import { LIBELLES_MOTIFS, LIBELLE_HORS_COUVERTURE } from '$lib/data/etats-libelles';
	import { PAS_ZERO, libelleDepartement, libelleVoie, titreAdresseA } from '$lib/data/recherche-lieux';
	import {
		CHARGEMENT_COMPARABLES,
		MESSAGE_PANNE_VENTES,
		MESSAGE_PANNE_COMPARABLES,
		RELANCER_COMPARABLES,
		PHRASE_COMPARABLES_SANS_POSITION,
		PHRASE_CRITERE_NON_RECONNU,
		PHRASE_RECOUVREMENT_VENTES,
		PHRASE_SANS_TYPE,
		RECHARGER_VENTES,
		TITRE_COMPARABLES,
		TITRE_VENTES,
		TITRE_VENTES_ADRESSE,
		lienPage,
		lireCriteres,
		motifServiVentesAdresse,
		phraseEffectif,
		phraseZeroComparables,
		phraseZeroVentes,
		phraseZeroVentesAdresse,
		plageVentes,
		preremplissageComparables,
		recouvrementPossible,
		reservesComparables,
		voletComparables,
		voletVentes,
		voletVentesAdresse
	} from '$lib/data/adresse-ventes';
	import {
		DEPENDANCE_VENTES,
		type IssueComparables,
		type IssueDocumentsDpe,
		type IssueRapportAdresse,
		type IssueVentes,
		type IssueVentesAdresse
	} from '$lib/api/adresses';
	import { lecturesEnCours } from '$lib/api/rapport';
	import type { AdresseIdentiteData } from './+layout.server';
	import { addressComparisonSelection } from '$lib/stores/comparison-selection.svelte';
	import { lienSelectionNature } from '$lib/data/commune-comparison-selection';
	import FilAriane from '$lib/components/ds/FilAriane.svelte';
	import { OG_IMAGE_URL, SITE_URL } from '$lib/config';

	// The identity and the report come from `+layout.server.ts`, the lists from `+page.server.ts`
	// (FRONT WR-03); the lists are rendered for a served identity only.
	let {
		data
	}: {
		data: AdresseIdentiteData & {
			documents?: IssueDocumentsDpe | null;
			ventes?: IssueVentes | null;
			ventesAdresse?: IssueVentesAdresse | null;
			comparables?: IssueComparables | null;
		};
	} = $props();

	let panneau = $state<ReturnType<typeof SourcePanel>>();

	// R-07: the first answer does not wait for the slow sources. Their criteria are served « en cours »,
	// and the page asks once for the follow-up, which replaces the report when every read has ended. The
	// follow-up belongs to the answer it completes: another address, or a reload, drops it.
	// Raw state: both are compared by identity with the answer they complete, which a proxy would break.
	let suite = $state.raw<{ de: IssueRapportAdresse; issue: IssueRapportAdresse } | null>(null);
	let suiteInterrompue = $state.raw<IssueRapportAdresse | null>(null);
	const issue = $derived(suite !== null && suite.de === data.issue ? suite.issue : data.issue);
	const enAttente = $derived(data.issue.kind === 'ok' ? lecturesEnCours(data.issue.rapport) : []);

	async function lireSuite(de: IssueRapportAdresse & { kind: 'ok' }, signal: AbortSignal) {
		try {
			const reponse = await fetch(`/adresse/${encodeURIComponent(de.fiche.identite.ban_id ?? '')}/suite`, { signal, headers: { accept: 'application/json' } });
			if (!reponse.ok) throw new Error(String(reponse.status));
			const corps = (await reponse.json()) as { issue?: IssueRapportAdresse };
			if (corps.issue?.kind !== 'ok') throw new Error('suite');
			suite = { de, issue: corps.issue };
		} catch {
			// One attempt, never a loop: the rows stay « en cours », the mention gives the reload link.
			if (!signal.aborted) suiteInterrompue = de;
		}
	}

	$effect(() => {
		const de = data.issue;
		if (de.kind !== 'ok' || lecturesEnCours(de.rapport).length === 0) return;
		const abandon = new AbortController();
		void lireSuite(de, abandon.signal);
		return () => abandon.abort();
	});
	const fiche = $derived(issue.kind === 'ok' || issue.kind === 'hors_couverture' ? issue.fiche : null);
	const identite = $derived(fiche?.identite ?? null);
	const rapport = $derived(issue.kind === 'ok' ? issue.rapport : null);
	const themes = $derived(rapport?.themes ?? []);
	// The summary and the attachment sentence are served (04.7-17): read, never computed again.
	const resume = $derived(lireResume(rapport?.resume ?? null, themes));
	// The categories (D-09): every served theme at its place in the classement; a theme this front does not
	// know goes to « Autres critères ».
	const placement = $derived(placerThemes(themes));
	const partiePrix = $derived(placement.parties.some((p) => p.id === 'q-prix'));
	const entreesSommaire = $derived<EntreeSommaire[]>([
		...placement.parties.flatMap((p): EntreeSommaire[] => [
			{ ancre: p.id, titre: p.titre, niveau: 1, partie: p.id },
			...[...p.premiers, ...p.autres].map((t) => ({ ancre: `critere-${t.id}`, titre: typographieFr(t.titre), niveau: 2 as const })),
			...(p.id === 'q-prix' ? [{ ancre: 's-comparables', titre: TITRE_COMPARABLES, niveau: 2 as const }] : [])
		]),
		...(placement.autres.length ? [{ ancre: 'q-autres', titre: 'Autres critères', niveau: 1 as const }] : []),
		{ ancre: 'lire', titre: 'Lire une fiche', niveau: 1, fin: true },
		{ ancre: 'sources', titre: 'Sources de la fiche', niveau: 1, fin: true }
	]);
	const sources = $derived(sourcesDuRapport(themes));
	const rattachement = $derived(rapport?.rattachementPhrase ?? null);
	const restantes = $derived(rapport ? lecturesEnCours(rapport) : []);
	const mention = $derived(
		mentionLectures({
			attendues: enAttente.length,
			restantes: restantes.length,
			pannes: themes.filter((t) => t.etat === 'panne' && enAttente.includes(t.id)).length,
			interrompue: suiteInterrompue === data.issue
		})
	);
	// Acronyms spelled out at their first occurrence (04.8-08, D-17, D-31). What is in sight comes first: the
	// labels of the key figures, the arrival line, the attachment sentence. A closed category hides its text, so
	// each one is read on its own after that head, and an acronym is spelled out in every category the reader
	// may open first. Never a value, a badge, a table cell or the export, which reads the report itself.
	const textes = $derived.by(() => {
		const critere = (t: (typeof themes)[number]) =>
			[
				[`titre:${t.id}`, typographieFr(t.titre)],
				[`verdict:${t.id}`, typographieFr(t.verdict)],
				[`alerte:${t.id}`, alerteDe(t) === null ? null : typographieFr(alerteDe(t)!)]
			] as const;
		const enTete = [
			...(rapport?.chiffresCles ?? []).map((k) => [`cle:${k.id}`, k.libelle] as const),
			...(resume?.pointsAdresse ?? []).map((t) => [`bref:bref-l-adresse:${t.id}`, typographieFr(t.titre)] as const),
			['rattachement', rattachement === null ? null : typographieFr(rattachement)] as const,
			[
				'bref:sources',
				resume && resume.pannes.length + resume.sansValeur.length > 0 && resume.sourcesEnPanne.length ? resume.sourcesEnPanne.join(', ') : null
			] as const
		];
		const bilan = [
			...(resume ? phraseEnBref(resume).map((s, i) => [`bref:phrase:${i}`, s.texte] as const) : []),
			...(resume?.pointsSecteur ?? []).map((t) => [`bref:bref-l-secteur:${t.id}`, typographieFr(t.titre)] as const)
		];
		const parPartie = placement.parties.map((p) =>
			developperParCle([...enTete, ...(p.id === 'q-risques' ? bilan : []), ...[...p.premiers, ...p.autres].flatMap(critere)])
		);
		const fin = developperParCle([
			...enTete,
			...placement.autres.flatMap(critere),
			['source:adresse', issue.kind === 'ok' ? (issue.source?.nom ?? null) : null],
			...sources.flatMap((s) => [[`source:${s.cle}:nom`, s.nom] as const, [`source:${s.cle}:producteur`, s.producteur || null] as const])
		]);
		return new Map([...parPartie.flatMap((m) => [...m]), ...fin]);
	});
	// The lists sit in the detail of their criterion; a report without that criterion keeps them
	// after the parts, so that no list is lost.
	const porteDpe = $derived(themes.some((t) => t.id === 'dpe'));
	const porteVentes = $derived(themes.some((t) => t.id === 'ventes_parcelle'));

	const nomCommune = $derived(
		identite ? (identite.commune.nom ?? `Commune INSEE ${identite.commune.code_insee}`) : ''
	);
	// The street of the served identity, readable (UI-SPEC v4 § 7.5): breadcrumb, h1 and title.
	const voie = $derived(
		identite?.numero && identite.nom_voie
			? [identite.numero, identite.rep, voieLisible(identite.nom_voie)].filter(Boolean).join(' ')
			: identite
				? libelleVoie(identite.numero, identite.rep, identite.nom_voie)
				: ''
	);
	const departement = $derived(identite ? libelleDepartement(identite.departement) : '');
	// An undetermined identity keeps its title; the disagreement on the position is said under it.
	const phraseIndeterminee = $derived(issue.kind === 'ok' && identite ? phraseIdentiteIndeterminee(identite) : null);
	const parcelle = $derived(
		identite?.parcelle?.etat === 'unique' && identite.parcelle.id_parcelle ? identite.parcelle.id_parcelle : null
	);
	const titre = $derived(
		issue.kind === 'panne'
			? (issue.panne.titre ?? '')
			: identite?.etat === 'etablie' && identite.numero && identite.nom_voie && issue.kind === 'ok'
				? // D-33: composed of the served number, suffix and street of an established identity; the
					// served label otherwise, and « Adresse à … » for an undetermined one (04-DECISIONS-TEXTES).
					voie
				: identite?.libelle && issue.kind === 'ok'
					? identite.libelle
					: identite
						? titreAdresseA(nomCommune, identite.departement)
						: ''
	);
	const motifHorsCouverture = $derived.by(() => {
		const motif = issue.kind === 'hors_couverture' ? issue.fiche.couverture.motif : null;
		return motif && Object.hasOwn(LIBELLES_MOTIFS, motif) ? LIBELLES_MOTIFS[motif].phrase : `${LIBELLE_HORS_COUVERTURE}.`;
	});

	// « Télécharger (CSV) »: one row per criterion, as the rows show it (UI-SPEC v3 § 4.1).
	let exportOuvert = $state(false);
	const themesExport = $derived(
		lignesExportRapport(
			themes,
			nomCommune || null,
			identite ? { adresse: identite.libelle ?? (voie || null), commune: nomCommune, departement } : null
		)
	);
	// Date of consultation of the printed document (D-12), taken when the page is rendered.
	const consulteLe = dateConsultationFr();

	// « Comparer avec une autre adresse » (UI-SPEC point 2, 04-07): a link to the comparator with this
	// address, which works without JavaScript; with it, the address joins the address selection of
	// the tab (its own storage, never the communes') and the comparator opens on that selection.
	// Preloaded on tap only (04-DECISIONS-TEXTES row 48): a hover never reads the fiche again.
	const lienComparer = $derived(identite ? lienSelectionNature('adresse', [identite.ban_id], false) : '/comparer');

	function comparer(event: MouseEvent) {
		if (!identite || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
		event.preventDefault();
		const ajout = addressComparisonSelection.add(identite.ban_id);
		// A full selection is opened as kept (the comparator says the limit), never replaced by this
		// address alone (D-15 FRONT WR-03); only an address the store refuses opens alone.
		const garder = ajout.ajoute || ajout.raison !== 'invalide';
		const ids = garder ? addressComparisonSelection.codes : [identite.ban_id];
		void goto(lienSelectionNature('adresse', ids, false));
	}
	// Client navigation to another address: the figures of this one are never shown as the next
	// one's (same rule as the commune fiche). A page change of a list stays on this address.
	const chargement = $derived(
		$navigating?.to?.route.id === '/(marketing)/adresse/[id]' && $navigating.to.params?.id !== identite?.ban_id
	);

	let rechargement = $state(false);

	async function recharger() {
		rechargement = true;
		try {
			await invalidateAll();
		} finally {
			rechargement = false;
		}
	}

	// Parameters of the page kept by the links of each list (the other lists, the criteria); the
	// `theme` and `vue` of the former reading by theme are dropped, and `panneau` opens the panel once.
	const parametres = $derived.by(() => {
		const lus = new URLSearchParams($page.url.searchParams);
		for (const cle of ['theme', 'vue', 'panneau']) lus.delete(cle);
		return lus;
	});
	const autresQueDocuments = $derived.by(() => {
		const sp = new URLSearchParams(parametres);
		sp.delete('page');
		sp.delete('g');
		return sp.toString();
	});

	// The criteria that hold a list open when the URL points into that list (a page of the list,
	// its anchor), on the server render too; another address starts closed again.
	const listeCiblee = (url: URL) => ({
		dpe: url.searchParams.has('page') || url.hash === '#documents',
		ventes: ['ventes_page', 'vd_page'].some((cle) => url.searchParams.has(cle)) || ['#ventes', '#ventes-adresse'].includes(url.hash)
	});
	let dpeOuvert = $state(untrack(() => listeCiblee($page.url).dpe));
	let ventesOuvert = $state(untrack(() => listeCiblee($page.url).ventes));
	// Under the threshold of two columns the categories are closed; each one is opened by what the URL points
	// to, on the server render too. Several may stay open. Above the threshold they are cards, always open.
	let deuxColonnes = $state(false);
	const partiesFermees = () => Object.fromEntries(QUESTIONS.map((p) => [p.id, false])) as Record<IdPartie, boolean>;
	const partiesCiblees = (url: URL) => ({ ...partiesFermees(), 'q-logement': listeCiblee(url).dpe, 'q-prix': listeCiblee(url).ventes });
	let ouvertes = $state(untrack(() => partiesCiblees($page.url)));
	// Opened on the server render before the hydration: kept, never closed by `bind:open` (R-06).
	let sourcesOuvertes = $state(typeof document !== 'undefined' && Boolean(document.querySelector<HTMLDetailsElement>('#sources details.sources-repli')?.open));
	const adresseLue = $derived(identite?.ban_id ?? null);
	// Another address starts as its URL says. The first run is not one: it would close what the reader opened
	// before the hydration.
	let adressePrecedente = untrack(() => adresseLue);
	$effect(() => {
		if (adresseLue === adressePrecedente) return;
		adressePrecedente = adresseLue;
		const cible = untrack(() => listeCiblee($page.url));
		dpeOuvert = cible.dpe;
		ventesOuvert = cible.ventes;
		ouvertes = untrack(() => partiesCiblees($page.url));
	});
	$effect(() => {
		const cible = listeCiblee($page.url);
		if (cible.dpe) {
			dpeOuvert = true;
			ouvertes['q-logement'] = true;
		}
		if (cible.ventes) {
			ventesOuvert = true;
			ouvertes['q-prix'] = true;
		}
	});

	/** Opens what an anchor points to, before the browser moves to it. */
	function ouvrirCible(hash: string) {
		const partie = cibleDeAncre(hash);
		if (partie) ouvertes[partie] = true;
		if (hash === '#sources') sourcesOuvertes = true;
	}

	/** The reader lands on the title of what the link named, one Tab from what follows it. */
	function viserTitre(cible: HTMLElement) {
		const titre = cible.matches('h2, h3, h4, h5') ? cible : cible.querySelector<HTMLElement>('h2, h3, h4, h5');
		if (!titre) return;
		titre.tabIndex = -1;
		titre.focus({ preventScroll: true });
	}

	/** The target was folded when the browser looked for it: goes to it now that it is open. */
	async function allerALAncre(viser: boolean) {
		await tick();
		const cible = location.hash.length > 1 ? document.getElementById(location.hash.slice(1)) : null;
		if (!cible) return;
		cible.scrollIntoView?.();
		if (viser) viserTitre(cible);
	}

	function suivreAncre(viser: boolean) {
		const cible = listeCiblee(new URL(location.href));
		if (cible.dpe) dpeOuvert = true;
		if (cible.ventes) ventesOuvert = true;
		ouvrirCible(location.hash);
		if (location.hash === '#ventes-ressemblantes') ouvrirPanneau();
		return allerALAncre(viser);
	}

	// A link to the anchor already in the URL fires no hashchange: the click opens its category, and the title
	// takes the focus once the browser has followed the link.
	function ouvrirDepuisAncre(event: MouseEvent) {
		const lien = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href^="#"]') : null;
		if (!lien) return;
		ouvrirCible(lien.hash);
		if (lien.hash === location.hash) setTimeout(() => void allerALAncre(true));
	}

	// Printing opens every criterion, the legend and the sources, then gives their state back
	// (UI-SPEC v3 § 12; `details::details-content` covers the browsers that print closed details).
	let avantImpression: { element: HTMLDetailsElement; ouvert: boolean }[] | null = null;
	function ouvrirPourImpression() {
		if (avantImpression) return;
		const tous = [...document.querySelectorAll<HTMLDetailsElement>('.adresse details')];
		avantImpression = tous.map((element) => ({ element, ouvert: element.open }));
		for (const element of tous) element.open = true;
	}
	function rendreApresImpression() {
		for (const { element, ouvert } of avantImpression ?? []) element.open = ouvert;
		avantImpression = null;
	}

	// --- Sales of the parcel of the point (04-05) -------------------------------------------------
	let panneauSansReponse = $state<ReturnType<typeof SourcePanel>>();
	let panneauVentes = $state<ReturnType<typeof SourcePanel>>();
	let titreVentes = $state<HTMLHeadingElement>();
	let ventesEnCours = $state(false);
	let annonceVentes = $state('');
	const issueVentes = $derived(data.ventes ?? null);
	const ventesServies = $derived(issueVentes?.kind === 'ok' ? issueVentes.ventes : null);
	// A list without a readable count is an outage of the section, never « 0 » (SEM-01).
	const listeVentes = $derived(
		ventesServies?.etat === 'value' && typeof ventesServies.total === 'number' ? ventesServies : null
	);
	const absenceVentes = $derived(
		ventesServies && (ventesServies.etat === 'not_available' || ventesServies.etat === 'out_of_coverage')
			? ventesServies
			: null
	);
	const panneVentes = $derived(issueVentes?.kind === 'panne' ? issueVentes.panne : null);
	const plageDesVentes = $derived(listeVentes ? plageVentes(listeVentes, listeVentes.total) : null);
	const pagesVentes = $derived(listeVentes?.pages ?? 1);

	function lienVentes(numero: number): string {
		return lienPage(parametres, { ventes_page: String(numero), vg: listeVentes?.generation ?? null }, '#ventes');
	}

	/** With JavaScript: same URL as the link, in place; the cards stay until the answer. */
	async function changerDePageVentes(event: MouseEvent, href: string) {
		if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
		event.preventDefault();
		if (ventesEnCours) return;
		ventesEnCours = true;
		annonceVentes = '';
		try {
			await goto(href, { noScroll: true, keepFocus: true });
		} finally {
			ventesEnCours = false;
		}
		await tick();
		annonceVentes = plageDesVentes ?? '';
		titreVentes?.focus();
	}

	// « Recharger les ventes »: the lists are read again, never the identity nor the report.
	async function rechargerVentes() {
		if (ventesEnCours) return;
		ventesEnCours = true;
		try {
			await invalidate(DEPENDANCE_VENTES);
		} finally {
			ventesEnCours = false;
		}
	}

	// --- Sales declared at the address (C37) ------------------------------------------------------
	let panneauVentesAdresse = $state<ReturnType<typeof SourcePanel>>();
	let titreVentesAdresse = $state<HTMLHeadingElement>();
	let ventesAdresseEnCours = $state(false);
	let annonceVentesAdresse = $state('');
	const issueVentesAdresse = $derived(data.ventesAdresse ?? null);
	const ventesAdresseServies = $derived(issueVentesAdresse?.kind === 'ok' ? issueVentesAdresse.ventes : null);
	// A list without a readable count is an outage of the section, never « 0 » (SEM-01).
	const listeVentesAdresse = $derived(
		ventesAdresseServies?.etat === 'value' && typeof ventesAdresseServies.total === 'number' ? ventesAdresseServies : null
	);
	const absenceVentesAdresse = $derived(
		ventesAdresseServies && (ventesAdresseServies.etat === 'not_available' || ventesAdresseServies.etat === 'out_of_coverage')
			? ventesAdresseServies
			: null
	);
	// Codex review M1 (04.5-08): a reason without wording is « Valeur non affichable », marked, without a sentence.
	const motifAdresse = $derived(absenceVentesAdresse?.etat === 'not_available' ? motifServiVentesAdresse(absenceVentesAdresse.motif) : null);
	const panneVentesAdresse = $derived(issueVentesAdresse?.kind === 'panne' ? issueVentesAdresse.panne : null);
	// C4 (04.5-07): « …, dont {nombre_vefa} VEFA hors statistiques », the served count as read.
	const plageDesVentesAdresse = $derived(
		listeVentesAdresse ? plageVentes(listeVentesAdresse, listeVentesAdresse.total, 'Ventes', listeVentesAdresse.nombre_vefa) : null
	);
	const pagesVentesAdresse = $derived(listeVentesAdresse?.pages ?? 1);
	// UI-005 (D45-05, D45-06): the two relations stay two lists; the same sale may be in both, said once.
	const recouvrement = $derived(recouvrementPossible(ventesServies, ventesAdresseServies));

	function lienVentesAdresse(numero: number): string {
		return lienPage(parametres, { vd_page: String(numero), vg: listeVentesAdresse?.generation ?? null }, '#ventes-adresse');
	}

	async function changerDePageVentesAdresse(event: MouseEvent, href: string) {
		if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
		event.preventDefault();
		if (ventesAdresseEnCours) return;
		ventesAdresseEnCours = true;
		annonceVentesAdresse = '';
		try {
			await goto(href, { noScroll: true, keepFocus: true });
		} finally {
			ventesAdresseEnCours = false;
		}
		await tick();
		annonceVentesAdresse = plageDesVentesAdresse ?? '';
		titreVentesAdresse?.focus();
	}

	async function rechargerVentesAdresse() {
		if (ventesAdresseEnCours) return;
		ventesAdresseEnCours = true;
		try {
			await invalidate(DEPENDANCE_VENTES);
		} finally {
			ventesAdresseEnCours = false;
		}
	}

	// --- Similar sales (04-05, D-114), in a modal panel (UI-SPEC v3 § 4.8) -------------------------
	let panneauComparables = $state<ReturnType<typeof SourcePanel>>();
	let titreComparables = $state<HTMLHeadingElement>();
	let comparablesEnCours = $state(false);
	let annonceComparables = $state('');
	const issueComparables = $derived(data.comparables ?? null);
	const criteresLus = $derived(lireCriteres(parametres));
	const comparables = $derived(issueComparables?.kind === 'ok' ? issueComparables.comparables : null);
	const listeComparables = $derived(
		comparables?.etat === 'value' && typeof comparables.effectif === 'number' && comparables.criteres_appliques ? comparables : null
	);
	const absenceComparables = $derived(
		comparables && (comparables.etat === 'not_available' || comparables.etat === 'out_of_coverage') ? comparables : null
	);
	const phraseComparables = $derived(listeComparables ? phraseEffectif(listeComparables) : null);
	const plageDesComparables = $derived(
		listeComparables ? plageVentes(listeComparables, listeComparables.effectif, TITRE_COMPARABLES) : null
	);
	const pagesComparables = $derived(listeComparables?.pages ?? 1);
	// The form keeps the other lists where they are.
	const champsCaches = $derived(
		(['page', 'g', 'ventes_page', 'vd_page', 'vg'] as const).flatMap((cle) => {
			const valeur = parametres.get(cle);
			return valeur ? [[cle, valeur] as [string, string]] : [];
		})
	);
	const CLES_CRITERES = ['type', 'surface', 'tolerance', 'rayon', 'periode'] as const;
	// C42: a link without any criterion is prefilled from the served documents of the address; the
	// criteria of a link are always the visitor's. Nothing is searched until « Afficher… ».
	const preremplissage = $derived(
		CLES_CRITERES.some((cle) => parametres.has(cle))
			? null
			: preremplissageComparables(data.documents?.kind === 'ok' ? data.documents.documents : null)
	);
	const criteresFormulaire = $derived(
		preremplissage
			? { ...criteresLus, type: preremplissage.type ?? criteresLus.type, surface: preremplissage.surface ?? criteresLus.surface }
			: criteresLus
	);
	// Client navigation to new criteria of this address: « Chargement des ventes ressemblantes… »,
	// the form stays usable; a page change of another list is not a loading of this one.
	const chargementComparables = $derived.by(() => {
		const vers = $navigating?.to?.url;
		if (!vers || $navigating?.to?.params?.id !== identite?.ban_id || !vers.searchParams.get('type')) return false;
		return CLES_CRITERES.some((cle) => (vers.searchParams.get(cle) ?? '') !== (parametres.get(cle) ?? ''));
	});

	function lienComparables(numero: number): string {
		return lienPage(parametres, { vr_page: String(numero), vg: listeComparables?.generation ?? null }, '#ventes-ressemblantes');
	}

	async function changerDePageComparables(event: MouseEvent, href: string) {
		if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
		event.preventDefault();
		if (comparablesEnCours) return;
		comparablesEnCours = true;
		annonceComparables = '';
		try {
			await goto(href, { noScroll: true, keepFocus: true });
		} finally {
			comparablesEnCours = false;
		}
		await tick();
		annonceComparables = plageDesComparables ?? '';
		titreComparables?.focus();
	}

	// The panel opens on its link, on its anchor and when the URL carries criteria or a page of its
	// list. Without JavaScript the link (`panneau=ventes`) renders it open, as a plain dialog; with
	// it, the panel is modal, Escape or a click on the backdrop closes it and the focus goes back.
	let dialogue = $state<HTMLDialogElement>();
	let declencheurPanneau: HTMLElement | null = null;
	const panneauDemande = $derived(
		$page.url.searchParams.get('panneau') === 'ventes' || parametres.has('vr_page') || CLES_CRITERES.some((cle) => parametres.has(cle))
	);
	const lienPanneau = $derived(lienPage(parametres, { panneau: 'ventes' }, '#ventes-ressemblantes'));
	// Asked by the URL when the page is rendered. The dialog carries no `open` in the markup: a
	// re-render would set it back and close the modal panel without any `close` event. Without
	// JavaScript, a <noscript> style shows the asked panel in the flow of the page instead (its doubled
	// classes outrank the scoped rules of the drawer).
	const ouvertAuRendu = untrack(() => panneauDemande);
	const STYLE_SANS_SCRIPT =
		'<noscript><style>@media screen{dialog.panneau-comparables.panneau-comparables{display:block;position:static;width:auto;max-width:none;height:auto;max-height:none;margin:var(--s6) 0 0;border:1px solid var(--color-line);overflow:visible}dialog.panneau-comparables .panneau-tete.panneau-tete{position:static}dialog.panneau-comparables .panneau-fermer.panneau-fermer{display:none}}</style></noscript>';
	// Whether the visitor has the panel open. The element follows it: when a navigation of the page
	// renders the dialog again (new criteria, a page of its list), the new element is opened modal too.
	let panneauOuvert = $state(untrack(() => panneauDemande));
	let elementModal: HTMLDialogElement | null = null;
	// A URL that asks for the panel opens it; going back to one that no longer asks closes it.
	let demandePrecedente = untrack(() => panneauDemande);
	$effect(() => {
		const demande = panneauDemande;
		if (demande) panneauOuvert = true;
		else if (demandePrecedente) panneauOuvert = false;
		demandePrecedente = demande;
	});
	// Another address: its panel starts as its URL says, and the sales of this one are never shown
	// while the next one loads.
	$effect(() => {
		void adresseLue;
		untrack(() => {
			panneauOuvert = panneauDemande;
			demandePrecedente = panneauDemande;
		});
	});
	$effect(() => {
		if (chargement) untrack(() => (panneauOuvert = false));
	});
	$effect(() => {
		const d = dialogue;
		if (!d) return;
		if (!panneauOuvert) {
			if (elementModal === d && d.open) fermerPanneau();
			return;
		}
		if (elementModal === d && d.open) return;
		// Same fallback as SourcePanel for the environments without a modal dialog.
		if (d.open) d.removeAttribute('open');
		if (typeof d.showModal === 'function') d.showModal();
		else d.setAttribute('open', '');
		elementModal = d;
		titreComparables?.focus();
	});

	function ouvrirPanneau(declencheur?: HTMLElement | null) {
		if (panneauOuvert) return;
		declencheurPanneau = declencheur ?? null;
		panneauOuvert = true;
	}
	function fermerPanneau() {
		if (!dialogue) return;
		if (typeof dialogue.close === 'function') dialogue.close();
		else {
			dialogue.removeAttribute('open');
			dialogue.dispatchEvent(new Event('close'));
		}
	}
	function ouvrirDepuisLien(event: MouseEvent) {
		if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
		event.preventDefault();
		ouvrirPanneau(event.currentTarget as HTMLElement);
	}
	function apresFermeture() {
		panneauOuvert = false;
		elementModal = null;
		// The trigger may be gone (another address): the focus then stays where the browser puts it.
		if (declencheurPanneau?.isConnected) declencheurPanneau.focus();
		declencheurPanneau = null;
	}
	// Only a click on the backdrop closes: an empty area of the drawer is the dialog too.
	function fermerSurFond(event: MouseEvent) {
		if (!dialogue || event.target !== dialogue) return;
		const r = dialogue.getBoundingClientRect();
		const dedans = event.clientX >= r.left && event.clientX <= r.right && event.clientY >= r.top && event.clientY <= r.bottom;
		if (!dedans) fermerPanneau();
	}

	// Rendered open by the server, the dialog is made modal by the effect above.
	onMount(() => {
		void suivreAncre(false);
		if (typeof matchMedia !== 'function') return;
		const seuil = matchMedia(SEUIL_DEUX_COLONNES);
		const lire = () => (deuxColonnes = seuil.matches);
		lire();
		seuil.addEventListener('change', lire);
		return () => seuil.removeEventListener('change', lire);
	});

	const SEUIL_DEUX_COLONNES = '(min-width: 1081px)';

	const pageTitle = $derived(`${titre} — DataCommune`);
	const description = $derived(
		`Adresse ${titre} : documents DPE, ventes, risques, prix et quartier, avec leur source et leur échelle.`
	);
	const jourDeLecture = $derived(rapport?.date ? dateFr(rapport.date) : null);

	// The `i` of the identity line: definition, object and source.
	const volet = $derived<EntreeVolet[]>([
		{
			terme: 'Définition',
			valeur: 'Adresse de la Base Adresse Nationale (BAN) importée par ce site, désignée par sa clé BAN.'
		},
		{ terme: 'Objet', valeur: 'Adresse' },
		...(parcelle ? [{ terme: 'Parcelle', valeur: parcelle }] : []),
		{ terme: 'Source', valeur: issue.kind === 'ok' ? (issue.source?.nom ?? '') : '' }
	]);
</script>

<svelte:head>
	<title>{pageTitle}</title>
	<meta name="description" content={description} />
	<meta name="robots" content="noindex, nofollow" />
	<!-- Link preview (a shared fiche): the same image as the other public pages, the title and description above. -->
	<meta property="og:type" content="website" />
	<meta property="og:site_name" content="DataCommune" />
	<meta property="og:title" content={pageTitle} />
	<meta property="og:description" content={description} />
	{#if identite?.ban_id}<meta property="og:url" content={`${SITE_URL}/adresse/${encodeURIComponent(identite.ban_id)}`} />{/if}
	<meta property="og:image" content={OG_IMAGE_URL} />
	<meta property="og:locale" content="fr_FR" />
	<meta name="twitter:card" content="summary_large_image" />
	<meta name="twitter:title" content={pageTitle} />
	<meta name="twitter:description" content={description} />
	<meta name="twitter:image" content={OG_IMAGE_URL} />
	{#if ouvertAuRendu}{@html STYLE_SANS_SCRIPT}{/if}
</svelte:head>

<svelte:window onhashchange={() => suivreAncre(true)} onclick={ouvrirDepuisAncre} onbeforeprint={ouvrirPourImpression} onafterprint={rendreApresImpression} />

{#snippet listeDpe()}
	{#if data.documents && identite}
		<div id="documents">
			<DpeDocumentList issue={data.documents} banId={identite.ban_id} autres={autresQueDocuments} />
		</div>
	{/if}
{/snippet}

{#snippet listesVentes()}
	{#if issueVentes}
		<section class="ventes" id="ventes" aria-labelledby="ventes-titre" aria-busy={ventesEnCours}>
			<h3 id="ventes-titre" class="liste-titre" tabindex="-1" bind:this={titreVentes}>{TITRE_VENTES}</h3>
			<p class="ventes-annonce sr-only" aria-live="polite">{annonceVentes}</p>
			{#if listeVentes}
				<p class="ventes-commune">
					<span>DVF (DGFiP){#if listeVentes.periode}{' · '}{listeVentes.periode}{/if} · ventes de la parcelle, rattachées à aucun logement</span>
					<SourceButton libelle="ventes publiées sur la parcelle" onouvrir={(declencheur) => panneauVentes?.ouvrir(declencheur)} />
				</p>
				{#if listeVentes.total === 0}
					<p class="ventes-phrase">{phraseZeroVentes(listeVentes.periode)}</p>
				{:else}
					<VenteList ventes={listeVentes.ventes} />
				{/if}
				<Pagination
					plage={plageDesVentes}
					pages={pagesVentes}
					lienPrecedent={listeVentes.page > 1 ? lienVentes(listeVentes.page - 1) : null}
					lienSuivant={listeVentes.page < pagesVentes ? lienVentes(listeVentes.page + 1) : null}
					etiquette="Pages des ventes publiées sur la parcelle"
					prefixe="ventes"
					onchanger={changerDePageVentes}
				/>
				<SourcePanel bind:this={panneauVentes} titre={TITRE_VENTES} entrees={voletVentes(listeVentes)} brut={[]} source={listeVentes.source} />
			{:else if absenceVentes}
				<!-- A point outside every parcel, several parcels, an undetermined parcel or a source out of
				     coverage: said as such, never as zero (N-01, N-02). -->
				<StateValue
					etat={{ kind: 'absence', etat: absenceVentes.etat === 'out_of_coverage' ? 'out_of_coverage' : 'not_available', motif: absenceVentes.motif ?? '', effectif: absenceVentes.nombre_parcelles }}
				/>
			{:else}
				<div class="ventes-panne" role="alert">
					<!-- 429, 401, 403 and a 409 other than a stale generation are typed by panne.ts (never an outage). -->
					<p>{panneVentes?.message ?? MESSAGE_PANNE_VENTES}</p>
					<button type="button" class="adresse-secondary" onclick={rechargerVentes} disabled={ventesEnCours} aria-busy={ventesEnCours}
						>{RECHARGER_VENTES}</button
					>
				</div>
			{/if}
		</section>
	{/if}
	{#if issueVentesAdresse}
		<section class="ventes-adresse" id="ventes-adresse" aria-labelledby="ventes-adresse-titre" aria-busy={ventesAdresseEnCours}>
			<h3 id="ventes-adresse-titre" class="liste-titre" tabindex="-1" bind:this={titreVentesAdresse}>{TITRE_VENTES_ADRESSE}</h3>
			{#if recouvrement}<p class="ventes-recouvrement">{PHRASE_RECOUVREMENT_VENTES}</p>{/if}
			<p class="ventes-annonce sr-only" aria-live="polite">{annonceVentesAdresse}</p>
			{#if listeVentesAdresse}
				<p class="ventes-commune">
					<span
						>DVF (DGFiP){#if listeVentesAdresse.periode}{' · '}{listeVentesAdresse.periode}{/if} · ventes déclarées à ce numéro, rattachées
						à aucun logement</span
					>
					<SourceButton libelle="ventes déclarées à cette adresse" onouvrir={(declencheur) => panneauVentesAdresse?.ouvrir(declencheur)} />
				</p>
				{#if listeVentesAdresse.total === 0}
					<p class="ventes-phrase">{phraseZeroVentesAdresse(listeVentesAdresse.periode)}</p>
				{:else}
					<VenteList ventes={listeVentesAdresse.ventes} />
				{/if}
				<Pagination
					plage={plageDesVentesAdresse}
					pages={pagesVentesAdresse}
					lienPrecedent={listeVentesAdresse.page > 1 ? lienVentesAdresse(listeVentesAdresse.page - 1) : null}
					lienSuivant={listeVentesAdresse.page < pagesVentesAdresse ? lienVentesAdresse(listeVentesAdresse.page + 1) : null}
					etiquette="Pages des ventes déclarées à cette adresse"
					prefixe="ventes-adresse"
					onchanger={changerDePageVentesAdresse}
				/>
				<SourcePanel
					bind:this={panneauVentesAdresse}
					titre={TITRE_VENTES_ADRESSE}
					entrees={voletVentesAdresse(listeVentesAdresse)}
					brut={[]}
					source={listeVentesAdresse.source}
				/>
			{:else if absenceVentesAdresse}
				<!-- An address with a suffix (DVF carries none), without a comparable number, or a source out of
				     coverage: said as such, never as zero (C37). -->
				<div
					class="ventes-adresse-absence"
					data-motif={absenceVentesAdresse.motif ?? undefined}
					data-provisoire={motifAdresse?.provisoire ? 'true' : undefined}
				>
					<StateValue
						etat={{
							kind: 'absence',
							etat: absenceVentesAdresse.etat === 'out_of_coverage' ? 'out_of_coverage' : 'not_available',
							motif: absenceVentesAdresse.motif ?? '',
							effectif: null,
							aide: motifAdresse?.phrase ?? undefined
						}}
						aide={motifAdresse?.provisoire !== true}
						libelle={motifAdresse?.libelle}
					/>
				</div>
			{:else}
				<div class="ventes-panne" role="alert">
					<p>{panneVentesAdresse?.message ?? MESSAGE_PANNE_VENTES}</p>
					<button
						type="button"
						class="adresse-secondary"
						onclick={rechargerVentesAdresse}
						disabled={ventesAdresseEnCours}
						aria-busy={ventesAdresseEnCours}>{RECHARGER_VENTES}</button
					>
				</div>
			{/if}
		</section>
	{/if}
{/snippet}

<!-- One criterion: the page binds the detail of the DPE and sales criteria, which hold the lists it reads
     beside the report. -->
{#snippet critere({ theme, niveauTitre }: ArgsCritere)}
	{#if theme.id === 'dpe'}
		<LigneCritere {theme} {niveauTitre} {textes} ouvert={dpeOuvert} onbascule={(ouvert) => (dpeOuvert = ouvert)} suite={listeDpe} />
	{:else if theme.id === 'ventes_parcelle'}
		<LigneCritere {theme} {niveauTitre} {textes} ouvert={ventesOuvert} onbascule={(ouvert) => (ventesOuvert = ouvert)} suite={listesVentes} />
	{:else}
		<LigneCritere {theme} {niveauTitre} {textes} />
	{/if}
{/snippet}

<!-- The similar sales: the sentence that keeps them from reading as an estimate, and the way to them. -->
{#snippet bilan()}
	{#if resume}<BilanRisques {resume} {textes} />{/if}
{/snippet}

{#snippet comparablesTexte()}
	<p class="fiche-phrase">
		Des ventes du même type de bien, de surface proche, autour de l’adresse&nbsp;: elles situent les prix du quartier,
		elles n’estiment pas ce logement. Une vente récente et isolée n’est jamais détaillée&nbsp;: seule son année est
		donnée, et elle vient en fin de tableau.
	</p>
	<p><a class="adresse-secondary lien-panneau" href={lienPanneau} onclick={ouvrirDepuisLien}>Voir les ventes ressemblantes</a></p>
{/snippet}

{#snippet comparablesPanneau()}
	<section class="comparables-appel" id="s-comparables" aria-labelledby="s-comparables-titre">
		<h4 id="s-comparables-titre" class="liste-titre">{TITRE_COMPARABLES}</h4>
		{@render comparablesTexte()}
	</section>
{/snippet}

{#snippet listesHorsRapport()}
	{#if (!porteDpe && data.documents) || (!porteVentes && (issueVentes || issueVentesAdresse))}
		<Panneau titre="Listes de l’adresse" id="listes">
			{#if !porteDpe}{@render listeDpe()}{/if}
			{#if !porteVentes}{@render listesVentes()}{/if}
		</Panneau>
	{/if}
{/snippet}

<div class="adresse gabarit">
	{#if identite}
		<!-- The order of the DOM is the order of reading at every width: identity, answer, categories, then the
		     attachment, the actions and the map, which the grid lifts beside the identity above 1080 px. -->
		<div class="fiche-tete" class:avec-carte={issue.kind === 'ok' && !chargement && rapport?.carte}>
			<div class="fiche-identite">
				<FilAriane
					etapes={[
						{ nom: 'Recherche', href: '/' },
						{ nom: nomCommune, href: `/commune/${encodeURIComponent(identite.commune.code_insee)}` },
						{ nom: voie || 'Adresse' }
					]}
				/>

				<header class="adresse-header">
					<h1 class="adresse-title">{titre}</h1>
					<p class="adresse-identity">
						{#if identite.code_postal}{identite.code_postal}{' '}{/if}<a href={`/commune/${encodeURIComponent(identite.commune.code_insee)}`}
							>{nomCommune}</a
						>
						· {departement}{#if jourDeLecture}{' · '}lu le {jourDeLecture}{/if}
						{#if issue.kind === 'ok'}
							<SourceButton libelle="adresse" onouvrir={(declencheur) => panneau?.ouvrir(declencheur)} />
						{/if}
					</p>
					{#if phraseIndeterminee}<p class="adresse-indeterminee">{phraseIndeterminee}</p>{/if}
					{#if mention && !chargement}
						<!-- R-07: one polite live region, in the served markup, so that its change is announced once;
						     the rows carry no announcement of their own. The link is the way without JavaScript. -->
						<p class="lectures-en-cours" role="status">
							{mention}
							{#if restantes.length > 0}<a href={`/adresse/${encodeURIComponent(identite.ban_id ?? '')}`} data-sveltekit-reload>{LIEN_RECHARGER_FICHE}</a>{/if}
						</p>
					{/if}
					{#if issue.kind === 'ok' && rapport && !chargement}<EnTetePrint {consulteLe} />{/if}
				</header>
			</div>
			{#if issue.kind === 'ok' && rapport && !chargement && themes.length}
				<EnBref {resume} chiffres={rapport.chiffresCles} {themes} {textes} lienCommune={`/commune/${encodeURIComponent(identite.commune.code_insee)}`}>
					{#snippet suite()}
						<a class="lien-suite" href={lienPanneau} onclick={ouvrirDepuisLien}>Voir les ventes ressemblantes</a>
					{/snippet}
				</EnBref>
				<div class="fiche-sommaire"><Sommaire entrees={entreesSommaire} /></div>
				<section class="lecture" aria-labelledby="lecture-titre">
					<h2 id="lecture-titre" class="sr-only">Pour aller plus loin</h2>
					<div class="lecture-liste">
						{#each placement.parties as partie (partie.id)}
							<PartieQuestion
								{partie}
								resume={rapport.resume}
								commune={nomCommune}
								{critere}
								{deuxColonnes}
								ouverte={ouvertes[partie.id]}
								onbascule={(ouverte) => (ouvertes[partie.id] = ouverte)}
								entete={partie.id === 'q-risques' && resume ? bilan : undefined}
								pied={partie.id === 'q-prix' ? comparablesPanneau : undefined}
							/>
						{/each}
					</div>
					{#if resume && resume.pannes.length + resume.sansValeur.length > 0 && resume.sourcesEnPanne.length}
						<!-- That sources failed stays in sight, it qualifies what is read; their names are behind the « i ». -->
						<p class="lecture-sources">
							Certaines sources n’ont pas répondu&#8239;; ce n’est jamais un «&#8239;aucun risque&#8239;».
							<SourceButton libelle="sources qui n’ont pas répondu" onouvrir={(declencheur) => panneauSansReponse?.ouvrir(declencheur)} />
						</p>
						<SourcePanel
							bind:this={panneauSansReponse}
							titre="Sources qui n’ont pas répondu"
							entrees={[{ terme: 'Sources', valeur: textes.get('bref:sources') ?? resume.sourcesEnPanne.join(', ') }]}
							brut={[]}
						/>
					{/if}
					<LienLimites />
				</section>
			{/if}
			{#if issue.kind === 'ok' && rapport && !chargement}
				<div class="fiche-suite">
					{#if rattachement}<p class="rattachement-phrase">{textes.get('rattachement') ?? typographieFr(rattachement)}</p>{/if}
					<!-- Actions (4.9 lot 5): the one plain button of the page in sight, the others one click away. -->
					<div class="adresse-actions">
						<a class="adresse-primary" href={lienComparer} onclick={comparer} data-sveltekit-preload-data="tap">Comparer avec une autre adresse</a>
						<details class="actions-plus">
							<summary class="adresse-secondary">Autres actions</summary>
							<div class="actions-liste">
								<a class="adresse-secondary" href={lienPanneau} onclick={ouvrirDepuisLien}>{TITRE_COMPARABLES}</a>
								<button type="button" class="adresse-secondary" onclick={() => (exportOuvert = true)}>Télécharger (CSV)</button>
								<button type="button" class="adresse-secondary" onclick={() => window.print()}>Imprimer la fiche</button>
							</div>
						</details>
					</div>
				</div>
				{#if rapport.carte}
					<div class="adresse-carte"><CarteAdresse carte={rapport.carte} adresse={titre} banId={identite.ban_id} /></div>
				{/if}
			{/if}
			{#if issue.kind === 'ok' && rapport && !chargement && themes.length}
				<div class="fiche-corps">
					{#if placement.autres.length}
						<!-- A served theme this front does not place: rendered whole, counted in no category. -->
						<Panneau titre="Autres critères" id="q-autres">
							<ul class="lignes">
								{#each placement.autres as theme (theme.id)}{@render critere({ theme, niveauTitre: 3 })}{/each}
							</ul>
						</Panneau>
					{/if}

					{@render listesHorsRapport()}

					{#if !partiePrix}
						<Panneau titre={TITRE_COMPARABLES} id="s-comparables">{@render comparablesTexte()}</Panneau>
					{/if}

					<LireUneFiche repliable />

					<Panneau titre="Sources de la fiche" id="sources">
						<p class="fiche-phrase">Chaque critère cite ses sources derrière son bouton d’information. Voici toutes les sources lues pour cette fiche.</p>
						<details class="sources-repli" bind:open={sourcesOuvertes}>
							<summary>Voir les sources</summary>
							<ul class="sources-liste">
								{#if issue.source?.nom}<li><strong>{textes.get('source:adresse') ?? issue.source.nom}</strong>, adresse</li>{/if}
								{#each sources as s (s.cle)}
									{@const nom = textes.get(`source:${s.cle}:nom`) ?? s.nom}
									<li>
										<strong>{#if s.url}<a href={s.url} rel="noopener">{nom}</a>{:else}{nom}{/if}</strong>{s.producteur
											? `, ${textes.get(`source:${s.cle}:producteur`) ?? s.producteur}`
											: ''}
										· {s.direct ? `interrogée en direct${jourDeLecture ? ` le ${jourDeLecture}` : ''}` : s.millesime ? dateFr(s.millesime) : 'millésime non publié'}{s.licence ? ` · ${s.licence}` : ''}
										· {s.criteres.join(', ')}
									</li>
								{/each}
							</ul>
						</details>
					</Panneau>

					<p class="adresse-pied">Cette fiche n’est pas indexée par les moteurs de recherche. Chaque valeur dit sa source, son millésime et son échelle.</p>
				</div>
			{/if}
		</div>

		{#if issue.kind === 'hors_couverture'}
			<div class="adresse-section">
				<Notice titre="Adresse hors couverture" variante="warn">
					<p>{motifHorsCouverture} {PAS_ZERO}</p>
					<p class="adresse-links">
						<a href="/datasets">Voir la couverture des données</a>
						<a href="/couverture">Limites connues des données</a>
						<a href={`/commune/${encodeURIComponent(identite.commune.code_insee)}`}>Voir la fiche de {nomCommune}</a>
					</p>
				</Notice>
			</div>
		{/if}

		{#if issue.kind === 'ok' && chargement}
			<section class="adresse-section" aria-busy="true" aria-labelledby="chargement-titre">
				<h2 id="chargement-titre" class="chargement-titre">Chargement des données de l'adresse…</h2>
			</section>
		{:else if issue.kind === 'ok' && rapport}
			{#if !themes.length}
				{@render listesHorsRapport()}
			{/if}

			<ExportDialog
				open={exportOuvert}
				themes={themesExport}
				filenameBase={baseFichierExport(identite.ban_id)}
				onclose={() => (exportOuvert = false)}
			/>

			<SourcePanel bind:this={panneau} titre="Adresse" entrees={volet} brut={[]} source={issue.source} />
		{/if}

		<!-- Outside the branches of the page: a loading state never re-creates it closed. -->
		{#if issue.kind === 'ok' && issueVentes}
			<!-- The similar sales (C38), in their own panel: the form prefilled from the most recent DPE (C42). -->
			<dialog
				class="panneau-comparables"
				bind:this={dialogue}
				aria-labelledby="comparables-titre"
				onclose={apresFermeture}
				onclick={fermerSurFond}
			>
				<div class="panneau-tete">
					<h2 id="comparables-titre" class="panneau-titre" tabindex="-1" bind:this={titreComparables}>{TITRE_COMPARABLES}</h2>
					<form method="dialog" onsubmit={(event) => (event.preventDefault(), fermerPanneau())}>
						<button type="submit" class="adresse-secondary panneau-fermer"><IconFermer class="panneau-fermer-icone" />Fermer</button>
					</form>
				</div>
				<section
					class="comparables"
					id="ventes-ressemblantes"
					aria-labelledby="comparables-titre"
					aria-busy={comparablesEnCours || chargementComparables}
				>
					<p class="comparables-annonce sr-only" aria-live="polite">{annonceComparables}</p>
					<ComparablesForm
						criteres={criteresFormulaire}
						caches={champsCaches}
						origine={preremplissage?.phrase}
						provenance={preremplissage?.numero
							? { numero: preremplissage.numero, qualification: preremplissage.qualification, type: preremplissage.type !== null }
							: null}
					/>
					<div aria-live="polite">
						{#if chargementComparables}
							<p class="comparables-phrase" role="status" aria-busy="true">{CHARGEMENT_COMPARABLES}</p>
						{:else if issueComparables?.kind === 'sans_type'}
							<!-- C42: a prefilled type waits for the visitor's click; no invitation to choose one. -->
							{#if !preremplissage?.type}<p class="comparables-phrase">{PHRASE_SANS_TYPE}</p>{/if}
						{:else if issueComparables?.kind === 'criteres_non_reconnus'}
							<!-- A criterion outside the closed lists: no read, never widened (row 34). -->
							<p class="comparables-phrase" data-criteres="non-reconnus">{PHRASE_CRITERE_NON_RECONNU}</p>
						{:else if listeComparables}
							<p class="comparables-effectif">
								<span>{phraseComparables}</span>
								{#each reservesComparables(listeComparables) as reserve (reserve)}
									<span class="comparables-reserve" data-reserve="liste-incomplete">{reserve}</span>
								{/each}
								<SourceButton libelle="ventes ressemblantes" onouvrir={(declencheur) => panneauComparables?.ouvrir(declencheur)} />
							</p>
							{#if listeComparables.effectif === 0}
								<!-- C16: a zero over a radius that leaves the hosted departements is not written. -->
								{#if phraseZeroComparables(listeComparables)}
									<p class="comparables-phrase">{phraseZeroComparables(listeComparables)}</p>
								{/if}
							{:else}
								<VenteList ventes={listeComparables.ventes} />
							{/if}
							<Pagination
								plage={plageDesComparables}
								pages={pagesComparables}
								lienPrecedent={listeComparables.page > 1 ? lienComparables(listeComparables.page - 1) : null}
								lienSuivant={listeComparables.page < pagesComparables ? lienComparables(listeComparables.page + 1) : null}
								etiquette="Pages des ventes ressemblantes"
								prefixe="comparables"
								onchanger={changerDePageComparables}
							/>
							<SourcePanel
								bind:this={panneauComparables}
								titre={TITRE_COMPARABLES}
								entrees={voletComparables(listeComparables)}
								brut={[]}
								source={listeComparables.source}
							/>
						{:else if absenceComparables}
							<!-- No single point (no distance) or DVF out of coverage: said by its badge, never a zero. -->
							<StateValue
								etat={{
									kind: 'absence',
									etat: absenceComparables.etat === 'out_of_coverage' ? 'out_of_coverage' : 'not_available',
									motif: absenceComparables.motif ?? '',
									effectif: null,
									aide:
										absenceComparables.etat === 'out_of_coverage'
											? // C16: the glossary sentence speaks of « ce département », the address's.
												absenceComparables.liste_incomplete
												? ''
												: undefined
											: absenceComparables.motif === 'position_indeterminee'
												? PHRASE_COMPARABLES_SANS_POSITION
												: ''
								}}
							/>
							{#each reservesComparables(absenceComparables) as reserve (reserve)}
								<p class="comparables-reserve" data-reserve="liste-incomplete">{reserve}</p>
							{/each}
						{:else if issueComparables}
							<div class="ventes-panne" role="alert">
								{#if issueComparables.kind === 'panne' && issueComparables.panne.message}
									<p>{issueComparables.panne.message}</p>
								{/if}
								<p>{MESSAGE_PANNE_COMPARABLES}</p>
								<button type="button" class="adresse-secondary" onclick={rechargerVentes} disabled={ventesEnCours} aria-busy={ventesEnCours}
									>{RELANCER_COMPARABLES}</button
								>
							</div>
						{/if}
					</div>
				</section>
			</dialog>
		{/if}
	{:else if issue.kind === 'panne'}
		<!-- A refused request, typed by panne.ts (409, 429, 401, 403): never « momentanément
		     indisponible », no value. -->
		<FilAriane etapes={[{ nom: 'Recherche', href: '/' }, { nom: 'Adresse' }]} />
		<header class="adresse-header">
			<h1 class="adresse-title">{titre}</h1>
		</header>
		<div class="adresse-section adresse-refus" role="alert">
			<p>{issue.panne.message}</p>
			<button type="button" class="adresse-secondary" onclick={recharger} disabled={rechargement} aria-busy={rechargement}
				>Recharger la fiche</button
			>
		</div>
	{/if}
</div>

<style>
	/* Address fiche (decisions of 06/11): one column in the order of the DOM; above 1080 px one grid holds the
	   head, with the actions and the map beside the identity, the answer on the whole width, then the sticky
	   summary in its margin beside everything that follows. */
	.adresse {
		padding-block: var(--s4) var(--s8);
		color: var(--color-ink);
	}

	.fiche-tete {
		display: flex;
		flex-direction: column;
		gap: var(--s4);
	}

	/* Fallback measure: component-specific limits take precedence. */
	:where(.adresse) :global(p) {
		max-width: var(--mesure);
	}

	.fiche-identite,
	.fiche-suite,
	.lecture {
		min-width: 0;
	}

	/* The pale band of the head, as wide as the window: a shadow, which takes no room and scrolls nothing, cut
	   to the height of the block. The fiche keeps its light ground: the solid head is the home page's. */
	@media screen {
		.fiche-identite,
		.fiche-suite {
			background: var(--color-bande);
			box-shadow: 0 0 0 100vmax var(--color-bande);
			clip-path: inset(calc(-1 * var(--s4)) -100vmax);
		}

		.adresse-carte {
			position: relative;
		}

		.fiche-tete > :global(.bref) {
			margin-top: var(--s3);
		}
	}

	/* No box of its own: under the threshold the compact control is sticky along the whole fiche, above it the
	   summary is a grid item that keeps its height while the column beside it grows. */
	.fiche-sommaire {
		display: contents;
	}

	@media screen and (min-width: 1081px) {
		.fiche-tete:has(> .lecture) {
			display: grid;
			grid-template-columns: 220px minmax(0, 1fr) 320px;
			/* The second row takes what the map adds: « Autres actions » that opens stretches nothing beside it. */
			grid-template-rows: auto 1fr auto auto auto;
			grid-template-areas:
				'identite identite identite'
				'suite suite suite'
				'bref bref bref'
				'sommaire lecture lecture'
				'sommaire corps corps';
			column-gap: var(--s6);
		}

		.fiche-tete.avec-carte:has(> .lecture) {
			grid-template-areas:
				'identite identite carte'
				'suite suite carte'
				'bref bref bref'
				'sommaire lecture lecture'
				'sommaire corps corps';
		}

		.fiche-identite {
			grid-area: identite;
		}

		.fiche-suite {
			grid-area: suite;
		}

		.fiche-tete > :global(.bref) {
			grid-area: bref;
		}

		.fiche-sommaire > :global(.sommaire-large) {
			grid-area: sommaire;
			align-self: start;
			min-width: 0;
		}

		.lecture {
			grid-area: lecture;
		}

		.fiche-corps {
			grid-area: corps;
		}

		.fiche-tete.avec-carte .adresse-carte {
			grid-area: carte;
			align-self: start;
			margin-top: 0;
		}

		.fiche-tete.avec-carte .adresse-carte :global(.carte-image) {
			aspect-ratio: 2 / 1;
		}

		/* An unavailable map is one line of text: it does not keep a column of its own. */
		.fiche-tete.avec-carte:has(> .lecture):has(:global(.carte-erreur)) {
			grid-template-rows: none;
			grid-template-areas:
				'identite identite identite'
				'suite suite suite'
				'bref bref bref'
				'sommaire lecture lecture'
				'sommaire carte carte'
				'sommaire corps corps';
		}
	}

	.adresse-carte {
		min-width: 0;
		max-width: 560px;
		margin-top: var(--s4);
	}

	/* A long address wraps, never truncated nor shrunk. */
	.adresse-title {
		margin: var(--s1) 0 var(--s2);
		font-family: var(--font-display);
		font-size: 52px;
		font-weight: 500;
		line-height: 1.04;
		letter-spacing: -0.02em;
		text-wrap: balance;
		overflow-wrap: anywhere;
	}

	/* « {code postal} {commune} · {département} · lu le … », inline, its Source button at its end. A line of
	   metadata, not running text: it takes the width of the page, not the reading measure. */
	.adresse-identity {
		max-width: none;
		margin: 0;
		color: var(--color-muted);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.adresse-identity a {
		color: var(--color-accent);
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.adresse-indeterminee,
	.rattachement-phrase {
		max-width: var(--mesure);
		margin: 0;
		font-size: 16px;
		line-height: 1.5;
	}

	.adresse-indeterminee {
		margin-top: var(--s3);
	}

	.rattachement-phrase {
		color: var(--color-muted);
		font-size: 14px;
	}

	/* Under the price: the next natural step, as a link of the size of the notes. */
	.lien-suite {
		width: fit-content;
		margin-top: var(--s-demi);
		color: var(--color-accent);
		font-size: 14px;
		line-height: 1.4;
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	/* The categories: one panel of closed rows under the threshold, a row that opens pushes the next one down;
	   above it the cards (PartieQuestion). Nothing stretches. */
	.lecture-liste {
		overflow: clip;
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-panneau);
	}

	@media screen and (min-width: 1081px) {
		.lecture-liste {
			display: grid;
			gap: var(--s4);
			overflow: visible;
			border: 0;
			background: none;
		}
	}

	.lecture > :global(.lien-limites) {
		margin-top: var(--s2);
	}

	.lecture-sources {
		margin: var(--s3) 0 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.comparables-appel {
		margin-top: var(--s4);
		padding-top: var(--s3);
		border-top: 1px solid var(--color-filet-pale);
	}

	.sources-repli > summary {
		display: list-item;
		box-sizing: border-box;
		min-height: 44px;
		padding-block: var(--s2);
		color: var(--color-accent);
		font-weight: 600;
		cursor: pointer;
	}

	.adresse-section {
		margin-top: var(--s6);
	}

	.chargement-titre {
		margin: 0;
		font-size: 20px;
		font-weight: 600;
	}

	/* Actions (§ 4.6): one row that wraps, 44 px high; opened, the others take a row of their own. */
	.adresse-actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
	}

	.rattachement-phrase + .adresse-actions {
		margin-top: var(--s2);
	}

	.actions-plus[open] {
		flex-basis: 100%;
	}

	.actions-plus > summary {
		gap: var(--s2);
		width: fit-content;
		list-style: none;
		cursor: pointer;
	}

	.actions-plus > summary::-webkit-details-marker {
		display: none;
	}

	/* The same chevron as the detail of a row. */
	.actions-plus > summary::after {
		content: '';
		flex: none;
		width: 7px;
		height: 7px;
		border-right: 1.5px solid currentColor;
		border-bottom: 1.5px solid currentColor;
		transform: rotate(45deg);
		transition: transform 0.15s ease-out;
	}

	.actions-plus[open] > summary::after {
		transform: rotate(-135deg);
	}

	.actions-liste {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
		margin-top: var(--s2);
	}

	.fiche-corps {
		min-width: 0;
	}

	.fiche-phrase {
		max-width: var(--mesure);
		line-height: 1.6;
	}

	/* Rows of « Autres critères », as in a category. */
	.lignes {
		container-type: inline-size;
		margin: var(--s2) 0 0;
		padding: 0;
		list-style: none;
	}

	.liste-titre {
		margin: 0 0 var(--s2);
		font-family: var(--font-display);
		font-size: 20px;
		font-weight: 500;
		line-height: 1.25;
	}

	.liste-titre:focus,
	.panneau-titre:focus {
		outline: none;
	}

	.ventes,
	.ventes-adresse {
		margin-top: var(--s5);
	}

	.ventes-recouvrement {
		max-width: var(--mesure);
		margin: 0 0 var(--s4);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.ventes-commune {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0 var(--s2);
		margin: 0 0 var(--s4);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.ventes-phrase {
		max-width: var(--mesure);
		margin: 0;
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	/* A status panel (§ 3.13): ink on the panel, ochre being the Vigilance's only. */
	.ventes-panne,
	.adresse-refus {
		box-sizing: border-box;
		max-width: 100%;
		padding: var(--s5);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-panneau);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.ventes-panne p,
	.adresse-refus p {
		margin: 0 0 var(--s2);
	}

	/* The similar sales: a drawer of 560 px on the right, the whole screen on a phone. */
	.panneau-comparables {
		box-sizing: border-box;
		width: min(560px, 100vw);
		max-width: 100vw;
		height: 100dvh;
		max-height: 100dvh;
		margin: 0 0 0 auto;
		padding: 0;
		border: 0;
		border-left: 1px solid var(--color-line-strong);
		background: var(--color-paper);
		color: var(--color-ink);
		overflow-y: auto;
	}

	.panneau-comparables::backdrop {
		background: color-mix(in srgb, var(--color-ink) 40%, transparent);
	}

	.panneau-tete {
		position: sticky;
		top: 0;
		z-index: 1;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--s4);
		padding: var(--s4) var(--s5);
		background: var(--color-paper);
		border-bottom: 1px solid var(--color-line);
	}

	/* A dialog title: Inter 600 (rule of app.css kept, § 4.6). */
	.panneau-titre {
		margin: 0;
		font-family: var(--font-sans);
		font-size: 22px;
		font-weight: 600;
		line-height: 1.2;
	}

	.panneau-tete form {
		margin: 0;
	}

	.panneau-fermer {
		gap: var(--s-demi);
	}

	.panneau-fermer :global(.panneau-fermer-icone) {
		width: 16px;
		height: 16px;
	}

	.comparables {
		padding: var(--s4) var(--s5) var(--s6);
	}

	.comparables-effectif {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0 var(--s2);
		margin: var(--s4) 0;
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.comparables-phrase {
		max-width: var(--mesure);
		margin: var(--s4) 0;
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.comparables-reserve {
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.5;
	}

	.sources-liste {
		display: grid;
		gap: var(--s-demi);
		padding: 0;
		list-style: none;
		font-size: 14px;
		overflow-wrap: anywhere;
	}

	.adresse-pied {
		max-width: var(--mesure);
		margin: var(--s5) 0 0;
		color: var(--color-muted);
		font-size: 14px;
	}

	.adresse-links {
		display: flex;
		flex-wrap: wrap;
		gap: 0 var(--s5);
	}

	.adresse-links a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		text-decoration-line: underline;
		text-underline-offset: 3px;
	}

	/* Secondary buttons (§ 4.6): outlined in accent, 16/400, as the shared `.bouton-secondaire` (2026-11-10). A label wraps rather than leave the window
	   (« Ventes ressemblantes » is 148 px wide, 144 px are left at a 200 % zoom of 320 px). */
	.adresse-secondary,
	.adresse-primary {
		max-width: 100%;
		text-align: center;
		overflow-wrap: anywhere;
	}

	.adresse-secondary {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		box-sizing: border-box;
		min-height: 44px;
		padding: var(--s1) var(--s4);
		border: 1px solid var(--color-accent);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-accent);
		font-family: var(--font-sans);
		font-style: inherit;
		font-size: 16px;
		font-weight: 400;
		line-height: 1.2;
		text-decoration: none;
		cursor: pointer;
	}

	.adresse-secondary:hover {
		border-color: var(--color-accent-hover);
		background: var(--color-accent-doux);
		color: var(--color-accent-hover);
	}

	/* « Comparer avec une autre adresse », the one plain button of the page. */
	.adresse-primary {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		box-sizing: border-box;
		min-height: 44px;
		padding: 0 var(--s4);
		border: 1px solid var(--color-accent);
		border-radius: var(--radius-control);
		background: var(--color-accent);
		color: var(--color-paper);
		font-size: 16px;
		font-weight: 600;
		line-height: 1.2;
		text-decoration: none;
	}

	.adresse-primary:hover {
		background: var(--color-accent-hover);
	}

	/* An anchor lands 96 px down, clear of the compact summary. The offset lives on the targets, not on `html`:
	   as a padding of the window it hides what sticks to the top, and a focus there scrolls the page to find it. */
	:global(html):has(.adresse) {
		scroll-padding-top: 0;
	}

	.adresse :global(*) {
		scroll-margin-top: calc(5rem + var(--s4));
	}

	@media (max-width: 720px) {
		.adresse {
			padding-block: var(--s3) var(--s7);
		}

		.fiche-tete {
			gap: var(--s3);
		}

		/* A line of text on the short first screen: its target reaches 44 px around it. */
		.lien-suite {
			position: relative;
		}

		.lien-suite::after {
			content: '';
			position: absolute;
			inset: -12px 0;
		}

		.adresse-title {
			font-size: 34px;
		}

		.adresse-carte {
			max-width: none;
		}

		.ventes-panne {
			padding: var(--s4);
		}

		.panneau-tete {
			padding: var(--s3) var(--s4);
		}

		.comparables {
			padding: var(--s3) var(--s4) var(--s5);
		}
	}

	@media (max-width: 360px) {
		.adresse-title {
			font-size: 28px;
		}
	}

	@media print {
		/* The actions, the panels and the drawer are not printed; every criterion is opened (§ 2.5). */
		.adresse-actions,
		.panneau-comparables,
		.lien-panneau,
		.lien-suite,
		.adresse :global(dialog.source-panel) {
			display: none;
		}

		.adresse {
			padding: 0;
		}

		.fiche-tete {
			display: block;
		}

		.adresse :global(details::details-content) {
			content-visibility: visible;
			display: contents;
		}
	}

	/* R-07: the reads still in progress, said once in the head; the line keeps its place when they end. */
	.lectures-en-cours {
		max-width: var(--mesure);
		margin: var(--s2) 0 0;
		color: var(--color-muted);
		font-size: 16px;
		line-height: 1.5;
	}

	.lectures-en-cours a {
		margin-inline-start: var(--s2);
		color: var(--color-accent);
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}
</style>
