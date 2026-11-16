// Wording and composition of the single search field and of the address page (UI-SPEC « / —
// recherche unique », « /communes?q= — résultats typés », « /adresse/[id] », D-105..D-107). Pure
// module — zero Svelte imports, leaf module like `recherche-code-postal.ts`. Every label is built
// from served fields; nothing here knows which departements the instance hosts (A14), and the order
// of the result types is the served one (`ordre_types`, D-106), never decided here.
import type {
	CorrespondanceNumeroRecherche,
	DepartementRef,
	HebergementBanRecherche,
	ResultatRechercheAdresse,
	TypeResultat
} from '$lib/api/adresses';
import type { CommuneSearchResult, SearchSuggestion } from '$lib/api/communes';

/**
 * Entries of « Pour commencer » on the home page and of « Pour repartir » on the root 404 (04.8-07, D-34):
 * generic pages of the site, with no place, no identifier and no count. The home is prerendered without
 * any /v1 request, so no served example can be read there; the former examples, written by hand with
 * their counts and their date of observation, are gone.
 */
export const EXEMPLES_ACCUEIL = [
	{ label: 'Parcourir l’annuaire des communes', note: 'Trier et filtrer les communes', href: '/communes' },
	{ label: 'Parcourir les départements', note: 'Une page par département', href: '/departements' },
	{ label: 'Comparer des communes', note: 'Côte à côte, source par source', href: '/comparer' },
	{ label: 'Comment lire une fiche', note: 'Les échelles et les états', href: '/datasets#lire' }
] as const;

/** At most 8 options: the head type up to 5, the other up to 3, the remainder filled (UI-SPEC). */
export const MAX_OPTIONS = 8;
const TETE = 5;
const SECOND = 3;

/** Announcement of a street chosen in the field (D-107). */
export const ANNONCE_VOIE = 'Ajoutez le numéro de voie au début de la saisie.';
/** End of the second line of a street option, and label of a street card. */
export const PRECISEZ_LE_NUMERO = 'Précisez le numéro';
/** Partial outage of the single field: the half that failed, the other one stays listed. */
export const PANNE_ADRESSES = "La recherche d'adresses est momentanément indisponible.";
export const PANNE_COMMUNES = 'La recherche de communes est momentanément indisponible.';
export const RELANCER = 'Relancer la recherche';
/**
 * Address search served `etat: 'trop_large'` (R-08): the national approximate read ran out of its
 * budget. Neither an outage (no « Relancer »: the same input meets the same budget) nor a zero.
 * Wording validated by Ianis on 2026-11-10.
 */
export const RECHERCHE_TROP_LARGE = 'Recherche trop large pour aboutir : ajoutez la commune ou le code postal.';
/** Body of « Aucun résultat » for the single field (contract of 04-UI-SPEC). */
export const CORPS_AUCUN_RESULTAT =
	"Cela ne veut pas dire qu'elle n'existe pas. Vérifiez l'orthographe, ajoutez la commune ou le code postal, ou retirez le filtre département.";
/** Second sentence of every out-of-coverage notice: out of coverage is never a zero. */
export const PAS_ZERO = 'Une absence de couverture ne vaut pas zéro.';

// States of the address page (contract of 04-UI-SPEC). The three titles are also the constant
// messages of the errors thrown by the server loader: the error page reads them back.
export const TITRE_ADRESSE_INCONNUE = 'Adresse inconnue';
export const MESSAGE_ADRESSE_INCONNUE =
	"Aucune adresse ne porte cet identifiant dans la base d'adresses de ce site.";
export const TITRE_VOIE = PRECISEZ_LE_NUMERO;
export const MESSAGE_VOIE =
	"Une page d'adresse porte sur un numéro précis. Choisissez le numéro dans la recherche.";
export const TITRE_PANNE_ADRESSE = 'Adresse momentanément indisponible';
// 429 of the weekly fiche allowance (H-3): the message is built by the loader (threshold, date).
export const TITRE_QUOTA_FICHES = 'Limite de consultation atteinte';
export const MESSAGE_PANNE_ADRESSE =
	"Le service n'a pas répondu. Aucune valeur n'est affichée plutôt qu'une valeur incomplète.";

/**
 * Options of the field in the served order: the head type of `ordre` up to 5, the other up to 3,
 * then the remainder filled by the type that has more, never beyond `max`. Inside a type, the
 * served order is kept: nothing is sorted in the browser.
 */
