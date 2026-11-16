// What the site deliberately does not show, and why (04.7-14, Origami R11/E7). One entry per limit:
// what is withheld, why, and the base of the rule (a text, a source, or a rule of DataCommune said as
// such). The figures quoted here are those the API applies: `FENETRE_RECENCE_MOIS` of
// `api/backend/app/adresse/ventes.py`, checked by the page test, never restated by hand elsewhere.
import { LIBELLE_STATUT, type CleStatut } from '$lib/data/adresse-rapport';

/** Recency window of the isolated-sale rule, in months (mirror of the API constant, tested). */
export const FENETRE_VENTE_ISOLEE_MOIS = 24;

export const CHEMIN_CE_QUE_NOUS_NE_MONTRONS_PAS = '/ce-que-nous-ne-montrons-pas';
export const LIEN_CE_QUE_NOUS_NE_MONTRONS_PAS = 'Ce que cette fiche ne montre pas';

/** Article of the tax procedures book that frames the reuse of DVF (no re-identification, no indexing). */
const BASE_DVF = 'Conditions de réutilisation des demandes de valeurs foncières, article R*112 A-3 du livre des procédures fiscales';

export interface Limite {
	id: string;
	titre: string;
	/** What is withheld, said concretely. */
	quoi: string;
	/** Why it is withheld. */
	pourquoi: string;
	/** The base of the rule: a text, the source itself, or a rule of DataCommune said as such. */
	base: string;
}

export const LIMITES: readonly Limite[] = [
	{
		id: 'parties-dvf',
		titre: 'Les acheteurs et les vendeurs',
		quoi: 'Aucun nom d’acheteur ni de vendeur, pour aucune vente.',
		pourquoi:
			'Les demandes de valeurs foncières publiées par la DGFiP n’en contiennent pas, et leur réutilisation ne doit pas permettre de retrouver les personnes.',
		base: BASE_DVF
	},
	{
		id: 'vente-isolee',
		titre: 'Le prix d’une vente isolée récente',
		quoi: `Le prix d’une vente datée des ${FENETRE_VENTE_ISOLEE_MOIS} derniers mois de la base des ventes, quand l’une de ses parcelles ne compte que cette vente ; et le prix d’une vente récente dont l’isolement ne peut pas être établi. La vente reste listée, avec la mention « Prix non affiché », et son prix au mètre carré est masqué aussi.`,
		pourquoi:
			'Sur une parcelle qui ne compte qu’une vente récente, le prix désigne un bien et son acheteur.',
		base: `${BASE_DVF}. Règle de DataCommune appliquée par l’API sur les fiches d’adresse : les ${FENETRE_VENTE_ISOLEE_MOIS} mois se comptent depuis la dernière date de la base des ventes, jamais depuis la date du jour.`
	},
	{
		id: 'occupants',
		titre: 'Les occupants',
		quoi: 'Ni l’habitant, ni le propriétaire, ni le locataire d’un logement.',
		pourquoi:
			'Une fiche décrit un lieu, pas des personnes. Un DPE décrit un logement à une date, pas la personne qui l’occupe.',
		base: 'Aucune des sources utilisées ne publie ces informations, et le site n’en importe aucune.'
	},
	{
		id: 'adresse-dpe',
		titre: 'L’adresse saisie dans un DPE, quand elle porte un complément',
		quoi: 'Quand l’adresse saisie par le diagnostiqueur porte un complément (étage, porte, bâtiment ou nom), elle n’est pas affichée ; la fiche dit qu’elle est masquée.',
		pourquoi: 'Ce complément peut désigner un logement précis, ou une personne.',
		base: 'Règle de DataCommune : le texte saisi est gardé par l’API pour le rattachement, jamais servi.'
	},
	{
		id: 'indexation',
		titre: 'Les fiches d’adresse dans les moteurs de recherche',
		quoi: 'Les fiches d’adresse ne sont pas indexées par les moteurs de recherche. Les fiches de commune, qui ne montrent que des chiffres agrégés, le sont.',
		pourquoi: 'Une fiche d’adresse montre des ventes issues des demandes de valeurs foncières, dont l’indexation par des moteurs de recherche externes est exclue.',
		base: BASE_DVF
	},
	{
		id: 'note-globale',
		titre: 'Une note globale du lieu',
		quoi: 'Aucune note, aucun classement d’un lieu.',
		pourquoi:
			'Une note mêlerait des sources d’échelles et de dates différentes, et cacherait ce qu’elle pèse. Chaque critère garde sa valeur, sa source, son millésime et son échelle.',
		base: 'Choix éditorial de DataCommune.'
	}
];

export interface DefinitionEtat {
	cle: CleStatut;
	libelle: string;
	definition: string;
}

/** The states a value can take when it is not a plain value, with the label the fiches print. */
export const ETATS: readonly DefinitionEtat[] = (
	[
		['zero', 'La source a répondu, et ne compte rien à cet endroit. C’est un vrai zéro.'],
		[
			'manque',
			'La source n’a pas publié la valeur, ou elle n’a pas pu être rattachée au lieu ; la fiche dit pourquoi. Ce n’est pas un zéro.'
		],
		[
			'hors_couverture',
			'Le territoire ou la source n’est pas encore servi par le site : le site ne sait pas. Une absence de couverture ne vaut jamais zéro.'
		],
		[
			'panne',
			'La source n’a pas répondu au moment de la lecture. Ce n’est ni une absence ni un zéro, et la fiche le dit en tête.'
		],
		['non_concerne', 'Le critère ne s’applique pas à ce lieu, par exemple une obligation qui ne concerne pas la commune.']
	] as const
).map(([cle, definition]) => ({ cle, libelle: LIBELLE_STATUT[cle], definition }));
