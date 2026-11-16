// Legend « Lire une fiche » (04.8-03; UI-SPEC v4 § 3.12, § 7.6; D-14). Pure module, zero Svelte runtime.
// One definition per level of the scale table (`NIVEAUX`) and one per key of the badges (the statuses of
// `LIBELLE_STATUT`, the outlined vigilance, and the absence of badge): `lire-une-fiche.test.ts` fails when
// a table has an entry left out. The words of the badges and of the scales are not written here again: the
// legend reads them from `LIBELLE_STATUT` and `NIVEAUX`.

import type { Pictogramme } from '$lib/components/ds/RepereEchelle.svelte';
import { LIBELLE_STATUT, type CleStatut, type Statut } from './adresse-rapport';
import { NIVEAUX, niveauDe, type DefinitionNiveau, type NiveauServi } from './echelles';

const NBSP = String.fromCharCode(0xa0); // no-break space
const NNBSP = String.fromCharCode(0x202f); // narrow no-break space

/** What each scale says (`a/pages.mjs:559-567` for the seven of the mockup, § 7.6 for the others). */
export const DEFINITIONS_ECHELLES: Readonly<Record<NiveauServi, string>> = {
	adresse: 'Le point exact de l’adresse.',
	batiment: 'Le bâtiment qui porte l’adresse, quand il est établi.',
	parcelle: 'La parcelle cadastrale qui contient le point de l’adresse.',
	rayon: `Un rayon autour de l’adresse, de 500${NBSP}m à 10${NBSP}km selon le critère. Ce qui est au-delà n’est pas compté.`,
	iris: 'Le quartier statistique de l’Insee qui contient l’adresse.',
	arrondissement: 'Tout l’arrondissement de Paris, Lyon ou Marseille qui contient l’adresse.',
	commune: 'Toute la commune. La valeur ne dit pas si l’adresse elle-même est concernée.',
	epci: 'Toute l’intercommunalité dont la commune fait partie. La valeur ne dit pas si l’adresse elle-même est concernée.',
	departement: 'Tout le département.'
};

/**
 * The nine keys of the legend of the states: the seven statuses, the vigilance in outline, no badge.
 * « Lecture en cours » (R-07) is not one of them: it lasts a few seconds, and the head of the fiche
 * says it while it lasts.
 */
export type CleLegende = Exclude<CleStatut, 'en_cours'> | 'vigilance_contour' | 'sans_badge';

/** What each badge says (§ 7.6). */
export const DEFINITIONS_ETATS: Readonly<Record<CleLegende, string>> = {
	vigilance: `Un point à connaître avant de décider${NBSP}: la source signale quelque chose à l’adresse ou autour. Ce n’est pas l’annonce d’un danger immédiat.`,
	vigilance_contour: `Une vigilance qui vaut pour toute la commune, ou pour un périmètre plus large que l’adresse${NBSP}: elle ne dit pas si l’adresse est concernée.`,
	rassurant: `La donnée est positive pour ce critère${NNBSP}; la phrase dit ce qui a été vérifié.`,
	sans_badge: 'Une valeur à lire, sans jugement.',
	zero: `La source a répondu et ne recense rien dans le périmètre lu${NBSP}: c’est un vrai zéro.`,
	non_concerne: `Le critère ne s’applique pas ici, ou la source ne recense rien dans la commune${NNBSP}; la phrase dit lequel.`,
	panne: 'La source n’a pas répondu lors de la lecture. Ce n’est pas un zéro.',
	manque: `La source ne publie pas cette information ici, ou DataCommune ne peut pas la rattacher à l’adresse${NNBSP}; le badge dit la raison quand elle est courte.`,
	hors_couverture: 'Cette source n’est pas encore couverte par DataCommune pour ce territoire.'
};

/** Order of the states in the legend (§ 3.12). */
const ORDRE_ETATS: readonly CleLegende[] = [
	'vigilance',
	'vigilance_contour',
	'rassurant',
	'sans_badge',
	'zero',
	'non_concerne',
	'panne',
	'manque',
	'hors_couverture'
];

/** The radius the legend shows for « rayon »: the mockup's example, the definition says 500 m to 10 km. */
const RAYON_EXEMPLE_M = 500;

export interface LigneEchelle {
	niveau: NiveauServi;
	pictogramme: Pictogramme;
	/** The word after the pictogram, as `RepereEchelle` shows it on a row. */
	mot: string;
	definition: string;
}

/** One line per served level, from the most specific to the widest (rank order, stable). */
export function lignesEchelles(): LigneEchelle[] {
	return (Object.entries(NIVEAUX) as [NiveauServi, DefinitionNiveau][])
		.sort(([, a], [, b]) => a.rang - b.rang)
		.map(([niveau, definition]) => ({
			niveau,
			pictogramme: definition.pictogramme,
			mot: niveauDe({ niveau, rayon_m: niveau === 'rayon' ? RAYON_EXEMPLE_M : null }).mot,
			definition: DEFINITIONS_ECHELLES[niveau]
		}));
}

export interface LigneEtat {
	cle: CleLegende;
	/** The badge to draw; null for the absence of badge. */
	statut: Statut | null;
	/** `tuile` draws the vigilance in outline (sector or commune), `ligne` filled. */
	zone: 'ligne' | 'tuile';
	definition: string;
}

/** One line per key of the legend, in the order of the contract. */
export function lignesEtats(): LigneEtat[] {
	return ORDRE_ETATS.map((cle): LigneEtat => {
		if (cle === 'sans_badge') return { cle, statut: null, zone: 'ligne', definition: DEFINITIONS_ETATS[cle] };
		const cleBadge: CleStatut = cle === 'vigilance_contour' ? 'vigilance' : cle;
		return {
			cle,
			statut: { cle: cleBadge, texte: LIBELLE_STATUT[cleBadge] },
			zone: cle === 'vigilance_contour' ? 'tuile' : 'ligne',
			definition: DEFINITIONS_ETATS[cle]
		};
	});
}
