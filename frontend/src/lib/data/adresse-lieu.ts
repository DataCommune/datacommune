// Wording of the served fields of the themes of the place (04-06; UI-SPEC « /adresse/[id] » point 6;
// D-103, D-104, LIEU-02, SEM-01). Pure module, zero Svelte imports.
//
// WHAT IT KEEPS: the row label and the value text of a served field (`libelleChamp`, `texteChamp`),
// the distance wording and the « Liste incomplète » reserve, read by the comparators and the
// declared sales. Counts, distances, lists and states are the API's; nothing here filters, sorts,
// counts or recomputes a value (D-51). A field served without a wording here has no label and no
// text (its key is never displayed).

import type { ValeurTheme } from '$lib/api/adresses';
import { formatBareNumber } from './formatters';

const NB = ' ';
const NNB = ' ';

const entier = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });

/** « 1 bâtiment sur la parcelle », « {n} bâtiments sur la parcelle » (never « le bâtiment »). */
export function batimentsSurLaParcelle(n: number): string {
	return `${entier.format(n)}${NB}${n > 1 ? 'bâtiments' : 'bâtiment'} sur la parcelle`;
}

/** « à {d} m (à vol d'oiseau, pas un temps de trajet) » (UI-SPEC « Contrat de rédaction », Distance). */
export function distanceTransport(distance: number): string {
	return `à ${entier.format(distance)}${NB}m (à vol d'oiseau, pas un temps de trajet)`;
}

export const LISTE_INCOMPLETE = 'Liste incomplète';

/** Wording of a served field of a theme: its row label and its value text. */
interface Rubrique {
	libelle: string;
	texte?: (champ: ValeurTheme) => string | null;
}

const nombreFormate = (champ: ValeurTheme): string | null =>
	typeof champ.valeur === 'number' ? formatBareNumber(champ.field_id, champ.valeur) : null;

/** The served items of a list, as served, in the served order. */
const listeServie = (champ: ValeurTheme): string | null =>
	champ.elements.length ? champ.elements.map((element) => element.libelle).join(`${NNB}; `) : nombreFormate(champ);

const RUBRIQUES: Readonly<Record<string, Rubrique>> = {
	// « Bâtiment et parcelle »
	'adresse.batiments.nombre_parcelle': {
		libelle: 'Bâtiments',
		texte: (champ) => (typeof champ.valeur === 'number' ? batimentsSurLaParcelle(champ.valeur) : null)
	},
	'adresse.batiments.nombre_parcelle_hors_annexe': { libelle: 'Bâtiments hors annexe déclarée' },
	'adresse.batiment.hauteur': { libelle: 'Hauteur' },
	'adresse.batiment.etages': { libelle: "Nombre d'étages" },
	// The acronym is spelled out at its first (and only) use on the page (Rapport C, jargon).
	'adresse.batiment.rnb_id': { libelle: 'Identifiant du Référentiel national des bâtiments (RNB)' },
	'adresse.parcelle.identifiant': { libelle: 'Parcelle' },
	'adresse.parcelle.contenance': { libelle: 'Contenance de la parcelle' },
	'adresse.copropriete.immatriculation': {
		libelle: 'Copropriétés immatriculées au Registre national des copropriétés'
	},
	// « Risques »: at the point, then the commune (reference)
	'adresse.risques.argiles_au_point': { libelle: 'Retrait-gonflement des argiles' },
	'adresse.risques.liste_commune': {
		libelle: 'Risques naturels et technologiques recensés',
		texte: listeServie
	},
	'adresse.risques.radon_commune': { libelle: 'Potentiel radon (catégorie de 1 à 3)' },
	'adresse.risques.icpe_commune': {
		libelle: "Installations classées pour la protection de l'environnement"
	},
	// « Urbanisme »
	'adresse.urbanisme.zone_plu': { libelle: 'Zone du PLU au point', texte: listeServie },
	'adresse.urbanisme.prescriptions': { libelle: 'Prescriptions au point', texte: listeServie },
	// « Réseaux et transports »
	'adresse.connectivite.eligibilite_fibre': {
		libelle: 'Éligibilité à la fibre',
		texte: (champ) => (typeof champ.valeur === 'boolean' ? (champ.valeur ? 'Oui' : 'Non') : null)
	},
	'adresse.connectivite.debit': { libelle: 'Débit publié pour la fibre' },
	'adresse.transports.arret_proche': { libelle: `Arrêts et gares à moins de 1${NNB}000${NB}m` }
};

/** Value text of one served field of a theme, as its row of the page writes it (reused by 04-07). */
export function texteChamp(champ: ValeurTheme): string | null {
	if (champ.etat !== 'value') return null;
	const rubrique = Object.hasOwn(RUBRIQUES, champ.field_id) ? RUBRIQUES[champ.field_id] : null;
	return (rubrique?.texte ?? texteParDefaut)(champ);
}

/** Row label of one served field of a theme on the page, null without a wording (04-07). */
export function libelleChamp(fieldId: string): string | null {
	return Object.hasOwn(RUBRIQUES, fieldId) ? RUBRIQUES[fieldId].libelle : null;
}

function texteParDefaut(champ: ValeurTheme): string | null {
	if (typeof champ.valeur === 'number') return nombreFormate(champ);
	if (typeof champ.valeur === 'string') return champ.valeur;
	return null;
}
