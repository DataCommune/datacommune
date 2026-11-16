// Texts of the interactive parcel map (04.10 UI contract, « Copywriting Contract »).
//
// `GABARITS` is the only source of the visible strings of the map at run time: one entry per number of
// the contract (`T-15`), a variant of that number under `T-15-v<rank>`, the rank being its place among
// the quoted variants of the contract row. Each entry is the contract text, character for character,
// with the spacing rules the contract states applied: a no-break space (U+00A0) before « : » and
// between a number and its unit, a narrow one (U+202F) before « ; ». A private check of the phase
// compares this table with the contract tables; nothing here is reworded by hand.
//
// « Fermer » and the labels of an absence are not here: they come from the modules that already own
// them (the state glossary, through `StateValue`).

import { LIBELLES_ETATS } from './etats-libelles';

// Built from its code point: a raw no-break space in a source file does not survive every editor.
const NB = String.fromCharCode(0xa0);

export const GABARITS = {
	'T-01': 'Ouvrir la carte des parcelles',
	'T-02': 'Parcelles autour de l’adresse',
	'T-03': 'Carte des parcelles autour de l’adresse {adresse}',
	'T-03-v1': 'Les flèches déplacent la carte, les touches plus et moins changent le zoom. La liste sous la carte donne les mêmes informations.',
	'T-04': 'Chargement de la carte…',
	'T-05': 'Chargement des parcelles…',
	'T-06': 'Chargement des ventes de la parcelle…',
	'T-07': 'Zoom avant',
	'T-08': 'Zoom arrière',
	'T-09': 'Revenir à l’adresse',
	'T-10': 'Parcelle de l’adresse',
	'T-11': 'Au moins une vente ({periode})',
	'T-12': 'Vendues ensemble',
	'T-13': `Ventes connues${NB}: {periode}. Cadastre${NB}: {millesime}. Fond${NB}: {attribution}, {licence}.`,
	'T-13-v1': `Détail${NB}: sources et limites de la carte`,
	'T-14': 'Parcelle {section} {numéro} sélectionnée.',
	'T-15': 'Parcelle {section} {numéro}',
	'T-16': 'Parcelle de l’adresse',
	'T-17': 'Surface cadastrale',
	'T-17-v1': `{n}${NB}m²`,
	'T-18': 'Référence cadastrale',
	'T-20': 'Prix de toute la vente ({total} parcelles).',
	'T-21': 'Vendue avec {autres} autres parcelles',
	'T-21-v1': 'Vendue avec 1 autre parcelle',
	'T-22': 'Montrer ces parcelles',
	'T-23': `{total} parcelles vendues ensemble · {date} · {surface}${NB}m² au total`,
	'T-23-v1': '{total} parcelles vendues ensemble · {date}',
	'T-24': 'Ne dit pas qui les possède aujourd’hui.',
	'T-25': `Surface totale non calculée${NB}: {k} de ces parcelles ne figurent plus au cadastre.`,
	'T-25-v1': `…${NB}: 1 de ces parcelles ne figure plus au cadastre.`,
	'T-26': '{k} de ces parcelles sont hors de la vue.',
	'T-26-v1': '1 de ces parcelles est hors de la vue.',
	'T-27': 'Avant {annee_debut}, les ventes ne sont pas connues ici.',
	'T-28': 'Parcelles affichées',
	'T-29': '{n} parcelles sur la carte, dont {v} avec une vente ({periode}).',
	'T-29-v1': '1 parcelle sur la carte, dont 1 avec une vente ({periode}).',
	'T-29-v2': '{n} parcelles sur la carte, aucune avec une vente ({periode}).',
	'T-29-v3': '{n} parcelles autour de l’adresse, dont {v} avec une vente ({periode}).',
	'T-30': 'Seulement les parcelles avec une vente',
	'T-31': 'Vendue ensemble',
	'T-32': 'Adresse',
	'T-33': 'Une partie du fond de carte n’a pas pu être chargée.',
	'T-34': 'Les parcelles d’une partie de la carte n’ont pas pu être lues. Ce n’est pas une absence de parcelles ni de ventes.',
	'T-35': 'Choisissez une parcelle sur la carte ou dans la liste.',
	'T-35-v1': 'Choisissez une parcelle dans la liste.',
	'T-36': 'Afficher 20 parcelles de plus',
	'T-36-v1': 'Afficher {k} parcelles de plus',
	'T-37': 'Aucune parcelle cadastrale dans cette vue.',
	'T-37-v1': 'Aucune parcelle cadastrale autour de l’adresse.',
	'T-38': 'La carte n’a pas pu être chargée. La liste des parcelles reste disponible.',
	'T-39': 'Recharger la carte',
	'T-41': 'Recharger les parcelles',
	'T-43': 'La carte n’a pas pu être chargée. Rechargez la page pour réessayer.',
	// Cases left without a text until the review of Ianis (2026-11-15): R3, R4, S2, S5, S6.
	'T-44': `Surface totale non calculée${NB}: {k} de ces parcelles n’ont pas de surface au cadastre.`,
	'T-44-v1': `…${NB}: 1 de ces parcelles n’a pas de surface au cadastre.`,
	'T-45': 'Groupe trop grand pour être montré sur la carte ({total} parcelles).',
	'T-46': '{k} de ces parcelles sont trop loin de l’adresse pour être montrées.',
	'T-46-v1': '1 de ces parcelles est trop loin de l’adresse pour être montrée.',
	'T-47': '{n} parcelles sur la carte. Les ventes n’ont pas pu être comptées.',
	'T-47-v1': '1 parcelle sur la carte. Les ventes n’ont pas pu être comptées.',
	'T-47-v2': '{n} parcelles autour de l’adresse. Les ventes n’ont pas pu être comptées.',
	'T-48': '{k} de ces parcelles sont hors de la zone listée.',
	'T-48-v1': '1 de ces parcelles est hors de la zone listée.'
} as const;

