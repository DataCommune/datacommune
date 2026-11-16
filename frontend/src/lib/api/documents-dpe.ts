// One DPE document read by its ADEME number (`GET /v1/dpe/{numero}`, 04-04, D-102, LIEU-03).
// Types mirror `DocumentDpePublic` of `api/backend/app/dpe/document_public.py`
// (`contracts/openapi.v1.json`). The page renders what the API serves: the number as is, the
// ADEME datasets of the document with their catalogue page, the method axis, the dates and the
// declared landmarks. Nothing here attributes the document to a dwelling, a sale or a commune.
import { api } from './client';
import { projeterAdresseDeclaree, projeterValeur, type AdresseDeclaree, type ValeurLieu } from './adresses';
import type { OutOfCoverageReason } from '$lib/types/fiche';
import { detailPanne, type DetailPanne } from '$lib/data/panne';

/** Default ADEME number form (block E of 04-MESURE-DPE.md), the pattern of the API route. */
export const NUMERO_DPE_RE = /^[0-9A-Z]{13}$/;

export type DatasetDpe = 'dpe03existant' | 'dpe02neuf';

/** One ADEME dataset of the document; `url` is the catalogue constant served by the API. */
export interface SourceJeu {
	dataset: DatasetDpe;
	url: string;
}

/** Contract V3: the ADEME number as is and the datasets established for it (possibly none). */
export interface SourceIdentity {
	reference: string;
	sources: SourceJeu[];
}

export type MethodeDocument = 'ancienne' | 'actuelle' | 'inconnue';

/**
 * A label of the document: its served class, the yearly total (`mesure`) and the per-square-metre
 * value published by the ADEME (`mesure_m2`, 04.1-12, C51), each as served: the per-m² value is never
 * derived from the total and the surface (D45-05).
 */
export interface RepereEtiquette {
	classe: ValeurLieu;
	mesure: ValeurLieu;
	mesure_m2: ValeurLieu;
}

export interface ReperesDocument {
	etiquette_energie: RepereEtiquette;
	etiquette_climat: RepereEtiquette;
	/** What the document describes: building type and declared surface (landmark ③). */
	logement_decrit: { type_batiment: ValeurLieu; surface: ValeurLieu };
	/** The served yearly cost, one value: no range is made up. */
	cout_annuel: ValeurLieu;
}

/** Closed list of the served status codes (one line of « Provenance et validité »). */
export type CodeStatut =
	| 'en_vigueur'
	| 'expire'
	| 'remplace'
	| 'remplacement_non_verifie'
	| 'ancienne_methode'
	| 'validite_inconnue'
	| 'dates_incoherentes'
	| 'etablissement_futur'
	| 'retire';

/**
 * The served status of the three axes (D-111): `validite` is the calendar axis as read, `date` the
 * end of validity of an expired document, `remplace_par` the verified successor, `remplace` the
 * number the document declares it replaces. A key outside the closed list is kept, never dropped.
 */
export interface StatutDocument {
	code: CodeStatut | string;
	validite: string | null;
	date: string | null;
	remplace_par: string | null;
	remplace: string | null;
}

/** BAN address the source attaches the document to (key equality, never verified). */
export interface AdresseBanDocument {
	etat: 'retenue' | 'sans_cle' | 'cle_inconnue' | 'hors_couverture' | 'indisponible';
	ban_id: string | null;
	libelle: string | null;
	numero: string | null;
	rep: string | null;
	nom_voie: string | null;
	commune: { code_insee: string; nom: string | null } | null;
	motif: OutOfCoverageReason | null;
}

/** Public model of the document page: the members the page renders, picked one by one. */
export interface DocumentDpePublic {
	numero: string;
	source_identity: SourceIdentity;
	/** `retire`: the source no longer publishes the document; no description is served. */
	disponibilite: 'disponible' | 'retire';
	methode: MethodeDocument;
	statut: StatutDocument;
	dates: { etablissement: string | null; fin_validite: string | null } | null;
	reperes: ReperesDocument | null;
	/** The served columns « Ce que déclare le document »; empty for a retired document. */
	caracteristiques: ValeurLieu[];
	/** Published as typed, masked with its reason, or absent (P-ADR-01, projection of 04-19). */
	adresse_declaree: AdresseDeclaree | null;
	adresse_ban: AdresseBanDocument | null;
	/** `true` only when the typed address strictly diverges from its BAN key. */
	adresse_divergente: boolean | null;
	provenance: { source: 'ADEME'; date_import: string | null };
}

