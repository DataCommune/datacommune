// 04-05: the address page reads the first page of the sales of the parcel of the point with its
// declared documents. Specs that do not assert the sales (adresse-fiche, adresse-recherche,
// dpe-document) get one companion `/ventes?page=1` expectation per address they open: a measured
// zero, as many replies as page loads may need, none required. The exact contract keeps refusing
// any other request; `adresse-ventes.spec.ts` configures the sales it asserts itself.
// 04-06: the page also reads the commune of the address (`/v1/commune?code_insee=`, the reference
// theme); `avecCommuneDeLAdresse` adds that companion read (a commune without served row: no
// landmark strip of the commune is mounted, the theme stays closed), none required.
// C37: the page also reads the sales declared at the address (`/ventes-declarees?page=1`);
// `avecCommuneDeLAdresse` adds that companion read too (a true zero), none required, unless the
// spec configures it itself. C4 (04.5-07): the page asks for the VEFA too
// (`/ventes-declarees?page=1&vefa=incluses`, `PREMIERE_PAGE_DECLAREES`); its answers carry
// `nombre_vefa` (0 when the list holds no VEFA). C3 (04.5-08): by number and suffix too
// (`&regle=numero_et_suffixe`).

type Reply = { status: number; body: unknown };
type Expectation = {
	method: 'GET';
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number;
	responses: Reply[];
};

export const SOURCE_DVF = {
	cle: 'dvf',
	nom: 'Demandes de valeurs foncières (DVF)',
	producteur: 'DGFiP / Etalab',
	url: 'https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees'
};

/** A parcel without sale over the imported period, as served by `/v1/adresse/{id}/ventes`. */
export const SANS_VENTE: Reply = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: {
			etat: 'value',
			motif: null,
			nombre_parcelles: null,
			rattachement: 'parcelle_du_point',
			source: SOURCE_DVF,
			periode: '2021-2025',
			total: 0,
			page: 1,
			pages: 1,
			par_page: 10,
			generation: 'fedcba9876543210',
			ventes: []
		}
	}
};

/**
 * No sale attached to the address over the imported period (C37), not a zero of the source (C44); as
 * served under `vefa=incluses` (C4): no VEFA either.
 */
export const SANS_VENTE_DECLAREE: Reply = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: {
			etat: 'value',
			motif: null,
			rattachement: 'adresse_declaree_vente',
			source: SOURCE_DVF,
			periode: '2021-2025',
			total: 0,
			nombre_vefa: 0,
			page: 1,
			pages: 1,
			par_page: 10,
			generation: 'fedcba9876543210',
			ventes: []
		}
	}
};

/**
 * Query of the first page of the declared sales, as the page asks for it: with the VEFA since plan
 * 04.5-07 (C4, R8); at its starting SHA the page asked for the sales only (`vefa=exclues`, the API
 * default). By number and suffix since plan 04.5-08 (C3, `regle=numero_et_suffixe`); until then the
 * page asked the API default (`regle=numero`).
 */
export const PREMIERE_PAGE_DECLAREES: Readonly<Record<string, string>> = { page: '1', vefa: 'incluses', regle: 'numero_et_suffixe' };

const ADRESSE_RE = /^\/v1\/adresse\/([^/]+)(?:\/rapport|\/dpe|\/ventes|\/ventes-declarees)?$/;
const LECTURES_MAX = 20;

/** A commune the synthesis does not serve (`donnees: []`, no hosting reason): « Non renseigné ». */
export const COMMUNE_NON_SERVIE: Reply = { status: 200, body: { statut: 'ok', donnees: [], meta: { source: [] } } };

/**
 * Adds the companion commune read (04-06) and the companion read of the sales declared at the
 * address (C37) of every address opened by *expectations*.
 */