/**
 * Source page of the base map (R1, Ianis, 2026-11-15): the Licence Ouverte asks for a link to the source.
 * The old product page of the IGN, geoservices.ign.fr/planign, redirects there permanently (2026-11-15).
 */
export const SOURCE_FOND = 'https://cartes.gouv.fr/rechercher-une-donnee/dataset/IGNF_PLAN-IGN';

/**
 * T-40, the panel « Sources et limites de la carte »: seven entries, in the order of the UI contract.
 * `{millesime}` and `{periode}` are served; the « Prix » entry is followed by the existing sentence of an
 * isolated sale (`PHRASE_VENTE_ISOLEE`), added by the dialog; « Fond » links to its source (R1).
 */
export const VOLET_CARTE: readonly { terme: string; valeur: string; url?: string }[] = [
	{ terme: 'Parcelles', valeur: 'Plan cadastral de la DGFiP, diffusé par Etalab, {millesime}. Une parcelle créée ou divisée depuis n’y figure pas.' },
	{ terme: 'Surface', valeur: 'La surface cadastrale est déclarée au cadastre, pas mesurée sur le terrain.' },
	{ terme: 'Ventes', valeur: 'DVF (DGFiP), {periode}. Une vente plus ancienne, ou que la source ne situe sur aucune parcelle, n’apparaît pas.' },
	{
		terme: 'Vendues ensemble',
		valeur: `Plusieurs parcelles réunies dans une même vente. C’est un fait daté${NB}: il ne dit pas qui les possède aujourd’hui, ni qu’elles forment encore un seul terrain.`
	},
	{ terme: 'Prix', valeur: 'Le prix est celui de toute la vente, toutes parcelles comprises.' },
	{
		terme: 'Ce que la carte ne dit pas',
		valeur: `Ni le propriétaire d’une parcelle, ni les limites exactes d’un terrain${NB}: le cadastre n’est pas un bornage.`
	},
	{ terme: 'Fond', valeur: 'Plan IGN', url: SOURCE_FOND }
];

export type CleGabarit = keyof typeof GABARITS;

function remplir(cle: CleGabarit, valeurs: Record<string, string>): string {
	return GABARITS[cle].replace(/\{([^}]+)\}/g, (marque, nom: string) => valeurs[nom] ?? marque);
}

/** The four parts of a parcel reference of 14 characters: commune, prefix, section, number. */
function parties(idParcelle: string) {
	return {
		commune: idParcelle.slice(0, 5),
		prefixe: idParcelle.slice(5, 8),
		section: idParcelle.slice(8, 10),
		numero: idParcelle.slice(10)
	};
}

const nomDe = (idParcelle: string) => {
	const { section, numero } = parties(idParcelle);
	return { section, numéro: numero };
};

/** T-15: the title of the panel of a parcel. */
export function titreParcelle(idParcelle: string): string {
	return remplir('T-15', nomDe(idParcelle));
}

