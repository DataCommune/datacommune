// Universal load of `/comparer?communes=a,b,c,d&diff=1` (UI-SPEC « /comparer », D-48, D-49).
// Server-rendered on the first request, re-run in the browser when the selection changes. The link
// is the selection: it is read here, never the tab storage (the page aligns the storage on it).
//   - 0 commune: the empty comparator, no request;
//   - 1 commune: its identity from the referential, no comparison call;
//   - 2 to 4: ONE call to `/v1/communes/comparaison`, composed without any computation
//     (`composeComparaison`); the codes the referential does not know come back in
//     `codes_inconnus` and are worded « Lien altéré »; an overseas collectivity comes back in
//     `codes_hors_couverture` and is worded « Hors couverture » (04.1-06, D-123), never altered;
//   - the comparison does not answer (503, or any failure): the outage state, nothing partial;
//     the failure is typed (`detail`): a rate limit or a refused access is not an outage of the
//     service and the page says so (WR-01).
// No departement is written here: a commune out of coverage keeps its column (D-34, D-44).
//
// 04-07 (D-102, same nature only): `?adresses=id1,id2` reads ONE call to `/v1/adresses/comparaison`
// for 2 to 4 keys; the keys the BAN does not know come back in `ids_ecartes`. One key reads the
// identity of its page (`/v1/adresse/{id}`), never a comparison. A link carrying both natures shows
// the communes.
//
// 04-11 (D-102): `designes=<ban_id>:<numero>` (addresses only) travels with the comparison call; the
// API keeps a pair only when the number is declared at this address. A pair it sets aside, or that
// the link carried in a form that is not kept, is said « Lien altéré » with the address named, and
// leaves the link (the page rewrites it). No designation is read from or written to any storage.
//
// 04-10 (CMP-02): `?dpe=n1,n2` reads ONE call to `/v1/dpe/comparaison` for 2 to 4 numbers; the
// numbers the instance does not know come back in `numeros_ecartes`. One number reads its document
// (`/v1/dpe/{numero}`, no upstream source), never a comparison. A link carrying two natures shows
// the first one (communes → addresses → dpe).
import { ApiError } from '$lib/api/client';
import { getCommuneComparaison } from '$lib/api/commune';
import { getCommuneReferentiel } from '$lib/api/communes';
import { getComparaisonAdresses, getFicheAdresse } from '$lib/api/adresses';
import { getComparaisonDocuments, getDocumentDpe } from '$lib/api/documents-dpe';
import { libelleCommune, libelleVoie, titreAdresseA } from '$lib/data/recherche-lieux';
import { titreDocument } from '$lib/data/document-dpe';
import {
	composeComparaison,
	composeComparaisonAdresses,
	composeComparaisonDocuments,
	type ComparaisonModel,
	type NatureComparaison
} from '$lib/data/commune-comparison';
import {
	lireSelectionNature,
	messageAdresseInconnue,
	messageDesignationRetiree,
	messageCodeHorsCouverture,
	messageCodeInconnu,
	messageDpeInconnu,
	statutDe,
	type SelectionComparaison
} from '$lib/data/commune-comparison-selection';
import { communeParente, refusCommuneEtArrondissement, type ConflitCommuneArrondissement } from '$lib/data/arrondissements';
import { detailPanne, type DetailPanne } from '$lib/data/panne';
import { markOutage, type SetHeaders } from '$lib/utils/etat-page';
import type { PageLoad } from './$types';

export const prerender = false;

/** A commune (or an address) shown alone, before a second one is added. */
export interface CommuneSeule {
	code: string;
	/** From the referential; `null` when it did not answer (the code is shown instead). */
	nom: string | null;
	/** Address only: link of its page and « {CP} {Commune} ». */
	lien?: string;
	identite?: string;
}

export type ComparerData = { nature: NatureComparaison } & (
	| { etat: 'vide'; selection: SelectionComparaison }
	| { etat: 'une'; selection: SelectionComparaison; commune: CommuneSeule }
	| { etat: 'prete'; selection: SelectionComparaison; modele: ComparaisonModel }
	| { etat: 'panne'; selection: SelectionComparaison; detail: DetailPanne; conflit?: ConflitCommuneArrondissement }
);

