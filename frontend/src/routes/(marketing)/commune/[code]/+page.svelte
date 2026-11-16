<script lang="ts">
	// Public commune fiche — GET /v1/commune (UI-SPEC v4 § 6.2, 04.8-04; contents of 04.7-15 kept, D-18).
	// The DOM order is the reading order: breadcrumb, title and identity, the actions, the panel
	// « L'essentiel » (scale mark Commune, the known markers as tiles in the validated order ① houses,
	// ② flats, ③ trend, ④ dominant DPE class, the DPE distribution), the caveats as anchor links, then the
	// categories of the address fiche (4.9) with its summary « Sur cette fiche » beside them, the computed
	// questions as details. Each category is a card above the two-column threshold and folded under it; an
	// anchor (a category, a group `#critere-…`, an old theme `#immobilier`) opens what it points to. Printing
	// opens the details, hides the chrome and the source panels, and frames the sheet with EnTetePrint and
	// SourcesPrint; the CSV export keeps the five themes with their served state and caveats.
	// Data is read by the universal `load` (+page.ts): the first response is server-rendered, a
	// client navigation re-runs the load. Nothing here computes a value: it renders the model.
	import { flushSync, onMount, tick, untrack } from 'svelte';
	import { goto, invalidateAll } from '$app/navigation';
	import { navigating } from '$app/stores'; // Project convention: stores, never the state module.
	import LienLimites from '$lib/components/fiche/LienLimites.svelte';
	import Categorie from '$lib/components/fiche/Categorie.svelte';
	import Sommaire, { type EntreeSommaire } from '$lib/components/adresse/rapport/Sommaire.svelte';
	import { aideAuNiveau1, definitionsCommune, mapCommuneToGrid, themesCommune } from '$lib/data/commune-grid';
	import { categoriesPourExport } from '$lib/data/commune-export';
	import { CATEGORIES_COMMUNE, categoriesCommune, categorieDeAncre } from '$lib/data/commune-categories';
	import { communeKpis, reperesConnus, reservesSynthese } from '$lib/data/commune-kpi';
	import { faqCommune, faqJsonLd } from '$lib/data/commune-faq';
	import { repartitionDpe } from '$lib/data/commune-repartition-dpe';
	import RepartitionDpe from '$lib/components/fiche/RepartitionDpe.svelte';
	import type { IdPartie } from '$lib/data/questions';
	import {
		ANCRE_ARRONDISSEMENTS,
		arrondissementsDe,
		chapoArrondissementAvecHeritage,
		libelleArrondissement,
		libelleCourtArrondissement
	} from '$lib/data/arrondissements';
	import { DEPARTEMENTS } from '$lib/data/departements';
	import { titreCommune } from '$lib/seo/titres';
	import { deNom, descriptionCommune } from '$lib/seo/descriptions';
	import { etatDeColonne } from '$lib/data/etats-colonnes';
	import { LIBELLES_MOTIFS } from '$lib/data/etats-libelles';
	import { millesimesFromCommune, sourcesCommuneParDataset } from '$lib/data/explications';
	import { formatBareNumber } from '$lib/data/formatters';
	import { blocsPublies } from '$lib/api/commune';
	import KpiStrip from '$lib/components/fiche/KpiStrip.svelte';
	import Notice from '$lib/components/fiche/Notice.svelte';
	import ChoixArrondissements from '$lib/components/compare/ChoixArrondissements.svelte';
	import ExportDialog from '$lib/components/fiche/ExportDialog.svelte';
	import EnTetePrint from '$lib/components/fiche/EnTetePrint.svelte';
	import SourcesPrint from '$lib/components/fiche/SourcesPrint.svelte';
	import { differerLesVolets } from '$lib/components/fiche/SourcePanel.svelte';
	import DefinitionsCommune from '$lib/components/fiche/DefinitionsCommune.svelte';
	import { recentFiches } from '$lib/stores/recent-fiches.svelte';
	import { comparisonSelection, confirmationAjout, type ConfirmationAjout } from '$lib/stores/comparison-selection.svelte';
	import { SITE_URL, OG_IMAGE_URL } from '$lib/config';
	import { carteCommune, OG_HAUTEUR, OG_LARGEUR, urlImageOg } from '$lib/seo/og-carte';
	import { nomDepartementDuFil } from '$lib/data/departement-presentation';
	import { liensVoisines } from '$lib/data/communes-voisines';
	import { jsonLdScript } from '$lib/seo/json-ld';
	import { developperParCle } from '$lib/utils/sigles';
	import type { PageData } from './$types';
	import FilAriane from '$lib/components/ds/FilAriane.svelte';
	import ListeLiens from '$lib/components/ds/ListeLiens.svelte';
	import ListeValeurs, { type ValeurDeListe } from '$lib/components/ds/ListeValeurs.svelte';
	import Panneau from '$lib/components/ds/Panneau.svelte';
	import RepereEchelle from '$lib/components/ds/RepereEchelle.svelte';
	import type { LigneTheme } from '$lib/data/commune-grid';

	let { data }: { data: PageData } = $props();

	// What the server sends is what a search engine compares between two fiches (Search Console of
	// 2026-11-16: 486 fiches seen as duplicates of another one). The texts that are the same on every
	// commune stay one click away and out of that HTML: the panels render when opened, the blocks of paper
	// when the page is printed, the definitions without JavaScript on their own page.
	differerLesVolets();
	let impression = $state(false);
	function preparerImpression() {
		if (impression) return;
		impression = true;
		flushSync();
	}

	// The screen reads unpublished blocks as no block (flat columns); `data` keeps them as served.
	const synthese = $derived(data.etat === 'fiche' ? blocsPublies(data.synthese) : null);
	const sources = $derived(data.etat === 'fiche' ? data.sources : null);
	const nomServi = $derived.by(() => {
		if (data.etat === 'fiche') return data.synthese.nom_commune ?? `Commune INSEE ${data.code}`;
		return data.identite?.nom ?? `Commune INSEE ${data.code}`;
	});
	// An arrondissement (04.7-09): the commune it belongs to, and a title read from its code
	// (« 12e arrondissement de Paris »), the served name (« Paris 12e Arrondissement ») being the
	// fallback. The page never adds a figure of the commune to an arrondissement: every value shown is
	// the one the API served for this code, the values of its commune included (`heritages`, R-09), each
	// marked with the scale « Commune ».
	const parent = $derived(data.parent ?? null);
	const nom = $derived(parent ? (libelleArrondissement(data.code) ?? nomServi) : nomServi);
	const rangDansLaCommune = $derived(parent ? (libelleCourtArrondissement(data.code) ?? nom) : null);

	let exportOpen = $state(false);
	// Result of « Copier le code INSEE », announced by a status region (03-17 F9); '' when none.
	let statutCopie = $state('');
	let statutCopieTimeout: ReturnType<typeof setTimeout> | undefined;

	// Reset per-commune UI state when navigating between communes.
	$effect(() => {
		void data.code;
		exportOpen = false;
		statutCopie = '';
	});

	// Record a loaded fiche for the « Reprendre » list. Client-side only ($effect never runs in SSR).
	$effect(() => {
		const s = synthese;
		if (s?.code_insee && s?.nom_commune) {
			recentFiches.record({ kind: 'commune', id: s.code_insee, label: s.nom_commune });
		}
	});

	const grids = $derived(synthese ? mapCommuneToGrid(synthese, sources) : null);
	const kpis = $derived(synthese ? communeKpis(synthese, sources) : []);
	// What « L'essentiel » shows: a value absent here stays in its theme (4.9, lot 3).
	const reperes = $derived(reperesConnus(kpis));
	const reserves = $derived(synthese && grids ? reservesSynthese(synthese, sources, grids) : []);
	// The five themes with the rows the API serves; a theme without a served row is absent (UI-01).
	// They are the themes of the reading projection (`lectureCommune` reads the same themesCommune),
	// in the COMMUNE_THEMES order: one list for both widths, nothing recomputed (D45-01, D45-07).
	const themes = $derived(synthese && grids ? themesCommune(synthese, sources, grids) : []);
	// R-09: rows of an arrondissement served with the value of its commune carry the mark « Commune »; the head
	// of the page then says so. A commune of Paris, Lyon or Marseille lists its arrondissements, where the rows
	// published by arrondissement lead.
	const avecHeritage = $derived(themes.some((theme) => theme.lignes.some((ligne) => ligne.echelle)));
	const arrondissements = $derived(parent ? [] : arrondissementsDe(data.code));
	/** The commune whose arrondissements can be compared from this fiche: its own, or the one it belongs to. */
	const villeDesArrondissements = $derived(parent ?? (arrondissements.length ? { code: data.code, nom } : null));
	// A theme without a served row is not rendered: the page never mounts an empty panel (UI-01).
	const themesAffiches = $derived(themes.filter((theme) => theme.lignes.length > 0));
	// C1 (arbitrated by Ianis on 2026-11-10): the CSV follows the three categories of the page, no longer the five former themes.
	const exportThemes = $derived(categoriesPourExport(themes));
	// The same rows, in the categories of the address fiche: each row once, nothing recomputed (4.9).
	const categories = $derived(categoriesCommune(themesAffiches));
	// Paper and a page without JavaScript have no panel: the explanations are written out for them (A9-9).
	const definitions = $derived(definitionsCommune(reperes, themesAffiches));
	const millesimes = $derived(synthese ? millesimesFromCommune(synthese) : {});
	// 04.7-15: the served DPE distribution and the computed FAQ, both on the « fiche » state only.
	const repartition = $derived(synthese ? repartitionDpe(synthese) : null);
	const sourcesParDataset = $derived(sourcesCommuneParDataset(sources));

	// Identity line: CP · Département (code) · INSEE · {population} habitants ({année}, INSEE). A
	// field the API does not serve is not displayed (the postal code comes from the referential).
	const departementCode = $derived(
		data.etat === 'fiche' ? data.synthese.code_departement : (data.identite?.departement ?? null)
	);
	const departement = $derived.by(() => {
		if (!departementCode) return null;
		const nomDepartement = DEPARTEMENTS.find((d) => d.code === departementCode)?.nom;
		return nomDepartement ? `${nomDepartement} (${departementCode})` : `Département ${departementCode}`;
	});
	// The department of the zone (04.7-10): linked in the breadcrumb and named in the JSON-LD only when the
	// static list knows its code, so that the page never links a department page that does not exist.
	const departementLie = $derived.by(() => {
		const connu = departementCode ? DEPARTEMENTS.find((d) => d.code === departementCode) : undefined;
		return connu ? { code: connu.code, nom: connu.nom, href: `/departement/${connu.code}` } : null;
	});

	// The communes around (SEO audit 2026-11-14): links rendered by the server, bordering ones first,
	// then the departement, whose page lists all its communes. Nothing when the read failed.
	const voisines = $derived(liensVoisines(data.voisines ?? [], departementCode));
	// The figure alone is set in tabular figures (`.chiffre`, 03-17 V12), not the whole line.
	const populationLue = $derived.by(() => {
		if (!synthese) return null;
		const lu = etatDeColonne(synthese, 'population');
		if (lu.kind !== 'valeur') return null;
		const n = Number(lu.valeur);
		const annee = synthese.annee_demographie != null ? ` (${synthese.annee_demographie}, INSEE)` : '';
		// A served zero (55039) is worded as the meta description words it, never « 0 habitants ».
		if (n === 0) return { nombre: null, suite: `aucun habitant recensé${annee}` };
		const nombre = formatBareNumber('demographie.population', n);
		return { nombre, suite: ` ${n === 1 ? 'habitant' : 'habitants'}${annee}` };
	});
	const codePostal = $derived(data.etat !== 'fiche' ? (data.identite?.codePostal ?? null) : null);
	const avantInsee = $derived([codePostal, departement].filter(Boolean).join(' · '));

	// Client navigation to another commune: the previous figures are never shown as the new ones.
	const chargement = $derived(
		$navigating?.to?.route.id === '/(marketing)/commune/[code]' &&
			$navigating.to.params?.code?.toUpperCase() !== data.code
	);

	// Typed failure of the outage state (WR-01): a rate limit or a refused access is worded by the
	// client; null keeps the generic outage text (read optionally: a fixture may omit `detail`).
	const messagePanne = $derived(data.etat === 'panne' ? (data.detail?.message ?? null) : null);
	// Typed title of the same failure (03-17 B C1): every 4xx is titled by the client; null keeps
	// the generic outage title (5xx, network).
	const titrePanne = $derived(data.etat === 'panne' ? (data.detail?.titre ?? null) : null);

	// « Recharger la fiche »: one reload in flight at most, announced by aria-busy (T-03-07-02).
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

	const motifHorsCouverture = $derived(
		data.etat === 'hors-couverture' && data.motif ? (LIBELLES_MOTIFS[data.motif]?.phrase ?? null) : null
	);
	const retourResultats = $derived(
		data.etat === 'hors-couverture' && data.identite.departement
			? `/communes?q=${encodeURIComponent(nom)}`
			: '/communes'
	);

	// Meta description carries the figures the page shows, nothing else (its served prices first).
	const metaDescription = $derived(descriptionCommune(nom, departementCode, synthese, kpis));
	const pageTitle = $derived(titreCommune(nom, departementCode, synthese !== null));
	// The fiche's own link preview: its served figures, drawn by /og/commune/{code}.png (generic without).
	const carteOg = $derived(data.etat === 'fiche' ? carteCommune(data) : null);
	const imageOg = $derived(carteOg ? { url: urlImageOg(SITE_URL, carteOg), alt: carteOg.alt } : null);
	const faq = $derived(synthese ? faqCommune(synthese, sources, nom, kpis) : []);
	const faqStructuree = $derived(faqJsonLd(faq));
	const entreesSommaire = $derived<EntreeSommaire[]>([
		...categories.flatMap((c): EntreeSommaire[] => [
			{ ancre: c.id, titre: c.titre, niveau: 1, partie: c.id },
			...c.groupes.map((g) => ({ ancre: `critere-${g.id}`, titre: g.titre, niveau: 2 as const }))
		]),
		...(faq.length ? [{ ancre: 'questions', titre: 'Questions fréquentes', niveau: 1 as const, fin: true }] : [])
	]);
	// Canonical points at the clean fiche URL: the theme read (`?theme=`, `?vue=`) is a reading state
	// of one fiche, and the code is the normalised one of the load (`2a004` and `2A004` are one
	// fiche, IN-02).
	const canonicalUrl = $derived(`${SITE_URL}/commune/${data.code}`);
	// The breadcrumb goes through the department (04.7-10, decision 9): Recherche > department >
	// commune, or Recherche > department > commune > rank for an arrondissement. One list feeds the
	// visible nav and the BreadcrumbList, so the markup never says what the page does not show; the
	// last step is the page itself. Without a known department the step is left out, never a dead link.
	const filAriane = $derived.by(() => {
		const etapes = [{ nom: 'Accueil', href: '/' }];
		// Paris is both: the step of the departement then says so (Opus P3-4, WCAG 2.4.4).
		if (departementLie) {
			const suivants = parent ? [parent.nom] : [nom];
			etapes.push({ nom: nomDepartementDuFil(departementLie.nom, suivants), href: departementLie.href });
		}
		if (parent) etapes.push({ nom: parent.nom, href: `/commune/${parent.code}` });
		etapes.push({ nom: rangDansLaCommune ?? nom, href: `/commune/${data.code}` });
		return etapes;
	});
	const filArianeStructure = $derived({
		'@context': 'https://schema.org',
		'@type': 'BreadcrumbList',
		itemListElement: filAriane.map((etape, index) => ({
			'@type': 'ListItem',
			position: index + 1,
			name: etape.nom,
			item: `${SITE_URL}${etape.href}`
		}))
	});
	// The place lies in its department; an arrondissement lies in its commune, itself in the department.
	const zoneDepartement = $derived(
		departementLie
			? { '@type': 'AdministrativeArea', name: departementLie.nom, identifier: departementLie.code, url: `${SITE_URL}${departementLie.href}` }
			: null
	);
	const contenuDans = $derived.by(() => {
		if (parent) {
			return {
				containedInPlace: {
					'@type': 'Place',
					name: parent.nom,
					identifier: parent.code,
					url: `${SITE_URL}/commune/${parent.code}`,
					...(zoneDepartement ? { containedInPlace: zoneDepartement } : {})
				}
			};
		}
		return zoneDepartement ? { containedInPlace: zoneDepartement } : {};
	});
	const structuredData = $derived({
		'@context': 'https://schema.org',
		'@type': 'Dataset',
		name: `Données publiques ${deNom(nom)}`,
		description: metaDescription,
		url: canonicalUrl,
		inLanguage: 'fr',
		isAccessibleForFree: true,
		creator: { '@type': 'Organization', name: 'DataCommune', url: SITE_URL },
		spatialCoverage: {
			'@type': 'Place',
			name: nom,
			identifier: data.code,
			...contenuDans
		}
	});

	// A theme row as a value of `dl.tab`: its served value or state, its period as the note, its caveats
	// printed, its panel (UI-SPEC v4 § 3.13). Nothing is recomputed.
	const valeurDeLigne = (ligne: LigneTheme): ValeurDeListe => ({
		id: ligne.id,
		libelle: ligne.libelle,
		etat: ligne.etat,
		fieldId: ligne.fieldId,
		note: ligne.etat.kind === 'valeur' ? ligne.periode : null,
		aide: aideAuNiveau1(ligne.etat),
		reserves: ligne.reserves,
		volet: ligne.volet,
		brut: ligne.brut,
		sourcePanneau: ligne.source,
		// R-09: a value of its commune keeps its mark (its object, « Commune de Paris », on paper); an absence
		// the other mesh fills leads there.
		...(ligne.echelle ? { echelle: { ...ligne.echelle, enClair: ligne.objet } } : {}),
		...(ligne.renvoi ? { lien: ligne.renvoi } : {})
	});
	const liensReserves = $derived(
		reserves.map((reserve) => ({ href: reserve.ancre, titre: kpis.find((kpi) => kpi.id === reserve.kpi)?.libelle ?? '', note: reserve.texte }))
	);

	// Acronyms spelled out at their first occurrence (04.8-08, D-17, D-31): the prose of the main, in
	// reading order — the label and sub-labels of each tile of « L'essentiel », the caveats, then each
	// category (title of a group, term of a row, its notes). A closed category hides its text, so each one is
	// read on its own after that head, as on the address fiche. Never a value, a period, a badge, an
	// attribute, the CSV, the open JSON or the JSON-LD, which read the model itself. The questions stay word
	// for word the text of their JSON-LD, so they are left out; the fixed texts of the page and of the DPE
	// distribution hold no acronym but DPE, always said before by the fourth tile.
	const textes = $derived.by(() => {
		const enTete = [
			...reperes.flatMap((kpi) => [
				[`kpi:${kpi.id}:libelle`, kpi.libelle] as const,
				...kpi.lignes.map((ligne) => [`kpi:${kpi.id}:sous:${ligne.id}`, ligne.libelle] as const)
			]),
			...liensReserves.flatMap((lien, index) => [
				[`reserve:${index}:titre`, lien.titre] as const,
				[`reserve:${index}:note`, lien.note] as const
			])
		];
		const parCategorie = categories.map((categorie) =>
			developperParCle([
				...enTete,
				...categorie.groupes.flatMap((groupe) => [
					[`groupe:${groupe.id}:titre`, groupe.titre] as const,
					...groupe.lignes.map((ligne) => [`ligne:${ligne.id}:libelle`, ligne.libelle] as const),
					...groupe.notes.flatMap((note) => [
						[`note:${note.id}:libelle`, note.libelle] as const,
						[`note:${note.id}:texte`, note.texte] as const
					])
				])
			])
		);
		return new Map([...developperParCle(enTete), ...parCategorie.flatMap((m) => [...m])]);
	});

	// Under the threshold of two columns the categories are closed; an anchor opens the one it points to.
	// Above it they are cards, always open (the shell of the address fiche, Categorie).
	const SEUIL_DEUX_COLONNES = '(min-width: 1081px)';
	let deuxColonnes = $state(false);
	const toutesFermees = () => Object.fromEntries(CATEGORIES_COMMUNE.map((c) => [c.id, false])) as Record<IdPartie, boolean>;
	let ouvertes = $state<Record<IdPartie, boolean>>(toutesFermees());
	// Another commune starts closed again. The first run is not one: it would close what the reader opened
	// before the hydration.
	let communePrecedente = untrack(() => data.code);
	$effect(() => {
		if (data.code === communePrecedente) return;
		communePrecedente = data.code;
		ouvertes = toutesFermees();
	});

	/** Opens the category an anchor points to; true when there is one. */
	function ouvrirCible(hash: string): boolean {
		const categorie = categorieDeAncre(hash, categories);
		if (categorie) ouvertes[categorie] = true;
		return categorie !== null;
	}

	/** The target was folded when the browser looked for it: goes to it now that it is open. */
	async function allerALAncre() {
		await tick();
		const cible = location.hash.length > 1 ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
		cible?.scrollIntoView?.();
		if (cible) garderAncre(cible);
	}

	/**
	 * Content above the anchor still grows after the jump (the header actions once the session is known, a
	 * caption reflowed by the fonts): a smooth scroll keeps the target it computed when it started, so the
	 * anchor could end 100 px lower, its rows out of view. While the page keeps changing size in the next
	 * two seconds, and until the reader scrolls, presses a key or touches the page, the anchor is put back
	 * under the header.
	 */
	function garderAncre(cible: HTMLElement) {
		if (typeof ResizeObserver !== 'function') return;
		const gestes = ['wheel', 'touchstart', 'pointerdown', 'keydown'] as const;
		const arreter = () => {
			observateur.disconnect();
			clearTimeout(delai);
			for (const geste of gestes) removeEventListener(geste, arreter);
		};
		const observateur = new ResizeObserver(() => cible.scrollIntoView({ behavior: 'instant', block: 'start' }));
		observateur.observe(document.body);
		const delai = setTimeout(arreter, 2000);
		for (const geste of gestes) addEventListener(geste, arreter, { passive: true });
	}

	// A link to the anchor already in the URL fires no hashchange: the click opens its category, then goes to it.
	function ouvrirDepuisAncre(event: MouseEvent) {
		const lien = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href^="#"]') : null;
		if (!lien || !ouvrirCible(lien.hash)) return;
		if (lien.hash === location.hash) setTimeout(() => void allerALAncre());
	}

	function suivreAncre() {
		if (ouvrirCible(location.hash)) void allerALAncre();
	}

	onMount(() => {
		suivreAncre();
		if (typeof matchMedia !== 'function') return;
		const seuil = matchMedia(SEUIL_DEUX_COLONNES);
		const lire = () => (deuxColonnes = seuil.matches);
		lire();
		seuil.addEventListener('change', lire);
		// The print medium without a `beforeprint` (an emulated medium, some print previews).
		const papier = matchMedia('print');
		const surPapier = () => {
			if (papier.matches) preparerImpression();
		};
		surPapier();
		papier.addEventListener('change', surPapier);
		return () => {
			seuil.removeEventListener('change', lire);
			papier.removeEventListener('change', surPapier);
		};
	});
	const liensReservesLus = $derived(
		liensReserves.map((lien, index) => ({
			...lien,
			titre: textes.get(`reserve:${index}:titre`) ?? lien.titre,
			note: textes.get(`reserve:${index}:note`) ?? lien.note
		}))
	);

	// Printing opens the categories and the questions and gives each its state back afterwards (UI-SPEC v4 § 2.5).
	const IMPRIMES = 'details.cat, [data-faq] details';
	let avantImpression: boolean[] | null = null;
	function ouvrirPourImpression() {
		preparerImpression();
		// A second `beforeprint` (page.pdf, a print preview) must not record the opened state as the reader's.
		if (avantImpression) return;
		const details = [...document.querySelectorAll<HTMLDetailsElement>(IMPRIMES)];
		avantImpression = details.map((d) => d.open);
		for (const d of details) d.open = true;
	}
	function rendreApresImpression() {
		if (!avantImpression) return;
		const etats = avantImpression;
		document.querySelectorAll<HTMLDetailsElement>(IMPRIMES).forEach((d, index) => (d.open = etats[index] ?? false));
		avantImpression = null;
	}

	async function copyInseeCode() {
		try {
			await navigator.clipboard.writeText(data.code);
			statutCopie = 'Code INSEE copié';
		} catch {
			// Clipboard refused (permissions, unsupported browser): said, and the code stays readable.
			statutCopie = 'Copie impossible';
		}
		clearTimeout(statutCopieTimeout);
		statutCopieTimeout = setTimeout(() => {
			statutCopie = '';
		}, 2000);
	}

	// Primary action: the commune joins the tab's comparison selection (0 to 4), then the
	// comparator opens on it. Without JavaScript the link opens the comparator on this commune.
	//
	// R-05 (review of 2026-11-10): a full selection refuses the commune. The fiche then says so with the
	// sentence of the directory (`confirmationAjout`) and the way to the comparator, where one commune
	// can be removed; it never opens a comparison this commune is absent from without a word.
	let refusComparaison = $state<ConfirmationAjout>(null);
	function comparer(event: MouseEvent) {
		event.preventDefault();
		const ajout = comparisonSelection.add(data.code);
		if (!ajout.ajoute && ajout.raison === 'limite') {
			refusComparaison = confirmationAjout(ajout, comparisonSelection.count);
			return;
		}
		refusComparaison = null;
		void goto(comparisonSelection.href);
	}