/** One outcome per served answer; the page renders exactly one state for each (SEM-01). */
export type IssueDocumentDpe =
	| { kind: 'ok'; document: DocumentDpePublic }
	/** 404 `dpe_inconnu`: the served departements of the instance, never the input. */
	| { kind: 'inconnu'; departements: string[] }
	| { kind: 'panne'; panne: DetailPanne };

type Brut = Record<string, unknown> | null | undefined;

const texte = (value: unknown): string | null => (typeof value === 'string' ? value : null);

const DATASETS: readonly string[] = ['dpe03existant', 'dpe02neuf'];

function projeterSources(brut: unknown): SourceJeu[] {
	if (!Array.isArray(brut)) return [];
	return (brut as Brut[]).flatMap((source) => {
		const dataset = texte(source?.dataset);
		const url = texte(source?.url);
		return dataset && DATASETS.includes(dataset) && url ? [{ dataset: dataset as DatasetDpe, url }] : [];
	});
}

/**
 * A per-m² value the answer does not serve (an answer older than 04.1-12) is the outage of its line,
 * under the field the contract gives it: never « not reported » (the API did not say so), never zero.
 */
function projeterEtiquette(brut: Brut, champM2: string): RepereEtiquette {
	const m2 = brut?.mesure_m2 as Brut;
	return {
		classe: projeterValeur(brut?.classe as Brut),
		mesure: projeterValeur(brut?.mesure as Brut),
		mesure_m2: m2 ? projeterValeur(m2) : projeterValeur({ field_id: champM2 })
	};
}

function projeterReperes(brut: Brut): ReperesDocument | null {
	if (!brut) return null;
	const logement = (brut.logement_decrit ?? {}) as Record<string, unknown>;
	return {
		etiquette_energie: projeterEtiquette(brut.etiquette_energie as Brut, 'dpe.document.conso_ep_m2'),
		etiquette_climat: projeterEtiquette(brut.etiquette_climat as Brut, 'dpe.document.emissions_ges_m2'),
		logement_decrit: {
			type_batiment: projeterValeur(logement.type_batiment as Brut),
			surface: projeterValeur(logement.surface as Brut)
		},
		cout_annuel: projeterValeur(brut.cout_annuel as Brut)
	};
}

/** A number that becomes a link `/dpe/{numero}` is kept only in the ADEME form. */
const numeroLien = (value: unknown): string | null => {
	const numero = texte(value);
	return numero && NUMERO_DPE_RE.test(numero) ? numero : null;
};

function projeterStatut(brut: Brut): StatutDocument {
	return {
		// A status without a code is not a status: it is said by the neutral label (provisional).
		code: texte(brut?.code) ?? '',
		validite: texte(brut?.validite),
		date: texte(brut?.date),
		remplace_par: numeroLien(brut?.remplace_par),
		remplace: numeroLien(brut?.remplace)
	};
}

const ETATS_BAN: readonly string[] = ['retenue', 'sans_cle', 'cle_inconnue', 'hors_couverture', 'indisponible'];

function projeterAdresseBan(brut: Brut): AdresseBanDocument | null {
	if (!brut) return null;
	const etat = texte(brut.etat);
	const commune = brut.commune as Brut;
	// An unreadable state is never a retained address: it carries no link.
	const retenue = etat === 'retenue';
	return {
		etat: etat && ETATS_BAN.includes(etat) ? (etat as AdresseBanDocument['etat']) : 'indisponible',
		ban_id: retenue ? texte(brut.ban_id) : null,
		libelle: retenue ? texte(brut.libelle) : null,
		numero: retenue ? texte(brut.numero) : null,
		rep: retenue ? texte(brut.rep) : null,
		nom_voie: retenue ? texte(brut.nom_voie) : null,
		commune: retenue && commune ? { code_insee: texte(commune.code_insee) ?? '', nom: texte(commune.nom) } : null,
		motif: (texte(brut.motif) as OutOfCoverageReason | null) ?? null
	};
}

function projeterDates(brut: Brut): DocumentDpePublic['dates'] {
	if (!brut) return null;
	return { etablissement: texte(brut.etablissement), fin_validite: texte(brut.fin_validite) };
}

const METHODES: readonly string[] = ['ancienne', 'actuelle', 'inconnue'];