export function avecCommuneDeLAdresse<T extends Expectation>(expectations: T[]): (T | Expectation)[] {
	const deja = new Set(
		expectations.filter((attendue) => attendue.pathname === '/v1/commune').map((attendue) => attendue.query.code_insee)
	);
	const chemins = new Set(expectations.map((attendue) => attendue.pathname));
	const compagnons = new Map<string, Expectation>();
	for (const attendue of expectations) {
		const id = ADRESSE_RE.exec(attendue.pathname)?.[1];
		const declarees = id ? `/v1/adresse/${id}/ventes-declarees` : null;
		if (declarees && !chemins.has(declarees) && !compagnons.has(declarees)) {
			compagnons.set(declarees, {
				method: 'GET',
				pathname: declarees,
				query: { ...PREMIERE_PAGE_DECLAREES },
				minCalls: 0,
				maxCalls: LECTURES_MAX,
				responses: Array.from({ length: LECTURES_MAX }, () => SANS_VENTE_DECLAREE)
			});
		}
	}
	for (const attendue of expectations) {
		const code = ADRESSE_RE.exec(attendue.pathname)?.[1]?.split('_', 1)[0];
		if (!code || deja.has(code) || compagnons.has(code)) continue;
		compagnons.set(code, {
			method: 'GET',
			pathname: '/v1/commune',
			query: { code_insee: code },
			minCalls: 0,
			maxCalls: LECTURES_MAX,
			responses: Array.from({ length: LECTURES_MAX }, () => COMMUNE_NON_SERVIE)
		});
	}
	return [...expectations, ...compagnons.values()];
}

/** Adds the companion sales read of every address opened by *expectations*. */
export function avecVentesDeLaParcelle<T extends Expectation>(expectations: T[]): (T | Expectation)[] {
	const deja = new Set(expectations.map((attendue) => attendue.pathname));
	const compagnons = new Map<string, Expectation>();
	for (const attendue of expectations) {
		const id = ADRESSE_RE.exec(attendue.pathname)?.[1];
		if (!id) continue;
		const pathname = `/v1/adresse/${id}/ventes`;
		if (deja.has(pathname) || compagnons.has(pathname)) continue;
		compagnons.set(pathname, {
			method: 'GET',
			pathname,
			query: { page: '1' },
			minCalls: 0,
			maxCalls: LECTURES_MAX,
			responses: Array.from({ length: LECTURES_MAX }, () => SANS_VENTE)
		});
	}
	return avecCommuneDeLAdresse([...expectations, ...compagnons.values()]);
}

// --- Replays of plan 04.5-06 (B4): the two sales lists of 44109_zz04_00004 ---------------------
//
// The sales lists of `44109_zz04_00004` were captured on the site on 2026-10-24 and anonymised since
// (fictitious address, dates and prices, `scripts/check-real-data.py`)
// (`C:/tmp/dc-revue-ui-20261024/captures/m375/11-adresse--texte.txt`, l. 154-208): the same three
// sales in the list of the parcel and in the list declared at the address. No answer of either
// list was frozen, so these replies are RECONSTRUCTED from the rendered text (month, nature,
// composition, built surface, displayed price), shaped on `VentesParcelle` and `VentesAdresse` of
// contracts/openapi.v1.json. Synthetic: the days other than the served `derniere` (2025-06-09), the
// price per m² of the cards that showed none (not served), and the generation token.
//
// `ficheHorsParcelle` + `VENTES_HORS_PARCELLE` + `VENTES_DECLAREES_HORS_PARCELLE`: a SYNTHETIC
// variant of the same address, shaped on « 1 Place Royale » of the review
// (`m375/04-adresse-depuis-recherche--texte.txt`, l. 28-71): the point falls in no parcel (the
// landmark says « Point hors parcelle ») and one sale is declared at the number (février 2023,
// 461 m², 1 719 045 €). Only the identity parcel, the sales landmark and the two sales lists differ
// from the answer observed on 2026-10-13; the themes of the place are left as observed.

export const BAN_ID_44109 = '44109_zz04_00004';
export const PERIODE_44109 = '2021-01-02 → 2025-12-31';
export const GENERATION_44109 = '44109a0b1c2d3e4f';