/**
 * The comparison did not answer: one outage state for the whole page, marked for `hooks.server.ts`, which
 * answers 503 with `Retry-After` while the page stays rendered (R-21; plan 04.7-10, decision 11). A robot
 * or a cache then never keeps an « indisponible » page as a 200. `setHeaders` does nothing in the browser.
 */
function panne(nature: NatureComparaison, selection: SelectionComparaison, err: unknown, setHeaders: SetHeaders | undefined): ComparerData {
	markOutage(setHeaders, err instanceof ApiError ? err.retryAfter : null);
	return { nature, etat: 'panne', selection, detail: detailPanne(err) };
}

function sans(
	selection: SelectionComparaison,
	inconnus: readonly string[],
	codes: string[],
	message: (code: string) => string = messageCodeInconnu
): SelectionComparaison {
	return {
		...selection,
		statut: statutDe(codes),
		codes,
		messages: [...selection.messages, ...inconnus.map(message)]
	};
}

/** One address, before a second one is added: named by the identity of its page. */
async function adresseSeule(selection: SelectionComparaison, id: string, fetchImpl: typeof fetch): Promise<ComparerData> {
	const nature = 'adresse' as const;
	const lien = `/adresse/${encodeURIComponent(id)}`;
	const issue = await getFicheAdresse(fetchImpl, id);
	// The BAN does not know the key (or it names a street): the link was altered.
	if (issue.kind === 'inconnue' || issue.kind === 'voie') {
		return { nature, etat: 'vide', selection: sans(selection, [id], [], messageAdresseInconnue) };
	}
	// Identity unavailable: the address stays, named by its key.
	if (issue.kind === 'panne') return { nature, etat: 'une', selection, commune: { code: id, nom: null, lien } };
	const { identite } = issue.fiche;
	const commune = identite.commune.nom ?? identite.commune.code_insee;
	const nom = libelleVoie(identite.numero, identite.rep, identite.nom_voie) || titreAdresseA(commune, identite.departement);
	return { nature, etat: 'une', selection, commune: { code: id, nom, lien, identite: libelleCommune(identite.code_postal, commune) } };
}

async function adresses(selection: SelectionComparaison, fetchImpl: typeof fetch, setHeaders?: SetHeaders): Promise<ComparerData> {
	const nature = 'adresse' as const;
	if (selection.statut === 'une') return adresseSeule(selection, selection.codes[0], fetchImpl);
	let modele: ComparaisonModel;
	try {
		modele = composeComparaisonAdresses(await getComparaisonAdresses(selection.codes, fetchImpl, selection.designations ?? []));
	} catch (err) {
		// One outage state for the whole comparison: no column is shown partially.
		return panne(nature, selection, err, setHeaders);
	}
	const codes = modele.colonnes.map((colonne) => colonne.code);
	const lue = sans(selection, modele.codesInconnus, codes, messageAdresseInconnue);
	// The designations the API kept, in column order; the others are said with their address named.
	const gardees = modele.colonnes.flatMap((colonne) =>
		colonne.designation ? [{ ban_id: colonne.code, numero: colonne.designation.numero }] : []
	);
	delete lue.designations;
	if (gardees.length) lue.designations = gardees;
	const retirees = [
		...(selection.designationsEcartees ?? []).map((paire) => paire.ban_id),
		...(modele.designationsEcartees ?? []).map((paire) => paire.code)
	];
	for (const colonne of modele.colonnes) {
		if (retirees.includes(colonne.code) && !colonne.designation) lue.messages.push(messageDesignationRetiree(colonne.nom));
	}
	delete lue.designationsEcartees;
	if (codes.length >= 2) return { nature, etat: 'prete', selection: lue, modele };
	if (codes.length === 1) {
		const [colonne] = modele.colonnes;
		return {
			nature,
			etat: 'une',
			selection: lue,
			commune: { code: colonne.code, nom: colonne.nom, lien: colonne.lien, identite: colonne.identite }
		};
	}
	return { nature, etat: 'vide', selection: lue };
}

/** One document, before a second one is added: named by its number, read once to know it. */
async function documentSeul(selection: SelectionComparaison, numero: string, fetchImpl: typeof fetch): Promise<ComparerData> {
	const nature = 'dpe' as const;
	const issue = await getDocumentDpe(fetchImpl, numero);
	// No document carries the number in the served departements: the link was altered.
	if (issue.kind === 'inconnu') return { nature, etat: 'vide', selection: sans(selection, [numero], [], messageDpeInconnu) };
	// Document unavailable: it stays, named by its number all the same.
	return { nature, etat: 'une', selection, commune: { code: numero, nom: titreDocument(numero), lien: `/dpe/${encodeURIComponent(numero)}` } };
}