/** The served document, member by member: no other key of the answer reaches the SSR model. */
export function projeterDocument(donnees: Brut): DocumentDpePublic {
	const identite = (donnees?.source_identity ?? {}) as Record<string, unknown>;
	const numero = texte(donnees?.numero) ?? '';
	const methode = texte(donnees?.methode);
	const retire = donnees?.disponibilite === 'retire';
	return {
		numero,
		source_identity: { reference: texte(identite.reference) ?? numero, sources: projeterSources(identite.sources) },
		disponibilite: retire ? 'retire' : 'disponible',
		methode: methode && METHODES.includes(methode) ? (methode as MethodeDocument) : 'inconnue',
		statut: projeterStatut(donnees?.statut as Brut),
		// A retired document carries no description, whatever the answer holds.
		dates: retire ? null : projeterDates(donnees?.dates as Brut),
		reperes: retire ? null : projeterReperes(donnees?.reperes as Brut),
		caracteristiques: retire || !Array.isArray(donnees?.caracteristiques) ? [] : (donnees.caracteristiques as Brut[]).map(projeterValeur),
		adresse_declaree: retire ? null : projeterAdresseDeclaree(donnees?.adresse_declaree as Brut),
		adresse_ban: retire ? null : projeterAdresseBan(donnees?.adresse_ban as Brut),
		adresse_divergente: !retire && typeof donnees?.adresse_divergente === 'boolean' ? donnees.adresse_divergente : null,
		provenance: { source: 'ADEME', date_import: texte((donnees?.provenance as Brut)?.date_import) }
	};
}

interface ProblemeDocument {
	code: string | null;
	departements: string[];
}

/** `code` and `couverture.departements` of a served problem, read before `api()` throws. */
async function lireProbleme(res: Response): Promise<ProblemeDocument> {
	try {
		const body = (await res.clone().json()) as Brut;
		const couverture = body?.couverture as Brut;
		const departements = Array.isArray(couverture?.departements)
			? (couverture.departements as unknown[]).filter((code): code is string => typeof code === 'string')
			: [];
		return { code: texte(body?.code), departements };
	} catch {
		return { code: null, departements: [] };
	}
}

/**
 * One document by its number, as a typed outcome. The caller checks the number against
 * `NUMERO_DPE_RE` first: a malformed input never reaches the API. A 404 `dpe_inconnu` carries the
 * coverage of the instance; a 5xx (refused publication included) or a network failure is an
 * outage; a 409, 429, 401 or 403 is typed by `detailPanne`, never worded as an outage.
 * Server-side only (the page loader): the projected model is what crosses the SSR serialization.
 */
export async function getDocumentDpe(fetchImpl: typeof fetch, numero: string): Promise<IssueDocumentDpe> {
	const probleme: ProblemeDocument = { code: null, departements: [] };
	// Same Response object handed back to `api()`: its auth-mode mark (client.ts) is kept.
	const fetchLisantLeProbleme: typeof fetch = async (input, init) => {
		const res = await fetchImpl(input, init);
		if (!res.ok) Object.assign(probleme, await lireProbleme(res));
		return res;
	};
	try {
		const res = await api<{ donnees: Brut }>(`/v1/dpe/${encodeURIComponent(numero)}`, {}, fetchLisantLeProbleme);
		return { kind: 'ok', document: projeterDocument(res.donnees) };
	} catch (err) {
		const panne = detailPanne(err);
		if (panne.statut === 404 && probleme.code === 'dpe_inconnu') {
			return { kind: 'inconnu', departements: probleme.departements };
		}
		return { kind: 'panne', panne };
	}
}

// --- Documents compared (`GET /v1/dpe/comparaison`, 04-10, CMP-02, D-116) ----------------------

/** Reason why no gap is served between two documents (the 03-09 rule and its document reasons). */
export type MotifNonCalculDocument =
	| 'donnee_absente'
	| 'periode_inconnue'
	| 'periodes_differentes'
	| 'unites_differentes'
	| 'definitions_differentes'
	| 'non_numerique'
	| 'methodes_differentes'
	| 'methode_indeterminee'
	| 'adresse_declaree_non_affichee';

export type CleGroupeComparaisonDocument = 'etiquettes' | 'logement' | 'cout_validite' | 'adresse';

/** A compared document: its number, method, status axes and declared address as published. */
export interface ColonneDocument {
	numero: string;
	disponibilite: 'disponible' | 'retire';
	methode: MethodeDocument;
	statut: CodeStatut | string;
	validite: string | null;
	adresse_declaree: AdresseDeclaree | null;
}

/** One document's value for a row (the value of its page) and the verdict the API served. */
export interface CelluleComparaisonDocument extends ValeurLieu {
	numero: string;
	ecart: { valeur: number; unite: string } | null;
	motif_non_calcul: MotifNonCalculDocument | null;
	verdict: 'identique' | 'different' | null;
	identique_a_la_reference: boolean | null;
}

export interface LigneComparaisonDocument {
	cle: string;
	field_id: string;
	identiques: boolean;
	cellules: CelluleComparaisonDocument[];
}

export interface GroupeComparaisonDocument {
	cle: CleGroupeComparaisonDocument;
	titre: string;
	lignes: LigneComparaisonDocument[];
}

