// Synthetic variants of the list of the DPE documents of 44109_zz04_00004 (plan 04.5-05, C2;
// 04.1-13, D-127), and the documentary record of a rendered list (D45-01).
//
// VARIANTS: built from the answer observed on 2026-10-13, before the API served the group of the
// documents attached by their exact raw address (2026-10-19), and shaped on `DocumentsDpeAdresse`
// and `DocumentDpeCarteAdresseBrute` of contracts/openapi.v1.json. Every group, card, number and
// state added here is SYNTHETIC: none was observed in production. `sansGroupe` gives the same
// answer without the three keys of the group (an answer of the API before 2026-10-19).
//
// RECORD: `releveDocumentaire` reads what the list section renders, per block (the ADEME key, or
// the raw-address group once displayed) and per DPE number: the header texts (title, head
// sentence, common line, signal, D-110 notice, A→G distribution, range, panel entries) and, for
// each card in its rendered order, its number, its link and six attributes (energy class, climate
// class, type and surface, dates, served badges, declared address), plus the attachment badge. It
// is self-contained (no import, no closure) so that Playwright can serialize it into the page and
// Vitest can run it on a jsdom container: the same reading, before and after the change.
import { ADRESSE_44109_zz04_00004_DPE_OBSERVED } from '../../../src/lib/data/__fixtures__/adresse-44109-zz04-00004-20261013';

export const BAN_ID_ADRESSE_BRUTE = '44109_zz04_00004';

type Donnees = Record<string, unknown>;

export interface ReponseDocuments {
	statut: 'ok';
	donnees: Donnees;
	meta: unknown;
}

function observee(): ReponseDocuments {
	return structuredClone(ADRESSE_44109_zz04_00004_DPE_OBSERVED) as unknown as ReponseDocuments;
}

/** A card of the raw-address group, as `DocumentDpeCarteAdresseBrute` serves it (status badges only). */
function carteAdresseBrute(numero: string, partiel: Donnees = {}): Donnees {
	return {
		numero,
		etiquette_dpe: 'E',
		etiquette_ges: 'D',
		type_batiment: 'appartement',
		surface_habitable: 54.2,
		date_etablissement: '2025-06-02',
		date_fin_validite: '2035-06-01',
		badges: [],
		adresse_declaree: null,
		rattachement: 'adresse_brute_exacte',
		...partiel
	};
}

/**
 * Ten synthetic cards, most recent first (the served order of the group): the ten most recent of a
 * group whose served total may be larger. Declared addresses published, masked and absent.
 */
export const DIX_CARTES_ADRESSE_BRUTE: readonly Donnees[] = [
	carteAdresseBrute('2644E9000010S', { etiquette_dpe: 'C', etiquette_ges: 'A', surface_habitable: 61.5, date_etablissement: '2026-01-20', date_fin_validite: '2036-01-19', adresse_declaree: { etat: 'publiee', texte: '4 R JEAN JAURES 44000 NANTES', motif: null } }),
	carteAdresseBrute('2544E9000009S', { etiquette_dpe: 'D', etiquette_ges: 'B', type_batiment: 'immeuble', surface_habitable: null, date_etablissement: '2025-11-03', date_fin_validite: '2035-11-02' }),
	carteAdresseBrute('2544E9000008S'),
	carteAdresseBrute('2444E9000007S', { etiquette_dpe: 'F', etiquette_ges: 'F', surface_habitable: 28.4, date_etablissement: '2024-09-18', date_fin_validite: '2034-09-17', adresse_declaree: { etat: 'masquee', texte: null, motif: 'complement_present' } }),
	carteAdresseBrute('2344E9000006S', { etiquette_dpe: 'D', etiquette_ges: 'C', surface_habitable: 72, date_etablissement: '2023-12-05', date_fin_validite: '2033-12-04' }),
	carteAdresseBrute('2344E9000005S', { etiquette_dpe: null, etiquette_ges: null, type_batiment: null, surface_habitable: null, date_etablissement: '2023-04-11', date_fin_validite: '2033-04-10' }),
	carteAdresseBrute('2244E9000004S', { etiquette_dpe: 'E', etiquette_ges: 'E', surface_habitable: 45.1, date_etablissement: '2022-08-30', date_fin_validite: '2032-08-29', badges: ['remplacement_incertain'] }),
	carteAdresseBrute('2144E9000003S', { etiquette_dpe: 'G', etiquette_ges: 'F', surface_habitable: 19.7, date_etablissement: '2021-11-15', date_fin_validite: '2031-11-14' }),
	carteAdresseBrute('2044E9000002S', { etiquette_dpe: 'E', etiquette_ges: 'D', surface_habitable: 88, date_etablissement: '2020-03-09', date_fin_validite: '2030-03-08', badges: ['ancienne_methode'] }),
	carteAdresseBrute('1544E9000001S', { etiquette_dpe: 'F', etiquette_ges: 'E', surface_habitable: 66.3, date_etablissement: '2015-05-12', date_fin_validite: '2025-05-11', badges: ['ancienne_methode', 'expire'] })
];

