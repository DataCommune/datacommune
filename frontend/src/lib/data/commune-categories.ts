// Categories of the commune fiche (4.9): the categories of the address fiche (questions.ts), in its order of
// reading — prices, rents and DPE first, then the rest folded by category. Pure module, zero Svelte runtime.
// It only REGROUPS the rows `themesCommune` already reads (the five themes stay those of the CSV export and
// of the comparator): each row lands in exactly one group, by its served field; nothing is computed.
// A group is the counterpart of a criterion of the address fiche and takes its anchor `critere-<id>`.

import type { LigneTheme, NoteTheme, ThemeFiche, CleTheme } from '$lib/data/commune-grid';
import { QUESTIONS, type IdPartie } from '$lib/data/questions';

const NBSP = String.fromCharCode(0xa0); // no-break space

interface GroupeTable {
	id: string;
	titre: string;
	/** Field ids, or their prefixes, the group holds; the longest prefix wins. */
	champs: readonly string[];
}

interface CategorieTable {
	id: IdPartie;
	titre: string;
	groupes: readonly GroupeTable[];
	/** Fields whose served value the closed row announces; the head already says prices, rents and DPE. */
	annonce: readonly string[];
}

const titreDe = (id: IdPartie): string => QUESTIONS.find((q) => q.id === id)!.titre;

/**
 * « Autour de l’adresse » would contradict the page, whose values never hold for an address: the commune names
 * the same category for itself. No risk field is served by /v1/commune: the commune has no « Risques ».
 */
export const CATEGORIES_COMMUNE: readonly CategorieTable[] = [
	{
		id: 'q-prix',
		titre: titreDe('q-prix'),
		groupes: [
			{ id: 'prix_commune', titre: 'Prix au m²', champs: ['dvf.'] },
			{ id: 'loyers', titre: 'Loyers', champs: ['loyers.'] },
			{ id: 'fiscalite', titre: 'Fiscalité locale', champs: ['fiscalite.'] },
			{ id: 'zonage_abc', titre: 'Zonage ABC', champs: ['zonage_abc.'] }
		],
		annonce: ['dvf.nb_mutations_12m', 'fiscalite.taux_tfpb']
	},
	{
		id: 'q-logement',
		titre: titreDe('q-logement'),
		groupes: [
			{ id: 'dpe', titre: 'DPE', champs: ['dpe.'] },
			{ id: 'fibre', titre: 'Fibre', champs: ['connectivite.'] },
			{ id: 'construction', titre: 'Construction', champs: ['dpe.annee_construction_mediane'] },
			{ id: 'parc', titre: 'Parc de logements', champs: ['demographie.nb_logements', 'vacance.'] },
			{ id: 'foncier_pm', titre: 'Foncier des personnes morales', champs: ['foncier_pm.'] },
			{ id: 'monuments', titre: 'Monuments historiques', champs: ['monuments.'] }
		],
		annonce: ['connectivite.fibre_pct', 'dpe.annee_construction_mediane']
	},
	{
		id: 'q-quotidien',
		titre: 'Vivre dans la commune',
		groupes: [
			{ id: 'equipements', titre: 'Équipements', champs: ['equipements.'] },
			{ id: 'ecoles', titre: 'Établissements scolaires', champs: ['scolaire.'] },
			{ id: 'population', titre: 'Population', champs: ['demographie.'] },
			{ id: 'revenus', titre: 'Revenus', champs: ['revenus.'] }
		],
		annonce: ['equipements.nb_equipements', 'revenus.revenu_median']
	}
];

/** A row without a field, or a field no group names, stays with its former theme: never lost. */
const GROUPE_DU_THEME: Readonly<Record<CleTheme, string>> = {
	immobilier: 'prix_commune',
	'energie-bati': 'dpe',
	'services-transports': 'equipements',
	'contexte-communal': 'population',
	'risques-urbanisme': 'zonage_abc'
};

/** A note of a theme qualifies one group; another note stays with its former theme. */
const GROUPE_DE_LA_NOTE: Readonly<Record<string, string>> = {
	'label:Portée des dénombrements': 'equipements',
	'label:Portée du foncier observé': 'foncier_pm',
	// R-04: the caveat of a field is read in the group of that field.
	'label:Portée du revenu déclaré': 'revenus',
	'label:Portée de la vacance': 'parc'
};

/**
 * The former theme anchors (`/commune/{code}#immobilier`, before 4.9) and the group each one now opens. The
 * category is the one that holds that group.
 */