</script>

<svelte:head>
	<title>{pageTitle}</title>
	<meta name="description" content={metaDescription} />
	<link rel="canonical" href={canonicalUrl} />
	<meta property="og:type" content="website" />
	<meta property="og:site_name" content="DataCommune" />
	<meta property="og:title" content={pageTitle} />
	<meta property="og:description" content={metaDescription} />
	<meta property="og:url" content={canonicalUrl} />
	<meta property="og:image" content={imageOg?.url ?? OG_IMAGE_URL} />
	<meta property="og:image:width" content={String(OG_LARGEUR)} />
	<meta property="og:image:height" content={String(OG_HAUTEUR)} />
	{#if imageOg}<meta property="og:image:alt" content={imageOg.alt} />{/if}
	<meta property="og:locale" content="fr_FR" />
	<meta name="twitter:card" content="summary_large_image" />
	<meta name="twitter:title" content={pageTitle} />
	<meta name="twitter:description" content={metaDescription} />
	<meta name="twitter:image" content={imageOg?.url ?? OG_IMAGE_URL} />
	<!-- Structured data only when the fiche rendered: never on an outage or out-of-coverage page. -->
	{#if synthese}
		{@html jsonLdScript(structuredData)}
		{@html jsonLdScript(filArianeStructure)}
		{#if faqStructuree}
			{@html jsonLdScript(faqStructuree)}
		{/if}
	{/if}
</svelte:head>

<svelte:window onbeforeprint={ouvrirPourImpression} onafterprint={rendreApresImpression} onhashchange={suivreAncre} />

{#snippet questions()}
	<!-- Computed, never written by hand (04.7-15 T4): the JSON-LD above repeats this text. -->
	<div data-faq>
		<Panneau titre="Questions fréquentes" id="questions">
			<div class="faq">
				{#each faq as item (item.id)}
					<details class="det" data-faq-question={item.id}>
						<summary>{item.question}</summary>
						<p>{item.reponse}</p>
					</details>
				{/each}
			</div>
		</Panneau>
	</div>
{/snippet}

{#snippet autour()}
	{#if voisines.length || departementLie}
		<Panneau titre={`Autour de ${nom}`} id="autour">
			{#if voisines.length}
				<ul class="voisines">
					{#each voisines as voisine (voisine.code)}<li><a href={voisine.href}>{voisine.nom}</a></li>{/each}
				</ul>
			{/if}
			{#if departementLie}
				<ListeLiens liens={[{ href: departementLie.href, titre: `${departementLie.nom}\u00a0(${departementLie.code})`, note: 'Chiffres clés et toutes ses communes' }]} />
			{/if}
		</Panneau>
	{/if}
{/snippet}

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (only links are listened to, and a link reached by the keyboard fires this click too) -->
<div class="fiche gabarit" onclick={ouvrirDepuisAncre}>
	<div class="fiche-tete">
	<FilAriane etapes={filAriane} />

	<header class="fiche-header">
		<h1 class="fiche-title">{nom}</h1>
		<!-- Identity line (03-17 F9, V34): the former overline is its first clause, then CP · Département
		     (code) · INSEE {code} with its copy button, then the population. The result is announced. -->
		<p class="fiche-sous">
			<span class="fiche-overline">{parent ? 'Fiche arrondissement' : 'Fiche commune'}</span>
			· {avantInsee ? `${avantInsee} · ` : ''}INSEE <span class="chiffre">{data.code}</span>
			<button type="button" class="fiche-copy" onclick={copyInseeCode}>Copier le code INSEE</button>
			{#if populationLue} · {#if populationLue.nombre}<span class="chiffre">{populationLue.nombre}</span>{/if}{populationLue.suite}{/if}
			<span class="fiche-copy-status" role="status">{statutCopie}</span>
		</p>
		{#if data.etat === 'fiche' && synthese && !chargement}
			<!-- « Comparer » is the one filled button of the page outside the chrome (UI-SPEC v4 § 6.2). -->
			<div class="fiche-actions">
				<a class="fiche-primary" href={`/comparer?communes=${data.code}`} onclick={comparer}>Comparer avec une autre commune</a>
				<button type="button" class="fiche-secondary" onclick={() => (exportOpen = true)}>Télécharger (CSV)</button>
				<!-- Open JSON export of this fiche (04.7-14): the same reading as the page, no key. -->
				<a class="fiche-secondary" href={`/commune/${data.code}.json`} download={`commune-${data.code}.json`} data-sveltekit-reload
					>Télécharger (JSON)</a
				>
			</div>
			<!-- R-05: the refusal of a full selection, announced; empty, the live region takes no room. The
			     sentence is the one of the directory; the link under it was validated by Ianis on 2026-11-10. -->
			<p class="fiche-comparer-statut" class:fiche-comparer-statut-vide={refusComparaison === null} role="status">
				{#if refusComparaison}
					{refusComparaison.texte}
					<a href={comparisonSelection.href}>Ouvrir le comparateur</a>
				{/if}
			</p>
			<!-- Paris, Lyon, Marseille: the entry to the comparison of arrondissements, on the fiche of the
			     commune and on the fiche of each arrondissement (its own box checked). -->
			{#if villeDesArrondissements}
				<!-- Mounted again for each fiche: the box checked on arrival is the one of this arrondissement. -->
				{#key data.code}
					<ChoixArrondissements ville={villeDesArrondissements} courant={parent ? data.code : null} />
				{/key}
			{/if}
		{/if}
		<LienLimites />
	</header>
	</div>

	{#if chargement}
		<!-- Client navigation to another commune: skeleton tiles and empty panels, never the previous figures. -->
		<div class="fiche-chargement" aria-busy="true">
			<Panneau variante="essentiel" titre="Chargement des données de la commune…">
				<KpiStrip kpis={null} />
			</Panneau>
			{#each CATEGORIES_COMMUNE as categorie (categorie.id)}<div class="panneau-squelette" aria-hidden="true"></div>{/each}
		</div>
	{:else if data.etat === 'fiche' && synthese}
		{#if impression}<EnTetePrint consulteLe={data.consulteLe} />{/if}

		<Panneau variante="essentiel" titre="L’essentiel" id="essentiel">
			<p class="essentiel-chapo">
				<RepereEchelle pictogramme="commune" mot={parent ? 'Arrondissement' : 'Commune'} />
				<span
					>{parent
						? avecHeritage
							? chapoArrondissementAvecHeritage(parent.nom)
							: 'Toutes les valeurs de cette page valent pour l’arrondissement entier, jamais pour une adresse.'
						: 'Toutes les valeurs de cette page valent pour la commune entière, jamais pour une adresse.'}</span
				>
			</p>
			{#if reperes.length}
				<KpiStrip kpis={reperes} etiquette="Repères de la commune" {textes} forme="detail" />
			{:else}
				<p class="essentiel-vide">Prix et DPE sont sans valeur ici : chaque thème ci-dessous dit pourquoi.</p>
			{/if}
			{#if repartition}
				<div class="fiche-repartition"><RepartitionDpe {repartition} /></div>
			{/if}
		</Panneau>

		{#if reserves.length}
			<Panneau titre="Ce qui mérite une lecture attentive">
				<ListeLiens liens={liensReservesLus} cible="ancre" />
			</Panneau>
		{/if}

		<!-- The categories of the address fiche (4.9), its summary beside them above the threshold. -->
		{#if categories.length}
			<div class="fiche-lecture">
				<div class="fiche-sommaire"><Sommaire entrees={entreesSommaire} /></div>
				<section class="lecture" aria-labelledby="lecture-titre">
					<h2 id="lecture-titre" class="sr-only">Pour aller plus loin</h2>
					<div class="lecture-liste">
						{#each categories as categorie (categorie.id)}
							<Categorie id={categorie.id} titre={categorie.titre} annonce={categorie.annonce} {deuxColonnes} ouverte={ouvertes[categorie.id]} onbascule={(ouverte) => (ouvertes[categorie.id] = ouverte)}>
								{#each categorie.groupes as groupe (groupe.id)}
									<div class="groupe" id={`critere-${groupe.id}`}>
										<!-- Anchors of the themes before 4.9, kept for the links already shared. -->
										{#each groupe.anciennesAncres as ancre (ancre)}<span id={ancre}></span>{/each}
										<h4 class="groupe-titre">{textes.get(`groupe:${groupe.id}:titre`) ?? groupe.titre}</h4>
										{#if groupe.lignes.length}<ListeValeurs valeurs={groupe.lignes.map(valeurDeLigne)} {textes} forme="detail" />{/if}
										{#each groupe.notes as note (note.id)}
											<p class="theme-note"><strong>{textes.get(`note:${note.id}:libelle`) ?? note.libelle}</strong> {textes.get(`note:${note.id}:texte`) ?? note.texte}</p>
										{/each}
									</div>
								{/each}
							</Categorie>
						{/each}
					</div>
				</section>
				{#if faq.length}
					<div class="fiche-fin">{@render questions()}</div>
				{/if}
			</div>
		{:else if faq.length}
			{@render questions()}
		{/if}

		{#if arrondissements.length}
			<Panneau titre={`Les ${arrondissements.length} arrondissements de ${nom}`} id={ANCRE_ARRONDISSEMENTS}>
				<ListeLiens liens={arrondissements.map((a) => ({ href: `/commune/${a.code}`, titre: a.libelle }))} />
			</Panneau>
		{/if}

		{@render autour()}

		{#if impression}
			<div id="definitions-impression" class="fiche-impression fiche-definitions">
				<h3>Définitions</h3>
				<DefinitionsCommune {definitions} />
			</div>
			<SourcesPrint {millesimes} sources={sourcesParDataset} />
		{/if}
		<noscript>
			<p class="fiche-sans-js fiche-definitions"><a href={`/commune/${data.code}/definitions`}>Définitions et sources des indicateurs de cette fiche</a></p>
		</noscript>

		<ExportDialog
			open={exportOpen}
			themes={exportThemes}
			filenameBase={`commune-${data.code}`}
			onclose={() => (exportOpen = false)}
		/>
	{:else if data.etat === 'hors-couverture'}
		<div class="fiche-etat">
			<Notice titre="Commune hors couverture" variante="warn">
				<p>{[motifHorsCouverture, 'Une absence de couverture ne vaut pas zéro.'].filter(Boolean).join(' ')}</p>
				<p class="fiche-links">
					<a href="/datasets">Voir la couverture des données</a>
					<a href="/couverture">Limites connues des données</a>
					<a href={retourResultats}>Revenir aux résultats</a>
				</p>
			</Notice>
		</div>
		{@render autour()}
	{:else if data.etat === 'non-renseignee'}
		<div class="fiche-etat">
			<!-- An answer without data nor reason is not the served sub-state « Non renseigné »
			     (03-17 F1): the page says it could not be read, and shows nothing. -->
			<Notice titre="Les données de cette commune n'ont pas pu être lues" variante="warn" role="alert">
				<p>Le service a répondu sans données ni motif pour cette commune. Aucune valeur n'est affichée.</p>
				<p class="fiche-links">
					<a href="/">Revenir à la recherche</a>
					<a href="/datasets">Voir la couverture des données</a>
				</p>
			</Notice>
		</div>
	{:else}
		<div class="fiche-etat">
			<Notice titre={titrePanne ?? "Les données de cette commune sont momentanément indisponibles"} variante="warn" role="alert">
				<p>{messagePanne ?? "Le service n'a pas répondu. Aucune valeur n'est affichée plutôt qu'une valeur incomplète."}</p>
				<button type="button" class="fiche-secondary" onclick={recharger} disabled={rechargement} aria-busy={rechargement}>Recharger la fiche</button>
			</Notice>
		</div>
	{/if}
</div>

<style>
	/* A simple page of the direction A (UI-SPEC v4 § 6.0): `.gabarit`, h1 44/32. */
	.fiche {
		/* The page padding of `main` (UI-SPEC v4 § 2.3), as the address fiche writes it. */
		padding-block: var(--s6) var(--s8);
		color: var(--color-ink);
	}

	/* The pale band of the head, as on the address fiche: a shadow as wide as the window, cut to the block. */
	@media screen {
		.fiche-tete {
			background: var(--color-bande);
			box-shadow: 0 0 0 100vmax var(--color-bande);
			clip-path: inset(calc(-1 * var(--s6)) -100vmax calc(-1 * var(--s4)));
		}
	}

	.fiche-title {
		margin: var(--s4) 0 0;
		font-family: var(--font-display);
		font-size: 44px;
		font-weight: 500;
		line-height: 1.12;
		overflow-wrap: anywhere;
	}

	.fiche-sous {
		max-width: var(--mesure);
		margin: var(--s2) 0 0;
		color: var(--color-muted);
		font-size: 16px;
		line-height: 1.6;
		overflow-wrap: anywhere;
	}

	/* Inline text button after the INSEE code: a 44px hit area pulled back by negative margins, so
	   the line keeps its height (same pattern as SourceButton). */
	.fiche-copy {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		margin: calc(-1 * var(--s3)) 0 calc(-1 * var(--s3)) var(--s1);
		padding: 0 var(--s1);
		border: 0;
		background: transparent;
		color: var(--color-accent);
		font-family: inherit;
		font-weight: inherit;
		font-style: inherit;
		line-height: inherit;
		font-size: 16px;
		text-decoration: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
		cursor: pointer;
	}

	.fiche-copy-status {
		margin-left: var(--s1);
		color: var(--color-ink);
	}

	.fiche-actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
		margin: var(--s5) 0 0;
	}

	.fiche-primary,
	.fiche-secondary {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-height: 44px;
		padding: var(--s1) var(--s4);
		border-radius: var(--radius-control);
		font-family: inherit;
		font-weight: 400;
		font-style: inherit;
		font-size: 16px;
		line-height: 1.2;
		text-decoration: none;
		cursor: pointer;
	}

	.fiche-primary {
		border: 1px solid var(--color-accent);
		background: var(--color-accent);
		color: var(--color-paper);
		font-weight: 600;
	}

	.fiche-primary:hover {
		background: var(--color-accent-hover);
	}

	.fiche-secondary {
		border: 1px solid var(--color-accent);
		background: var(--color-paper);
		color: var(--color-accent);
	}

	.fiche-secondary:hover {
		border-color: var(--color-accent-hover);
		background: var(--color-accent-doux);
		color: var(--color-accent-hover);
	}

	.fiche-secondary:disabled {
		cursor: progress;
		opacity: 0.7;
	}

	.fiche-comparer-statut {
		max-width: var(--mesure);
		margin: var(--s2) 0 0;
		font-size: 16px;
		line-height: 1.5;
	}

	.fiche-comparer-statut-vide {
		margin: 0;
	}

	.fiche-comparer-statut a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-accent);
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.fiche-header :global(.lien-limites) {
		max-width: var(--mesure);
	}

	/* The scale mark of the page, then its sentence (UI-SPEC v4 § 6.2, `pages.mjs:344`). */
	.essentiel-chapo {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s2);
		max-width: var(--mesure);
		margin: var(--s-demi) 0 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.fiche-repartition {
		max-width: 640px;
	}

	.essentiel-vide {
		max-width: var(--mesure);
		margin: var(--s4) 0 0;
	}

	/* The categories and the questions, the summary beside them above the threshold, as on the address fiche.
	   Under it the compact control of the summary is sticky along this block. */
	.fiche-lecture {
		display: flex;
		flex-direction: column;
		gap: var(--s4);
		margin-top: var(--s6);
	}

	.fiche-sommaire {
		display: contents;
	}

	.lecture {
		min-width: 0;
	}

	.fiche-fin {
		min-width: 0;
	}

	.fiche-fin > :global([data-faq] > .panneau) {
		margin-top: 0;
	}

	/* One panel of closed rows under the threshold, a row that opens pushes the next one down; above it the
	   cards (Categorie). Nothing stretches. */
	.lecture-liste {
		overflow: clip;
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-panneau);
	}

	@media screen and (min-width: 1081px) {
		.fiche-lecture {
			display: grid;
			grid-template-columns: 220px minmax(0, 1fr);
			grid-template-areas:
				'sommaire lecture'
				'sommaire fin';
			column-gap: var(--s6);
			row-gap: var(--s4);
		}

		.fiche-sommaire > :global(.sommaire-large) {
			grid-area: sommaire;
			align-self: start;
			min-width: 0;
		}

		.lecture {
			grid-area: lecture;
		}

		.fiche-fin {
			grid-area: fin;
		}

		.lecture-liste {
			display: grid;
			gap: var(--s4);
			overflow: visible;
			border: 0;
			background: none;
		}
	}

	/* A group of rows: the counterpart of a criterion of the address fiche. */
	.groupe + .groupe {
		margin-top: var(--s4);
	}

	.groupe-titre {
		margin: var(--s3) 0 0;
		color: var(--color-ink);
		font-family: var(--font-display);
		font-size: 18px;
		font-weight: 500;
		line-height: 1.25;
	}

	.fiche-definitions {
		display: none;
		max-width: var(--mesure);
		font-size: 14px;
		line-height: 1.4;
	}

	.fiche-definitions h3 {
		margin: var(--s5) 0 var(--s2);
		font-size: 16px;
	}

	@media screen {
		.fiche-sans-js {
			display: block;
		}
	}

	.theme-note {
		max-width: var(--mesure);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.theme-note strong {
		color: var(--color-ink);
	}

	/* The communes around: names in running text, underlined, 44 px high for the touch widths. */
	.voisines {
		display: flex;
		flex-wrap: wrap;
		gap: 0 var(--s4);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.voisines a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		overflow-wrap: anywhere;
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.voisines a:hover {
		text-decoration-thickness: 2px;
	}

	/* The questions: one detail each, a pale line between two (UI-SPEC v4 § 6.2, `.det`). */
	.faq {
		max-width: var(--mesure);
	}

	.det + .det {
		border-top: 1px solid var(--color-filet-pale);
	}

	.det > summary {
		display: flex;
		align-items: center;
		gap: var(--s2);
		min-height: 44px;
		padding: var(--s2) 0;
		color: var(--color-ink);
		font-weight: 600;
		line-height: 1.4;
		list-style: none;
		cursor: pointer;
	}

	.det > summary::-webkit-details-marker {
		display: none;
	}

	/* The chevron is drawn by CSS: it turns when the detail opens. */
	.det > summary::before {
		content: '';
		flex: none;
		width: 7px;
		height: 7px;
		border-right: 1.5px solid var(--color-accent);
		border-bottom: 1.5px solid var(--color-accent);
		transform: rotate(-45deg);
		transition: transform 0.15s ease-out;
	}

	.det[open] > summary::before {
		transform: rotate(45deg);
	}

	.det > p {
		margin: 0 0 var(--s3);
		line-height: 1.6;
	}

	.panneau-squelette {
		height: 120px;
		margin-top: var(--s6);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-panneau);
	}

	.fiche-etat {
		margin-top: var(--s6);
	}

	.fiche-links {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2) var(--s5);
	}

	.fiche-links a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		text-decoration: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	@media (max-width: 720px) {
		.fiche {
			padding-block: var(--s5) var(--s7);
		}

		.fiche-title {
			font-size: 32px;
		}
	}

	@media print {
		/* The site chrome carries print:hidden; the page hides its own controls and the source panels. */
		.fiche-actions,
		.fiche-comparer-statut,
		.fiche-copy,
		.fiche :global(dialog.source-panel) {
			display: none;
		}

		.fiche-impression {
			display: block;
		}

		.det > summary::before {
			display: none;
		}

		/* Every answer is printed, even before hydration or without JavaScript (UI-SPEC v4 § 2.5); the
		   `beforeprint` handler stays the fallback of browsers without `::details-content`. */
		.det::details-content,
		.fiche :global(details.cat)::details-content {
			content-visibility: visible;
		}
	}
</style>