/**
 * The six attributes of each card of the raw-address group, written here from the served cards above
 * (review 05, IMPORTANT 1): never read from the rendering, so that an attribute altered by the
 * rendering of the raw cards alone fails. Classes « Classe {c} » / « GES {c} », type and surface as
 * served (« non renseigné(e) » for a null), the two served days spelled by the site, the served
 * status badges, the declared address published, masked or absent.
 */
export const ATTENDUS_CARTES_ADRESSE_BRUTE: Readonly<Record<string, Pick<CarteDocumentaire, 'energie' | 'climat' | 'typeSurface' | 'dates' | 'badges' | 'adresse'>>> = {
	'2644E9000010S': { energie: 'Classe C', climat: 'GES A', typeSurface: 'Appartement · 61,5 m²', dates: "Établi le 20 janvier 2026 · valable jusqu'au 19 janvier 2036", badges: [], adresse: 'Adresse déclarée : « 4 R JEAN JAURES 44000 NANTES »' },
	'2544E9000009S': { energie: 'Classe D', climat: 'GES B', typeSurface: 'Immeuble · surface non renseignée', dates: "Établi le 3 novembre 2025 · valable jusqu'au 2 novembre 2035", badges: [], adresse: null },
	'2544E9000008S': { energie: 'Classe E', climat: 'GES D', typeSurface: 'Appartement · 54,2 m²', dates: "Établi le 2 juin 2025 · valable jusqu'au 1 juin 2035", badges: [], adresse: null },
	'2444E9000007S': { energie: 'Classe F', climat: 'GES F', typeSurface: 'Appartement · 28,4 m²', dates: "Établi le 18 septembre 2024 · valable jusqu'au 17 septembre 2034", badges: [], adresse: 'Adresse déclarée non affichée' },
	'2344E9000006S': { energie: 'Classe D', climat: 'GES C', typeSurface: 'Appartement · 72 m²', dates: "Établi le 5 décembre 2023 · valable jusqu'au 4 décembre 2033", badges: [], adresse: null },
	'2344E9000005S': { energie: 'Non renseigné', climat: 'GES non renseigné', typeSurface: 'Type non renseigné · surface non renseignée', dates: "Établi le 11 avril 2023 · valable jusqu'au 10 avril 2033", badges: [], adresse: null },
	'2244E9000004S': { energie: 'Classe E', climat: 'GES E', typeSurface: 'Appartement · 45,1 m²', dates: "Établi le 30 août 2022 · valable jusqu'au 29 août 2032", badges: ['Remplacement non vérifié'], adresse: null },
	'2144E9000003S': { energie: 'Classe G', climat: 'GES F', typeSurface: 'Appartement · 19,7 m²', dates: "Établi le 15 novembre 2021 · valable jusqu'au 14 novembre 2031", badges: [], adresse: null },
	'2044E9000002S': { energie: 'Classe E', climat: 'GES D', typeSurface: 'Appartement · 88 m²', dates: "Établi le 9 mars 2020 · valable jusqu'au 8 mars 2030", badges: ['Ancienne méthode'], adresse: null },
	'1544E9000001S': { energie: 'Classe F', climat: 'GES E', typeSurface: 'Appartement · 66,3 m²', dates: "Établi le 12 mai 2015 · valable jusqu'au 11 mai 2025", badges: ['Ancienne méthode', 'Expiré'], adresse: null }
};

function avecGroupe(reponse: ReponseDocuments, etat: 'value' | 'not_available', total: number | null, cartes: readonly Donnees[]): ReponseDocuments {
	reponse.donnees.etat_adresse_brute = etat;
	reponse.donnees.total_adresse_brute = total;
	reponse.donnees.documents_adresse_brute = structuredClone([...cartes]);
	return reponse;
}

