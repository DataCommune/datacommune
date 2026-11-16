<script lang="ts">
	// Comparison table of 2 to 4 communes (UI-SPEC « /comparer », D-48, D-49). It renders the model
	// of `composeComparaison` and computes nothing: each cell is the SERVED value or state of the
	// criterion through `StateValue`, its period and source, its explanation control, and for columns
	// 2 to 4 the gap line the API served (gap, reason for no gap, or verdict of a non-numeric value).
	//
	// Layout (03-18, R-2): the page scrolls, the region only scrolls horizontally, the criteria column
	// sticks to its left. A container that scrolls horizontally is a scroll container on both axes,
	// so its `thead` cannot stick to the viewport: the column names stay in view through a duplicated
	// header band. The band copies the names (cut, followed by a discriminant always shown) and the
	// widths of the `thead` (widths read on the `th`, never written), takes no room (its sticky anchor has no height, the table
	// never moves when it shows), and is hidden from assistive technologies: the `thead` stays the
	// only semantics (D-49 kept). Criteria 112px and communes 124px up to 720px (two communes fit at
	// 375px), 200px and 224px beyond. Printed whole, without scrolling, band or sticky cells. One
	// source panel serves every cell: the DOM does not carry a <dialog> per cell.
	//
	// 04-11 (D-102): the header of an address column carries the designation of a DPE
	// (`DesignationDpe`); the rows of the designated documents follow the rows of « Documents à
	// l'adresse », preceded by the sentence of the contract; a column without designation says
	// « Aucun DPE désigné » in muted text, without a state badge (it is not a state of a datum).
	//
	// Lot 6 of phase 4.9 (amends UI-013, UI-027): a row whose metadata are factorised has one explanation
	// control, in its header, and its panel covers every commune; any other row keeps one per cell.
	//
	// 04.8-05 (UI-SPEC v4 § 6.4, `style.css:297-305`): the `.cmp` form of the mock-up. The region is named
	// « Tableau de comparaison »; the names in Newsreader 22/18 above a line; one `tbody` per group, its
	// title a `th scope="rowgroup"` in Inter 600 16; the criteria in Inter 400; the values in Newsreader
	// 20/16 with tabular figures; a pale rule between two rows. Behaviour, band and stickiness unchanged.
	import { tick, untrack } from 'svelte';
	import {
		factoriserMetadonnees,
		type ComparaisonModel,
		type CelluleAffichee,
		type LigneComparee
	} from '$lib/data/commune-comparison';
	import type { EntreeVolet, SourceKpi, ValeurServie } from '$lib/data/commune-kpi';
	import { DEPARTEMENTS } from '$lib/data/departements';
	import { LIBELLE_NON_AFFICHABLE, PHRASES_MOTIFS_NON_PUBLIES } from '$lib/data/etats-libelles';
	import SourceButton from '$lib/components/fiche/SourceButton.svelte';
	import SourcePanel from '$lib/components/fiche/SourcePanel.svelte';
	import StateValue from '$lib/components/fiche/StateValue.svelte';
	import RepereEchelle from '$lib/components/ds/RepereEchelle.svelte';
	import IconeCategorie from '$lib/components/ds/IconeCategorie.svelte';
	import type { IdPartie } from '$lib/data/questions';
	import { SIGNE_DIFFERENT, SIGNE_IDENTIQUE, nombreDeLignes } from '$lib/data/comparateur-textes';
	import DesignationDpe from './DesignationDpe.svelte';

	interface Props {
		modele: ComparaisonModel;
		/** « Différences seulement »: the criteria served `identiques` are hidden. */
		differencesSeulement?: boolean;
		/** A new comparison is being read: the current columns stay, marked busy. */
		occupe?: boolean;
		onretirer?: (code: string) => void;
		onplacerpremier?: (code: string) => void;
		descriptionPlacerPremierId?: string;
		/** 04-11: a DPE designated on the address `code` (the page writes the link). */
		ondesigner?: (code: string, numero: string) => void;
		onretirerdesignation?: (code: string) => void;
	}

	let {
		modele,
		differencesSeulement = false,
		occupe = false,
		onretirer,
		onplacerpremier,
		descriptionPlacerPremierId,
		ondesigner,
		onretirerdesignation
	}: Props = $props();

	const colonnes = $derived(modele.colonnes);
	// 04-07: the same table for the addresses (D-102, same nature only). An address column links to
	// its page and carries « {CP} {Commune} »; its band shows the street on two lines at most, then
	// the postal code, never cut, when the columns' codes differ (UI-SPEC « Bande d'en-tête »).
	const adresses = $derived(modele.nature === 'adresse');
	// 04-10: a DPE column links to its document, carries its declared address as published and its
	// badges; its band shows the number whole, never cut (UI-SPEC « Bande d'en-tête collante »).
	const documents = $derived(modele.nature === 'dpe');
	const objets = $derived(adresses ? 'adresses' : documents ? 'documents DPE' : 'communes');
	// C3 (inventory of 2026-11-10, arbitrated by Ianis on 2026-11-10: « oui à la période et à la source
	// derrière le bouton d'info »): the face of the comparator of communes and of DPE documents shows the
	// label, the value and the gap; the period and the source are read in the panel, which already says both
	// (the row panel when the metadata of a row are common, the panel of each cell otherwise). The addresses
	// keep their dated wording under each value: their panel does not word the date the same way yet.
	// A DPE designated on an address reads like the same document in its own comparator (R06-09/7): no metadata.
	const metaSurLaFace = $derived(adresses);
	// Readability (2026-11-10): a comparison of communes is a table of numbers. From 721 px its value columns
	// are read on their right edge (names, values, gaps), so figures line up by magnitude; the addresses and
	// the documents, whose cells carry sentences and dated lines, keep the left edge.
	const alignees = $derived(!adresses && !documents);
	// On a phone two communes are read stacked (the label on its line, the two values under it); three or
	// four keep the scrolling table: in 84 px a column header and a value with its unit do not hold.
	// A gap is read on the line of its value while a column has room for it: with four communes (208 px
	// a column at 1440 px) the slot widened the table past the page, so the gap stays under its value.
	const ecartsEnLigne = $derived(alignees && modele.colonnes.length <= 3);
	const empile = $derived(alignees && modele.colonnes.length <= 2);
	/** UI-SPEC « /comparer », group 4 of the addresses (CMP-01). */
	const PHRASE_REFERENCE_COMMUNE = "Ces valeurs décrivent la commune, pas l'adresse.";
	/** UI-SPEC « Désignation » (D-102, 04-11): before the rows of the designated documents. */
	const PHRASE_DESIGNATION = "Désigné par vous pour comparer ; ce document n'est rattaché à aucun logement.";
	/** UI-SPEC « Désignation »: a column without designation, muted, without a badge. */
	const AUCUN_DPE_DESIGNE = 'Aucun DPE désigné';
	// Keep served ISO dates whole on narrow columns; wrapping remains possible around each date.
	const metaFragments = (texte: string) => texte.split(/(\d{4}-\d{2}-\d{2})/);
	const isDate = (texte: string) => /^\d{4}-\d{2}-\d{2}$/.test(texte);
	/**
	 * D-15 FRONT WR-05: an absence whose served reason has a decided sentence (04-DECISIONS-TEXTES rows
	 * 44, 45, 49 to 51, 56) says it in the cell: « Aucun DPE depuis juillet 2021 à cette adresse… » is a zero
	 * observed, never « Non publiée » alone. Other absences keep the badge, the sentence in the panel.
	 */
	function phraseDecidee(etat: CelluleAffichee['etat']): boolean {
		return etat?.kind === 'absence' && Object.hasOwn(PHRASES_MOTIFS_NON_PUBLIES, etat.motif);
	}
	const groupes = $derived(
		modele.groupes
			.map((groupe) => ({
				...groupe,
				lignes: differencesSeulement ? groupe.lignes.filter((ligne) => !ligne.identiques) : groupe.lignes
			}))
			.filter((groupe) => groupe.lignes.length > 0)
	);
	const masquees = $derived(
		differencesSeulement ? modele.groupes.flatMap((groupe) => groupe.lignes).filter((ligne) => ligne.identiques).length : 0
	);
	const horsCouverture = $derived(colonnes.filter((colonne) => colonne.horsCouverture).map((colonne) => colonne.nom));
	// The band cuts the names: what tells two columns apart stays visible after the name (D-15 CR-01),
	// the departement number, or the INSEE code when another column shares that departement.
	const discriminants = $derived.by(() => {
		if (documents) return colonnes.map(() => null);
		if (adresses) {
			// The postal code when the codes differ, else the commune when the communes differ: one
			// postal code covers several communes (85000), two « 1 rue de la Gare » stay apart (D-15
			// FRONT WR-06).
			const codesPostaux = new Set(colonnes.map((colonne) => colonne.codePostal ?? ''));
			const communes = new Set(colonnes.map((colonne) => colonne.commune ?? ''));
			return colonnes.map((colonne) =>
				codesPostaux.size > 1 ? (colonne.codePostal ?? null) : communes.size > 1 ? (colonne.commune ?? null) : null
			);
		}
		return colonnes.map((colonne) =>
			colonnes.filter((autre) => autre.departement === colonne.departement).length > 1 ? colonne.code : colonne.departement
		);
	});
	// « Différences seulement » hides only what the API served as identical. One commune out of
	// coverage makes no criterion identical in every column (03-09): the filter then hides nothing,
	// and the page says why instead of looking broken.
	const noteFiltre = $derived.by(() => {
		if (!differencesSeulement) return '';
		if (masquees > 0) {
			return masquees === 1
				? '1 critère identique dans toutes les colonnes est masqué.'
				: `${masquees} critères identiques dans toutes les colonnes sont masqués.`;
		}
		if (horsCouverture.length) {
			const noms = horsCouverture.join(' et ');
			return `Aucun critère n’est masqué : ${noms} ${horsCouverture.length > 1 ? 'sont' : 'est'} hors couverture, donc aucun critère n’est identique dans toutes les colonnes.`;
		}
		return 'Aucun critère n’est identique dans toutes les colonnes : rien n’est masqué.';
	});

	let entetes = $state<Record<string, HTMLTableCellElement>>({});
	let titresGroupes = $state<Record<string, HTMLTableCellElement>>({});
	// C2 (inventory of 2026-11-10; frame of the orchestrator on the reading rule of Ianis: « Repères » alone
	// open on arrival, comparison of communes only). « Repères » keeps its plain title and is always shown,
	// so the first values stay where they were on a phone (UI-012); each category after it is a native
	// `details` in its title row, closed on arrival. Its rows follow the [open] state in CSS only: it works
	// without JavaScript, and they are printed whatever that state.
	let plis = $state<Record<string, HTMLDetailsElement>>({});
	const repliable = (cle: string) => !adresses && !documents && cle !== 'reperes';
	let designations = $state<Record<string, ReturnType<typeof DesignationDpe>>>({});
	// D-15 FRONT WR-02: an open designation group is shown in a full-width row under the header,
	// never inside a column header; the row exists only while a group is open.
	let groupesOuverts = $state<Record<string, boolean>>({});
	// R5 (plan 04.5-13): the detail level of a group (the yearly totals of the documents), closed by
	// default. Its rows follow the native [open] state of its <details> in CSS only, so it works without
	// JavaScript, and they are printed whatever that state (no script state, no `hidden` attribute).

	/** Ids of the detail rows of a group, controlled by its summary. */
	const idsDetail = (groupe: { cle: string; lignes: LigneComparee[] }) =>
		groupe.lignes.filter((ligne) => ligne.detail).map((ligne) => idLigne(groupe.cle, ligne.cle)).join(' ');
	const idLigne = (groupe: string, cle: string) => `comparaison-ligne-${groupe}-${cle}`;
	let zoneDesignation = $state<HTMLTableCellElement | null>(null);
	const designationOuverte = $derived(colonnes.some((colonne) => groupesOuverts[colonne.code]));
	let coin = $state<HTMLTableCellElement>();
	let region = $state<HTMLDivElement>();
	let tableau = $state<HTMLTableElement>();
	let teteTableau = $state<HTMLTableSectionElement>();
	let panneau = $state<ReturnType<typeof SourcePanel>>();
	let actif = $state<{ titre: string; entrees: EntreeVolet[]; brut: (ValeurServie & { objet?: string })[]; source: SourceKpi | null }>({
		titre: '',
		entrees: [],
		brut: [],
		source: null
	});

	// Geometry read on the rendered table (03-18): horizontal scroll of the region, widths of the
	// `th` (corner first), height of the site header, and whether the real `thead` left the window.
	let defilementGauche = $state(0);
	let defilable = $state(false);
	let fin = $state(true);
	let largeurRegion = $state(0);
	let largeurs = $state<number[]>([]);
	let hauteurEnTeteSite = $state(0);
	let bande = $state<HTMLDivElement>();
	let hauteurBande = $state(0);
	let enTeteSortie = $state(false);
	let tableauEnVue = $state(true);
	const bandeVisible = $derived(enTeteSortie && tableauEnVue);

	/** Columns whose right edge lies beyond the visible width of the region (none when it fits). */
	const colonnesMasquees = $derived.by(() => {
		if (!defilable || largeurs.length !== colonnes.length + 1) return [] as string[];
		let droite = largeurs[0];
		return colonnes.filter((_, index) => (droite += largeurs[index + 1]) > largeurRegion + 1).map((colonne) => colonne.nom);
	});
	const noteDefilement = $derived.by(() => {
		const noms = colonnesMasquees;
		if (!noms.length) return `${colonnes.length} ${objets} : faites défiler le tableau vers la droite pour voir toutes les colonnes.`;
		const liste = noms.length === 1 ? noms[0] : `${noms.slice(0, -1).join(', ')} et ${noms[noms.length - 1]}`;
		return `${colonnes.length} ${objets} : faites défiler le tableau vers la droite pour voir ${liste}.`;
	});

	function mesurer() {
		if (!region) return;
		const cellules = [coin, ...colonnes.map((colonne) => entetes[colonne.code])];
		largeurs = cellules.map((th) => (th ? th.getBoundingClientRect().width : 0));
		defilementGauche = region.scrollLeft;
		largeurRegion = region.clientWidth;
		defilable = region.scrollWidth > region.clientWidth + 1;
		fin = region.scrollLeft + region.clientWidth >= region.scrollWidth - 1;
		// The band sticks under the site header only while that header sticks: the public one does not
		// since 04.8-02 (A-01), the band then sticks at the top of the window.
		const enTete = document.querySelector('header.site-header');
		hauteurEnTeteSite = enTete && getComputedStyle(enTete).position === 'sticky' ? enTete.getBoundingClientRect().height : 0;
		hauteurBande = bande ? bande.getBoundingClientRect().height : 0;
	}

	function installer(zone: HTMLDivElement, table: HTMLTableElement, tete: HTMLTableSectionElement): () => void {
		mesurer();
		let observateur: IntersectionObserver | undefined;
		const observer = () => {
			observateur?.disconnect();
			// jsdom has no IntersectionObserver: the band then stays hidden and the table is complete.
			if (typeof IntersectionObserver === 'undefined') return;
			observateur = new IntersectionObserver(
				(entrees) => {
					for (const entree of entrees) {
						const haut = entree.rootBounds?.top ?? hauteurEnTeteSite;
						if (entree.target === tete) enTeteSortie = !entree.isIntersecting && entree.boundingClientRect.bottom <= haut + 1;
						else tableauEnVue = entree.isIntersecting;
					}
				},
				{ rootMargin: `-${hauteurEnTeteSite.toFixed(0)}px 0px 0px 0px`, threshold: 0 }
			);
			observateur.observe(tete);
			observateur.observe(table);
		};
		observer();
		const surRedimensionnement = () => {
			const avant = hauteurEnTeteSite;
			mesurer();
			if (hauteurEnTeteSite !== avant) observer();
		};
		window.addEventListener('resize', surRedimensionnement);
		let tailles: ResizeObserver | undefined;
		if (typeof ResizeObserver !== 'undefined') {
			tailles = new ResizeObserver(() => mesurer());
			tailles.observe(table);
			tailles.observe(zone);
		}
		return () => {
			window.removeEventListener('resize', surRedimensionnement);
			tailles?.disconnect();
			observateur?.disconnect();
		};
	}

	$effect(() => {
		// Measured again when the columns change (added, removed, reordered).
		void colonnes.map((colonne) => colonne.code).join(',');
		const zone = region;
		const table = tableau;
		const tete = teteTableau;
		if (!zone || !table || !tete) return;
		return untrack(() => installer(zone, table, tete));
	});

	$effect(() => {
		// A focused control of the table never lands under the site header plus the band (D-15 WR-02):
		// the page's scroll padding covers both, measured, never less than its own default.
		if (!hauteurBande) return;
		const racine = document.documentElement;
		const inline = racine.style.scrollPaddingTop;
		const defaut = getComputedStyle(racine).scrollPaddingTop;
		racine.style.scrollPaddingTop = `max(${defaut}, ${hauteurEnTeteSite + hauteurBande + 8}px)`;
		return () => {
			racine.style.scrollPaddingTop = inline;
		};
	});

	/** Focus on the header of a column (after « Retirer » or « Placer en premier »). */
	export function focaliserColonne(code: string): boolean {
		const entete = entetes[code];
		if (!entete) return false;
		entete.focus();
		return true;
	}

	/** Focus on a displayed group reached from the page summary; a folded category opens first. */
	export function focaliserGroupe(cle: string): boolean {
		const titre = titresGroupes[cle];
		if (!titre) return false;
		const pli = plis[cle];
		if (pli) pli.open = true;
		titre.focus();
		return true;
	}

	/** Focus on the designation control of an address column (after a designation or its removal). */
	export function focaliserDesignation(code: string): boolean {
		return designations[code]?.focaliser() ?? false;
	}

	function departement(code: string): string {
		const nom = DEPARTEMENTS.find((item) => item.code === code)?.nom;
		return nom ? `${nom} (${code})` : `Département ${code}`;
	}

	// B6 (counter-review I4): the panel is titled by the row and `nomAccessible` of its column, like its button.
	async function ouvrirSource(ligne: LigneComparee, cellule: CelluleAffichee, nom: string, declencheur: HTMLButtonElement) {
		actif = { titre: `${ligne.libelle} · ${nom}`, entrees: cellule.volet, brut: cellule.brut, source: cellule.source };
		await tick();
		panneau?.ouvrir(declencheur);
	}

	/** The label cut before its last word: `[debut, fin]`, `debut` keeping its trailing space. */
	function derniersMots(libelle: string): [string, string] {
		const coupure = libelle.lastIndexOf(' ') + 1;
		return [libelle.slice(0, coupure), libelle.slice(coupure)];
	}

	/** Name of a column inside a row panel: the code tells two homonymous communes apart. */
	const nomDansVolet = (colonne: (typeof colonnes)[number]) =>
		adresses || documents ? nomAccessible(colonne) : `${colonne.nom} (${colonne.code})`;

	/**
	 * What the panels of the cells of a row say, in one panel: an entry every cell words the same is
	 * written once; any other (the Effectif of a commune) keeps one entry per commune that has it. Nothing
	 * a cell panel said is dropped, and nothing is merged by assumption: the entries are compared as served.
	 */
	function voletDeLigne(ligne: LigneComparee): EntreeVolet[] {
		const termes = [...new Set(ligne.cellules.flatMap((cellule) => cellule.volet.map((entree) => entree.terme)))];
		return termes.flatMap((terme) => {
			const parCellule = ligne.cellules.map((cellule) => cellule.volet.find((entree) => entree.terme === terme));
			const [premiere] = parCellule;
			if (premiere && parCellule.every((entree) => JSON.stringify(entree) === JSON.stringify(premiere))) return [premiere];
			return parCellule.flatMap((entree, index) =>
				entree ? [{ ...entree, terme: `${terme} · ${nomDansVolet(colonnes[index])}` } as EntreeVolet] : []
			);
		});
	}

	async function ouvrirLigne(ligne: LigneComparee, source: SourceKpi, declencheur: HTMLButtonElement) {
		actif = {
			titre: ligne.libelle,
			entrees: voletDeLigne(ligne),
			brut: ligne.cellules.flatMap((cellule, index) =>
				cellule.brut.map((servie) => ({ ...servie, objet: nomDansVolet(colonnes[index]) }))
			),
			source
		};
		await tick();
		panneau?.ouvrir(declencheur);
	}

	/** B6 (UI-014): the displayed wording of a period when the model gives one (addresses), else the period. */
	function periodeDe(cellule: CelluleAffichee): string | null {
		return cellule.periodeAffichee !== undefined ? cellule.periodeAffichee : cellule.periode;
	}

	function etatDe(cellule: CelluleAffichee): string {
		if (cellule.sansDesignation) return 'sans-designation';
		if (!cellule.etat) return 'non-affiche';
		return cellule.etat.kind === 'valeur' ? 'value' : cellule.etat.etat;
	}

	/** A cell of a column out of coverage that is itself out of coverage: said once, in the header (C-03). */
	function horsCouvertureDeColonne(cellule: CelluleAffichee, index: number): boolean {
		return colonnes[index]?.horsCouverture === true && cellule.etat?.kind === 'absence' && cellule.etat.etat === 'out_of_coverage';
	}

	/** Name of a column for its actions: an address also by « {CP} {Commune} » (homonymous streets). */
	function nomAccessible(colonne: (typeof colonnes)[number]): string {
		return colonne.identite && adresses ? `${colonne.nom}, ${colonne.identite}` : colonne.nom;
	}

	// C4 (inventory of 2026-11-10): the model words each gap « +100 €/m² par rapport à {first column} ». The
	// reference is the same in every cell, so the eye reads it once, in the corner of the table; each cell
	// keeps the words for a screen reader, which reads a cell without the corner. The served sentence is
	// only cut where it names the first column; any other wording is shown whole.
	const suiteReference = $derived(` par rapport à ${colonnes[0]?.nom ?? ''}`);
	function ecartSeul(texte: string): string | null {
		return colonnes[0] && texte.endsWith(suiteReference) ? texte.slice(0, -suiteReference.length) : null;
	}

	// Direction A (Ianis, 2026-11-10; D4, T7 to T11 carried on the orchestrator's reading, to be reread): in a
	// comparison of communes a row says its unit once, after its label, when every served value of the row
	// carries that unit. The cells keep theirs in the page (screen readers, frozen references): it is only
	// not drawn. Nothing is computed and no served text is rewritten.
	function uniteDite(ligne: LigneComparee): string | null {
		if (!alignees || !ligne.unite) return null;
		const unites = ligne.cellules.flatMap((cellule) => (cellule.etat?.kind === 'valeur' ? [cellule.etat.unite ?? null] : []));
		return unites.length > 0 && unites.every((unite) => unite === ligne.unite) ? ligne.unite : null;
	}

	/** A gap « +100 €/m² » cut before the unit its row already says: `[gap, unit]`, or null when it has none. */
	function ecartSansUnite(seul: string, unite: string | null): [string, string] | null {
		return unite && seul.endsWith(` ${unite}`) ? [seul.slice(0, -unite.length - 1), unite] : null;
	}

	/** T7, T8: the sign drawn for a verdict of the communes; its sentence stays in the cell. */
	function signeDe(ecart: NonNullable<CelluleAffichee['ecart']>): string | undefined {
		if (!alignees || ecart.kind !== 'verdict') return undefined;
		return ecart.texte.startsWith('Valeur différente') ? SIGNE_DIFFERENT : SIGNE_IDENTIQUE;
	}

	/** T9: under an absent value the badge already says the absence; the reason of the gap is not drawn. */
	const motifTu = (cellule: CelluleAffichee) => alignees && cellule.ecart?.kind === 'motif' && cellule.etat?.kind === 'absence';
	/** A gap or a sign drawn on the line of its value fills the slot at its right; any other cell leaves it free. */
	const ecartSurLaLigne = (cellule: CelluleAffichee) => !!cellule.ecart && cellule.ecart.kind !== 'motif';

	const px = (valeur: number | undefined) => (valeur ? `${valeur}px` : undefined);
