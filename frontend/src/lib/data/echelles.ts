// Scales of the address fiche (04.8-03; UI-SPEC v4 § 3.5, § 4.3; D-07, D-09, D-32, D-33). Pure module,
// zero Svelte runtime. One entry per level the API serves (`NiveauGranularite`,
// api/backend/app/rapport/schemas.py): its A2 pictogram, its short word, its long form (said in the
// detail), its rank. The zone of a theme (row or tile) is the cut the API serves (`est_de_l_adresse`,
// api/backend/app/rapport/resume.py:35-47), pinned by a test until the API serves the zone itself.

import type { GranulariteRapport } from '$lib/api/rapport';
import type { Pictogramme } from '$lib/components/ds/RepereEchelle.svelte';
import { distanceFr } from '$lib/utils/nombre-fr';

const NBSP = String.fromCharCode(0xa0); // no-break space
const NNBSP = String.fromCharCode(0x202f); // narrow no-break space

export type NiveauServi = 'adresse' | 'batiment' | 'parcelle' | 'rayon' | 'iris' | 'arrondissement' | 'commune' | 'epci' | 'departement';

export interface DefinitionNiveau {
	pictogramme: Pictogramme;
	/** Short word, after the pictogram. */
	mot: string;
	/** Long form, in « Cette valeur vaut {longue}. ». */
	longue: string;
	/** Sort rank, from the most specific scale to the widest. */
	rang: number;
}

/** Every served level (D-07): none is left without a mark; `arrondissement` and `epci` included (A-05). */
export const NIVEAUX: Readonly<Record<NiveauServi, DefinitionNiveau>> = {
	adresse: { pictogramme: 'adresse', mot: 'Adresse', longue: 'au point de l’adresse', rang: 0 },
	batiment: { pictogramme: 'batiment', mot: 'Bâtiment', longue: 'pour le bâtiment', rang: 1 },
	parcelle: { pictogramme: 'parcelle', mot: 'Parcelle', longue: 'pour la parcelle cadastrale', rang: 1 },
	rayon: { pictogramme: 'rayon', mot: 'Autour', longue: 'autour de l’adresse', rang: 2 },
	iris: {
		pictogramme: 'carreau',
		mot: 'Quartier IRIS',
		longue: 'pour le quartier statistique de l’Insee (IRIS) qui contient l’adresse',
		rang: 3
	},
	// The arrondissements of Paris, Lyon and Marseille have their own town hall.
	arrondissement: { pictogramme: 'commune', mot: 'Arrondissement', longue: 'pour tout l’arrondissement', rang: 4 },
	commune: { pictogramme: 'commune', mot: 'Commune', longue: 'pour toute la commune', rang: 5 },
	epci: { pictogramme: 'departement', mot: 'Intercommunalité', longue: 'pour toute l’intercommunalité', rang: 6 },
	departement: { pictogramme: 'departement', mot: 'Département', longue: 'pour tout le département', rang: 7 }
};

/** Rank of a level this front does not know (an API newer than the front): with the commune. */
export const RANG_INCONNU = 5;

/** The mark of a served scale: what `RepereEchelle` draws and the detail says. */
export interface Repere {
	/** Absent for a level unknown to this front: the word alone. */
	pictogramme?: Pictogramme;
	mot: string;
	longue: string;
	rang: number;
	niveau: string;
}

const estServi = (niveau: string): niveau is NiveauServi => Object.hasOwn(NIVEAUX, niveau);

/**
 * The mark of a scale. A radius says its distance (« 500 m autour »); an arrondissement its served name
 * in the long form, never a guessed one; a level unknown to this front its code made readable, without
 * pictogram and without error.
 */
export function niveauDe(g: GranulariteRapport): Repere {
	if (!estServi(g.niveau)) {
		return { mot: `Échelle${NBSP}: ${g.niveau}`, longue: `pour l’échelle «${NNBSP}${g.niveau}${NNBSP}»`, rang: RANG_INCONNU, niveau: g.niveau };
	}
	const definition = NIVEAUX[g.niveau];
	const base = { pictogramme: definition.pictogramme, mot: definition.mot, longue: definition.longue, rang: definition.rang, niveau: g.niveau };
	if (g.niveau === 'rayon' && g.rayon_m !== null) {
		const d = distanceFr(g.rayon_m);
		return { ...base, mot: `${d} autour`, longue: `dans un rayon de ${d} autour de l’adresse` };
	}
	if (g.niveau === 'arrondissement' && g.arrondissement?.nom) return { ...base, longue: `pour tout le ${g.arrondissement.nom}` };
	return base;
}

/** Sort rank of a scale (§ 4.3): the level's rank; a radius is then ordered by its distance. */
export const rangEchelle = (g: GranulariteRapport): number => niveauDe(g).rang;

export type Zone = 'ligne' | 'tuile';

// The cut the API serves (`est_de_l_adresse`, api/backend/app/rapport/resume.py:35-47): the address,
// the building, the parcel and a radius of 1 000 m at most are of the address; the rest is the area.
const NIVEAUX_ADRESSE: ReadonlySet<string> = new Set(['adresse', 'batiment', 'parcelle']);
export const RAYON_ADRESSE_MAX_M = 1000;

/** Row or tile: the served cut (D-32), the same as the lists of « En bref » and the pills. */
export function zoneDe(g: GranulariteRapport): Zone {
	if (NIVEAUX_ADRESSE.has(g.niveau)) return 'ligne';
	return g.niveau === 'rayon' && g.rayon_m !== null && g.rayon_m <= RAYON_ADRESSE_MAX_M ? 'ligne' : 'tuile';
}

/** Sort key of a theme in its zone: (rank, radius, order of the table), stable. */
export const cleDeTri = (g: GranulariteRapport, ordre: number): [number, number, number] => [rangEchelle(g), g.rayon_m ?? 0, ordre];

/** A3/A4: an empty list keeps the communal wording; any other level needs the wider wording. */
export const perimetreSecteur = (themes: readonly { granularite: GranulariteRapport }[]): string =>
	themes.every((t) => t.granularite.niveau === 'commune') ? 'pour toute la commune' : 'pour un périmètre plus large que l’adresse';