function carte44109(partiel: Record<string, unknown>) {
	return {
		date: '2025-06-09',
		nature: 'Vente',
		composition: { maisons: 0, appartements: 0, dependances: 0, locaux_activite: 0 },
		surface_batie: null,
		prix: null,
		prix_m2: null,
		prix_affiche: true,
		minimisation: null,
		distance_m: null,
		...partiel
	};
}

/** The three sales captured in both lists, in the served (rendered) order. */
export const VENTES_44109 = [
	carte44109({ date: '2025-06-09', composition: { maisons: 0, appartements: 0, dependances: 0, locaux_activite: 1 }, surface_batie: 47, prix: 141000 }),
	carte44109({ date: '2022-12-18', composition: { maisons: 0, appartements: 1, dependances: 2, locaux_activite: 0 }, surface_batie: 99, prix: 532000, prix_m2: 5374 }),
	carte44109({ date: '2021-10-18', composition: { maisons: 0, appartements: 3, dependances: 2, locaux_activite: 0 }, surface_batie: 115, prix: 482000 })
];

function liste44109(rattachement: 'parcelle_du_point' | 'adresse_declaree_vente', partiel: Record<string, unknown>): Reply {
	return {
		status: 200,
		body: {
			statut: 'ok',
			donnees: {
				etat: 'value',
				motif: null,
				...(rattachement === 'parcelle_du_point' ? { nombre_parcelles: null } : {}),
				rattachement,
				source: SOURCE_DVF,
				periode: PERIODE_44109,
				total: 3,
				page: 1,
				pages: 1,
				par_page: 10,
				generation: GENERATION_44109,
				ventes: VENTES_44109,
				...partiel
			}
		}
	};
}

/** The sales of the parcel of 44109_zz04_00004 as captured (reconstructed). */
export const VENTES_PARCELLE_44109 = liste44109('parcelle_du_point', {});
/** The sales declared at 44109_zz04_00004 as captured (reconstructed): the same three sales. */
export const VENTES_DECLAREES_44109 = liste44109('adresse_declaree_vente', {});

/** Synthetic: the point of the address in no parcel, as `/ventes` serves it (no count, no period). */
export const VENTES_HORS_PARCELLE: Reply = liste44109('parcelle_du_point', {
	etat: 'not_available',
	motif: 'point_hors_parcelle',
	periode: null,
	total: null,
	pages: null,
	generation: null,
	ventes: []
});

/** Synthetic: one sale declared at the number of an address whose point is in no parcel. */
export const VENTE_DECLAREE_HORS_PARCELLE = carte44109({
	date: '2023-02-15',
	composition: { maisons: 0, appartements: 0, dependances: 0, locaux_activite: 1 },
	surface_batie: 461,
	prix: 1719045
});
export const VENTES_DECLAREES_HORS_PARCELLE: Reply = liste44109('adresse_declaree_vente', { total: 1, ventes: [VENTE_DECLAREE_HORS_PARCELLE] });

/**
 * C4 (04.5-07): a declared list without any VEFA as served under `vefa=incluses` — the same answer,
 * `nombre_vefa` 0 and every card marked as a sale. The replays of plan 06 above are the answers of
 * `vefa=exclues`, kept as frozen.
 */
export function sansVefa(reply: Reply): Reply {
	const copie = structuredClone(reply) as { status: number; body: { donnees: Record<string, unknown> & { ventes: Record<string, unknown>[] } } };
	copie.body.donnees.nombre_vefa = 0;
	copie.body.donnees.ventes = copie.body.donnees.ventes.map((carte) => ({ ...carte, hors_stats_vefa: false }));
	return copie;
}
export const VENTES_DECLAREES_44109_INCLUSES = sansVefa(VENTES_DECLAREES_44109);
export const VENTES_DECLAREES_HORS_PARCELLE_INCLUSES = sansVefa(VENTES_DECLAREES_HORS_PARCELLE);

