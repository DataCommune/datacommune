<script lang="ts">
	// `/comparer` (UI-SPEC « /comparer », D-48, D-49). Anchor of the screen: the table. The page
	// presents the comparison the API served (`+page.ts`); it computes no gap and judges no
	// comparability. No departement is written here (D-34): coverage is read in the API.
	//
	// The link is the selection: every action writes a new link (communes in column order, then
	// `diff=1`) and the load reads the comparison again, so « Placer en premier » gets the gaps the
	// API serves for the new first column. The tab storage follows the link at every load (URL
	// first). « Différences seulement » only rewrites the address bar: it hides what the API served
	// as identical, and needs no new request.
	//
	// 04-07 (D-102, same nature only): `?adresses=` compares addresses with the same table. Each
	// nature keeps its own selection (link and tab storage); the commune of each address is a
	// reference group served last by the API (CMP-01). Nothing here mixes the two natures.
	//
	// 04-10 (CMP-02): `?dpe=` compares DPE documents added by their number (checked here before any
	// request); the nature switch (`NatureNav`) opens each nature with its own selection, and
	// `/comparer` without parameter opens the nature modified last (UI-SPEC Q3).
	//
	// 04-11 (D-102): a DPE designated on a compared address lives in the link only (`designes`): the
	// page writes it with the selection at every action and keeps it in no storage; « Retirer la
	// désignation » removes the pair from the link, without confirmation.
	//
	// 04.5-09 (B5, UI-012; D45-01, D45-11): the values come before the settings. Above the table: the
	// title, the nature switch, the compared objects and one compact command, « Modifier la sélection »
	// (a native `details`) holding the add controls, the sentence of the limit and the filter, next to
	// « Copier le lien ». The reference, the access to the groups and the reading note are folded after
	// the table (« Référence des écarts : … »), their text unchanged. Nothing is removed: the removal and
	// « Placer en premier » stay in the columns (CompareTable is not touched). The panel is open while
	// fewer than two objects are compared, and opened before the focus moves to the add control.
	//
	// 04.8-05 (UI-SPEC v4 § 6.4): the form of the direction A. Breadcrumb « Accueil › Comparer », a
	// comparison of communes titled by their names with the sentence of the mock-up under it, the
	// table in the `.cmp` form, then the sentence on absences (the word of the state served), the
	// links to each compared page (→), and the two folded panels in the `.det` form. The three
	// natures, the selection and every behaviour above are kept.
	import { onMount, tick, untrack } from 'svelte';
	import { afterNavigate, goto, invalidateAll, replaceState } from '$app/navigation';
	import CommuneSearch from '$lib/components/CommuneSearch.svelte';
	import CompareTable from '$lib/components/compare/CompareTable.svelte';
	import FilAriane from '$lib/components/ds/FilAriane.svelte';
	import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
	import ListeLiens, { type LienDeListe } from '$lib/components/ds/ListeLiens.svelte';
	import NatureNav from '$lib/components/compare/NatureNav.svelte';
	import Notice from '$lib/components/fiche/Notice.svelte';
	import { libelleEtat, phraseEtat } from '$lib/data/etats-libelles';
	import { developperHorsCitations } from '$lib/utils/sigles';
	import { typographieFr } from '$lib/utils/typographie';
	import type { CommuneSearchResult } from '$lib/api/communes';
	import type { ResultatRechercheAdresse } from '$lib/api/adresses';
	import { NUMERO_DPE_RE } from '$lib/api/documents-dpe';
	import { phraseMalForme, titreDocument } from '$lib/data/document-dpe';
	import { communeParente, libelleArrondissement } from '$lib/data/arrondissements';
	import {
		MAX_COMMUNES,
		NATURES,
		PARAMETRES_NATURE,
		avecAdresse,
		avecCommune,
		avecDesignation,
		avecDocument,
		enPremier,
		lienNature,
		lienSelectionNature,
		sansCommune,
		sansDesignation,
		type Designation,
		type NatureSelection
	} from '$lib/data/commune-comparison-selection';
	import { derniereNature, selectionDeNature } from '$lib/stores/comparison-selection.svelte';
	import {
		COLONNES_APERCU,
		EXEMPLES_COMMUNES,
		LIGNES_APERCU,
		PROMESSE_COMMUNES,
		TITRE_APERCU,
		TITRE_EXEMPLES
	} from '$lib/data/comparateur-textes';
	import type { ComparerData } from './+page';

	let { data }: { data: ComparerData } = $props();

	/** Wording of each nature (UI-SPEC « Contrat de rédaction »: comparateur vide, limite de 4). */
	const TEXTES = {
		commune: {
			h1: 'Comparer des communes',
			ajouter: 'Ajouter une commune',
			plein: 'Quatre communes au maximum : retirez-en une pour en ajouter une autre.',
			vide: 'Choisissez deux communes à comparer',
			videAide: 'Ajoutez-les ici ou depuis une fiche.',
			une: 'Ajoutez une deuxième commune pour lire les écarts.',
			objets: 'communes'
		},
		adresse: {
			h1: 'Comparer des adresses',
			ajouter: 'Ajouter une adresse',
			plein: 'Quatre adresses au maximum : retirez-en une pour en ajouter une autre.',
			vide: 'Choisissez deux adresses à comparer',
			videAide: "Ajoutez-les ici ou depuis la page d'une adresse.",
			une: 'Ajoutez une deuxième adresse pour lire les écarts.',
			objets: 'adresses'
		},
		dpe: {
			h1: 'Comparer des documents DPE',
			ajouter: 'Ajouter un DPE',
			// UI-SPEC « Limite de 4 »: « Quatre documents DPE au maximum : … », agreed with its object.
			plein: 'Quatre documents DPE au maximum : retirez-en un pour en ajouter un autre.',
			vide: 'Choisissez deux documents DPE à comparer',
			videAide: "Ajoutez-les ici par leur numéro ou depuis la page d'un DPE.",
			une: 'Ajoutez un deuxième document DPE pour lire les écarts.',
			objets: 'documents DPE'
		}
	} as const;

	/** UI-SPEC « Ajout » and « Lire les écarts (ajouts) » of the DPE documents. */
	const AIDE_NUMERO_DPE = 'Le numéro figure sur le document et sur la page de chaque adresse.';
	const LIRE_ECARTS_DPE =
		'Deux DPE établis avec des méthodes différentes (avant et après juillet 2021) ne se comparent pas : la ligne le dit.';

	// A load of the phase 3 shape (no nature) is the communes comparator.
	const nature = $derived<NatureSelection>(data.nature ?? 'commune');
	const textes = $derived(TEXTES[nature]);
	const selectionOnglet = $derived(selectionDeNature(nature));
	const MESSAGE_PLEIN = $derived(textes.plein);
	// Direction A (Ianis, 2026-11-10): the empty comparator of communes says what one gets, offers ready
	// comparisons and shows the shape of the result. Wordings in `comparateur-textes.ts` (to be reread).
	const apercu = $derived(nature === 'commune' && data.etat === 'vide');
	// The counts of the other natures are read in the tab storage, after mounting only (the server
	// cannot read it, and the server HTML must match the first client render).
	let monte = $state(false);
	onMount(() => {
		monte = true;
	});
	const comptesNatures = $derived(
		Object.fromEntries(
			NATURES.map((autre) => [autre, autre === nature ? codes.length : monte ? selectionDeNature(autre).count : 0])
		) as Record<NatureSelection, number>
	);
	const liensNatures = $derived(
		Object.fromEntries(
			NATURES.map((autre) => [
				autre,
				// The active nature keeps its whole link, designations and filter included (D-15 FRONT IN-04).
				autre === nature
					? codes.length
						? lienSelectionNature(autre, codes, differences, designationsLien)
						: lienNature(autre, [])
					: lienNature(autre, monte ? selectionDeNature(autre).codes : [])
			])
		) as Record<NatureSelection, string>
	);
	let saisieDpe = $state('');
	let erreurDpe = $state('');

	// Read from the link at once: an effect never runs on the server, where `diff=1` was served unchecked
	// with every row (R-14). The effect below keeps it in step at each later load.
	let differences = $state(untrack(() => data.selection.differences));
	let occupe = $state(false);
	let rechargement = $state(false);
	let rechercheOuverte = $state(false);
	let annonce = $state('');
	let table = $state<ReturnType<typeof CompareTable>>();
	// Same shape as R-06: `bind:open` writes the initial state on the `details` the server rendered, so a
	// fold opened while the scripts were loading closed under the reader's finger. The state of the served
	// element is read first (the guard of `fiche/Categorie.svelte`); nothing exists yet on a client navigation.
	const ID_MODIFIER = 'comparer-modifier';
	const ID_LECTURE = 'comparer-lecture';
	const ouvertAvantHydratation = (id: string) =>
		typeof document !== 'undefined' && (document.getElementById(id) as HTMLDetailsElement | null)?.open === true;
	/** « Modifier la sélection » open: on arrival only while fewer than two objects are compared. */
	let modification = $state(untrack(() => data.selection.codes.length < 2) || ouvertAvantHydratation(ID_MODIFIER));
	/** The reference and the reading note, folded after the table; opened for printing. */
	let lecture = $state(ouvertAvantHydratation(ID_LECTURE));
	let lectureAvantImpression: boolean | null = null;
	let boutonAjouter = $state<HTMLButtonElement>();
	let zoneRecherche = $state<HTMLDivElement>();

	const codes = $derived(data.selection.codes);
	/** The designations of the link the API kept (addresses only, 04-11). */
	const designationsLien = $derived<Designation[]>(data.selection.designations ?? []);
	const plein = $derived(codes.length >= MAX_COMMUNES);
	const noms = $derived.by(() => {
		const connus: Record<string, string> = {};
		if (data.etat === 'prete') for (const colonne of data.modele.colonnes) connus[colonne.code] = colonne.nom;
		if (data.etat === 'une' && data.commune.nom) connus[data.commune.code] = data.commune.nom;
		return connus;
	});
	/** « {n} {objects} : {name}, {name}… », in column order (names the API served). */
	const objetsCompares = $derived(
		data.etat === 'prete'
			? `${data.modele.colonnes.length} ${textes.objets} : ${data.modele.colonnes.map((colonne) => colonne.nom).join(', ')}`
			: null
	);
	/** « A et B », « A, B et C »: the names of the columns, in their order. */
	const nomsLies = $derived.by(() => {
		if (data.etat !== 'prete') return '';
		const liste = data.modele.colonnes.map((colonne) => colonne.nom);
		return liste.length > 1 ? `${liste.slice(0, -1).join(', ')} et ${liste[liste.length - 1]}` : (liste[0] ?? '');
	});
	/** UI-SPEC v4 § 6.4: a comparison of communes is titled by their names (`pages.mjs:489-490`). */
	const titreVisible = $derived(nature === 'commune' && data.etat === 'prete' ? `Comparer ${nomsLies}` : textes.h1);
	const SOUS_TITRE_COMMUNES =
		'Valeur par valeur, sans note globale. Ce sont des références communales, pas l’estimation d’une adresse.';
	const ETAPES = [{ nom: 'Accueil', href: '/' }, { nom: 'Comparer' }];
	/** What a compared object is called in the sentence on absences, by nature. */
	const OBJET_SINGULIER = { commune: 'une commune', adresse: 'une adresse', dpe: 'un document' } as const;
	// The absences the source does not publish: their words are the ones the cells show (StateValue),
	// read on the served model; never a word written here, never « 0 ». Only an absence whose served
	// sentence says so (« La source ne publie / ne fournit pas… ») counts: a reason of attachment (point
	// out of the parcel) or of coverage is not a source that does not publish (Codex 04.8-05-1, P1). The
	// sentence itself says « absente », not « non publiée »: a generic « not reported » does not prove
	// that the producer publishes nothing (Codex 04.8-05-2, P1).
	const ETATS_NON_PUBLIES = new Set(['not_available', 'not_reported']);
	const OBJET_PHRASE = { commune: 'commune', adresse: 'adresse', dpe: 'document' } as const;
	const NON_PUBLIE_RE = /^La source ne (publie|fournit) pas/;
	const motsNonPublies = $derived.by(() => {
		if (data.etat !== 'prete') return [] as string[];
		const mots = new Set<string>();
		// What the table shows: the reference group of an address (its commune) and the rows hidden by
		// « Différences seulement » are left out (Opus 04.8-05-1, P3).
		for (const groupe of data.modele.groupes) {
			if (groupe.reference) continue;
			for (const ligne of groupe.lignes) {
				if (differences && ligne.identiques) continue;
				for (const cellule of ligne.cellules) {
					const etat = cellule.etat;
					if (etat?.kind !== 'absence' || !ETATS_NON_PUBLIES.has(etat.etat) || etat.provisoire) continue;
					const phrase = etat.aide ?? phraseEtat(etat.etat, etat.motif, etat.effectif, OBJET_PHRASE[nature]);
					if (!phrase || !NON_PUBLIE_RE.test(phrase)) continue;
					const mot = etat.libelle ?? libelleEtat(etat.etat, etat.motif);
					if (mot) mots.add(mot);
				}
			}
		}
		return [...mots];
	});
	const noteAbsences = $derived(
		motsNonPublies.length
			? typographieFr(`Une valeur absente pour ${OBJET_SINGULIER[nature]} reste ${motsNonPublies.map((mot) => `« ${mot} »`).join(' ou ')}, jamais zéro.`)
			: ''
	);
	// R-09: an arrondissement is served the values its commune alone publishes (ABC zone, voted tax rates),
	// marked « Commune » in their cell like on the fiche. The page says once what the mark means, naming the
	// commune, when the table shows such a value. Wording validated by Ianis on 2026-11-10: it follows
	// the head of the arrondissement fiche (« … sauf celles marquées « Commune », qui valent pour tout Paris »).
	const villesHeritees = $derived.by(() => {
		if (data.etat !== 'prete') return [] as string[];
		const villes = new Set<string>();
		for (const groupe of data.modele.groupes) {
			for (const ligne of groupe.lignes) {
				if (differences && ligne.identiques) continue;
				for (const cellule of ligne.cellules) {
					if (cellule.echelle) villes.add(communeParente(cellule.code)?.nom ?? cellule.echelle.enClair);
				}
			}
		}
		return [...villes];
	});
	const noteEchelle = $derived(
		villesHeritees.length === 0
			? ''
			: typographieFr(
					villesHeritees.length === 1
						? `Les valeurs marquées « Commune » valent pour tout ${villesHeritees[0]}, pas pour le seul arrondissement.`
						: `Les valeurs marquées « Commune » valent pour la commune entière (${villesHeritees.join(', ')}), pas pour le seul arrondissement.`
				)
	);
	/** The page of each compared object (→), in column order. */
	const liensFiches = $derived<LienDeListe[]>(
		data.etat === 'prete'
			? data.modele.colonnes.map((colonne) => ({
					href: colonne.lien ?? `/commune/${colonne.code}`,
					titre: nature === 'dpe' ? `Le document ${colonne.nom}` : `La fiche de ${colonne.nom}`
				}))
			: []
	);
	const titre = $derived(
		data.etat === 'prete'
			? `${data.modele.colonnes.map((colonne) => colonne.nom).join(', ')} — ${textes.h1} — DataCommune`
			: `${textes.h1} — DataCommune`
	);

	/** 04-DECISIONS-TEXTES row 54: the three natures of the comparator. */
	const DESCRIPTION =
		'Comparer des communes, des adresses ou des documents DPE : chaque valeur garde sa période, sa définition et sa source.';
	/** The link a shared card points to: the selection of the link, designations included, never the filter. */
	const lienPartage = $derived(codes.length ? lienSelectionNature(nature, codes, false, designationsLien) : lienNature(nature, []));

	// Acronyms spelled out at their first occurrence (04.8-08, D-17, D-31): the sentences of the main in
	// reading order — the title of the nature, the error and the limit of the add control, the notices
	// of the link, then the note on absences and the reading note, or the sentence of the outage, of a
	// single object or of an empty comparison. Never a cell, a row label or a column of the table, the
	// nature switch, a control label, a compared name, a link or the document title. A quotation « … »
	// (an input quoted back, a state word) is kept as it is: only the text around it is passed.
	const prose = $derived(
		developperHorsCitations([
			['h1', titreVisible === textes.h1 ? textes.h1 : null],
			['promesse', apercu ? PROMESSE_COMMUNES : null],
			['erreur-dpe', nature === 'dpe' ? erreurDpe : null],
			['plein', plein ? MESSAGE_PLEIN : null],
			...data.selection.messages.map((message, i) => [`message:${i}`, message] as const),
			...(data.selection.horsCouverture ?? []).map((message, i) => [`hors:${i}`, message] as const),
			['note-absences', data.etat === 'prete' ? noteAbsences : null],
			['lire-ecarts-dpe', data.etat === 'prete' && nature === 'dpe' ? LIRE_ECARTS_DPE : null],
			['panne:titre', data.etat === 'panne' ? data.detail?.titre : null],
			['panne:message', data.etat === 'panne' ? data.detail?.message : null],
			['une', data.etat === 'une' ? textes.une : null],
			['vide', data.etat === 'vide' ? textes.vide : null],
			['vide-aide', data.etat === 'vide' ? textes.videAide : null]
		])
	);

	// The filter follows the link at every load.
	$effect(() => {
		differences = data.selection.differences;
	});

	// Fewer than two objects: adding is the next step, the panel opens (it never closes by itself).
	$effect(() => {
		if (codes.length < 2) modification = true;
	});

	// URL first (D-49): a link that names communes replaces the tab storage. `/comparer` without
	// `communes` (the header link, a typed address) names none: the page reads the tab selection
	// back instead of emptying it (D-15 03-10). The server cannot read the tab storage, so this is
	// done here, once the router has started (goto and the shallow rewrite need it: the callbacks
	// of the first navigation run just before, hence the microtask). A link that carried dropped
	// codes is rewritten in place, so a copied link is the clean one.
	//
	// 04-10: a link that names no nature opens the nature modified last (UI-SPEC Q3), the communes
	// before any modification, with its tab selection; a nature named without any object (the
	// switch before hydration) reads its own tab selection back the same way. Each nature fills its
	// own tab storage only.
	afterNavigate((navigation) => {
		const selection = data.selection;
		const parametres = navigation.to?.url.searchParams;
		const nommee = NATURES.some((autre) => parametres?.has(PARAMETRES_NATURE[autre]));
		const ouvrir = (cible: NatureSelection) => {
			const onglet = selectionDeNature(cible);
			const lien = onglet.count > 0 ? lienSelectionNature(cible, onglet.codes, selection.differences) : lienNature(cible, []);
			queueMicrotask(() => void goto(lien, { replaceState: true }));
		};
		if (!nommee) {
			const derniere = derniereNature.nature ?? 'commune';
			if (selectionDeNature(derniere).count > 0 || derniere !== 'commune') ouvrir(derniere);
			return;
		}
		const ecartes = selection.messages.length + (selection.horsCouverture?.length ?? 0);
		if (!selection.codes.length && !ecartes && selectionOnglet.count > 0) {
			ouvrir(nature);
			return;
		}
		selectionOnglet.replace(selection.codes);
		if (ecartes) {
			// Every object set aside: the link keeps its nature, never a bare `/comparer` that would open
			// the nature modified last in another tab (D-15 FRONT IN-02).
			const lien = selection.codes.length
				? lienSelectionNature(nature, selection.codes, selection.differences, selection.designations ?? [])
				: lienNature(nature, []);
			queueMicrotask(() => rafraichirAdresse(lien));
		}
	});

	/** Rewrites the address bar without a new load (shallow routing). */
	function rafraichirAdresse(lien: string) {
		try {
			replaceState(lien, {});
		} catch {
			// Router not ready yet: the link keeps its former form, the page state is unchanged.
		}
	}

	function nomDe(code: string): string {
		if (nature === 'dpe') return noms[code] ?? titreDocument(code);
		return noms[code] ?? (nature === 'adresse' ? `l'adresse ${code}` : `la commune ${code}`);
	}

	/** « ajoutée » / « retirée » agree with the object: a commune, an address, but a document. */
	const accord = (participe: string) => (nature === 'dpe' ? participe : `${participe}e`);

	/**
	 * Writes the new selection in the link and waits for its comparison; the columns stay meanwhile.
	 * The designations follow their addresses (a removed address takes its designation away).
	 */
	async function naviguer(suivants: string[], apres: () => void | Promise<void>, designations: Designation[] = designationsLien) {
		if (occupe) return;
		occupe = true;
		// Each action writes the tab storage with the link: removing the last object empties it.
		selectionOnglet.replace(suivants);
		annonce = '';
		// An emptied selection keeps its nature: its own empty comparator (`/comparer` for communes).
		const lien = suivants.length ? lienSelectionNature(nature, suivants, differences, designations) : lienNature(nature, []);
		try {
			await goto(lien, { noScroll: true, keepFocus: true });
		} catch {
			annonce = 'La sélection n’a pas pu être chargée. Réessayez.';
			return;
		} finally {
			occupe = false;
		}
		// The controls are enabled again before the focus moves to one of them.
		await tick();
		await apres();
	}

	async function focaliserAjout() {
		// The add control lives in « Modifier la sélection »: opened first, a closed panel hides it.
		modification = true;
		await tick();
		// A full selection disables the controls: the focus goes to the sentence of the limit, never
		// lost on the page (D-15 FRONT IN-05). Else the DPE documents are added by their number.
		if (plein) {
			document.getElementById('comparer-plein')?.focus();
			return;
		}
		(boutonAjouter ?? champDpe)?.focus();
	}

	function retirer(code: string) {
		const position = codes.indexOf(code);
		const nom = nomDe(code);
		const suivants = sansCommune(codes, code);
		void naviguer(suivants, async () => {
			annonce = `${nom} ${accord('retiré')} de la comparaison.`;
			// Focus goes to the neighbouring column; without a table, to « Ajouter une commune ».
			const voisine = suivants[position] ?? suivants[suivants.length - 1];
			if (!(suivants.length >= 2 && table?.focaliserColonne(voisine))) await focaliserAjout();
		});
	}

	/** « Désigner ce DPE » (04-11): the pair joins the link; the focus goes to « Retirer la désignation ». */
	function designer(code: string, numero: string) {
		void naviguer(codes, () => void table?.focaliserDesignation(code), avecDesignation(designationsLien, code, numero));
	}

	/** « Retirer la désignation »: the pair leaves the link; the focus goes back to « Désigner un DPE ». */
	function retirerDesignation(code: string) {
		void naviguer(codes, () => void table?.focaliserDesignation(code), sansDesignation(designationsLien, code));
	}

	function placerPremier(code: string) {
		const nom = nomDe(code);
		void naviguer(enPremier(codes, code), () => {
			// « Document » is masculine (D-15 FRONT IN-01).
			annonce = `${nom} est en première colonne : les écarts sont lus par rapport à ${nature === 'dpe' ? 'lui' : 'elle'}.`;
			table?.focaliserColonne(code);
		});
	}

	function ajouter(commune: CommuneSearchResult) {
		if (codes.includes(commune.code_insee)) {
			annonce = `${commune.nom} est déjà dans la comparaison (${codes.length}/${MAX_COMMUNES}).`;
			return;
		}
		if (plein) {
			annonce = MESSAGE_PLEIN;
			return;
		}
		const suivants = avecCommune(codes, commune.code_insee);
		rechercheOuverte = false;
		void naviguer(suivants, async () => {
			annonce = `${commune.nom} ajoutée à la comparaison (${suivants.length}/${MAX_COMMUNES}).`;
			await focaliserAjout();
		});
	}

	/** « {numéro}{indice} {voie} » of a search result, as the column header will name it. */
	function nomAdresse(adresse: ResultatRechercheAdresse): string {
		const numero = `${adresse.numero ?? ''}${adresse.rep ?? ''}`;
		return [numero, adresse.nom_voie].filter(Boolean).join(' ') || (adresse.ban_id ?? '');
	}

	function ajouterAdresse(adresse: ResultatRechercheAdresse) {
		const id = adresse.ban_id;
		if (!id) return;
		const nom = nomAdresse(adresse);
		if (codes.includes(id)) {
			annonce = `${nom} est déjà dans la comparaison (${codes.length}/${MAX_COMMUNES}).`;
			return;
		}
		if (plein) {
			annonce = MESSAGE_PLEIN;
			return;
		}
		const suivants = avecAdresse(codes, id);
		rechercheOuverte = false;
		void naviguer(suivants, async () => {
			annonce = `${nom} ajoutée à la comparaison (${suivants.length}/${MAX_COMMUNES}).`;
			await focaliserAjout();
		});
	}

	/**
	 * « Ajouter un DPE » (UI-SPEC « Ajout »): a number of the ADEME form joins the link; a malformed
	 * input is said under the field and never sent (the contract sentence of a malformed number).
	 * Without JavaScript the form is a GET of the same link (`dpe` twice: the selection, then the
	 * input), read the same way by the load.
	 */
	function ajouterDocument(event: SubmitEvent) {
		event.preventDefault();
		const numero = saisieDpe.trim();
		if (!NUMERO_DPE_RE.test(numero)) {
			erreurDpe = phraseMalForme(numero);
			return;
		}
		erreurDpe = '';
		const nom = titreDocument(numero);
		if (codes.includes(numero)) {
			annonce = `${nom} est déjà dans la comparaison (${codes.length}/${MAX_COMMUNES}).`;
			return;
		}
		if (plein) {
			annonce = MESSAGE_PLEIN;
			return;
		}
		const suivants = avecDocument(codes, numero);
		void naviguer(suivants, async () => {
			saisieDpe = '';
			annonce = `${nom} ajouté à la comparaison (${suivants.length}/${MAX_COMMUNES}).`;
			await focaliserAjout();
		});
	}

	let champDpe = $state<HTMLInputElement>();

	async function ouvrirRecherche() {
		rechercheOuverte = !rechercheOuverte;
		if (!rechercheOuverte) return;
		await tick();
		zoneRecherche?.querySelector<HTMLInputElement>('input[role="combobox"]')?.focus();
	}

	// Audit C-3: an Escape the search leaves unconsumed (empty field, no list) folds the zone and gives the
	// focus back to « Ajouter une commune », as the button would. Read on the window: Svelte delegates the
	// field's own keydown to the root, so only there is its preventDefault already known.
	function replierRecherche(event: KeyboardEvent) {
		if (event.key !== 'Escape' || event.defaultPrevented || !rechercheOuverte) return;
		if (!(event.target instanceof Node) || !zoneRecherche?.contains(event.target)) return;
		event.preventDefault();
		rechercheOuverte = false;
		boutonAjouter?.focus();
	}

	function basculerDifferences(event: Event) {
		differences = (event.currentTarget as HTMLInputElement).checked;
		rafraichirAdresse(lienSelectionNature(nature, codes, differences, designationsLien));
	}

	async function copierLien() {
		try {
			await navigator.clipboard.writeText(window.location.href);
			annonce = `Lien copié. Il reprend les ${textes.objets}, leur ordre et le filtre ; les valeurs sont relues à l’ouverture.`;
		} catch {
			annonce = 'Le lien n’a pas pu être copié. Copiez l’adresse de cette page.';
		}
	}

	/** Printing shows the folded reference and reading note, then gives the reader's state back. */
	function avantImpression() {
		lectureAvantImpression = lecture;
		lecture = true;
	}

	function apresImpression() {
		if (lectureAvantImpression !== null) lecture = lectureAvantImpression;
		lectureAvantImpression = null;
	}

	/** « Recharger la comparaison »: one call in flight at most (T-03-10-03). */
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