</script>

{#if noteFiltre}
	<p class="compare-filtre" role="status">{noteFiltre}</p>
{/if}

<p class="compare-defilement" hidden={!defilable}>{noteDefilement}</p>

<div
	class="compare-cadre"
	data-defilable={defilable}
	data-fin={fin}
	style={`--compare-bande-top: ${hauteurEnTeteSite}px; --compare-ancre-offset: ${hauteurEnTeteSite + hauteurBande + 16}px`}
>
	<div class="compare-bande-ancre">
		<div
			class="compare-bande"
			class:compare-bande-adresses={adresses}
			class:compare-bande-documents={documents}
			class:compare-bande-nombres={alignees}
			class:compare-bande-empilee={empile}
			aria-hidden="true"
			style:visibility={bandeVisible ? 'visible' : 'hidden'}
			bind:this={bande}
		>
			<span class="compare-bande-coin" style:width={px(largeurs[0])}>Critère</span>
			<div class="compare-bande-piste" style:transform={`translateX(${-defilementGauche}px)`}>
				{#each colonnes as colonne, index (colonne.code)}
					<span class="compare-bande-cellule" data-code={colonne.code} style:width={px(largeurs[index + 1])}>
						<span class="compare-bande-nom">{colonne.nomBande ?? colonne.nom}</span>
						{#if discriminants[index]}<span class="compare-bande-discriminant">({discriminants[index]})</span>{/if}
					</span>
				{/each}
			</div>
		</div>
	</div>

	<!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access is required for the scroll region) -->
	<div
		class="compare-region"
		role="region"
		tabindex="0"
		aria-label="Tableau de comparaison"
		aria-busy={occupe}
		style={`--colonnes: ${colonnes.length}`}
		bind:this={region}
		onscroll={mesurer}
	>
		<table class="cmp compare-table" class:compare-nombres={alignees} class:compare-ecarts-en-ligne={ecartsEnLigne} class:compare-empile={empile} data-colonnes={colonnes.length} bind:this={tableau}>
			<caption class="compare-caption">Comparaison de {colonnes.map((colonne) => colonne.nom).join(', ')}</caption>
			<colgroup>
				<col class="compare-col-critere" />
				{#each colonnes as colonne (colonne.code)}<col class="compare-col-commune" />{/each}
			</colgroup>
			<thead bind:this={teteTableau}>
				<tr>
					<th scope="col" class="compare-coin" bind:this={coin}>
						<span class="compare-coin-mot">Critère</span>
						<!-- C4: the reference of every gap, once. Wording validated by Ianis on 2026-11-10 (the words
						     of each gap line). Hidden from screen readers, which hear them in each cell. -->
						{#if colonnes.length > 1}
							<span class="compare-reference-ecarts" aria-hidden="true">Écarts par rapport à {colonnes[0].nom}</span>
						{/if}
					</th>
					{#each colonnes as colonne, index (colonne.code)}
						<th
							scope="col"
							class="compare-commune"
							data-code={colonne.code}
							tabindex="-1"
							bind:this={entetes[colonne.code]}
						>
							{#if colonne.lien}
								<a class="compare-nom" href={colonne.lien}>{colonne.nom}</a>
								{#if colonne.identite}<span class="compare-identite">{colonne.identite}</span>{/if}
								{#if colonne.badges?.length}
									<span class="compare-badges">
										{#each colonne.badges as badge (badge)}<span class="compare-badge">{badge}</span>{/each}
									</span>
								{/if}
							{:else}
								<a class="compare-nom" href={`/commune/${colonne.code}`}>{colonne.nom}</a>
								<span class="compare-identite"><span class="chiffre">{colonne.code}</span> · {departement(colonne.departement)}</span>
							{/if}
							{#if colonne.horsCouverture}
								<StateValue
									etat={{ kind: 'absence', etat: 'out_of_coverage', motif: colonne.hebergement.motif ?? '', effectif: null }}
									aide={false}
								/>
								<span class="compare-hc-note">Aucune valeur : ne vaut pas zéro.</span>
							{/if}
							<span class="compare-actions">
								{#if index > 0}
									<button
										type="button"
										class="bouton-lien compare-action"
										aria-label={`Placer ${nomAccessible(colonne)} en premier`}
										aria-describedby={descriptionPlacerPremierId}
										disabled={occupe}
										onclick={() => onplacerpremier?.(colonne.code)}>Placer en premier</button
									>
								{/if}
								<button
									type="button"
									class="bouton-lien compare-action"
									aria-label={`Retirer ${nomAccessible(colonne)}`}
									disabled={occupe}
									onclick={() => onretirer?.(colonne.code)}>Retirer</button
								>
							</span>
							{#if adresses && (colonne.designable || colonne.designation)}
								<DesignationDpe
									bind:this={designations[colonne.code]}
									banId={colonne.code}
									adresse={nomAccessible(colonne)}
									designation={colonne.designation ?? null}
									{occupe}
									cible={zoneDesignation}
									onbasculer={(ouvert) => (groupesOuverts[colonne.code] = ouvert)}
									ondesigner={(numero) => ondesigner?.(colonne.code, numero)}
									onretirer={() => onretirerdesignation?.(colonne.code)}
								/>
							{/if}
						</th>
					{/each}
				</tr>
			</thead>
			{#if designationOuverte}
				<tbody class="compare-designation-zone" data-designation-zone="true">
					<tr><td colspan={colonnes.length + 1} bind:this={zoneDesignation}></td></tr>
				</tbody>
			{/if}
			{#each groupes as groupe (groupe.cle)}
				<tbody data-groupe={groupe.cle} data-reference={groupe.reference ? 'true' : undefined}>
					<tr
						class={['compare-groupe', repliable(groupe.cle) && `compare-groupe-teinte teinte-${groupe.cle}`]}
						class:compare-groupe-reference={groupe.reference}
					>
						<th
							id={`comparaison-groupe-${groupe.cle}`}
							scope="rowgroup"
							colspan={colonnes.length + 1}
							tabindex="-1"
							bind:this={titresGroupes[groupe.cle]}
						>
							{#if repliable(groupe.cle)}
								<!-- C2: the title of a category is the summary of its fold. No `open` attribute is written:
								     closed on arrival, then the reader's own (nothing for the hydration to write over). -->
								<details class="depliant compare-groupe-pli" bind:this={plis[groupe.cle]}>
									<summary><IconeCategorie partie={groupe.cle as IdPartie} taille={20} />{groupe.titre}</summary>
								</details>
								<!-- T11: what the fold holds, beside its title (outside the summary, whose name is the title). -->
								<span class="compare-groupe-compte" data-compte={nombreDeLignes(groupe.lignes.length)}></span>
							{:else}
								<span>{groupe.titre}</span>
							{/if}
							{#if groupe.reference}
								<!-- CMP-01: the commune of the address is a reference, kept apart and last. -->
								<span class="compare-reference-phrase">{PHRASE_REFERENCE_COMMUNE}</span>
							{/if}
						</th>
					</tr>
					{#each groupe.lignes as ligne, position (ligne.cle)}
						{@const metadonnees = factoriserMetadonnees(ligne)}
						{@const unite = uniteDite(ligne)}
						{#if ligne.designe && !groupe.lignes[position - 1]?.designe}
							<tr class="compare-designation-note" data-designation-note="true">
								<td colspan={colonnes.length + 1}><p>{PHRASE_DESIGNATION}</p></td>
							</tr>
						{/if}
						{#if ligne.detail && !groupe.lignes[position - 1]?.detail}
							<!-- R5 (plan 04.5-13): a native disclosure opens the detail rows that follow it (a <details>
							     cannot hold table rows: its summary controls them, and they keep the columns). -->
							<tr class="compare-detail-bascule" data-detail-bascule={groupe.cle}>
								<td colspan={colonnes.length + 1}>
									<details class="depliant depliant-accent compare-detail">
										<summary class="compare-detail-resume" aria-controls={idsDetail(groupe)}
											>{ligne.detail}</summary
										>
									</details>
								</td>
							</tr>
						{/if}
						<tr
							id={ligne.detail ? idLigne(groupe.cle, ligne.cle) : undefined}
							data-ligne={ligne.ligne}
							data-cle={ligne.cle}
							data-identiques={ligne.identiques}
							data-meta-communes={metadonnees ? 'true' : undefined}
							data-designe={ligne.designe ? 'true' : undefined}
							data-detail={ligne.detail ? 'true' : undefined}
							data-unite-dite={unite ? 'true' : undefined}
						>
							<th scope="row" class="compare-critere" class:compare-critere-source={!!metadonnees}>
								<!-- After the label: its last line has room far more often than the source's, and a line of
								     its own under the source would add 20 px to the row on a phone (B6). The button shares an
								     unbreakable span with the last word, so it never wraps alone (review of 09/11). The unit is
								     read with each value, as on the fiche, never alone under the label. -->
								<span class="compare-libelle"
									>{derniersMots(ligne.libelle)[0]}{#if metadonnees}<span class="compare-libelle-fin"
											>{derniersMots(ligne.libelle)[1]}<SourceButton
												forme="detail"
												libelle={ligne.libelle}
												onouvrir={(declencheur) => ouvrirLigne(ligne, metadonnees.source, declencheur)}
											/></span
										>{:else}{derniersMots(ligne.libelle)[1]}{/if}</span
								>{#if unite}{' '}<span class="compare-unite">{unite}</span>{/if}
							</th>
							{#each ligne.cellules as cellule, index (cellule.code)}
								{@const periode = periodeDe(cellule)}
								<td data-code={cellule.code} data-state={etatDe(cellule)}>
									{#if cellule.sansDesignation}
										<p class="compare-sans-designation">{AUCUN_DPE_DESIGNE}</p>
									{:else if horsCouvertureDeColonne(cellule, index)}
										<!-- The column header already carries the badge, its reason and « ne vaut pas zéro »:
										     each cell names the state once more, typed by data-state, without a repeated gap. -->
										<p class="compare-hors-couverture">Hors couverture</p>
									{:else}
										<div
											class="compare-valeur"
											class:compare-valeur-absence={cellule.etat?.kind === 'absence'}
											class:compare-valeur-creneau={ecartsEnLigne && index > 0 && !ecartSurLaLigne(cellule)}
										>
											{#if cellule.etat}
												<!-- In a comparison of communes the value ends its cell, on the edge of its column: its
												     explanation control comes first in the page too (no visual reordering, UI-01). -->
												{#if alignees && !metadonnees}
													<SourceButton
														forme="detail"
														libelle={`${ligne.libelle} · ${nomAccessible(colonnes[index])}`}
														onouvrir={(declencheur) => ouvrirSource(ligne, cellule, nomAccessible(colonnes[index]), declencheur)}
													/>
												{/if}
												<StateValue etat={cellule.etat} aide={phraseDecidee(cellule.etat)} />
												{#if !alignees && !metadonnees}
													<SourceButton
														forme="detail"
														libelle={`${ligne.libelle} · ${nomAccessible(colonnes[index])}`}
														onouvrir={(declencheur) => ouvrirSource(ligne, cellule, nomAccessible(colonnes[index]), declencheur)}
													/>
												{/if}
											{:else}
												<!-- Nothing honest to render (undeclared absence, unknown state, already named on the
												     console by the data layer): a neutral marker, never a glossary state, never blank. -->
												<p class="compare-non-affichable">{LIBELLE_NON_AFFICHABLE}</p>
											{/if}
										</div>
										{#if cellule.echelle}
											<!-- R-09: a value of the commune of the arrondissement keeps the mark of the fiche; on paper,
											     its object in words. -->
											<p class="compare-echelle">
												<span class="compare-echelle-ecran"
													><RepereEchelle pictogramme={cellule.echelle.pictogramme} mot={cellule.echelle.mot} /></span
												><span class="compare-echelle-clair">{cellule.echelle.enClair}</span>
											</p>
										{/if}
										{#if metaSurLaFace && !ligne.designe && (periode || cellule.source)}
											<p class="compare-meta">
												{#if periode}<span>{#each metaFragments(periode) as fragment}{#if isDate(fragment)}<span class="compare-date">{fragment}</span>{:else}{fragment}{/if}{/each}</span>{/if}
												{#if cellule.source}<span>Source : {cellule.source.nom}</span>{/if}
											</p>
										{/if}
										{#if cellule.ecart}
											{@const seul = cellule.ecart.kind === 'ecart' ? ecartSeul(cellule.ecart.texte) : null}
											{@const nu = seul ? ecartSansUnite(seul, unite) : null}
											{@const signe = signeDe(cellule.ecart)}
											<p
												class="compare-ecart"
												class:compare-ecart-warn={cellule.ecart.kind === 'motif'}
												data-ecart={cellule.ecart.kind}
												data-signe={signe}
												data-tu={motifTu(cellule) ? 'true' : undefined}
											>
												{#if motifTu(cellule)}<span class="sr-only">{cellule.ecart.texte}</span>{:else if nu}{nu[0]} <span class="sr-only">{nu[1]} {suiteReference.trim()}</span
													>{:else if seul}{seul} <span class="sr-only">{suiteReference.trim()}</span
													>{:else if signe}<span class="sr-only">{cellule.ecart.texte}</span>{:else}{cellule.ecart.texte}{/if}
											</p>
										{/if}
									{/if}
								</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			{/each}
		</table>
	</div>
</div>

<SourcePanel bind:this={panneau} titre={actif.titre} entrees={actif.entrees} brut={actif.brut} source={actif.source} />

<style>
	.compare-filtre,
	.compare-defilement {
		max-width: var(--mesure);
		margin: 0 0 var(--s2);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.compare-defilement[hidden] {
		display: none;
	}

	/* Non-scrolling wrapper: carries the scroll state and the right-edge hint, which therefore stays
	   at the right edge whatever the horizontal scroll of the region. */
	.compare-cadre {
		position: relative;
	}

	.compare-cadre[data-defilable='true'][data-fin='false']::after {
		content: '';
		position: absolute;
		top: 0;
		right: 0;
		bottom: 0;
		width: 24px;
		background: linear-gradient(to right, transparent, var(--color-bg));
		pointer-events: none;
	}

	/* The sticky container of the band never takes room: the table does not move when it shows. */
	.compare-bande-ancre {
		position: sticky;
		top: var(--compare-bande-top, 0);
		z-index: 30;
		height: 0;
		overflow: visible;
	}

	.compare-bande {
		position: absolute;
		top: 0;
		left: 0;
		right: 0;
		display: flex;
		box-sizing: border-box;
		overflow: hidden;
		border-bottom: 1px solid var(--color-line);
		background: var(--color-soft);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.compare-bande-coin,
	.compare-bande-cellule {
		flex: none;
		box-sizing: border-box;
		padding: var(--s2) var(--s3);
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}

	/* Opaque and above the cells of the band: the names slide under it, as the columns do under the
	   criteria column. */
	.compare-bande-coin {
		position: relative;
		z-index: 1;
		background: var(--color-soft);
	}

	/* One line per column: the name is cut, never its discriminant (D-15 CR-01). */
	.compare-bande-cellule {
		display: flex;
		gap: var(--s1);
	}

	.compare-bande-nom {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.compare-bande-discriminant {
		flex: none;
		color: var(--color-muted);
		font-weight: 400;
	}

	.compare-bande-piste {
		display: flex;
		flex: none;
		will-change: transform;
	}

	/* The `.cmp-wrap` of the mock-up (`style.css:297`): the table on the page, without a frame; only the
	   horizontal axis belongs to the region, the page scrolls (03-18, R-2). */
	.compare-region {
		box-sizing: border-box;
		max-width: 100%;
		overflow-x: auto;
		overflow-y: visible;
	}

	.compare-region:focus-visible {
		outline: 3px solid var(--color-focus);
		outline-offset: 4px;
	}

	.compare-region[aria-busy='true'] {
		opacity: 0.6;
	}

	.compare-table {
		/* 200 + 4 × 210 = 1040 px at least: four columns fit at 1440 px (Opus 04.8-05-1, P2; 224 px before
		   the port, in a page of 1312 px). Readability (2026-11-10): the table is no longer stretched over
		   `.gabarit`; 360 px of labels and 272 px per column at most, so a label stays next to its values
		   (two communes took 1 192 px, a value 385 px away from its label). */
		width: max(min(100%, calc(360px + var(--colonnes) * 272px)), calc(200px + var(--colonnes) * 210px));
		table-layout: fixed;
		border-collapse: separate;
		border-spacing: 0;
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.5;
		/* Cancels the table rule of app.css: tabular figures widened the hyphens of the names
		   (« La Roche - sur - Yon »); the values keep theirs through StateValue. */
		font-variant-numeric: normal;
	}

	.compare-caption {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}

	/* The labels keep their share of the bounded table (360 of 360 + n × 272); the columns share the rest. */
	.compare-col-critere {
		width: calc(360 / (360 + var(--colonnes) * 272) * 100%);
	}

	/* `.cmp th, .cmp td` (`style.css:299`): a pale rule between two rows, the text from the left edge. */
	th,
	td {
		box-sizing: border-box;
		padding: var(--s3) var(--s4) var(--s3) 0;
		border-bottom: 1px solid var(--color-filet-pale);
		background: var(--color-bg);
		text-align: left;
		vertical-align: top;
		overflow-wrap: anywhere;
		hyphens: manual;
	}

	/* `.cmp thead th` (`style.css:301`): the names in Newsreader 22 (18 up to 720 px), a line under them. */
	thead th {
		border-bottom-color: var(--color-line-strong);
		font-size: 14px;
		font-weight: 400;
		line-height: 1.4;
		vertical-align: bottom;
	}

	/* « Critère » labels a column; it is not a compared name: the text face, on the line of the header. */
	.compare-coin {
		position: sticky;
		left: 0;
		z-index: 3;
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	/* The reference of the gaps, under « Critère »: a legend in the text face, never taller than the names. */
	.compare-reference-ecarts {
		display: block;
		margin-top: var(--s1);
		color: var(--color-muted);
		font-family: var(--font-sans);
		font-size: 14px;
		font-weight: 400;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.compare-commune:focus-visible {
		outline: 3px solid var(--color-focus);
		outline-offset: -3px;
	}

	/* 24 px at least: the target of a link under WCAG 2.5.8 (Codex 04.8-05-1, P2). */
	.compare-nom {
		display: block;
		min-height: 24px;
		color: var(--color-ink);
		font-family: var(--font-display);
		font-size: 22px;
		font-weight: 500;
		line-height: 1.2;
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	.compare-nom:hover {
		text-decoration-thickness: 2px;
	}

	.compare-identite {
		display: block;
		margin-bottom: var(--s1);
		color: var(--color-muted);
	}

	.compare-hc-note {
		display: block;
		margin-top: var(--s1);
		color: var(--color-ink);
		font-size: 14px;
	}

	.compare-actions {
		display: flex;
		flex-wrap: nowrap;
		gap: var(--s1);
		margin-top: var(--s1);
	}

	/* C7: the actions of a column are the link button of the socle (`.bouton-lien`), like « Copier le lien »
	   and « Retirer » of the page. Only what a narrow column header needs stays here: 14 px on short lines
	   (two actions share 116 px at 375 px) and a 44 px wide target. While a comparison is being read the
	   whole region is already dimmed: the actions wait without fading twice. */
	button.compare-action {
		min-width: 44px;
		font-size: 14px;
		font-weight: 400;
		line-height: 1.2;
		text-align: left;
	}

	button.compare-action:disabled {
		opacity: 1;
		cursor: wait;
	}

	/* `.cmp-g th` (`style.css:303-304`): the group in Inter 600 16, room above it, a line under it. */
	.compare-groupe th {
		padding-top: var(--s5);
		border-bottom-color: var(--color-line);
		font-size: 16px;
		font-weight: 600;
		line-height: 1.4;
		scroll-margin-top: var(--compare-ancre-offset);
	}

	.compare-groupe th:focus-visible {
		outline: 3px solid var(--color-focus);
		outline-offset: -3px;
	}

	.compare-groupe th span,
	.compare-groupe-pli {
		position: sticky;
		left: 0;
	}

	/* C10: a category wears the tint of its category on the commune fiche (`teinte-q-…`, app.css): its title
	   row on the pale ground, its title and its mark in the ink of the tint, as the fiche writes them. */
	.compare-groupe-teinte th {
		padding-inline: var(--s3);
		background: var(--c-fond);
	}

	.compare-groupe-teinte .compare-groupe-pli > summary {
		color: var(--c-encre);
	}

	/* C2: the fold of a category takes the width of its title, kept in view when the region scrolls. */
	.compare-groupe-pli {
		display: inline-block;
	}

	@media (min-width: 721px) {
		/* The value columns of a comparison of communes, read on their right edge: the name, each value, its
		   gap. The room between two columns moves to their left. */
		.compare-nombres thead th.compare-commune,
		.compare-nombres td {
			padding: var(--s3) 0 var(--s3) var(--s4);
			text-align: right;
		}

		/* The value ends the cell; its explanation control sits just before it, never at the far end. */
		.compare-nombres .compare-valeur {
			justify-content: flex-end;
		}

		/* The control keeps its 44 px target without reaching over the value next to it. */
		.compare-nombres .compare-valeur :global(.source-button) {
			margin-inline: 0;
		}

		/* Direction A: one line per criterion. With two or three communes a gap or the sign of a verdict is read to the right of its
		   value, in a slot of its own (72 px after 12 px: a badge, its control and the slot hold in a column of 272 px), so the values of a column keep one right edge
		   whatever follows them; a gap that has no room goes back under its value by itself. A reason that
		   is drawn (a sentence) stays under the value, which then leaves the same slot free. */
		.compare-ecarts-en-ligne td .compare-valeur {
			display: inline-flex;
			vertical-align: baseline;
		}

		.compare-ecarts-en-ligne td .compare-ecart:not([data-ecart='motif']) {
			display: inline-block;
			width: var(--s8);
			margin: 0 0 0 var(--s3);
			text-align: left;
			vertical-align: baseline;
			white-space: nowrap;
		}

		/* The free slot is a box of the value's own row (its width less the gap of that row, plus the 4 px
		   of the space that separates a value from its gap in the line). Every cell after the reference
		   column keeps it, with or without a gap, so a badge ends where a figure ends. */
		/* A reason kept for screen readers only (1 px) goes to the free slot, off the control of its value. */
		.compare-ecarts-en-ligne td .compare-ecart[data-tu] {
			margin-left: auto;
		}

		.compare-ecarts-en-ligne td .compare-valeur-creneau::after {
			content: '';
			/* It gives way first and alone: a badge wider than the room left takes the slot, up to the edge of
			   its column, and breaks on two lines only once the slot is spent (seen on real data: « Hors
			   couverture · non couvert par la source », kept on one line, pushed the table out of the page). */
			flex: 0 9999 auto;
			width: calc(var(--s8) + var(--s3) + var(--s1) - var(--s2));
		}

		/* T10: the unit a row says once is no longer drawn in its cells; it stays there for screen readers
		   (the clipping of `.sr-only`, written here because the unit belongs to StateValue). The value is the
		   containing block: clipped against the frame of the table, the unit widened the page on a phone. */
		tr[data-unite-dite] td :global(.state-value) {
			position: relative;
		}

		tr[data-unite-dite] td :global(.state-value .unit) {
			position: absolute;
			/* Inside its value: left after it, the clipped pixel passed the edge of the last column. */
			top: 0;
			right: 0;
			width: 1px;
			height: 1px;
			overflow: hidden;
			clip-path: inset(50%);
			white-space: nowrap;
		}

		/* Two actions, told apart: they read as one sentence 4 px from each other. */
		.compare-nombres .compare-actions {
			flex-wrap: wrap;
			justify-content: flex-end;
			gap: 0 var(--s4);
		}

		/* The labels start where the titles of the categories start. */
		.compare-nombres .compare-coin,
		.compare-nombres .compare-critere {
			padding-left: var(--s3);
		}

		/* A title band is as tall as its title; the room between two bands is the page showing through. */
		.compare-nombres .compare-groupe-teinte th {
			padding-block: 0;
			border-top: var(--s2) solid var(--color-bg);
			border-bottom: 0;
			border-radius: var(--radius-control);
			background-clip: padding-box;
		}

		.compare-nombres .compare-groupe:not(.compare-groupe-teinte) th {
			padding-left: var(--s3);
		}
	}

	/* A row is followed from its label to its last value. */
	@media (hover: hover) {
		tbody tr[data-ligne]:hover > th,
		tbody tr[data-ligne]:hover > td {
			background: var(--color-soft);
		}
	}

	/* On screen, a closed category folds its rows (display: none keeps them out of the accessibility tree and
	   of the tab order). Printing is left out: the rows are always printed. */
	@media screen {
		tbody:has(> .compare-groupe .compare-groupe-pli:not([open])) > tr:not(.compare-groupe) {
			display: none;
		}
	}

	/* The commune of the address (CMP-01): header cell on the `soft` background, its sentence in muted
	   14 under the title of the group. */
	.compare-groupe-reference th {
		background: var(--color-soft);
	}

	.compare-groupe th .compare-reference-phrase {
		display: block;
		margin-top: var(--s1);
		color: var(--color-muted);
		font-size: 14px;
		font-weight: 400;
	}

	/* Address band: the street on two lines at most, then the postal code, never cut. */
	.compare-bande-adresses .compare-bande-cellule {
		align-items: flex-start;
		white-space: normal;
	}

	.compare-bande-adresses .compare-bande-nom {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		overflow-wrap: anywhere;
	}

	/* DPE band: the number whole, never cut (13 characters fit in a 124 px column once the cell keeps
	   6 px on each side instead of 12, measured at 375 px). */
	.compare-bande-documents .compare-bande-cellule {
		padding-inline: var(--s-demi);
	}

	.compare-bande-documents .compare-bande-nom {
		overflow: visible;
		text-overflow: clip;
		font-variant-numeric: tabular-nums;
	}

	/* Badges of a document column (« Ancienne méthode », « Expiré »): neutral facts, never `warn`. */
	.compare-badges {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s1);
		margin-bottom: var(--s1);
	}

	.compare-badge {
		display: inline-block;
		max-width: 100%;
		box-sizing: border-box;
		padding: var(--s-fin) var(--s-demi);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-badge);
		background: var(--color-soft);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	/* `.cmp tbody th[scope="row"]` (`style.css:305`): the criterion in Inter 400. */
	.compare-critere {
		position: sticky;
		left: 0;
		z-index: 1;
		font-weight: 400;
		overflow-wrap: normal;
		word-break: normal;
	}

	/* The target of the row's explanation starts at its icon: it never covers the label before it. */
	.compare-critere :global(.source-button) {
		margin-inline-start: 0;
		padding-inline-start: 0;
	}

	.compare-libelle-fin {
		white-space: nowrap;
	}

	.compare-valeur {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: var(--s2);
	}

	/* `.cmp td` (`style.css:300`): the served value in Newsreader 20 (16 up to 720 px), tabular figures
	   (StateValue sets the family and the figures; the cell sets the size). */
	.compare-valeur :global(.state-value) {
		font-size: 20px;
		line-height: 1.3;
	}

	.compare-valeur :global(.state-badge) {
		padding-inline: var(--s1);
		overflow-wrap: normal;
		word-break: normal;
		hyphens: none;
	}

	/* 04-11: the sentence before the rows of the designated documents, kept in view when the region
	   scrolls, and a column without designation (not a state: no badge). */
	.compare-designation-note td {
		padding-top: var(--s2);
		padding-bottom: var(--s2);
		background: var(--color-soft);
		font-size: 14px;
		line-height: 1.4;
	}

	/* On screen, a closed detail folds the rows that follow it (display: none keeps them out of the
	   accessibility tree and of the tab order). Printing is left out: the rows are always printed. */
	@media screen {
		tbody:has(> .compare-detail-bascule .compare-detail:not([open])) > tr[data-detail] {
			display: none;
		}
	}

	.compare-detail-bascule td {
		padding-top: var(--s-demi);
		padding-bottom: var(--s-demi);
	}

	/* C11: the toggle is the fold of the socle (`.depliant.depliant-accent`: a 44 px summary, its chevron).
	   Only what the table needs stays here: kept in view when the region scrolls, on one line. */
	.compare-detail-resume {
		position: sticky;
		left: 0;
		white-space: nowrap;
	}

	.compare-designation-note p {
		position: sticky;
		left: 0;
		max-width: min(100%, calc(100vw - 48px));
		margin: 0;
	}

	/* D-15 FRONT WR-02: the open designation group, full width under the header, kept in view. */
	.compare-designation-zone td {
		padding-top: var(--s2);
		padding-bottom: var(--s2);
	}

	.compare-designation-zone td :global(.designation-groupe) {
		position: sticky;
		left: 0;
		box-sizing: border-box;
		max-width: min(100%, calc(100vw - 48px));
		margin-top: 0;
	}

	.compare-non-affichable,
	.compare-sans-designation,
	.compare-hors-couverture {
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	/* A value of the commune on an arrondissement (R-09): its mark on screen, its object in words on paper. */
	.compare-echelle {
		margin: var(--s1) 0 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.compare-echelle-clair {
		display: none;
	}

	/* Period, then source, each on its own line: no separator left at the start of a line (C-08). */
	.compare-meta {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		margin: var(--s1) 0 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.compare-meta span {
		overflow-wrap: normal;
		word-break: normal;
		hyphens: none;
	}

	.compare-date {
		white-space: nowrap;
	}

	/* `position: relative`: the words kept for screen readers (`.sr-only`, absolutely positioned) are then
	   contained by their cell, inside the scrolling region; contained by the frame around the region, they
	   widened the page by the hidden part of the table (seen at 320 and 375 px). */
	.compare-ecart {
		position: relative;
		margin: var(--s2) 0 0;
		color: var(--color-ink);
		font-size: 14px;
		line-height: 1.4;
	}

	.compare-ecart-warn {
		color: var(--color-warn);
	}

	/* T9: a reason kept for screen readers only takes no room under its badge. */
	.compare-ecart[data-tu] {
		width: 1px;
		height: 1px;
		margin: 0;
		overflow: hidden;
	}

	/* T7, T8: the verdict of a cell is drawn as a sign; its sentence stays in the cell, out of sight. */
	.compare-ecart[data-signe]::before {
		content: attr(data-signe);
		color: var(--color-muted);
	}

	/* T10: the unit a row says once, after its label, from 721 px (rule of 09/11 kept: never a unit alone
	   under a label; it is tied to the last word by a no-break space). On a phone the 112 px label column
	   has no room for it: the cells keep drawing theirs. */
	.compare-unite {
		color: var(--color-muted);
		font-size: 14px;
		white-space: nowrap;
	}

	/* T11: the number of rows a folded category holds, at the end of its title band. Drawn from an
	   attribute: the text of the title cell stays the title. */
	.compare-groupe th .compare-groupe-compte {
		position: static;
		float: right;
		color: var(--c-encre);
		font-size: 14px;
		font-weight: 400;
		line-height: 44px;
	}

	.compare-groupe-compte::after {
		content: attr(data-compte);
	}

	@media (max-width: 720px) {
		.compare-table {
			width: max(100%, calc(112px + var(--colonnes) * 124px));
		}

		/* T10 from 721 px only: on a phone the cells draw their unit, the label says none. */
		.compare-unite {
			display: none;
		}

		/* In a comparison of communes the control precedes its value: both start the cell on a phone. */
		.compare-nombres .compare-valeur {
			justify-content: flex-start;
		}

		/* Direction A on a phone (decision D5, 2026-11-10), for two communes: the label of a row takes its
		   own line and the two values sit under it in equal columns, so a label is no longer squeezed in
		   112 px. The rows are grids; the table stays a table in the page (same elements, same order).
		   Seen at the e2e slot: with four communes (84 px a column) the column headers broke and the table
		   still overflowed, so three or four communes keep the scrolling table, like the addresses and the
		   documents. */
		.compare-empile,
		.compare-empile thead,
		.compare-empile tbody {
			display: block;
			width: 100%;
		}

		.compare-empile colgroup {
			display: none;
		}

		.compare-empile tr {
			display: grid;
			grid-template-columns: repeat(var(--colonnes), minmax(0, 1fr));
		}

		.compare-empile .compare-coin,
		.compare-empile .compare-critere,
		.compare-empile .compare-groupe > th {
			grid-column: 1 / -1;
		}

		/* The label and its values read as one block: one rule under the values, none under the label. */
		.compare-empile .compare-coin,
		.compare-empile .compare-critere {
			padding-bottom: 0;
			border-bottom: 0;
			font-size: 16px;
		}

		.compare-empile .compare-coin {
			font-size: 14px;
		}

		.compare-empile tr[data-ligne] > td {
			padding-top: var(--s1);
		}

		/* A label that carries the source of its row keeps room for the 44 px of that control. */
		.compare-empile .compare-critere-source {
			padding-bottom: var(--s3);
		}

		/* No column of labels is left under « Critère »: the word stays for screen readers, the reference
		   of the gaps keeps its line, and the column header holds in a fifth of the window (UI-012). */
		.compare-empile .compare-coin-mot {
			position: absolute;
			width: 1px;
			height: 1px;
			overflow: hidden;
			clip-path: inset(50%);
			white-space: nowrap;
		}

		.compare-empile .compare-reference-ecarts {
			margin-top: 0;
		}

		/* A gap starts under its value, not under the control that precedes it. */
		.compare-empile td .compare-ecart:not([data-tu]) {
			margin-left: var(--s3);
			padding-left: var(--s6);
		}

		/* The band copies the names only: there is no column of labels to keep beside them. */
		.compare-bande-empilee .compare-bande-coin {
			display: none;
		}

		.compare-col-critere {
			width: 112px;
		}

		.compare-col-commune {
			width: 124px;
		}

		/* 18 px in § 6.4; 16 px here (gap decided on 05/11 with UI-012): at 18 px four long names took
		   166 px of the 160 px ceiling (a fifth of 800 px) of the column header. */
		.compare-nom {
			font-size: 16px;
			line-height: 1.2;
		}

		.compare-critere {
			font-size: 14px;
			line-height: 1.4;
		}

		.compare-valeur :global(.state-value) {
			font-size: 16px;
		}

		.compare-valeur-absence {
			flex-wrap: wrap;
			row-gap: var(--s2);
		}

		.compare-valeur-absence :global(.state-absence) {
			flex: 1 1 0;
			min-width: min-content;
		}

		.compare-valeur-absence :global(.source-button) {
			margin-block: 0;
		}

		/* 04.8-01 (14 px floor): with 8 px on each side, 108 px of a 124 px column wrapped the names
		   (« Saint-Philbert-de-Grand-Lieu » on three lines), « Placer en premier » (three lines) and the
		   out-of-coverage note: the column header passed a fifth of the window and the first value left
		   the first screen (UI-012). 4 px on each side give 116 px, the header band follows. */
		th,
		td {
			padding: var(--s3) var(--s1);
		}

		/* The 44 px hit zone of a Source button stays inside its cell: pulled back by the 4 px of the
		   cell instead of 8: the « i » takes less of a cell than a word, so the values keep their width and
		   the table its height (B6). */
		th :global(.source-button),
		td :global(.source-button) {
			margin-inline: calc(-1 * var(--s1));
			padding-inline: 0;
		}

		thead th {
			padding-block: var(--s2);
		}

		/* --s5 above a group in § 6.4; --s3 here (gap decided on 05/11 with UI-012 and B6). */
		.compare-groupe th {
			padding-top: var(--s3);
		}

		.compare-bande-coin,
		.compare-bande-cellule {
			padding: var(--s2) var(--s1);
		}

		/* 04.8-01, 14 px floor: the 13 characters of a DPE number take 118.3 px at 14 px / 600 in a
		   124 px column, so the cell keeps 2 px on each side (120 px). */
		.compare-bande-documents .compare-bande-cellule {
			padding-inline: var(--s-fin);
		}
	}

	@media print {
		.compare-defilement,
		.compare-bande-ancre {
			display: none;
		}

		.compare-cadre::after {
			display: none;
		}

		.compare-region {
			overflow: visible;
		}

		.compare-region[aria-busy='true'] {
			opacity: 1;
		}

		.compare-table {
			width: 100%;
		}

		.compare-coin,
		.compare-critere,
		.compare-groupe th span {
			position: static;
		}

		.compare-actions,
		.compare-echelle-ecran {
			display: none;
		}

		.compare-echelle-clair {
			display: inline;
		}

		tr {
			break-inside: avoid;
		}

		/* The detail rows print even closed, without script: only the screen rule folds them. */
		.compare-detail-bascule {
			display: none;
		}
	}
</style>