/** T-14: what the status line announces when a parcel is chosen. */
export function annonceSelection(idParcelle: string): string {
	return remplir('T-14', nomDe(idParcelle));
}

/** A served value the map writes in a sentence; absent, the label of the glossary, never a hole. */
const servi = (valeur: string | null) => valeur ?? LIBELLES_ETATS.not_reported;

/** T-03: the accessible name of the map. */
export function nomCarte(adresse: string): string {
	return remplir('T-03', { adresse });
}

/** Description of the map (variant of T-03). */
export function descriptionCarte(): string {
	return GABARITS['T-03-v1'];
}

/** T-11: the legend of the sold mark, with the served period. */
export function legendeVentes(periode: string): string {
	return remplir('T-11', { periode });
}

/** Name of the detail button of the limits line, as `SourceButton` words it (« Détail : … »). */
export const LIBELLE_DETAIL_LIMITES = GABARITS['T-13-v1'].slice(`Détail${NB}: `.length);

/** T-13: what the map knows, from when, and on what ground; every value served. */
export function ligneLimites(valeurs: { periode: string | null; millesime: string | null; attribution: string; licence: string }): string {
	return remplir('T-13', {
		periode: servi(valeurs.periode),
		millesime: servi(valeurs.millesime),
		attribution: valeurs.attribution,
		licence: valeurs.licence
	});
}

/** The entries of T-40 with the served period and vintage. */
export function voletCarte(valeurs: { periode: string | null; millesime: string | null }): { terme: string; valeur: string; url?: string }[] {
	return VOLET_CARTE.map(({ terme, valeur, url }) => ({
		terme,
		valeur: valeur.replace('{periode}', servi(valeurs.periode)).replace('{millesime}', servi(valeurs.millesime)),
		...(url ? { url } : {})
	}));
}

const entier = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });

/** Value of T-17: a served surface in square metres. An absent surface is a state, never this text. */
export function surfaceCadastrale(contenance: number): string {
	return remplir('T-17-v1', { n: entier.format(contenance) });
}

/** Value of T-18: the reference as the cadastre groups it, nothing dropped. */
export function referenceCadastrale(idParcelle: string): string {
	const { commune, prefixe, section, numero } = parties(idParcelle);
	return `${commune} ${prefixe} ${section} ${numero}`;
}

/** T-27: the reserve under a zero, with the first served year of the sales. */
export function reserveZero(anneeDebut: number): string {
	return remplir('T-27', { annee_debut: String(anneeDebut) });
}

/**
 * The source line of the sales of the fiche (`.ventes-commune` of the address page), taken as is: an
 * existing text of the UI contract, not a number of it. A test holds it equal to the line of the page.
 */
export function ligneSourceVentes(periode: string | null): string {
	return ['DVF (DGFiP)', periode, 'ventes de la parcelle, rattachées à aucun logement'].filter(Boolean).join(' · ');
}

// A sale of several parcels (T-20 to T-26). Every number is served: `total` counts the parcels of the
// sale, the chosen one included; `autres` is served too, never computed here.

/** T-20: under the price of a sale of several parcels, the price is that of the whole sale. */
export function prixDeLaVente(total: number): string {
	return remplir('T-20', { total: entier.format(total) });
}

/** T-21: the other parcels of the sale, « 1 autre parcelle » in the singular. */
export function venduAvec(autres: number): string {
	return autres === 1 ? GABARITS['T-21-v1'] : remplir('T-21', { autres: entier.format(autres) });
}

/**
 * T-23: the shown group, with the date of the first line of its card and the served total surface; the
 * short form (variant) when that surface is not served.
 */
export function ligneGroupe(total: number, date: string, surface: number | null): string {
	const valeurs = { total: entier.format(total), date };
	return surface === null ? remplir('T-23-v1', valeurs) : remplir('T-23', { ...valeurs, surface: entier.format(surface) });
}

/**
 * T-25: why the total surface is not given. The singular variant of the contract writes only its end
 * (« … : 1 de ces parcelles ne figure plus au cadastre. »): its beginning is that of T-25.
 */
export function surfaceIncomplete(k: number): string {
	if (k !== 1) return remplir('T-25', { k: entier.format(k) });
	const debut = GABARITS['T-25'].slice(0, GABARITS['T-25'].indexOf(`${NB}:`));
	return debut + GABARITS['T-25-v1'].slice('…'.length);
}

/**
 * T-44 (R3): no total surface although no parcel is missing from the cadastre; `k` is the served count of
 * those without a surface. The singular variant writes only its end, like T-25.
 */