export const ANCRES_DES_THEMES: Readonly<Record<CleTheme, string>> = {
	immobilier: 'prix_commune',
	'energie-bati': 'dpe',
	'services-transports': 'equipements',
	'contexte-communal': 'population',
	'risques-urbanisme': 'zonage_abc'
};

/** The group that holds a served field, or null when no group names it. */
export function groupeDuChamp(fieldId: string): string | null {
	let trouve: { id: string; longueur: number } | null = null;
	for (const categorie of CATEGORIES_COMMUNE) {
		for (const groupe of categorie.groupes) {
			for (const champ of groupe.champs) {
				if (fieldId.startsWith(champ) && (!trouve || champ.length > trouve.longueur)) trouve = { id: groupe.id, longueur: champ.length };
			}
		}
	}
	return trouve?.id ?? null;
}

export interface GroupeCommune {
	id: string;
	titre: string;
	lignes: LigneTheme[];
	notes: NoteTheme[];
	/** The former theme anchors that land on this group. */
	anciennesAncres: string[];
}

export interface CategorieCommune {
	id: IdPartie;
	titre: string;
	groupes: GroupeCommune[];
	/** What the closed row says: served values, else a count. */
	annonce: string[];
}

/** The text of a served value, as the row shows it. */
const texteDeValeur = (ligne: LigneTheme): string | null =>
	ligne.etat.kind === 'valeur' ? `${ligne.etat.texte}${ligne.etat.unite ? `${NBSP}${ligne.etat.unite}` : ''}` : null;

/**
 * Places the rows of the themes in the categories, each row once, in the served order within its theme and
 * the order of the themes. A category or a group without a row does not exist.
 */
export function categoriesCommune(themes: readonly ThemeFiche[]): CategorieCommune[] {
	const lignesDe = new Map<string, LigneTheme[]>();
	const notesDe = new Map<string, NoteTheme[]>();
	const ajouter = <T>(carte: Map<string, T[]>, cle: string, item: T) => carte.set(cle, [...(carte.get(cle) ?? []), item]);
	for (const theme of themes) {
		let precedent = GROUPE_DU_THEME[theme.cle];
		for (const ligne of theme.lignes) {
			const groupe = (ligne.fieldId ? groupeDuChamp(ligne.fieldId) : null) ?? precedent;
			ajouter(lignesDe, groupe, ligne);
			precedent = groupe;
		}
		for (const note of theme.notes) ajouter(notesDe, GROUPE_DE_LA_NOTE[note.id] ?? GROUPE_DU_THEME[theme.cle], note);
	}
	const anciennes = Object.entries(ANCRES_DES_THEMES);
	return CATEGORIES_COMMUNE.flatMap((categorie): CategorieCommune[] => {
		const groupes = categorie.groupes.flatMap((g): GroupeCommune[] => {
			const lignes = lignesDe.get(g.id) ?? [];
			const notes = notesDe.get(g.id) ?? [];
			if (lignes.length + notes.length === 0) return [];
			return [{ id: g.id, titre: g.titre, lignes, notes, anciennesAncres: anciennes.filter(([, cible]) => cible === g.id).map(([ancre]) => ancre) }];
		});
		if (groupes.length === 0) return [];
		const lignes = groupes.flatMap((g) => g.lignes);
		const faits = categorie.annonce.flatMap((champ) => {
			const ligne = lignes.find((l) => l.fieldId === champ);
			const texte = ligne ? texteDeValeur(ligne) : null;
			return ligne && texte ? [`${ligne.libelle}${NBSP}: ${texte}`] : [];
		});
		const annonce = faits.length ? faits : [`${lignes.length} ${lignes.length > 1 ? 'indicateurs' : 'indicateur'}`];
		return [{ id: categorie.id, titre: categorie.titre, groupes, annonce }];
	});
}

/** The category an anchor of the fiche asks to open: a category, a group, or a former theme anchor. */
export function categorieDeAncre(hash: string, categories: readonly CategorieCommune[]): IdPartie | null {
	const cle = hash.replace(/^#/, '');
	const parId = categories.find((c) => c.id === cle);
	if (parId) return parId.id;
	const groupe = cle.startsWith('critere-') ? cle.slice('critere-'.length) : (ANCRES_DES_THEMES as Record<string, string>)[cle];
	if (!groupe) return null;
	return categories.find((c) => c.groupes.some((g) => g.id === groupe))?.id ?? null;
}
