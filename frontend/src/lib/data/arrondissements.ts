// Arrondissements municipaux of Paris, Lyon and Marseille (04.7-09). Pure module, zero Svelte imports.
//
// The API serves `type_zone` / `commune_parent` on the referential rows and on the synthesis; this
// module only holds what the code itself says for the 45 arrondissements (the INSEE ranges are
// fixed by law) so that a page can word its identity and breadcrumb without a second request, and
// so that the comparator can tell a whole from its part before the API answers 422. Nothing here
// decides what is served: a row the API did not mark is still a commune.

/** The three communes divided into arrondissements, by INSEE code. */
export const COMMUNES_PLM = [
	{ code: '75056', nom: 'Paris', premier: 75101, dernier: 75120 },
	{ code: '69123', nom: 'Lyon', premier: 69381, dernier: 69389 },
	{ code: '13055', nom: 'Marseille', premier: 13201, dernier: 13216 }
] as const;

export interface CommuneParente {
	code: string;
	nom: string;
}

function communePlm(code: string) {
	if (!/^\d{5}$/.test(code)) return null;
	const n = Number(code);
	return COMMUNES_PLM.find((c) => n >= c.premier && n <= c.dernier) ?? null;
}

/** The commune an arrondissement code belongs to (75112 → Paris), null for any other code. */
export function communeParente(code: string): CommuneParente | null {
	const plm = communePlm(code);
	return plm ? { code: plm.code, nom: plm.nom } : null;
}

/** « 1er », « 12e »: the rank of the arrondissement in its commune, from the code. */
export function rangArrondissement(code: string): number | null {
	const plm = communePlm(code);
	return plm ? Number(code) - plm.premier + 1 : null;
}

/** « 1er arrondissement », « 12e arrondissement »: the label under its commune (breadcrumb). */
export function libelleCourtArrondissement(code: string): string | null {
	const rang = rangArrondissement(code);
	return rang === null ? null : `${rang === 1 ? '1er' : `${rang}e`} arrondissement`;
}

/** « 12e arrondissement de Paris »: the readable title of the fiche. */
export function libelleArrondissement(code: string): string | null {
	const court = libelleCourtArrondissement(code);
	const parente = communeParente(code);
	return court && parente ? `${court} de ${parente.nom}` : null;
}

/** A row of the referential, the search or the directory: the two fields the API adds. */
export interface ZoneServie {
	code_insee: string;
	type_zone?: 'commune' | 'arrondissement' | null;
	commune_parent?: string | null;
}

/** True when the API marks the row as an arrondissement (an unmarked row is a commune). */
export function estArrondissement(zone: Pick<ZoneServie, 'type_zone'>): boolean {
	return zone.type_zone === 'arrondissement';
}

/**
 * Directory rows with each arrondissement placed under its commune, never merged into it. A page of
 * the directory is sorted by the API (population or name), which scatters the arrondissements;
 * here the commune keeps its place and its arrondissements follow it, in code order. An
 * arrondissement whose commune is not on the page (another page, or filtered out) keeps its own
 * place and says whose it is (`communeAbsente`).
 */
export function grouperParCommune<T extends ZoneServie>(
	lignes: readonly T[]
): { ligne: T; sous: boolean; communeAbsente: CommuneParente | null }[] {
	const parents = new Set(lignes.filter((l) => !estArrondissement(l)).map((l) => l.code_insee));
	const enfants = new Map<string, T[]>();
	for (const ligne of lignes) {
		if (!estArrondissement(ligne)) continue;
		const parent = ligne.commune_parent ?? communeParente(ligne.code_insee)?.code ?? null;
		if (parent && parents.has(parent)) {
			enfants.set(parent, [...(enfants.get(parent) ?? []), ligne]);
		}
	}
	const rangees: { ligne: T; sous: boolean; communeAbsente: CommuneParente | null }[] = [];
	for (const ligne of lignes) {
		if (!estArrondissement(ligne)) {
			rangees.push({ ligne, sous: false, communeAbsente: null });
			const suite = (enfants.get(ligne.code_insee) ?? []).sort((a, b) => a.code_insee.localeCompare(b.code_insee));
			for (const enfant of suite) rangees.push({ ligne: enfant, sous: true, communeAbsente: null });
			continue;
		}
		const parent = ligne.commune_parent ?? communeParente(ligne.code_insee)?.code ?? null;
		if (parent && parents.has(parent)) continue; // already placed under its commune
		rangees.push({ ligne, sous: false, communeAbsente: parent ? (communeParente(ligne.code_insee) ?? { code: parent, nom: parent }) : null });
	}
	return rangees;
}

