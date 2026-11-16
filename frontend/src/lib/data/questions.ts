// Categories of the address fiche (4.9, reading of 06/11; CLASSEMENT.md, CADRAGE.md § 4). Pure module, zero
// Svelte runtime. Four categories hold the 43 themes of the API registry. Their rows come in groups, each in
// the order of the classement, a group named only where a long list needs a place to aim at; `autres` holds the
// rest of the category, in sight after them, nothing is nested. A test reads the registry from the API code and
// fails when a theme has no place or two.
// Everything said about a category is read from what the API serves: the closed row repeats served
// headlines, its counts come from the served lists restricted to the category. The front splits and counts
// nothing by itself (D-08, D-33).

import type { ResumeServi, ThemeRapport } from '$lib/api/rapport';
import { LIBELLE_STATUT, repliSansValeur } from '$lib/data/adresse-rapport';
import { perimetreSecteur, zoneDe } from '$lib/data/echelles';
import { nombreFr } from '$lib/utils/nombre-fr';
import { typographieFr } from '$lib/utils/typographie';

const NBSP = String.fromCharCode(0xa0); // no-break space

import { QUESTIONS, type IdPartie } from './categories';
export { QUESTIONS, type GroupeTable, type IdPartie, type PartieTable } from './categories';

/** A headline that says what it counts: the others read after the title of their theme. */
const ACCROCHES_AUTONOMES: ReadonlySet<string> = new Set(['ventes_parcelle', 'batiment', 'equipements', 'catnat', 'risques_commune']);

/**
 * Served signals kept out of the arrival line, the one place to tune it. Measured on 28 reports of 28
 * departments (06/11/2026): inondation 1/28, because `pointsAdresse` already keeps it to an established
 * point, and 21/28 at the commune scale, which never reaches the arrival; argiles 17/28, construction
 * 9/28, nothing else above 6/28. None lights up almost everywhere: the list is empty.
 */
const SIGNAUX_HORS_ARRIVEE: ReadonlySet<string> = new Set<string>();

export interface GroupePlace {
	titre: string | null;
	lignes: ThemeRapport[];
}

export interface PartiePlacee {
	id: IdPartie;
	titre: string;
	groupes: GroupePlace[];
	/** The rows of the groups, in their order. */
	premiers: ThemeRapport[];
	autres: ThemeRapport[];
	annonce: ThemeRapport[];
}

export interface Placement {
	/** The categories that have at least one served theme, each list holding what is served. */
	parties: PartiePlacee[];
	/** Served themes the table does not know (a front older than the API), in the served order. */
	autres: ThemeRapport[];
}

/** What the page hands to the snippet that renders a criterion in a category. */
export interface ArgsCritere {
	theme: ThemeRapport;
	/** `h5` under the title of a group, `h4` in a category, `h3` under « Autres critères ». */
	niveauTitre: 3 | 4 | 5;
}

const themesDe = (partie: Pick<PartiePlacee, 'premiers' | 'autres'>): ThemeRapport[] => [...partie.premiers, ...partie.autres];

/** The themes of `ids`, once each, that the category holds. */
const placesDans = (partie: Pick<PartiePlacee, 'premiers' | 'autres'>, ids: readonly string[]): ThemeRapport[] => {
	const voulus = new Set(ids);
	return themesDe(partie).filter((t) => voulus.has(t.id));
};

/** Places the served themes in the table. A category without a served theme does not exist. */
export function placerThemes(themes: readonly ThemeRapport[]): Placement {
	const parId = new Map(themes.map((t) => [t.id, t]));
	const servis = (ids: readonly string[]): ThemeRapport[] => ids.flatMap((id) => parId.get(id) ?? []);
	const dansLaTable = new Set(QUESTIONS.flatMap((p) => [...p.groupes.flatMap((g) => g.ids), ...p.autres]));
	const parties = QUESTIONS.flatMap((p): PartiePlacee[] => {
		const groupes = p.groupes.flatMap(({ titre, ids }) => {
			const lignes = servis(ids);
			return lignes.length ? [{ titre, lignes }] : [];
		});
		const premiers = groupes.flatMap((g) => g.lignes);
		const autres = servis(p.autres);
		if (premiers.length + autres.length === 0) return [];
		return [{ id: p.id, titre: p.titre, groupes, premiers, autres, annonce: servis(p.annonce) }];
	});
	return { parties, autres: themes.filter((t) => !dansLaTable.has(t.id)) };
}

/** The category that holds a theme of the registry, whatever its place in it. */
export const partieDeTheme = (id: string): IdPartie | null =>
	QUESTIONS.find((p) => p.groupes.some((g) => g.ids.includes(id)) || p.autres.includes(id))?.id ?? null;