export function composerOptions(
	communes: CommuneSearchResult[],
	adresses: ResultatRechercheAdresse[],
	ordre: TypeResultat[],
	max = MAX_OPTIONS
): SearchSuggestion[] {
	const parType: Record<TypeResultat, SearchSuggestion[]> = {
		commune: communes.map((result) => ({ kind: 'commune', result })),
		adresse: adresses.map((result) => ({ kind: 'adresse', result }))
	};
	const tete = ordre[0] ?? 'commune';
	const premier = parType[tete];
	const second = parType[tete === 'commune' ? 'adresse' : 'commune'];
	let nPremier = Math.min(premier.length, TETE);
	let nSecond = Math.min(second.length, SECOND);
	let reste = max - nPremier - nSecond;
	const complementPremier = Math.max(0, Math.min(reste, premier.length - nPremier));
	nPremier += complementPremier;
	reste -= complementPremier;
	nSecond += Math.max(0, Math.min(reste, second.length - nSecond));
	return [...premier.slice(0, nPremier), ...second.slice(0, nSecond)];
}

/**
 * Live announcement of the field, agreed in number. A type that was not searched, or whose search
 * failed, is `null`: it is not counted as a zero (a failure is said by its own line).
 */
export function annonceResultats(
	nCommunes: number | null,
	nAdresses: number | null,
	q: string,
	adresses: ResultatRechercheAdresse[] = [],
	nArrondissements = 0
): string {
	// `nArrondissements` of the `nCommunes` are arrondissements of Paris, Lyon or Marseille
	// (04.7-09): counted apart, never called communes.
	const parts: string[] = [];
	const communes = nCommunes ? nCommunes - nArrondissements : 0;
	if (communes) parts.push(communes === 1 ? '1 commune' : `${communes} communes`);
	if (nArrondissements) parts.push(nArrondissements === 1 ? '1 arrondissement' : `${nArrondissements} arrondissements`);
	if (nAdresses) {
		const voies = adresses.filter((resultat) => resultat.type_resultat === 'voie').length;
		const numerotees = adresses.length - voies;
		if (numerotees) parts.push(numerotees === 1 ? '1 adresse' : `${numerotees} adresses`);
		if (voies) parts.push(voies === 1 ? '1 voie' : `${voies} voies`);
		if (adresses.length === 0) parts.push(nAdresses === 1 ? '1 adresse' : `${nAdresses} adresses`);
	}
	if (parts.length > 0) {
		const total = (nCommunes ?? 0) + (nAdresses ?? 0);
		// One masculine noun (arrondissement) makes the agreement masculine.
		const accord = nArrondissements ? 'proposé' : 'proposée';
		return `${parts.join(' et ')} ${total === 1 ? accord : `${accord}s`}`;
	}
	return `Aucune ${objetsCherches(nCommunes, nAdresses)} proposée pour « ${q} »`;
}

/** Heading derived only from the result types actually returned by the API. */
export function libelleGroupeAdresses(resultats: ResultatRechercheAdresse[]): string {
	const adresses = resultats.some((resultat) => resultat.type_resultat === 'adresse');
	const voies = resultats.some((resultat) => resultat.type_resultat === 'voie');
	if (adresses && voies) return 'Adresses et voies';
	return voies ? 'Voies' : 'Adresses';
}

/** Conservative copy for the API-qualified number match; no query parsing happens here. */
export function messageCorrespondanceNumero(
	correspondance: CorrespondanceNumeroRecherche | null | undefined
): string | null {
	if (correspondance?.etat !== 'proches' || correspondance.portee !== 'resultats_retournes') return null;
	const suffixe = correspondance.suffixe_demande ? ` ${correspondance.suffixe_demande}` : '';
	return `Le numéro ${correspondance.numero_demande}${suffixe} n'apparaît pas dans ces résultats. Voici les résultats proches renvoyés.`;
}

/** Title of « Aucun résultat », naming what was searched (communes, addresses, or both). */
export function titreAucunResultat(nCommunes: number | null, nAdresses: number | null, q: string): string {
	return `Aucune ${objetsCherches(nCommunes, nAdresses)} trouvée pour « ${q} »`;
}