/**
 * A commune of the selection and one of its own arrondissements: the whole and its part, which the
 * API refuses to compare (422). Returns the first such pair, in selection order.
 */
export function communeEtSonArrondissement(codes: readonly string[]): { commune: string; arrondissement: string } | null {
	for (const arrondissement of codes) {
		const parente = communeParente(arrondissement);
		if (parente && codes.includes(parente.code)) return { commune: parente.code, arrondissement };
	}
	return null;
}

/** « Arrondissement de Paris »: the mention of a served arrondissement in a list, null for a commune. */
export function mentionArrondissement(zone: ZoneServie): string | null {
	if (!estArrondissement(zone)) return null;
	const parente = zone.commune_parent ? (COMMUNES_PLM.find((c) => c.code === zone.commune_parent) ?? null) : communeParente(zone.code_insee);
	return parente ? `Arrondissement de ${parente.nom}` : 'Arrondissement municipal';
}

// --- comparator: a whole and its part -------------------------------------------------------

export const TITRE_COMMUNE_ET_ARRONDISSEMENT = 'Une commune et son arrondissement ne se comparent pas';

export interface ConflitCommuneArrondissement {
	commune: string;
	arrondissement: string;
}

/**
 * The API refuses (422) a comparison of a commune with one of its own arrondissements: the part is
 * contained in the whole, so no gap between them would mean anything. Recognised here by the status
 * and, to name the two zones, by the selection; the wording is a constant of the client, never the
 * server text. Returns null for any other refusal (it keeps its generic wording).
 */
export function refusCommuneEtArrondissement(
	statut: number | null,
	detailServeur: string | null,
	codes: readonly string[]
): { conflit: ConflitCommuneArrondissement | null; titre: string; message: string } | null {
	if (statut !== 422) return null;
	const conflit = communeEtSonArrondissement(codes);
	if (!conflit && !/arrondissement/i.test(detailServeur ?? '')) return null;
	const parente = conflit ? communeParente(conflit.arrondissement) : null;
	const message = conflit && parente
		? `${parente.nom} et son ${libelleCourtArrondissement(conflit.arrondissement)} ne se comparent pas : l'arrondissement fait partie de la commune. Retirez l'un des deux, ou comparez des arrondissements entre eux.`
		: "Une commune et l'un de ses arrondissements ne se comparent pas : l'arrondissement fait partie de la commune. Retirez l'un des deux, ou comparez des arrondissements entre eux.";
	return { conflit, titre: TITRE_COMMUNE_ET_ARRONDISSEMENT, message };
}

const NNBSP = String.fromCharCode(0x202f); // narrow no-break space

// --- the other mesh (R-09) --------------------------------------------------------------------

/** The arrondissements of a commune of Paris, Lyon or Marseille, in code order; [] for any other code. */
export function arrondissementsDe(code: string): { code: string; libelle: string }[] {
	const plm = COMMUNES_PLM.find((c) => c.code === code);
	if (!plm) return [];
	return Array.from({ length: plm.dernier - plm.premier + 1 }, (_, i) => {
		const arrondissement = String(plm.premier + i);
		return { code: arrondissement, libelle: libelleCourtArrondissement(arrondissement)! };
	});
}

/** Anchor of the list of the arrondissements on the fiche of their commune: where « voir » leads. */
export const ANCRE_ARRONDISSEMENTS = 'arrondissements';

/**
 * Head of an arrondissement fiche that shows values of its commune (`heritages`): the marked rows hold for the
 * whole commune. Wording validated by Ianis (2026-11-10).
 */
export function chapoArrondissementAvecHeritage(ville: string): string {
	return `Les valeurs de cette page valent pour l’arrondissement entier, sauf celles marquées «${NNBSP}Commune${NNBSP}», qui valent pour tout ${ville}${NNBSP}; jamais pour une adresse.`;
}