<svelte:window onbeforeprint={avantImpression} onafterprint={apresImpression} onkeydown={replierRecherche} />

<!-- T1: title, description and sharing card through the socle. The page stays out of the index (no
     canonical); the card carries the link of the selection, without the filter. -->
<MetaPartage {titre} description={DESCRIPTION} chemin={lienPartage} canonique={false} robots="noindex, follow" />

<!-- The method sentences: around the title without a table, folded with the reading note after it (B5). -->
{#snippet surtitre()}
	<p class="comparer-surtitre">Comparer sans changer le sens des données</p>
{/snippet}
{#snippet intro()}
	<p class="comparer-intro">Les valeurs gardent leur période, leur définition et leur source. Aucun score global.</p>
{/snippet}

<div class="comparer gabarit">
	<FilAriane etapes={ETAPES} />
	<header class="comparer-entete">
		{#if data.etat !== 'prete' && !apercu}{@render surtitre()}{/if}
		<!-- Under 720 px the short title of the nature only (gap to § 6.4 decided on 05/11, UI-012): the names
		     are then read in the header of the table. One of the two spans is displayed at a time, so the
		     accessible name is the visible one. -->
		<h1 class="comparer-titre">
			{#if titreVisible !== textes.h1}<span class="titre-large">{titreVisible}</span>{' '}<span class="titre-court">{textes.h1}</span
				>{:else}{prose.get('h1') ?? titreVisible}{/if}
		</h1>
		{#if apercu}<p class="comparer-intro comparer-promesse">{prose.get('promesse') ?? PROMESSE_COMMUNES}</p
			>{:else if data.etat !== 'prete'}{@render intro()}{:else if nature === 'commune'}<p class="fiche-sous">{SOUS_TITRE_COMMUNES}</p>{/if}
	</header>

	<NatureNav active={nature} comptes={comptesNatures} liens={liensNatures} />

	<!-- T6: beyond 720 px the title of a comparison of communes names them already. -->
	{#if objetsCompares}<p class={['comparer-objets', nature === 'commune' && 'comparer-objets-redite']}>{objetsCompares}</p>{/if}

	<div class={['comparer-depart', apercu && 'comparer-depart-apercu']}>
	<div class="comparer-depart-agir">
	<!-- Readability (2026-11-10): with fewer than two objects the state is said first (what is chosen, what to
	     do), then the add control under it. Texts unchanged; only their place. -->
	{#if data.etat === 'une'}
		<section class="comparer-section comparer-etat" aria-labelledby="comparer-une" aria-busy={occupe}>
			<h2 id="comparer-une" class="comparer-h2">
				{#if data.commune.lien}
					<a href={data.commune.lien}>{data.commune.nom ?? data.commune.code}</a>
				{:else}
					<a href={`/commune/${data.commune.code}`}>{data.commune.nom ?? `Commune INSEE ${data.commune.code}`}</a>
				{/if}
			</h2>
			{#if data.commune.identite}<p class="comparer-identite">{data.commune.identite}</p>{/if}
			<p>{prose.get('une') ?? textes.une}</p>
			<button
				type="button"
				class="bouton-lien comparer-lien"
				aria-label={`Retirer ${data.commune.nom ?? data.commune.code}`}
				disabled={occupe}
				onclick={() => retirer(data.etat === 'une' ? data.commune.code : '')}>Retirer</button
			>
		</section>
	{:else if data.etat === 'vide'}
		<section class="comparer-section comparer-etat" aria-labelledby="comparer-vide">
			<h2 id="comparer-vide" class="comparer-h2">{prose.get('vide') ?? textes.vide}</h2>
			<p>{prose.get('vide-aide') ?? textes.videAide}</p>
		</section>
	{/if}

	<div class="comparer-controles">
		<details id={ID_MODIFIER} class={['det depliant depliant-accent comparer-modifier', codes.length < 2 && 'comparer-modifier-seul']} bind:open={modification}>
			<summary class="comparer-resume">Modifier la sélection</summary>
			<div class="comparer-modifier-corps">
				{#if nature === 'dpe'}
					<form class="comparer-ajout-dpe" method="GET" action="/comparer" novalidate onsubmit={ajouterDocument}>
						{#if codes.length}<input type="hidden" name="dpe" value={codes.join(',')} />{/if}
						{#if differences}<input type="hidden" name="diff" value="1" />{/if}
						<label class="comparer-label" for="comparer-numero-dpe">Numéro du DPE</label>
						<div class="comparer-ajout-ligne">
							<input
								bind:this={champDpe}
								bind:value={saisieDpe}
								id="comparer-numero-dpe"
								class="comparer-champ"
								name="dpe"
								type="text"
								autocomplete="off"
								spellcheck="false"
								disabled={plein || occupe}
								aria-invalid={erreurDpe ? 'true' : undefined}
								aria-describedby={erreurDpe ? 'comparer-aide-dpe comparer-erreur-dpe' : 'comparer-aide-dpe'}
							/>
							<button type="submit" class="bouton-primaire" disabled={plein || occupe}>{textes.ajouter}</button>
						</div>
						<p id="comparer-aide-dpe" class="comparer-aide">{AIDE_NUMERO_DPE}</p>
						{#if erreurDpe}<p id="comparer-erreur-dpe" class="comparer-erreur" role="alert">{prose.get('erreur-dpe') ?? erreurDpe}</p>{/if}
					</form>
				{:else}
					<div class="comparer-ajout-rangee">
						<button
							bind:this={boutonAjouter}
							type="button"
							class="bouton-primaire"
							disabled={plein || occupe}
							aria-expanded={rechercheOuverte}
							aria-describedby={plein ? 'comparer-plein' : undefined}
							onclick={ouvrirRecherche}>{textes.ajouter}</button
						>
					</div>
				{/if}
				{#if plein}<p id="comparer-plein" class="comparer-aide" tabindex="-1">{prose.get('plein') ?? MESSAGE_PLEIN}</p>{/if}
				{#if rechercheOuverte && !plein}
					<div class="comparer-recherche" bind:this={zoneRecherche}>
						{#if nature === 'adresse'}
							<CommuneSearch onselectadresse={ajouterAdresse} submitLabel="Voir les résultats" types={['adresse']} />
						{:else}
							<CommuneSearch onselect={ajouter} submitLabel="Voir les résultats" types={['commune']} />
						{/if}
					</div>
				{/if}
				{#if apercu}
					<!-- C8, T3: ready comparisons to start from (wordings and links in `comparateur-textes.ts`). -->
					<div class="comparer-exemples">
						<p class="comparer-aide">{TITRE_EXEMPLES}</p>
						<div class="comparer-exemples-liens">
							{#each EXEMPLES_COMMUNES as exemple (exemple.lien)}<a class="bouton-secondaire" href={exemple.lien}>{exemple.libelle}</a>{/each}
						</div>
					</div>
				{/if}
				{#if codes.length >= 2}
					<label class="comparer-filtre">
						<input type="checkbox" checked={differences} onchange={basculerDifferences} />
						<span>Différences seulement</span>
					</label>
				{/if}
			</div>
		</details>
		{#if codes.length}
			<button type="button" class="bouton-lien" onclick={copierLien}>Copier le lien</button>
		{/if}
	</div>
	<p class="comparer-annonce" role="status" aria-live="polite">{annonce}</p>
	</div>
	{#if apercu}
		<!-- T4 (decision D2, shape without values): what a comparison looks like, beside the control that starts
		     one. A drawing: no value is written, the table is hidden from assistive technologies. -->
		<aside class="comparer-apercu" aria-labelledby="comparer-apercu-titre">
			<div class="comparer-apercu-cadre">
				<p id="comparer-apercu-titre" class="comparer-apercu-titre">{TITRE_APERCU}</p>
				<table aria-hidden="true">
					<thead>
						<tr><th></th>{#each COLONNES_APERCU as colonne (colonne)}<th>{colonne}</th>{/each}</tr>
					</thead>
					<tbody>
						{#each LIGNES_APERCU as ligne (ligne)}
							<tr><th>{ligne}</th>{#each COLONNES_APERCU as colonne (colonne)}<td><span class="comparer-apercu-trait"></span></td>{/each}</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{@render intro()}
		</aside>
	{/if}
	</div>

	{#if data.selection.messages.length}
		<div class="comparer-section">
			<Notice titre="Lien altéré">
				{#each data.selection.messages as message, i (i)}<p>{prose.get(`message:${i}`) ?? message}</p>{/each}
			</Notice>
		</div>
	{/if}
	{#if data.selection.horsCouverture?.length}
		<div class="comparer-section">
			<Notice titre="Hors couverture">
				{#each data.selection.horsCouverture as message, i (i)}<p>{prose.get(`hors:${i}`) ?? message}</p>{/each}
			</Notice>
		</div>
	{/if}

	{#if data.etat === 'prete'}
		<section class="comparer-section comparer-tableau">
			<CompareTable
				bind:this={table}
				modele={data.modele}
				differencesSeulement={differences}
				{occupe}
				descriptionPlacerPremierId="comparer-reference-aide"
				onretirer={retirer}
				onplacerpremier={placerPremier}
				ondesigner={designer}
				onretirerdesignation={retirerDesignation}
			/>
		</section>
		{#if noteAbsences}<p class="cmp-note">{prose.get('note-absences') ?? noteAbsences}</p>{/if}
		{#if noteEchelle}<p class="cmp-note cmp-note-echelle">{noteEchelle}</p>{/if}
		<div class="cmp-liens"><ListeLiens liens={liensFiches} /></div>
		<details id={ID_LECTURE} class="det depliant depliant-accent comparer-lecture" bind:open={lecture}>
			<summary class="comparer-lecture-resume">
				<h2 id="comparer-reference-titre" class="comparer-h2">
					Référence des écarts : <span>{data.modele.colonnes[0].nom}</span>
				</h2>
			</summary>
			<div class="comparer-reference">
				{@render surtitre()}
				{@render intro()}
				<p id="comparer-reference-aide">
					« Placer en premier » change la référence utilisée pour lire les écarts. L’ordre ne constitue pas un classement.
				</p>
				<nav class="comparer-sommaire" aria-label="Accès aux groupes de comparaison">
					<span>Accès direct :</span>
					<ul>
						{#each data.modele.groupes.filter((groupe) => groupe.lignes.some((ligne) => !differences || !ligne.identiques)) as groupe (groupe.cle)}
							<li>
								<a href={`#comparaison-groupe-${groupe.cle}`} onclick={() => table?.focaliserGroupe(groupe.cle)}>{groupe.titre}</a>
							</li>
						{/each}
					</ul>
				</nav>
			</div>
			<div class="comparer-section">
				<Notice titre="Lire les écarts">
					<p>
						Une différence de taux est affichée en points. Aucun écart n'est calculé quand les périodes, les unités ou
						les définitions diffèrent : la ligne le dit.
					</p>
					{#if nature === 'adresse'}
						<p>Les valeurs de la commune sont rangées à part : elles ne décrivent pas l'adresse.</p>
					{/if}
					{#if nature === 'dpe'}
						<p>{prose.get('lire-ecarts-dpe') ?? LIRE_ECARTS_DPE}</p>
					{/if}
				</Notice>
			</div>
		</details>
	{:else if data.etat === 'panne'}
		<div class="comparer-section">
			<Notice titre={prose.get('panne:titre') ?? data.detail?.titre ?? "Les données de comparaison sont momentanément indisponibles."} variante="warn" role="alert">
				<p>{prose.get('panne:message') ?? data.detail?.message ?? "Le service n'a pas répondu. Aucune valeur n'est affichée plutôt qu'une valeur incomplète."}</p>
				{#if data.conflit}
					<!-- A commune and its arrondissement (422): reloading would refuse again, one of the two must leave. -->
					<button type="button" class="bouton-secondaire" disabled={occupe} onclick={() => retirer(data.conflit!.commune)}
						>Retirer {communeParente(data.conflit.arrondissement)?.nom ?? data.conflit.commune}</button
					>
					<button type="button" class="bouton-secondaire" disabled={occupe} onclick={() => retirer(data.conflit!.arrondissement)}
						>Retirer {libelleArrondissement(data.conflit.arrondissement) ?? data.conflit.arrondissement}</button
					>
				{:else}
					<button
						type="button"
						class="bouton-secondaire comparer-recharger"
						onclick={recharger}
						disabled={rechargement}
						aria-busy={rechargement}>Recharger la comparaison</button
					>
				{/if}
			</Notice>
		</div>
	{/if}
</div>

<style>
	/* A simple page of the direction A (UI-SPEC v4 § 6.0, § 6.4, `pages.mjs:528-540`): `.gabarit`,
	   breadcrumb, h1 44/32, the line under it, then the table. */
	.comparer {
		padding-block: var(--s6) var(--s8);
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.5;
	}

	.comparer-surtitre {
		margin: 0 0 var(--s1);
		color: var(--color-muted);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.comparer-titre {
		margin: 0;
		font-family: var(--font-display);
		font-size: 44px;
		font-weight: 500;
		line-height: 1.12;
		overflow-wrap: anywhere;
	}

	.titre-court {
		display: none;
	}

	.comparer-intro {
		max-width: var(--mesure);
		margin: var(--s2) 0 0;
		overflow-wrap: anywhere;
	}

	.fiche-sous {
		max-width: var(--mesure);
		margin: var(--s3) 0 0;
		color: var(--color-muted);
		font-size: 16px;
		line-height: 1.5;
	}

	/* The compared objects, named once above the table (B5, UI-012). */
	.comparer-objets {
		max-width: var(--mesure);
		margin: var(--s3) 0 0;
		font-weight: 600;
		overflow-wrap: anywhere;
	}

	/* One compact line: « Modifier la sélection », then « Copier le lien ». Open, the panel takes the
	   whole line and « Copier le lien » follows it, so the visual order stays the order of Tab. */
	.comparer-controles {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s2) var(--s4);
		margin-top: var(--s3);
	}

	.comparer-modifier {
		flex: 0 1 auto;
		min-width: 0;
	}

	.comparer-modifier[open] {
		flex-basis: 100%;
	}

	/* The two folds are the shared fold of the socle (`.depliant.depliant-accent`, app.css): its accent summary,
	   its chevron, its 44 px target and its print rule. `det` stays on them as the name the specs know. */

	.comparer-modifier-corps {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--s2);
		margin-top: var(--s3);
		padding: var(--s4);
		border-radius: var(--radius-ligne);
		background: var(--color-panneau);
	}

	.comparer-modifier-corps > .comparer-aide {
		margin: 0;
	}

	/* Fewer than two objects: there is nothing to « modify » yet. The fold stays in the page, open (the add
	   control lives in it), but neither its summary nor its panel is drawn: the control follows the sentence
	   that asks for it. `details.` outweighs the summary rule of the socle. */
	details.comparer-modifier-seul > summary {
		display: none;
	}

	.comparer-modifier-seul .comparer-modifier-corps {
		margin-top: 0;
		padding: 0;
		border-radius: 0;
		background: none;
	}

	/* The reference and the reading note, folded after the values. */
	.comparer-lecture {
		margin-top: var(--s5);
	}

	.comparer-lecture-resume .comparer-h2 {
		margin: 0;
		font-family: inherit;
		font-size: inherit;
		font-weight: inherit;
		line-height: inherit;
	}

	/* The add control and, in the empty comparator of communes, its example: one row, wrapped on a phone. */
	.comparer-ajout-rangee {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s4);
	}

	.comparer-exemples {
		display: flex;
		flex-direction: column;
		gap: var(--s2);
		margin-top: var(--s2);
	}

	.comparer-exemples > .comparer-aide {
		margin: 0;
	}

	.comparer-exemples-liens {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
	}

	/* The empty comparator of communes: the control on the left, the shape of the result on the right. */
	.comparer-depart-apercu {
		display: grid;
		grid-template-columns: minmax(0, 4fr) minmax(0, 7fr);
		gap: var(--s7);
		align-items: start;
	}

	.comparer-apercu {
		margin-top: var(--s5);
	}

	.comparer-apercu-cadre {
		overflow: hidden;
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-panneau);
	}

	.comparer-apercu-titre {
		margin: 0;
		padding: var(--s2) var(--s4);
		background: var(--color-soft);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.comparer-apercu table {
		width: 100%;
		border-collapse: collapse;
		font-size: 16px;
		line-height: 1.4;
	}

	.comparer-apercu th,
	.comparer-apercu td {
		padding: var(--s2) var(--s4);
		border-top: 1px solid var(--color-filet-pale);
		font-weight: 400;
		text-align: right;
	}

	.comparer-apercu thead th {
		border-top: 0;
		font-weight: 600;
	}

	.comparer-apercu tbody th {
		text-align: left;
	}

	/* A value to come: a pale stroke, never a figure. */
	.comparer-apercu-trait {
		display: inline-block;
		width: 56px;
		height: 12px;
		border-radius: var(--radius-badge);
		background: var(--color-filet-pale);
		vertical-align: middle;
	}

	.comparer-apercu .comparer-intro {
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	@media (min-width: 721px) {
		.comparer-objets-redite {
			display: none;
		}
	}

	.comparer-filtre {
		display: inline-flex;
		align-items: center;
		gap: var(--s2);
		min-height: 44px;
		cursor: pointer;
	}

	.comparer-filtre input {
		width: 20px;
		height: 20px;
		margin: 0;
		accent-color: var(--color-accent);
	}

	.comparer-aide {
		max-width: var(--mesure);
		margin: var(--s2) 0 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.comparer-ajout-dpe {
		box-sizing: border-box;
		width: 100%;
		max-width: 620px;
	}

	.comparer-label {
		display: block;
		margin-bottom: var(--s2);
		font-weight: 600;
	}

	.comparer-ajout-ligne {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
	}

	.comparer-champ {
		box-sizing: border-box;
		flex: 1 1 200px;
		min-width: 0;
		min-height: 44px;
		padding: 0 var(--s3);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-size: 16px;
		font-weight: 400;
		line-height: inherit;
		font-variant-numeric: tabular-nums;
	}

	.comparer-erreur {
		max-width: var(--mesure);
		margin: var(--s2) 0 0;
		color: var(--color-warn);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.comparer-recherche {
		box-sizing: border-box;
		width: 100%;
		max-width: 620px;
	}

	.comparer-annonce {
		max-width: var(--mesure);
		margin: var(--s2) 0 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.comparer-annonce:empty {
		margin: 0;
	}

	.comparer-section {
		margin-top: var(--s5);
	}

	/* The sentence on absences and the links to the compared pages (`style.css:289`, `pages.mjs:536-537`). */
	.cmp-note {
		max-width: var(--mesure);
		margin: var(--s4) 0 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.cmp-liens {
		max-width: 620px;
		margin-top: var(--s4);
	}

	.comparer-reference {
		margin-top: var(--s2);
	}

	.comparer-reference p {
		max-width: var(--mesure);
		margin: 0;
	}

	.comparer-reference .comparer-intro {
		margin-bottom: var(--s2);
	}

	.comparer-sommaire {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--s2) var(--s4);
		margin-top: var(--s3);
		font-size: 16px;
		line-height: 1.5;
	}

	.comparer-sommaire > span {
		font-weight: 600;
	}

	.comparer-sommaire ul {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s1) var(--s4);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* Links in running text: underlined, 1 px, 3 px offset (UI-SPEC v4 § 3.0). */
	.comparer-sommaire a,
	.comparer-h2 a {
		color: var(--color-ink);
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.comparer-sommaire a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
	}

	.comparer-sommaire a:hover,
	.comparer-h2 a:hover {
		text-decoration-thickness: 2px;
	}

	.comparer-section > p {
		max-width: var(--mesure);
		margin: 0;
		overflow-wrap: anywhere;
	}

	.comparer-recharger {
		margin-top: var(--s4);
	}

	.comparer-h2 {
		margin: 0 0 var(--s2);
		font-family: var(--font-display);
		font-size: 30px;
		font-weight: 500;
		line-height: 1.2;
		overflow-wrap: anywhere;
	}

	.comparer-section .comparer-identite {
		margin-bottom: var(--s2);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	@media (max-width: 720px) {
		.comparer {
			padding-block: var(--s3) var(--s7);
		}

		.comparer-titre {
			font-size: 32px;
		}

		.comparer-h2 {
			font-size: 26px;
		}

		/* Mobile gap to § 6.4, decided by the orchestrator on 05/11 to keep UI-012 (a served value in the
		   first screen at 375 × 812): the breadcrumb keeps its parent link only, the sentence of the
		   mock-up is not shown and the h1 is the short title of the nature, the names being read in the
		   header of the table (option C, then B as measured: 871 px > 812 with C). Beyond 720 px the
		   form of § 6.4 is whole. */
		.comparer :global(.ariane li:last-child),
		.comparer-entete .fiche-sous,
		.titre-large {
			display: none;
		}

		.titre-court {
			display: inline;
		}

		.comparer :global(.ariane) {
			margin-bottom: var(--s2);
		}

		.comparer-depart-apercu {
			grid-template-columns: minmax(0, 1fr);
			gap: 0;
		}

		/* Tighter steps above the table: the values come into the first screen at 375 px (B5). */
		.fiche-sous,
		.comparer-objets,
		.comparer-controles {
			margin-top: var(--s2);
		}

		.comparer-controles {
			gap: var(--s1) var(--s3);
		}

		.comparer-tableau {
			margin-top: var(--s4);
		}

		/* The table spans the width of the screen: two communes fit at 375 px. The frame bleeds, not
		   the region, so the header band and the right-edge hint stay on the table (03-18). */
		.comparer-tableau :global(.compare-cadre) {
			margin-inline: calc(-1 * var(--s4));
		}
	}

	@media print {
		.comparer {
			padding: 0;
		}

		/* The folded reading note prints even closed, without JavaScript (`beforeprint` only adds the open state). */
		.comparer-lecture::details-content {
			content-visibility: visible;
			display: block;
		}

		.comparer-controles,
		.comparer-ajout-dpe,
		.comparer-aide,
		.comparer-recherche,
		.comparer-annonce,
		.comparer-lien,
		.comparer-recharger,
		.comparer :global(dialog.source-panel) {
			display: none;
		}
	}
</style>