export function surfaceSansValeur(k: number): string {
	if (k !== 1) return remplir('T-44', { k: entier.format(k) });
	const debut = GABARITS['T-44'].slice(0, GABARITS['T-44'].indexOf(`${NB}:`));
	return debut + GABARITS['T-44-v1'].slice('…'.length);
}

/** T-45 (R4): a group beyond the cap of the route, said in place of the toggle, with its served total. */
export function groupeTropGrand(total: number): string {
	return remplir('T-45', { total: entier.format(total) });
}

/** T-46 (S2): parcels of the group in the cadastre but outside the bounds of the map (served). */
export function tropLoin(k: number): string {
	return k === 1 ? GABARITS['T-46-v1'] : remplir('T-46', { k: entier.format(k) });
}

/** T-48 (S6): without the map, parcels of the group outside the zone the list covers. */
export function horsDeLaZone(k: number): string {
	return k === 1 ? GABARITS['T-48-v1'] : remplir('T-48', { k: entier.format(k) });
}

/** T-26: parcels of the shown group the map leaves out of the view, counted by the map. */
export function horsDeLaVue(k: number): string {
	return k === 1 ? GABARITS['T-26-v1'] : remplir('T-26', { k: entier.format(k) });
}

// The list of the parcels shown (T-28 to T-37). Counts come from the parcels the map read, never from a
// guess; the period is served.

/** Number of parcels per batch of the list: the « 20 » of T-36. */
export const LOT_LISTE = 20;

/**
 * T-29: the parcels of the view and those with a sale. Its variants give the forms: « aucune » for no
 * sale (v2), « autour de l’adresse » without the map (v3), and the beginning of the singular
 * (v1, « 1 parcelle sur la carte… »), whose end is that of the plural; the same agreements apply to
 * the form without the map.
 */
export function compteParcelles(n: number, v: number, periode: string, sansCarte = false): string {
	let gabarit: string = GABARITS[sansCarte ? 'T-29-v3' : 'T-29'];
	if (v === 0) {
		const avec = GABARITS['T-29'];
		const sans = GABARITS['T-29-v2'];
		// The words the variant « sans vente » puts in place of « dont {v} ».
		gabarit = gabarit.replace(avec.slice(avec.indexOf('dont'), avec.indexOf(' avec')), sans.slice(sans.indexOf('aucune'), sans.indexOf(' avec')));
	}
	if (n === 1) {
		// « 1 parcelle », the beginning of the singular sentence of the contract (S8).
		const singulier = GABARITS['T-29-v1'].slice(0, GABARITS['T-29-v1'].indexOf(' sur'));
		gabarit = gabarit.replace('{n} parcelles', singulier);
	}
	return gabarit.replace('{n}', entier.format(n)).replace('{v}', entier.format(v)).replace('{periode}', periode);
}

/**
 * T-47 (S5): the parcels of the view when their sales could not be counted. Without the map, its variant
 * (« autour de l’adresse »), whose singular agrees like the singular of the map (« 1 parcelle »).
 */
export function compteSansVentes(n: number, sansCarte = false): string {
	if (!sansCarte) return n === 1 ? GABARITS['T-47-v1'] : remplir('T-47', { n: entier.format(n) });
	const texte = remplir('T-47-v2', { n: entier.format(n) });
	return n === 1 ? texte.replace('1 parcelles', '1 parcelle') : texte;
}

/**
 * T-36: the next batch of the list, 20 or what is left for the last one. The contract has no form for
 * one parcel: the noun alone agrees (« Afficher 1 parcelle de plus »), a case for the review of the texts.
 */
export function afficherPlus(k: number): string {
	if (k === LOT_LISTE) return GABARITS['T-36'];
	const texte = remplir('T-36-v1', { k: entier.format(k) });
	return k === 1 ? texte.replace('parcelles', 'parcelle') : texte;
}

/** A line of the list names its parcel « {section} {numéro} » (UI contract, « Ligne de liste »). */
export function referenceLigne(idParcelle: string): string {
	const { section, numéro } = nomDe(idParcelle);
	return `${section} ${numéro}`;
}

/**
 * The sales of a line, said only when there are some: « {n} ventes », « 1 vente », the count kept on the
 * line of its noun (U+00A0, as everywhere a count of sales is written on the site).
 */
export function nombreVentes(n: number): string {
	return n === 1 ? `1${NB}vente` : `${entier.format(n)}${NB}ventes`;
}