function objetsCherches(nCommunes: number | null, nAdresses: number | null): string {
	if (nCommunes !== null && nAdresses !== null) return 'commune ni adresse';
	return nAdresses !== null ? 'adresse' : 'commune';
}

/** « {numéro}{indice} {voie} », as the API words the served label (`libelle_adresse`). */
export function libelleVoie(numero: string | null, rep: string | null, voie: string | null): string {
	const numeroComplet = `${numero ?? ''}${rep ?? ''}`;
	return [numeroComplet, voie ?? ''].filter(Boolean).join(' ');
}

/** « Département (code) » with a no-break space, or « Département {code} » without a served name. */
export function libelleDepartement(departement: DepartementRef): string {
	return departement.nom ? `${departement.nom} (${departement.code})` : `Département ${departement.code}`;
}

/** h1 of an address page without a served label (out of coverage): « Adresse à {Commune} ({dép.}) ». */
export function titreAdresseA(commune: string, departement: DepartementRef): string {
	return `Adresse à ${commune} (${departement.code})`;
}

/** « {CP} {Commune} », the parts that are served. */
export function libelleCommune(codePostal: string | null, commune: string | null): string {
	return [codePostal ?? '', commune ?? ''].filter(Boolean).join(' ');
}

/** « {CP} {Commune} · {Département (code)} », from the served fields of a search result. */
export function lieuAdresse(result: ResultatRechercheAdresse): string {
	return [
		libelleCommune(result.code_postal, result.nom_commune),
		result.departement ? libelleDepartement(result.departement) : ''
	]
		.filter(Boolean)
		.join(' · ');
}

/** A street chosen in the field comes back as « {voie}, {CP} {Commune} », without a number (D-107). */
export function saisieVoie(result: ResultatRechercheAdresse): string {
	return [result.nom_voie ?? '', libelleCommune(result.code_postal, result.nom_commune)]
		.filter(Boolean)
		.join(', ');
}

/**
 * Title of the out-of-coverage notice of an address search: the territory served by the API — a
 * commune, every homonym, or, for a postal code, its departement — never a commune guessed by the
 * client (D-105).
 */
export function titreAdressesHorsCouverture(ban: HebergementBanRecherche): string {
	const territoires = ban.territoire ? [ban.territoire] : (ban.homonymes ?? []);
	const noms = territoires.map((territoire) =>
		territoire.nom
			? `${territoire.nom} (${territoire.departement.code})`
			: libelleDepartement(territoire.departement)
	);
	return noms.length > 0
		? `${noms.join(', ')} : adresses hors couverture`
		: 'Adresses hors couverture';
}

// ---- An empty search proposes a next step (04.7-14, Origami R16) ----------------------------------
// The commune search matches a name prefix, accents, case, hyphens and spaces folded (zone/queries.py
// `_NORMALIZED_NOM`): what it cannot fold is an abbreviation, a missing elision or a typographic
// apostrophe. Those variants are computed here and offered as links, never searched behind the
// visitor's back; communes « au nom proche » are read by the results page with a shortened prefix.

/** Other spellings of *q* the referential may carry: St/Ste expanded, elision and apostrophe restored. */
export function variantesOrthographe(q: string): string[] {
	const saisie = q.trim();
	if (saisie === '' || /^\d+$/.test(saisie)) return [];
	const casse = (mot: string, modele: string) => (modele[0] === modele[0].toUpperCase() ? mot : mot.toLowerCase());
	const variante = saisie
		.replace(/\b(st|ste)\b(?=[\s-])/gi, (abrev: string) => casse(abrev.toLowerCase() === 'st' ? 'Saint' : 'Sainte', abrev))
		.replace(/’/g, "'")
		.replace(/(^|[\s-])([ld]) (?=[aeiouyhàâäéèêëîïôöùûü])/giu, "$1$2'");
	return variante !== saisie ? [variante] : [];
}

/** Shortened prefixes of a commune name to read communes « au nom proche »: never an address. */
export function prefixesProches(q: string): string[] {
	const saisie = q.trim();
	if (/\d/.test(saisie)) return [];
	const longueurs = [saisie.length - 2, Math.floor(saisie.length / 2)].filter((n) => n >= 3);
	return [...new Set(longueurs.map((n) => saisie.slice(0, n).replace(/[\s'’-]+$/, '')))].filter((p) => p.length >= 3);
}