export interface ComparaisonDocuments {
	colonnes: ColonneDocument[];
	groupes: GroupeComparaisonDocument[];
	numeros_ecartes: string[];
}

const CLES_GROUPES_DOCUMENT: readonly string[] = ['etiquettes', 'logement', 'cout_validite', 'adresse'];
const MOTIFS_NON_CALCUL_DOCUMENT: readonly string[] = [
	'donnee_absente',
	'periode_inconnue',
	'periodes_differentes',
	'unites_differentes',
	'definitions_differentes',
	'non_numerique',
	'methodes_differentes',
	'methode_indeterminee',
	'adresse_declaree_non_affichee'
];

const nombreServi = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);

function projeterColonneDocument(brut: Brut): ColonneDocument {
	const methode = texte(brut?.methode);
	return {
		numero: texte(brut?.numero) ?? '',
		disponibilite: brut?.disponibilite === 'retire' ? 'retire' : 'disponible',
		methode: methode && METHODES.includes(methode) ? (methode as MethodeDocument) : 'inconnue',
		statut: texte(brut?.statut) ?? '',
		validite: texte(brut?.validite),
		// The text is kept for a published address only: a masked one never carries it.
		adresse_declaree: projeterAdresseDeclaree(brut?.adresse_declaree as Brut)
	};
}

function projeterCelluleDocument(brut: Brut): CelluleComparaisonDocument {
	const ecart = brut?.ecart as Brut;
	const motif = texte(brut?.motif_non_calcul);
	const verdict = brut?.verdict;
	const valeur = projeterValeur(brut);
	return {
		...valeur,
		// A withheld value is never a text, whatever the answer holds (P-ADR-01).
		valeur: valeur.etat === 'value' ? valeur.valeur : null,
		numero: texte(brut?.numero) ?? '',
		ecart: ecart && nombreServi(ecart.valeur) !== null ? { valeur: nombreServi(ecart.valeur) as number, unite: texte(ecart.unite) ?? '' } : null,
		motif_non_calcul: motif && MOTIFS_NON_CALCUL_DOCUMENT.includes(motif) ? (motif as MotifNonCalculDocument) : null,
		verdict: verdict === 'identique' || verdict === 'different' ? verdict : null,
		identique_a_la_reference: typeof brut?.identique_a_la_reference === 'boolean' ? brut.identique_a_la_reference : null
	};
}

/**
 * The served comparison, picked member by member: the groups in the served order, every cell as
 * served. A group with an unknown key is dropped (no wording).
 */
export function projeterComparaisonDocuments(donnees: Brut): ComparaisonDocuments {
	const groupes = Array.isArray(donnees?.groupes) ? (donnees.groupes as Brut[]) : [];
	return {
		colonnes: (Array.isArray(donnees?.colonnes) ? (donnees.colonnes as Brut[]) : []).map(projeterColonneDocument),
		groupes: groupes.flatMap((groupe): GroupeComparaisonDocument[] => {
			const cle = texte(groupe?.cle);
			if (!cle || !CLES_GROUPES_DOCUMENT.includes(cle)) return [];
			const lignes = Array.isArray(groupe?.lignes) ? (groupe.lignes as Brut[]) : [];
			return [
				{
					cle: cle as CleGroupeComparaisonDocument,
					titre: texte(groupe?.titre) ?? '',
					lignes: lignes.map((ligne) => ({
						cle: texte(ligne?.cle) ?? '',
						field_id: texte(ligne?.field_id) ?? '',
						identiques: ligne?.identiques === true,
						cellules: (Array.isArray(ligne?.cellules) ? (ligne.cellules as Brut[]) : []).map(projeterCelluleDocument)
					}))
				}
			];
		}),
		numeros_ecartes: (Array.isArray(donnees?.numeros_ecartes) ? (donnees.numeros_ecartes as unknown[]) : []).flatMap((numero) =>
			typeof numero === 'string' ? [numero] : []
		)
	};
}

/**
 * Compare 2 to 4 documents in one call; the first is the reference of the gaps. The caller sends
 * numbers of the ADEME form only; the API serves every value, gap, reason and verdict. A failure
 * throws (`ApiError`), refused publication included (503).
 */
export async function getComparaisonDocuments(
	numeros: readonly string[],
	fetchImpl?: typeof fetch
): Promise<ComparaisonDocuments> {
	const sp = new URLSearchParams({ numeros: numeros.join(',') });
	const res = await api<{ donnees: Brut }>(`/v1/dpe/comparaison?${sp}`, {}, fetchImpl);
	return projeterComparaisonDocuments(res.donnees);
}