/**
 * Synthetic: the observed fiche of an address whose point falls in no parcel. The identity serves
 * no parcel and the sales landmark serves `not_available` / `point_hors_parcelle` (no count).
 */
export function ficheHorsParcelle<T>(observee: T): T {
	const fiche = structuredClone(observee) as unknown as {
		donnees: {
			identite: { parcelle: unknown };
			reperes: { ventes: { nombre: Record<string, unknown>; derniere: Record<string, unknown> } };
		};
	};
	fiche.donnees.identite.parcelle = { etat: 'point_hors_parcelle', id_parcelle: null, nombre_candidats: 0, rattachement: 'parcelle_du_point', motif: null };
	const absente = (valeur: Record<string, unknown>) => ({ ...valeur, etat: 'not_available', motif: 'point_hors_parcelle', valeur: null, periode: null });
	fiche.donnees.reperes.ventes = { nombre: absente(fiche.donnees.reperes.ventes.nombre), derniere: absente(fiche.donnees.reperes.ventes.derniere) };
	return fiche as unknown as T;
}

// --- Variants of plan 04.5-07 (C4): VEFA in the sales declared at the address -------------------
//
// SYNTHETIC, shaped on `VentesAdresse` and `CarteVente` of contracts/openapi.v1.json as the API serves
// them under `vefa=incluses` since 4.1-15: `total` counts the listed mutations (sales and VEFA, the
// pages), `nombre_vefa` the VEFA among them, each card carries `hors_stats_vefa`. No real answer with
// a VEFA was captured; dates, compositions, surfaces and prices are invented, named as such here.
// Frozen at the starting SHA of plan 07 (d71cf851), before the page reads them (`vefa=incluses`):
//   - `VENTES_DECLAREES_44109_AVEC_VEFA`: the three sales of 44109 and two VEFA (total 5, 2 VEFA);
//   - `VENTES_PARCELLE_44109_AVEC_VEFA`: the same five mutations in the list of the parcel, which
//     already listed VEFA without a mark (4.1-15);
//   - `VENTES_DECLAREES_VEFA_SEULES`: an address in no parcel whose declared sales are VEFA only
//     (total 2, 2 VEFA): the comparator of addresses keeps « Aucune vente rattachée » for it;
//   - `VENTES_DECLAREES_VEFA_HORS_PAGE`: 23 listed mutations on three pages, 4 VEFA served, one of
//     them on page 1: the served count differs from the VEFA cards of the page (D45-05).


/** The nature DVF serves for a VEFA, as the card line 1 shows it. */
export const NATURE_VEFA = "Vente en l'état futur d'achèvement";

function carteVefa(partiel: Record<string, unknown>) {
	return carte44109({ nature: NATURE_VEFA, hors_stats_vefa: true, ...partiel });
}

/** Two synthetic VEFA of a new building at 44109_zz04_00004. */
export const VEFA_44109 = [
	carteVefa({ date: '2024-03-14', composition: { maisons: 0, appartements: 1, dependances: 1, locaux_activite: 0 }, surface_batie: 64, prix: 312000, prix_m2: 4875 }),
	carteVefa({ date: '2023-09-21', composition: { maisons: 0, appartements: 1, dependances: 0, locaux_activite: 0 }, surface_batie: 42, prix: 219000, prix_m2: 5214 })
];

/** The three sales of 44109 marked as sales (`hors_stats_vefa: false`, served on every card since 4.1-15). */
const VENTES_44109_MARQUEES = VENTES_44109.map((carte) => ({ ...carte, hors_stats_vefa: false }));

/** The five mutations in the served order (date descending): sale, VEFA, VEFA, sale, sale. */
export const MUTATIONS_44109_AVEC_VEFA = [VENTES_44109_MARQUEES[0], VEFA_44109[0], VEFA_44109[1], VENTES_44109_MARQUEES[1], VENTES_44109_MARQUEES[2]];