async function documents(selection: SelectionComparaison, fetchImpl: typeof fetch, setHeaders?: SetHeaders): Promise<ComparerData> {
	const nature = 'dpe' as const;
	if (selection.statut === 'une') return documentSeul(selection, selection.codes[0], fetchImpl);
	let modele: ComparaisonModel;
	try {
		modele = composeComparaisonDocuments(await getComparaisonDocuments(selection.codes, fetchImpl));
	} catch (err) {
		// One outage state for the whole comparison (refused publication included): nothing partial.
		return panne(nature, selection, err, setHeaders);
	}
	const codes = modele.colonnes.map((colonne) => colonne.code);
	const lue = sans(selection, modele.codesInconnus, codes, messageDpeInconnu);
	if (codes.length >= 2) return { nature, etat: 'prete', selection: lue, modele };
	if (codes.length === 1) {
		const [colonne] = modele.colonnes;
		return { nature, etat: 'une', selection: lue, commune: { code: colonne.code, nom: colonne.nom, lien: colonne.lien } };
	}
	return { nature, etat: 'vide', selection: lue };
}

async function communeSeule(selection: SelectionComparaison, code: string, fetchImpl: typeof fetch): Promise<ComparerData> {
	const nature = 'commune' as const;
	let nom: string | null = null;
	try {
		// The referential lists an arrondissement only on request (a plain commune keeps its request).
		const ligne = await getCommuneReferentiel(code, fetchImpl, { inclureArrondissements: communeParente(code) !== null });
		// The referential does not know the code: the link was altered.
		if (!ligne) return { nature, etat: 'vide', selection: sans(selection, [code], []) };
		nom = ligne.nom;
	} catch {
		// Referential unavailable: the commune stays, named by its code.
	}
	return { nature, etat: 'une', selection, commune: { code, nom } };
}

export const load: PageLoad = async ({ url, fetch, setHeaders }): Promise<ComparerData> => {
	const { nature: lue, ...selection } = lireSelectionNature(url.searchParams);
	if (lue === 'adresse') {
		if (selection.statut === 'vide') return { nature: lue, etat: 'vide', selection };
		return adresses(selection, fetch, setHeaders);
	}
	if (lue === 'dpe') {
		if (selection.statut === 'vide') return { nature: lue, etat: 'vide', selection };
		return documents(selection, fetch, setHeaders);
	}
	const nature = 'commune' as const;
	if (selection.statut === 'vide') return { nature, etat: 'vide', selection };
	if (selection.statut === 'une') return communeSeule(selection, selection.codes[0], fetch);

	let modele: ComparaisonModel;
	try {
		const reponse = await getCommuneComparaison(selection.codes, fetch);
		modele = composeComparaison(reponse.donnees, reponse.meta?.source ?? null);
	} catch (err) {
		// A commune against its own arrondissement (422): said as such, with the two zones to choose from.
		// A refusal the visitor solves by removing one of the two, not an outage: it is not marked (R-21).
		const refus = err instanceof ApiError ? refusCommuneEtArrondissement(err.status, typeof err.detail === 'string' ? err.detail : null, selection.codes) : null;
		if (refus) {
			return { nature, etat: 'panne', selection, detail: { statut: 422, titre: refus.titre, message: refus.message }, ...(refus.conflit ? { conflit: refus.conflit } : {}) };
		}
		// One outage state for the whole comparison: no column is shown partially.
		return panne(nature, selection, err, setHeaders);
	}
	const codes = modele.colonnes.map((colonne) => colonne.code);
	const lueCommunes = {
		...sans(selection, modele.codesInconnus, codes),
		horsCouverture: (modele.codesHorsCouverture ?? []).map(messageCodeHorsCouverture)
	};
	if (codes.length >= 2) return { nature, etat: 'prete', selection: lueCommunes, modele };
	if (codes.length === 1) {
		return { nature, etat: 'une', selection: lueCommunes, commune: { code: codes[0], nom: modele.colonnes[0].nom } };
	}
	return { nature, etat: 'vide', selection: lueCommunes };
};