/** No document at the ADEME key (the zero shape of the API: indeterminate selection, no document). */
function ademeZero(): ReponseDocuments {
	const reponse = observee();
	Object.assign(reponse.donnees, {
		total: 0,
		pages: 1,
		distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 },
		documents: [],
		selection: { etat: 'indeterminee', date: null, document: null, documents_ex_aequo: [], motif: 'aucun_document' },
		constat_d110: null
	});
	return reponse;
}

/** One house document at the ADEME key, with the D-110 finding served (criteria and sources). */
function ademeConstat(): ReponseDocuments {
	const reponse = observee();
	const [premier] = reponse.donnees.documents as Donnees[];
	const document = { ...premier, type_batiment: 'maison' };
	Object.assign(reponse.donnees, {
		total: 1,
		pages: 1,
		distribution: { A: 0, B: 0, C: 0, D: 1, E: 0, F: 0, G: 0, unknown: 0 },
		documents: [document],
		constat_d110: {
			criteres: ['maison', 'document_unique_cle', 'concordance_stricte', 'batiment_unique_parcelle_du_point', 'annexes_exclues_usage_inconnu_compte'],
			sources: [
				reponse.donnees.source,
				{ cle: 'batiments', nom: 'BD TOPO — Bâtiments', producteur: 'IGN', url: 'https://geoservices.ign.fr/bdtopo' }
			]
		}
	});
	return reponse;
}

/** The names of the variants, in a stable order. */
export const NOMS_VARIANTES = ['sans-groupe', 'servi', 'non-calcule', 'vide', 'ademe-zero', 'constat-ademe', 'douze'] as const;
export type NomVariante = (typeof NOMS_VARIANTES)[number];

/**
 * - `sans-groupe`: the observed answer, without the keys of the group (before 2026-10-19).
 * - `servi`: five ADEME documents and a group of three.
 * - `non-calcule`: the served generation was imported without the attachment (total null, never 0).
 * - `vide`: the attachment is computed and finds nothing (total 0).
 * - `ademe-zero`: no document at the ADEME key, a group of two.
 * - `constat-ademe`: one house document with the D-110 finding at the ADEME key, a group of one.
 * - `douze`: five ADEME documents, a group whose served total is 12, its ten most recent served.
 */
export function variante(nom: NomVariante): ReponseDocuments {
	switch (nom) {
		case 'sans-groupe':
			return observee();
		case 'servi':
			return avecGroupe(observee(), 'value', 3, DIX_CARTES_ADRESSE_BRUTE.slice(0, 3));
		case 'non-calcule':
			return avecGroupe(observee(), 'not_available', null, []);
		case 'vide':
			return avecGroupe(observee(), 'value', 0, []);
		case 'ademe-zero':
			return avecGroupe(ademeZero(), 'value', 2, DIX_CARTES_ADRESSE_BRUTE.slice(0, 2));
		case 'constat-ademe':
			return avecGroupe(ademeConstat(), 'value', 1, DIX_CARTES_ADRESSE_BRUTE.slice(0, 1));
		case 'douze':
			return avecGroupe(observee(), 'value', 12, DIX_CARTES_ADRESSE_BRUTE);
	}
}

/** The same answer without the three keys of the group: what the API served before 2026-10-19. */
export function sansGroupe(reponse: ReponseDocuments): ReponseDocuments {
	const copie = structuredClone(reponse);
	delete copie.donnees.etat_adresse_brute;
	delete copie.donnees.total_adresse_brute;
	delete copie.donnees.documents_adresse_brute;
	return copie;
}

// --- documentary record --------------------------------------------------------------------------

/** One header text of a block of the list (title, sentence, common line, panel entry…). */
export interface EnteteDocumentaire {
	bloc: string;
	element: string;
	texte: string | null;
}

/** One rendered card: its number, its link and its six attributes, in the rendered order. */
export interface CarteDocumentaire {
	bloc: string;
	element: 'carte';
	rang: number;
	numero: string | null;
	lien: string | null;
	energie: string | null;
	climat: string | null;
	typeSurface: string | null;
	dates: string | null;
	badges: string[];
	rattachement: string | null;
	adresse: string | null;
}

export type EntreeDocumentaire = EnteteDocumentaire | CarteDocumentaire;