export const VENTES_DECLAREES_44109_AVEC_VEFA: Reply = liste44109('adresse_declaree_vente', { total: 5, nombre_vefa: 2, ventes: MUTATIONS_44109_AVEC_VEFA });
export const VENTES_PARCELLE_44109_AVEC_VEFA: Reply = liste44109('parcelle_du_point', { total: 5, ventes: MUTATIONS_44109_AVEC_VEFA });

/** VEFA only, declared at the number of the synthetic address in no parcel. */
export const VENTES_DECLAREES_VEFA_SEULES: Reply = liste44109('adresse_declaree_vente', { total: 2, nombre_vefa: 2, ventes: VEFA_44109 });

/** Page 1 of 23 listed mutations, 4 VEFA served, the first card the only VEFA of the page. */
export const VENTES_DECLAREES_VEFA_HORS_PAGE: Reply = liste44109('adresse_declaree_vente', {
	total: 23,
	nombre_vefa: 4,
	pages: 3,
	ventes: [
		VEFA_44109[0],
		...Array.from({ length: 9 }, (_, rang) =>
			carte44109({
				date: '2022-05-02',
				composition: { maisons: 0, appartements: 1, dependances: 0, locaux_activite: 0 },
				surface_batie: 30 + rang,
				prix: 150000 + rang,
				hors_stats_vefa: false
			})
		)
	]
});

// --- Variants of plan 04.5-08 (C3): sales declared by number and suffix -------------------------
//
// SYNTHETIC, shaped on the PostGIS fixture of the API (`ADRESSES_SUFFIXES` and `VENTES_SUFFIXES`,
// api/backend/tests/integration/test_adresse_ventes_contract.py): one street, « 2 », « 2 bis » and
// « 4 quinquies »; sales declared at « 2 » (2025-03), « 2 B » (2024-05, 2022-06 and a VEFA 2023-02),
// « 2 C » (2023-07) and « 2 Z » (2021-08, no BAN « 2 z »). Each address has the answer of both rules,
// as `vefa=incluses` serves them (the page asks for the VEFA since 04.5-07):
//   - `numero` (C37, the default, what the page asked at the starting SHA of plan 08): « 2 » lists
//     every sale declared at its number, suffixes included; an address with a suffix is
//     `suffixe_non_distingue`;
//   - `numero_et_suffixe` (C44): « 2 » keeps its sale without suffix, « 2 bis » its « 2 B » sales and
//     VEFA, « quinquies » (no measured DVF code) is `suffixe_sans_correspondance`.
// Prices, compositions and surfaces are invented; frozen at the starting SHA of plan 08 (acc3eeff),
// before the page asks for the rule.

export const BAN_ID_SUFFIXE_2 = '85195_0911_00002';
export const BAN_ID_SUFFIXE_2_BIS = '85195_0911_00002_bis';
export const BAN_ID_SUFFIXE_QUINQUIES = '85195_0911_00004_quinquies';
export const PERIODE_SUFFIXES = '2021-2025';
const GENERATION_SUFFIXES = '85195a0b1c2d3e4f';

/** The query of the first page under each rule: the page asked `numero` (the default) until plan 08. */
export const PREMIERE_PAGE_REGLE_NUMERO: Readonly<Record<string, string>> = { page: '1', vefa: 'incluses' };
export const PREMIERE_PAGE_REGLE_SUFFIXE: Readonly<Record<string, string>> = { page: '1', vefa: 'incluses', regle: 'numero_et_suffixe' };

function carteSuffixe(date: string, surface: number, prix: number | null, partiel: Record<string, unknown> = {}) {
	return carte44109({
		date,
		composition: { maisons: 1, appartements: 0, dependances: 0, locaux_activite: 0 },
		surface_batie: surface,
		prix,
		prix_m2: prix === null ? null : Math.round(prix / surface),
		hors_stats_vefa: false,
		...partiel
	});
}