/** The category an anchor of the fiche asks to open. */
export function cibleDeAncre(hash: string): IdPartie | null {
	const LISTES: Record<string, string> = { '#documents': 'dpe', '#ventes': 'ventes_parcelle', '#ventes-adresse': 'ventes_parcelle' };
	if (hash === '#s-comparables') return 'q-prix';
	const partie = QUESTIONS.find((p) => `#${p.id}` === hash);
	if (partie) return partie.id;
	return partieDeTheme(hash.startsWith('#critere-') ? hash.slice('#critere-'.length) : LISTES[hash]);
}

/** The served signals named at the arrival: never a vigilance of the whole commune (CADRAGE R3). */
export function signauxArrivee<T extends { id: string }>(pointsAdresse: readonly T[]): T[] {
	return pointsAdresse.filter((t) => !SIGNAUX_HORS_ARRIVEE.has(t.id));
}

/**
 * The closed row, a served result (CADRAGE R5): the headlines of the themes the table announces, under
 * their title when they do not say what they count, with « commune » when that is their scale (R8). A
 * theme without an answer has no result to announce: its state is in the signals. Without any headline, a
 * named count.
 */
export function annoncePartie(partie: PartiePlacee): string[] {
	const faits = partie.annonce.flatMap((t) => {
		if ((t.etat !== 'ok' && t.etat !== 'zero') || !t.accroche) return [];
		const fait = t.etat === 'ok' && ACCROCHES_AUTONOMES.has(t.id) ? t.accroche : `${typographieFr(t.titre)}${NBSP}: ${t.accroche}`;
		return [t.granularite.niveau === 'commune' ? `${fait} (commune)` : fait];
	});
	if (faits.length > 0) return faits;
	const total = themesDe(partie).length;
	return [`${nombreFr(total)} ${total > 1 ? 'critères' : 'critère'}`];
}

export interface SignalPartie {
	texte: string;
	ton: 'vigilance' | 'panne' | 'discret';
}

/**
 * What the closed row counts, from the served lists restricted to the category, never adding two scales:
 * vigilances at the address or around, vigilances of a wider scale, outages by name, gaps (§ 4.4 amended).
 * Nothing is said at zero: the closed row announces a result, and the arrival says when there is nothing.
 * An empty list without a served summary: nothing is counted in its place.
 */
export function signauxPartie(partie: PartiePlacee, resume: ResumeServi | null): SignalPartie[] {
	if (!resume) return [];
	const signaux: SignalPartie[] = [];
	const adresse = placesDans(partie, resume.pointsAdresse).length;
	if (adresse > 0) signaux.push({ texte: `${nombreFr(adresse)} ${adresse > 1 ? 'vigilances' : 'vigilance'} à l’adresse ou autour`, ton: 'vigilance' });
	const secteur = placesDans(partie, resume.pointsSecteur);
	if (secteur.length > 0) {
		const mot = adresse > 0 ? '' : `${secteur.length > 1 ? 'vigilances' : 'vigilance'} `;
		signaux.push({ texte: `${nombreFr(secteur.length)} ${mot}${perimetreSecteur(secteur)}`, ton: adresse > 0 ? 'discret' : 'vigilance' });
	}
	const pannes = placesDans(partie, resume.pannes);
	if (pannes.length > 0) signaux.push({ texte: `${LIBELLE_STATUT.panne}${NBSP}: ${pannes.map((t) => typographieFr(t.titre)).join(', ')}`, ton: 'panne' });
	const sansPanne = new Set(pannes.map((t) => t.id));
	const manques = placesDans(partie, resume.sansValeur).filter((t) => !sansPanne.has(t.id));
	if (manques.length > 0) signaux.push({ texte: repliSansValeur(manques, false), ton: 'discret' });
	return signaux;
}

/** The themes of the category whose value reaches further than the address (the served cut, D-32). */
export const plusLargesQueLAdresse = (partie: Pick<PartiePlacee, 'premiers' | 'autres'>): ThemeRapport[] =>
	themesDe(partie).filter((t) => zoneDe(t.granularite) === 'tuile');

/** True when every given theme is of the commune scale (and there is at least one). */
export const estToutCommunal = (themes: readonly ThemeRapport[]): boolean =>
	themes.length > 0 && themes.every((t) => t.granularite.niveau === 'commune');

/**
 * Locked sentence of D-40, said once at the head of a category (UI-SPEC § 4.3 amended): « Pour toute la
 * commune de {nom} » when the wider themes are all communal, « Pour un périmètre plus large que l’adresse »
 * as soon as one is not, both followed by what such a value never says. None, no sentence. The commune is
 * named only as served.
 */
export function titreZoneTuiles(themes: readonly ThemeRapport[], nomCommune: string | null): string | null {
	if (themes.length === 0) return null;
	const suite = `${NBSP}: ne dit pas si l’adresse est concernée`;
	if (estToutCommunal(themes)) return `Pour toute la commune${nomCommune ? ` de ${nomCommune}` : ''}${suite}`;
	return `Pour un périmètre plus large que l’adresse${suite}`;
}