/** Reads the rendered list section `section.doclist`; self-contained (serialized by Playwright). */
export function releveDocumentaire(section: Element): EntreeDocumentaire[] {
	const norm = (texte: string | null | undefined): string | null => (texte == null ? null : texte.replace(/\s+/g, ' ').trim());
	const bloc = (noeud: Element): string => noeud.closest('[data-groupe-dpe]')?.getAttribute('data-groupe-dpe') ?? 'ademe';
	const entetes: [string, string][] = [
		['titre', '.doclist-h2, .doclist-brute-titre'],
		['phrase', '.doclist-phrase, .doclist-brute-phrase'],
		['limite', '.doclist-brute-limite'],
		['commune', '.doclist-commune > span'],
		['signal', '.doclist-signal'],
		['constat', '[data-constat-d110] .notice'],
		['repartition', '.doclist-distribution'],
		['plage', '.doclist-range'],
		['panne', '.doclist-panne > p']
	];
	// The source action is a control, not a text of the list: since 04.8-02 (E4) it is a text button
	// « Source », whose word is left out of the record, as its icon was before.
	const sansAction = (noeud: Element): string | null => {
		const copie = noeud.cloneNode(true) as Element;
		copie.querySelectorAll('.source-button').forEach((action) => action.remove());
		return norm(copie.textContent);
	};
	const entrees: EntreeDocumentaire[] = [];
	for (const [element, selecteur] of entetes) {
		for (const noeud of Array.from(section.querySelectorAll(selecteur))) entrees.push({ bloc: bloc(noeud), element, texte: sansAction(noeud) });
	}
	for (const entree of Array.from(section.querySelectorAll('dialog .source-panel-entry'))) {
		const terme = norm(entree.querySelector('dt')?.textContent);
		const valeur = norm(entree.querySelector('dd')?.textContent);
		entrees.push({ bloc: bloc(entree), element: entree.closest('[data-constat-d110]') ? 'volet-constat' : 'volet', texte: `${terme ?? ''} : ${valeur ?? ''}` });
	}
	for (const carte of Array.from(section.querySelectorAll('li.doccard'))) {
		const ligne1 = Array.from(carte.querySelector('.doccard-l1')?.children ?? []);
		entrees.push({
			bloc: bloc(carte),
			element: 'carte',
			rang: carte.parentElement ? Array.from(carte.parentElement.children).indexOf(carte) : -1,
			numero: norm(carte.querySelector('.doccard-lien .chiffre')?.textContent),
			lien: carte.querySelector('.doccard-lien')?.getAttribute('href') ?? null,
			energie: norm(ligne1[0]?.textContent),
			climat: norm(ligne1[1]?.textContent),
			typeSurface: norm(ligne1.length ? ligne1[ligne1.length - 1].textContent : null),
			dates: norm(carte.querySelector('.doccard-l2 > span:first-child')?.textContent),
			badges: Array.from(carte.querySelectorAll('.doccard-badge[data-badge]')).map((badge) => norm(badge.textContent) ?? ''),
			rattachement: norm(carte.querySelector('.doccard-rattachement')?.textContent),
			adresse: norm(carte.querySelector('.doccard-l4')?.textContent)
		});
	}
	return entrees;
}

/** One link of a Source panel of the list: its block, its entry term, its text and its target. */
export interface LienDocumentaire {
	bloc: string;
	element: 'volet-lien';
	terme: string | null;
	texte: string | null;
	href: string | null;
}

/**
 * Reads every link of the Source panels of the list section (review 05, IMPORTANT 2): the textual
 * record above keeps the entries without their targets, so a source whose link disappears or points
 * elsewhere would go unseen. Self-contained like `releveDocumentaire`.
 */
export function releveLiensDesVolets(section: Element): LienDocumentaire[] {
	const norm = (texte: string | null | undefined): string | null => (texte == null ? null : texte.replace(/\s+/g, ' ').trim());
	return Array.from(section.querySelectorAll('dialog a[href]')).map((lien) => ({
		bloc: lien.closest('[data-groupe-dpe]')?.getAttribute('data-groupe-dpe') ?? 'ademe',
		element: 'volet-lien' as const,
		terme: norm(lien.closest('.source-panel-entry')?.querySelector('dt')?.textContent),
		texte: norm(lien.textContent),
		href: lien.getAttribute('href')
	}));
}