/** The sales declared at the street, by declared number and suffix (served order: date descending). */
const DECLAREE_2 = carteSuffixe('2025-03-01', 90, null, { prix_affiche: false, minimisation: { motif: 'vente_isolee_recente' } });
const DECLAREE_2B_2024 = carteSuffixe('2024-05-01', 91, 302000);
const DECLAREE_2B_VEFA = carteSuffixe('2023-02-01', 70, 310000, { nature: NATURE_VEFA, hors_stats_vefa: true });
const DECLAREE_2B_2022 = carteSuffixe('2022-06-01', 92, 303000);
const DECLAREE_2C = carteSuffixe('2023-07-01', 93, 304000);
const DECLAREE_2Z = carteSuffixe('2021-08-01', 94, 305000);

function listeSuffixe(partiel: Record<string, unknown>): Reply {
	return {
		status: 200,
		body: {
			statut: 'ok',
			donnees: {
				etat: 'value',
				motif: null,
				rattachement: 'adresse_declaree_vente',
				source: SOURCE_DVF,
				periode: PERIODE_SUFFIXES,
				page: 1,
				pages: 1,
				par_page: 10,
				generation: GENERATION_SUFFIXES,
				...partiel
			}
		}
	};
}

function motifSuffixe(motif: string): Reply {
	return listeSuffixe({ etat: 'not_available', motif, periode: null, total: null, nombre_vefa: null, pages: null, generation: null, ventes: [] });
}

/** Rule `numero` (C37): « 2 » shows the six mutations declared at its number, VEFA included. */
export const DECLAREES_2_REGLE_NUMERO = listeSuffixe({
	total: 6,
	nombre_vefa: 1,
	ventes: [DECLAREE_2, DECLAREE_2B_2024, DECLAREE_2C, DECLAREE_2B_VEFA, DECLAREE_2B_2022, DECLAREE_2Z]
});
export const DECLAREES_2_BIS_REGLE_NUMERO = motifSuffixe('suffixe_non_distingue');
export const DECLAREES_QUINQUIES_REGLE_NUMERO = motifSuffixe('suffixe_non_distingue');

/** Rule `numero_et_suffixe` (C44): « 2 » its sale without suffix, « 2 bis » its « 2 B » mutations. */
export const DECLAREES_2_REGLE_SUFFIXE = listeSuffixe({ total: 1, nombre_vefa: 0, ventes: [DECLAREE_2] });
export const DECLAREES_2_BIS_REGLE_SUFFIXE = listeSuffixe({ total: 3, nombre_vefa: 1, ventes: [DECLAREE_2B_2024, DECLAREE_2B_VEFA, DECLAREE_2B_2022] });
export const DECLAREES_QUINQUIES_REGLE_SUFFIXE = motifSuffixe('suffixe_sans_correspondance');
/** A served generation imported without the suffix, under the suffix rule (C44). */
export const DECLAREES_SUFFIXE_NON_IMPORTE = motifSuffixe('suffixe_non_importe');

/** The three addresses: their BAN identity (number, suffix) and both answers. */
export const ADRESSES_SUFFIXES = {
	'2': { banId: BAN_ID_SUFFIXE_2, numero: '2', rep: null, numeroRegle: DECLAREES_2_REGLE_NUMERO, suffixeRegle: DECLAREES_2_REGLE_SUFFIXE },
	'2-bis': { banId: BAN_ID_SUFFIXE_2_BIS, numero: '2', rep: 'bis', numeroRegle: DECLAREES_2_BIS_REGLE_NUMERO, suffixeRegle: DECLAREES_2_BIS_REGLE_SUFFIXE },
	'4-quinquies': {
		banId: BAN_ID_SUFFIXE_QUINQUIES,
		numero: '4',
		rep: 'quinquies',
		numeroRegle: DECLAREES_QUINQUIES_REGLE_NUMERO,
		suffixeRegle: DECLAREES_QUINQUIES_REGLE_SUFFIXE
	}
} as const;
export type CleSuffixe = keyof typeof ADRESSES_SUFFIXES;
