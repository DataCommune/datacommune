import { readFileSync } from 'node:fs';
import { expect, type APIRequestContext } from '@playwright/test';

// SEM-02 audit of the public leaves of phase 4 (plan 04-09, R06-09/8): « chaque valeur porte
// source, période, unité, définition et nature du rapprochement ; le contrat sémantique
// `fiche-fields` reste la source unique de sens ».
//
// The inventory is built BEFORE any reading of metadata, from two sides:
//   - the schemas: every leaf path of the 200 answer of each route in `contracts/openapi.v1.json`
//     must be classified by a rule below (a field added to a schema without a rule fails);
//   - the serialized answers: every leaf actually served (null included, every page of the lists)
//     must be classified too (a scalar served outside the schema fails).
// Classes: `technique` (identifiers, pagination, generation: an explicit list, never a default),
// `metadonnee` (the source, period, unit, reason, state or relation OF a value), `absence` (an
// optional object served null) and `semantique` (a value the visitor reads). The denominator of a
// surface counts its semantic leaves by their path alone, whatever their metadata: a value whose
// metadata are missing stays in the denominator and lowers the numerator.
//
// A semantic leaf conforms when its rule resolves, on the same answer:
//   - définition: a `field_id` of the contract with a non-empty description (`fiche-fields`);
//   - source: served on the value, or inherited from a named ancestor (the list, the cell);
//   - période: served, or inherited from a named ancestor (the date of the document, the period of
//     the list or of the applied criteria, the millésime of a reference);
//   - unité ou motif: a value carries the served unit or the unit of its contract; an absence
//     carries its served reason or state;
//   - rattachement: served, or inherited from a named ancestor, and one of the contract's
//     `allowed_rattachements`; the contract's own relation only where a rule says why;
//   - méthode (gaps of the comparators only): the rule of the comparison it was computed by.
// A value is never skipped because it is not wrapped in a `ValeurLieu`.
//
// Known gaps: a gap observed in the synthetic answers AND read in the production code is listed in
// `ECARTS_CONNUS` (path and missing metadata, with the code that explains it). It is reported, never
// counted as conforming; any other gap fails. In `exiger` mode (synthetic source) a declared gap
// that is not observed fails as well, so a fixed gap must leave the list.

type Json = null | boolean | number | string | Json[] | { [cle: string]: Json };
type Objet = { [cle: string]: Json };

export type Surface = 'fiche' | 'documents' | 'ventes' | 'declarees' | 'declarees-vefa' | 'ressemblantes' | 'document' | 'cmp-adresses' | 'cmp-dpe';

/** The served route of each surface, as named in the OpenAPI contract. */
export const ROUTES: Record<Surface, string> = {
	fiche: '/v1/adresse/{ban_id}',
	documents: '/v1/adresse/{ban_id}/dpe',
	ventes: '/v1/adresse/{ban_id}/ventes',
	// C37: the sales declared at the address, a relation of its own.
	declarees: '/v1/adresse/{ban_id}/ventes-declarees',
	// C4 (04.5-07): the same route as the page reads it, `vefa=incluses` (VEFA listed and marked).
	'declarees-vefa': '/v1/adresse/{ban_id}/ventes-declarees',
	ressemblantes: '/v1/adresse/{ban_id}/ventes-ressemblantes',
	document: '/v1/dpe/{numero}',
	'cmp-adresses': '/v1/adresses/comparaison',
	'cmp-dpe': '/v1/dpe/comparaison'
};

/** The two registers of the report line `sem02 adresse=… dpe=…`. */
export const REGISTRES = {
	adresse: ['fiche', 'documents', 'ventes', 'declarees', 'declarees-vefa', 'ressemblantes', 'cmp-adresses'],
	dpe: ['document', 'cmp-dpe']
} as const satisfies Record<string, readonly Surface[]>;

// --- Contracts ---------------------------------------------------------------------------------

interface ChampContrat {
	field_id: string;
	unit: string;
	rattachement: string;
	description: string;
}
interface Contrat {
	allowed_rattachements: string[];
	fields: ChampContrat[];
}

const CONTRAT = JSON.parse(
	readFileSync(new URL('../../../src/lib/data/fiche-fields.v1.json', import.meta.url), 'utf8')
) as Contrat;
const CHAMPS = new Map(CONTRAT.fields.map((champ) => [champ.field_id, champ]));
const RATTACHEMENTS = new Set(CONTRAT.allowed_rattachements);

type Schema = { [cle: string]: unknown };
const OPENAPI = JSON.parse(
	readFileSync(new URL('../../../../contracts/openapi.v1.json', import.meta.url), 'utf8')
) as { paths: Record<string, { get: { responses: Record<string, { content: Record<string, { schema: Schema }> }> } }>; components: { schemas: Record<string, Schema> } };

/** Every leaf path of the 200 answer of *route* in the OpenAPI contract (`[]` for an array index, `*` for a map key). */
export function feuillesDuSchema(route: string): string[] {
	const racine = OPENAPI.paths[route]?.get.responses['200']?.content['application/json']?.schema;
	if (!racine) throw new Error(`route absente du contrat OpenAPI : ${route}`);
	const sortie = new Set<string>();
	const parcourir = (schema: Schema, chemin: string, pile: string[]): void => {
		if (typeof schema.$ref === 'string') {
			const nom = schema.$ref.split('/').pop()!;
			if (pile.includes(nom)) throw new Error(`schéma récursif non audité : ${nom} sous ${chemin}`);
			parcourir(OPENAPI.components.schemas[nom], chemin, [...pile, nom]);
			return;
		}
		for (const union of ['anyOf', 'oneOf', 'allOf']) {
			if (Array.isArray(schema[union])) {
				for (const branche of schema[union] as Schema[]) parcourir(branche, chemin, pile);
				return;
			}
		}
		if (schema.type === 'object' || schema.properties) {
			const proprietes = (schema.properties ?? {}) as Record<string, Schema>;
			for (const [cle, sous] of Object.entries(proprietes)) parcourir(sous, chemin ? `${chemin}.${cle}` : cle, pile);
			if (schema.additionalProperties && typeof schema.additionalProperties === 'object') {
				parcourir(schema.additionalProperties as Schema, `${chemin}.*`, pile);
			}
			return;
		}
		if (schema.type === 'array') {
			parcourir((schema.items ?? {}) as Schema, `${chemin}[]`, pile);
			return;
		}
		if (schema.type === 'null') return;
		sortie.add(chemin);
	};
	parcourir(racine, '', []);
	return [...sortie].sort();
}

// --- Inventory of the serialized answer --------------------------------------------------------

export interface Feuille {
	/** Normalized path: `[]` for an array index. */
	chemin: string;
	/** Concrete path: keys and indices. */
	cles: (string | number)[];
	valeur: Json;
}

/** Every leaf of a serialized answer: scalars and nulls, every array element, at any depth. */
export function inventaireFeuilles(reponse: unknown): Feuille[] {
	const sortie: Feuille[] = [];
	const parcourir = (valeur: Json, chemin: string, cles: (string | number)[]): void => {
		if (Array.isArray(valeur)) {
			valeur.forEach((element, index) => parcourir(element, `${chemin}[]`, [...cles, index]));
			return;
		}
		if (valeur !== null && typeof valeur === 'object') {
			for (const [cle, sous] of Object.entries(valeur)) parcourir(sous, chemin ? `${chemin}.${cle}` : cle, [...cles, cle]);
			return;
		}
		sortie.push({ chemin, cles, valeur });
	};
	parcourir(reponse as Json, '', []);
	return sortie;
}

// --- Rules -------------------------------------------------------------------------------------

interface Contexte {
	racine: Json;
	feuille: Feuille;
	/** The *n*-th enclosing object of the leaf (1 = the object holding it), arrays skipped. */
	objet(n: number): Objet;
	/** A value at an absolute dotted path of the answer (no array). */
	lire(chemin: string): Json;
}

interface Meta {
	champ: string | null;
	source: Json;
	periode: Json;
	/** Served unit (null: the contract's unit applies). */
	unite?: Json;
	/** Served reason or state of an absence. */
	motif: Json;
	rattachement: Json;
	/** The contract's relation is admitted, for the reason given in the rule. */
	rattachementDuContrat?: string;
	/** Rule of comparison of a gap (comparators). */
	methode?: Json;
}

type Regle =
	| { classe: 'technique' | 'metadonnee' | 'absence' }
	| { classe: 'semantique'; meta: (contexte: Contexte) => Meta };

const TECHNIQUE: Regle = { classe: 'technique' };
const METADONNEE: Regle = { classe: 'metadonnee' };
const ABSENCE: Regle = { classe: 'absence' };
const semantique = (meta: (contexte: Contexte) => Meta): Regle => ({ classe: 'semantique', meta });

const objet = (valeur: Json | undefined): Objet => (valeur !== null && typeof valeur === 'object' && !Array.isArray(valeur) ? valeur : {});
const sauf = (valeur: Json | undefined): Json => (valeur === undefined ? null : valeur);

/** A served value carrying its own metadata (`ValeurLieu`, `ValeurTheme`, a cell). */
function propre(porteur: Objet, partiel: Partial<Meta> = {}): Meta {
	return {
		champ: typeof porteur.field_id === 'string' ? porteur.field_id : null,
		source: sauf(porteur.source),
		periode: sauf(porteur.periode),
		unite: sauf(porteur.unite),
		motif: porteur.etat !== undefined && porteur.etat !== 'value' ? sauf(porteur.etat) : sauf(porteur.motif),
		rattachement: sauf(porteur.rattachement),
		...partiel
	};
}

/** The reference of a landmark (④): its own source, millésime, unit and commune. */
function reference(contexte: Contexte): Meta {
	const ref = contexte.objet(1);
	const valeur = contexte.objet(2);
	return {
		champ: typeof valeur.field_id === 'string' ? valeur.field_id : null,
		source: sauf(ref.source),
		periode: sauf(ref.millesime),
		unite: sauf(ref.unite),
		motif: null,
		rattachement: objet(ref.territoire).nom ? 'commune_reference' : null
	};
}

/** The selection of the most recent document (D-109), under the value that carries it. */
function selection(porteur: (contexte: Contexte) => Objet) {
	return (contexte: Contexte): Meta => {
		const valeur = porteur(contexte);
		const sel = objet(valeur.selection);
		return {
			champ: 'adresse.dpe.plus_recent',
			source: sauf(valeur.source),
			// The date of the selected document(s), else the period of the count (its import).
			periode: sel.date ?? sauf(valeur.periode),
			motif: sauf(sel.motif),
			rattachement: sauf(valeur.rattachement)
		};
	};
}

/** A field of a card of the declared documents: the list's source and relation, the document's date. */
/** A card of the raw-address group (04.1-13): its own relation, served on the card. */
function carteAdresseBrute(champ: string) {
	return (contexte: Contexte): Meta => {
		const texte = contexte.feuille.chemin.endsWith('adresse_declaree.texte');
		const carte = contexte.objet(texte ? 2 : 1);
		const liste = objet(objet(contexte.racine).donnees);
		return {
			champ,
			source: sauf(liste.source),
			periode: sauf(carte.date_etablissement),
			motif: texte ? sauf(contexte.objet(1).motif) : null,
			rattachement: sauf(carte.rattachement)
		};
	};
}

function carteDocument(champ: string) {
	return (contexte: Contexte): Meta => {
		const carte = contexte.objet(contexte.feuille.chemin.endsWith('adresse_declaree.texte') ? 2 : 1);
		const liste = objet(objet(contexte.racine).donnees);
		return {
			champ,
			source: sauf(liste.source),
			periode: sauf(carte.date_etablissement),
			motif: contexte.feuille.chemin.endsWith('adresse_declaree.texte') ? sauf(contexte.objet(1).motif) : null,
			rattachement: sauf(liste.rattachement)
		};
	};
}

/** A field of a sale: the source, period and relation of its list (parcel, or radius with criteria). */
function vente(champ: string, ressemblantes: boolean) {
	return (contexte: Contexte): Meta => {
		const liste = objet(objet(contexte.racine).donnees);
		const vente = contexte.objet(contexte.feuille.chemin.includes('.composition.') || contexte.feuille.chemin.includes('.minimisation.') ? 2 : 1);
		const criteres = objet(liste.criteres_appliques);
		return {
			champ,
			source: sauf(liste.source),
			periode: ressemblantes ? sauf(criteres.periode) : sauf(liste.periode),
			motif: objet(vente.minimisation).motif ?? null,
			rattachement: sauf(liste.rattachement)
		};
	};
}

/** A raw field of the document page: provenance of the page, date of the document. */
function document(champ: string, motif?: (contexte: Contexte) => Json) {
	return (contexte: Contexte): Meta => ({
		champ,
		source: sauf(objet(contexte.lire('donnees.provenance')).source),
		periode: sauf(objet(contexte.lire('donnees.dates')).etablissement),
		motif: motif ? motif(contexte) : null,
		rattachement: null,
		// The page is ONE document: its fields are tied to the address typed by its diagnostician,
		// the relation of every `dpe.document.*` field in the contract (04-04).
		rattachementDuContrat: 'champ du document affiché seul'
	});
}

/** A served value of the document page, dated by the document when it carries no period. */
function valeurDocument(contexte: Contexte): Meta {
	const valeur = contexte.objet(1);
	return propre(valeur, { periode: valeur.periode ?? sauf(objet(contexte.lire('donnees.dates')).etablissement) });
}

// Comparators -----------------------------------------------------------------------------------

/** The cell holding the leaf (`groupes[g].lignes[l].cellules[c]`). */
function cellule(contexte: Contexte): Objet {
	const { cles } = contexte.feuille;
	const i = cles.indexOf('cellules');
	let noeud: Json = contexte.racine;
	for (const cle of cles.slice(0, i + 2)) noeud = (noeud as Objet)[cle as string] ?? (noeud as Json[])[cle as number];
	return objet(noeud);
}

/** The cell of another row for the same column (`ban_id` or `numero`). */
function celluleDeLaLigne(racine: Json, cleLigne: string, colonne: string): Objet {
	for (const groupe of (objet(objet(racine).donnees).groupes ?? []) as Objet[]) {
		for (const ligne of (groupe.lignes ?? []) as Objet[]) {
			if (ligne.cle !== cleLigne) continue;
			const trouvee = ((ligne.cellules ?? []) as Objet[]).find((c) => c.numero === colonne || c.ban_id === colonne);
			if (trouvee) return trouvee;
		}
	}
	return {};
}

const colonnes = (racine: Json) => (objet(objet(racine).donnees).colonnes ?? []) as Objet[];

/** A cell of the DPE comparator: dated by its document (« Établi le »), compared under the method of both columns. */
function celluleDpe(contexte: Contexte, ecart: boolean): Meta {
	const c = cellule(contexte);
	const numero = String(c.numero ?? '');
	const date = celluleDeLaLigne(contexte.racine, 'etabli_le', numero).valeur ?? null;
	const meta = propre(c, { periode: c.periode ?? date });
	if (!ecart) return meta;
	const toutes = colonnes(contexte.racine);
	const sienne = toutes.find((colonne) => colonne.numero === numero);
	const referente = toutes[0];
	// The gap or verdict of a cell is computed against column 1 under the rule of the two
	// methods (`compare_cell`, « méthodes de calcul différentes »): both must be served.
	const methode = sienne?.methode && referente?.methode ? `${referente.methode}→${sienne.methode}` : null;
	return { ...meta, unite: ecart && contexte.feuille.chemin.endsWith('ecart.valeur') ? sauf(objet(c.ecart).unite) : meta.unite, motif: c.motif_non_calcul ?? meta.motif, methode };
}

/** The row holding the leaf (`groupes[g].lignes[l]`). */
function ligne(contexte: Contexte): Objet {
	const { cles } = contexte.feuille;
	const i = cles.indexOf('lignes');
	let noeud: Json = contexte.racine;
	for (const cle of cles.slice(0, i + 2)) noeud = (noeud as Objet)[cle as string] ?? (noeud as Json[])[cle as number];
	return objet(noeud);
}

/**
 * A cell of the address comparator: the value of the address page, compared by its field's rule.
 * Its period, when the cell carries none: the date of the selected document (row « Le plus récent »),
 * or the date of the document designated on its column (designated rows, 04-11).
 */
function celluleAdresse(contexte: Contexte, ecart: boolean): Meta {
	const c = cellule(contexte);
	const designee = ligne(contexte).designe === true
		? objet(colonnes(contexte.racine).find((colonne) => colonne.ban_id === c.ban_id)?.designation).date_etablissement ?? null
		: null;
	const meta = propre(c, { periode: c.periode ?? objet(c.selection).date ?? designee });
	if (!ecart) return meta;
	// `compare_cell` is keyed by the field of the row: the rule of the gap is the field itself.
	return { ...meta, unite: contexte.feuille.chemin.endsWith('ecart.valeur') ? sauf(objet(c.ecart).unite) : meta.unite, motif: c.motif_non_calcul ?? meta.motif, methode: sauf(c.field_id) };
}

/** A header of a designated DPE on a compared address: the same document's rows in the table. */
function designation(champ: string, cleLigne: string) {
	return (contexte: Contexte): Meta => {
		const colonne = contexte.objet(2);
		const designee = contexte.objet(1);
		const rangee = celluleDeLaLigne(contexte.racine, cleLigne, String(colonne.ban_id ?? ''));
		return { ...propre(rangee), champ, periode: rangee.periode ?? sauf(designee.date_etablissement), motif: sauf(designee.motif) };
	};
}

/** A column header of the DPE comparator: the declared address row of the same number. */
function colonneDpe(contexte: Contexte): Meta {
	const numero = String(contexte.objet(2).numero ?? '');
	const rangee = celluleDeLaLigne(contexte.racine, 'adresse_declaree', numero);
	const date = celluleDeLaLigne(contexte.racine, 'etabli_le', numero).valeur ?? null;
	return { ...propre(rangee), champ: 'dpe.document.adresse_declaree', periode: rangee.periode ?? date, motif: sauf(contexte.objet(1).motif) };
}

// Rule tables -----------------------------------------------------------------------------------

/** Metadata keys of any served value: classified once, everywhere (exact rules come first). */
const SUFFIXES_META = [
	'field_id', 'etat', 'motif', 'periode', 'unite', 'rattachement',
	'source', 'source.cle', 'source.nom', 'source.producteur', 'source.url', 'source.licence',
	'reference', 'reference.millesime', 'reference.unite', 'reference.url',
	'reference.source.cle', 'reference.source.nom', 'reference.source.producteur', 'reference.source.url',
	'reference.territoire.code_insee', 'reference.territoire.nom',
	'liste_incomplete', 'rayon_m'
];

const IDENTITE_TECHNIQUE = [
	'donnees.identite.etat', 'donnees.identite.ban_id', 'donnees.identite.libelle', 'donnees.identite.numero',
	'donnees.identite.rep', 'donnees.identite.nom_voie', 'donnees.identite.code_postal',
	'donnees.identite.commune.code_insee', 'donnees.identite.commune.nom',
	'donnees.identite.departement.code', 'donnees.identite.departement.nom'
];

/**
 * The parcel of the identity line (« Parcelle 85191000AB0012 », displayed and exported): a value the
 * visitor reads (final review FRONT WR-04). It is backed by the same value served with its metadata
 * by the theme « Bâtiment et parcelle » (`adresse.parcelle.identifiant`); without it, nothing
 * dates nor sources it.
 */
function parcelleDeLIdentite(contexte: Contexte): Meta {
	const themes = (objet(objet(contexte.racine).donnees).themes ?? []) as Objet[];
	for (const theme of themes) {
		for (const groupe of (theme.groupes ?? []) as Objet[]) {
			for (const champ of (groupe.champs ?? []) as Objet[]) {
				if (champ.field_id === 'adresse.parcelle.identifiant' && champ.valeur === contexte.feuille.valeur) return propre(champ);
			}
		}
	}
	return { champ: 'adresse.parcelle.identifiant', source: null, periode: null, motif: null, rattachement: null };
}

const SELECTION = (prefixe: string, porteur: (contexte: Contexte) => Objet): Record<string, Regle> => {
	const regle = semantique(selection(porteur));
	return {
		[`${prefixe}.selection`]: ABSENCE,
		[`${prefixe}.selection.etat`]: regle,
		[`${prefixe}.selection.motif`]: METADONNEE,
		[`${prefixe}.selection.date`]: regle,
		[`${prefixe}.selection.document`]: ABSENCE,
		[`${prefixe}.selection.document.numero`]: TECHNIQUE,
		[`${prefixe}.selection.document.etiquette_dpe`]: regle,
		[`${prefixe}.selection.document.etiquette_ges`]: regle,
		[`${prefixe}.selection.document.date_etablissement`]: regle,
		[`${prefixe}.selection.documents_ex_aequo[].numero`]: TECHNIQUE,
		[`${prefixe}.selection.documents_ex_aequo[].etiquette_dpe`]: regle,
		[`${prefixe}.selection.documents_ex_aequo[].etiquette_ges`]: regle,
		[`${prefixe}.selection.documents_ex_aequo[].date_etablissement`]: regle
	};
};

const VALEUR = semantique((contexte) => propre(contexte.objet(1)));
const REFERENCE = semantique(reference);

const VENTES = (ressemblantes: boolean, liste = ressemblantes ? 'adresse.dvf.ventes_ressemblantes.effectif' : 'adresse.dvf.ventes_parcelle.nombre'): Record<string, Regle> => {
	return {
		'donnees.ventes[].date': semantique(vente(liste, ressemblantes)),
		'donnees.ventes[].nature': semantique(vente(liste, ressemblantes)),
		'donnees.ventes[].composition.maisons': semantique(vente(liste, ressemblantes)),
		'donnees.ventes[].composition.appartements': semantique(vente(liste, ressemblantes)),
		'donnees.ventes[].composition.dependances': semantique(vente(liste, ressemblantes)),
		'donnees.ventes[].composition.locaux_activite': semantique(vente(liste, ressemblantes)),
		'donnees.ventes[].surface_batie': semantique(vente('adresse.dvf.vente.surface_batie', ressemblantes)),
		'donnees.ventes[].prix': semantique(vente('adresse.dvf.vente.prix', ressemblantes)),
		'donnees.ventes[].prix_m2': semantique(vente('adresse.dvf.vente.prix_m2', ressemblantes)),
		'donnees.ventes[].prix_affiche': METADONNEE,
		'donnees.ventes[].hors_stats_vefa': METADONNEE,
		'donnees.ventes[].minimisation': ABSENCE,
		'donnees.ventes[].minimisation.motif': METADONNEE,
		// The distance of a similar sale is its relation to the point (radius of the criteria).
		'donnees.ventes[].distance_m': ressemblantes ? semantique(vente(liste, true)) : METADONNEE,
		'donnees.etat': METADONNEE,
		'donnees.page': TECHNIQUE,
		'donnees.pages': TECHNIQUE,
		'donnees.par_page': TECHNIQUE,
		'donnees.generation': TECHNIQUE
	};
};

const REGLES: Record<Surface, Record<string, Regle>> = {
	fiche: {
		statut: TECHNIQUE,
		...Object.fromEntries(IDENTITE_TECHNIQUE.map((chemin) => [chemin, TECHNIQUE])),
		'donnees.identite.parcelle': ABSENCE,
		'donnees.identite.parcelle.id_parcelle': semantique(parcelleDeLIdentite),
		'donnees.identite.parcelle.etat': METADONNEE,
		'donnees.identite.parcelle.nombre_candidats': METADONNEE,
		'donnees.couverture.etat': METADONNEE,
		'donnees.couverture.motif': METADONNEE,
		'donnees.reperes.dpe.valeur': VALEUR,
		'donnees.reperes.ventes.nombre.valeur': VALEUR,
		'donnees.reperes.ventes.derniere.valeur': VALEUR,
		'donnees.reperes.argiles.valeur': VALEUR,
		'donnees.reperes.reference_commune.maisons.valeur': VALEUR,
		'donnees.reperes.reference_commune.appartements.valeur': VALEUR,
		'donnees.reperes.dpe.reference.valeur': REFERENCE,
		'donnees.reperes.ventes.nombre.reference.valeur': REFERENCE,
		'donnees.reperes.ventes.derniere.reference.valeur': REFERENCE,
		'donnees.reperes.argiles.reference.valeur': REFERENCE,
		'donnees.reperes.reference_commune.maisons.reference.valeur': REFERENCE,
		'donnees.reperes.reference_commune.appartements.reference.valeur': REFERENCE,
		...SELECTION('donnees.reperes.dpe', (contexte) => objet(objet(objet(objet(contexte.racine).donnees).reperes).dpe)),
		'donnees.themes[].cle': TECHNIQUE,
		'donnees.themes[].titre': TECHNIQUE,
		'donnees.themes[].applicable': METADONNEE,
		'donnees.themes[].groupes[].cle': TECHNIQUE,
		'donnees.themes[].groupes[].titre': TECHNIQUE,
		'donnees.themes[].groupes[].champs[].valeur': VALEUR,
		'donnees.themes[].groupes[].champs[].reference.valeur': REFERENCE,
		'donnees.themes[].groupes[].champs[].elements[].libelle': semantique((contexte) => propre(contexte.objet(2))),
		'donnees.themes[].groupes[].champs[].elements[].nature': semantique((contexte) => propre(contexte.objet(2))),
		'donnees.themes[].groupes[].champs[].elements[].distance_m': semantique((contexte) => propre(contexte.objet(2)))
	},
	documents: {
		statut: TECHNIQUE,
		'donnees.date_import': METADONNEE,
		'donnees.total': semantique((contexte) => {
			const liste = contexte.objet(1);
			return { champ: 'adresse.dpe.nombre_declares', source: sauf(liste.source), periode: sauf(liste.date_import), motif: liste.etat !== 'value' ? sauf(liste.motif) : null, rattachement: sauf(liste.rattachement) };
		}),
		'donnees.distribution': ABSENCE,
		...Object.fromEntries(
			['A', 'B', 'C', 'D', 'E', 'F', 'G', 'unknown'].map((classe) => [
				`donnees.distribution.${classe}`,
				semantique((contexte) => {
					const liste = contexte.objet(2);
					return { champ: 'dpe.document.etiquette_dpe', source: sauf(liste.source), periode: sauf(liste.date_import), unite: 'count', motif: null, rattachement: sauf(liste.rattachement) };
				})
			])
		),
		'donnees.documents[].numero': TECHNIQUE,
		'donnees.documents[].etiquette_dpe': semantique(carteDocument('dpe.document.etiquette_dpe')),
		'donnees.documents[].etiquette_ges': semantique(carteDocument('dpe.document.etiquette_ges')),
		'donnees.documents[].type_batiment': semantique(carteDocument('dpe.document.type_batiment')),
		'donnees.documents[].surface_habitable': semantique(carteDocument('dpe.document.surface_habitable')),
		'donnees.documents[].date_etablissement': semantique(carteDocument('dpe.document.date_etablissement')),
		'donnees.documents[].date_fin_validite': semantique(carteDocument('dpe.document.date_fin_validite')),
		// A badge says the place of the document among the declared ones (most recent, tie, expired,
		// former method, replaced): defined by the field of the selection (D-109).
		'donnees.documents[].badges[]': semantique(carteDocument('adresse.dpe.plus_recent')),
		'donnees.documents[].adresse_declaree': ABSENCE,
		'donnees.documents[].adresse_declaree.texte': semantique(carteDocument('dpe.document.adresse_declaree')),
		...SELECTION('donnees', (contexte) => {
			const liste = objet(objet(contexte.racine).donnees);
			return { ...liste, periode: liste.date_import };
		}),
		'donnees.signal_plusieurs_logements': ABSENCE,
		'donnees.signal_plusieurs_logements.nombre_lots_habitation': semantique((contexte) => ({
			...propre(contexte.objet(1)),
			champ: 'adresse.copropriete.immatriculation'
		})),
		'donnees.constat_d110': ABSENCE,
		'donnees.constat_d110.criteres[]': semantique((contexte) => {
			const constat = contexte.objet(1);
			const liste = contexte.objet(2);
			const sources = (constat.sources ?? []) as Json[];
			return {
				champ: 'adresse.dpe.constat_d110',
				source: sources.length > 0 ? sources : null,
				periode: sauf(liste.date_import),
				motif: null,
				rattachement: null,
				// The finding is defined on the parcel of the point (one building on it), the relation of
				// its contract entry; it carries no relation of its own (04-19).
				rattachementDuContrat: 'constat défini sur la parcelle du point'
			};
		}),
		'donnees.constat_d110.sources[].cle': METADONNEE,
		'donnees.constat_d110.sources[].nom': METADONNEE,
		'donnees.constat_d110.sources[].producteur': METADONNEE,
		'donnees.constat_d110.sources[].url': METADONNEE,
		'donnees.page': TECHNIQUE,
		'donnees.pages': TECHNIQUE,
		'donnees.par_page': TECHNIQUE,
		'donnees.generation': TECHNIQUE,
		// 04.1-13 (D-127): the separate group of documents attached by their exact raw address, outside
		// the count, the distribution and the selection of the ADEME key; each card serves its relation.
		'donnees.etat_adresse_brute': METADONNEE,
		'donnees.total_adresse_brute': semantique((contexte) => {
			const liste = contexte.objet(1);
			return {
				champ: 'adresse.dpe.documents_adresse_brute',
				source: sauf(liste.source),
				periode: sauf(liste.date_import),
				motif: liste.etat_adresse_brute !== 'value' ? sauf(liste.etat_adresse_brute) : null,
				rattachement: null,
				rattachementDuContrat: 'groupe défini par son entrée du contrat, chaque carte sert son rattachement'
			};
		}),
		'donnees.documents_adresse_brute[].numero': TECHNIQUE,
		'donnees.documents_adresse_brute[].etiquette_dpe': semantique(carteAdresseBrute('dpe.document.etiquette_dpe')),
		'donnees.documents_adresse_brute[].etiquette_ges': semantique(carteAdresseBrute('dpe.document.etiquette_ges')),
		'donnees.documents_adresse_brute[].type_batiment': semantique(carteAdresseBrute('dpe.document.type_batiment')),
		'donnees.documents_adresse_brute[].surface_habitable': semantique(carteAdresseBrute('dpe.document.surface_habitable')),
		'donnees.documents_adresse_brute[].date_etablissement': semantique(carteAdresseBrute('dpe.document.date_etablissement')),
		'donnees.documents_adresse_brute[].date_fin_validite': semantique(carteAdresseBrute('dpe.document.date_fin_validite')),
		'donnees.documents_adresse_brute[].badges[]': semantique(carteAdresseBrute('adresse.dpe.documents_adresse_brute')),
		'donnees.documents_adresse_brute[].adresse_declaree': ABSENCE,
		'donnees.documents_adresse_brute[].adresse_declaree.texte': semantique(carteAdresseBrute('dpe.document.adresse_declaree'))
	},
	ventes: {
		statut: TECHNIQUE,
		'donnees.total': semantique((contexte) => {
			const liste = contexte.objet(1);
			return { champ: 'adresse.dvf.ventes_parcelle.nombre', source: sauf(liste.source), periode: sauf(liste.periode), motif: liste.etat !== 'value' ? sauf(liste.motif) : null, rattachement: sauf(liste.rattachement) };
		}),
		'donnees.nombre_parcelles': METADONNEE,
		...VENTES(false)
	},
	// C37: same cards as the parcel list; the count and every card carry the declared-address relation.
	// Mode of the field (`vefa=exclues`, by number and suffix since 04.5-08): the total is
	// `adresse.dvf.ventes_adresse.nombre`, which never counts a
	// VEFA (D-12). A total served next to VEFA (a served VEFA count above zero, or a card marked) counts
	// them: it is not that field, and the audit reads it without one (counter-proof of 04.5-07).
	declarees: {
		statut: TECHNIQUE,
		'donnees.total': semantique((contexte) => {
			const liste = contexte.objet(1);
			const cartes = Array.isArray(liste.ventes) ? liste.ventes : [];
			const avecVefa = (typeof liste.nombre_vefa === 'number' && liste.nombre_vefa > 0) || cartes.some((carte) => objet(carte).hors_stats_vefa === true);
			return {
				champ: avecVefa ? null : 'adresse.dvf.ventes_adresse.nombre',
				source: sauf(liste.source),
				periode: sauf(liste.periode),
				motif: liste.etat !== 'value' ? sauf(liste.motif) : null,
				rattachement: sauf(liste.rattachement)
			};
		}),
		'donnees.nombre_vefa': METADONNEE,
		...VENTES(false, 'adresse.dvf.ventes_adresse.nombre')
	},
	// C4 (04.5-07): the mode the page reads (`vefa=incluses`). The total counts the listed mutations,
	// VEFA included, and `nombre_vefa` the VEFA among them: both are metadata of the list (its pages and
	// what it holds), never `adresse.dvf.ventes_adresse.nombre`, audited in the default mode above. The
	// cards carry the declared-address relation as in the default mode.
	'declarees-vefa': {
		statut: TECHNIQUE,
		'donnees.total': METADONNEE,
		'donnees.nombre_vefa': METADONNEE,
		...VENTES(false, 'adresse.dvf.ventes_adresse.nombre')
	},
	ressemblantes: {
		statut: TECHNIQUE,
		'donnees.effectif': semantique((contexte) => {
			const liste = contexte.objet(1);
			return { champ: 'adresse.dvf.ventes_ressemblantes.effectif', source: sauf(liste.source), periode: sauf(objet(liste.criteres_appliques).periode), motif: liste.etat !== 'value' ? sauf(liste.motif) : null, rattachement: sauf(liste.rattachement) };
		}),
		'donnees.criteres_appliques': ABSENCE,
		...Object.fromEntries(
			['type', 'surface', 'tolerance', 'surface_min', 'surface_max', 'perimetre', 'rayon_m', 'periode', 'periode.annees', 'periode.debut', 'periode.fin'].map((cle) => [
				`donnees.criteres_appliques.${cle}`,
				METADONNEE
			])
		),
		...VENTES(true)
	},
	document: {
		statut: TECHNIQUE,
		'donnees.numero': TECHNIQUE,
		'donnees.source_identity.reference': TECHNIQUE,
		'donnees.source_identity.sources[].dataset': METADONNEE,
		'donnees.source_identity.sources[].url': METADONNEE,
		'donnees.disponibilite': METADONNEE,
		'donnees.methode': semantique(document('dpe.document.methode')),
		'donnees.dates.etablissement': semantique(document('dpe.document.date_etablissement')),
		'donnees.dates.fin_validite': semantique(document('dpe.document.date_fin_validite')),
		...Object.fromEntries(
			[
				'etiquette_energie.classe', 'etiquette_energie.mesure', 'etiquette_climat.classe', 'etiquette_climat.mesure',
				// 04.1-12 (C51): the per-square-metre values published by the ADEME, served next to the totals.
				'etiquette_energie.mesure_m2', 'etiquette_climat.mesure_m2',
				'logement_decrit.type_batiment', 'logement_decrit.surface', 'cout_annuel'
			].flatMap((repere) => [
				[`donnees.reperes.${repere}.valeur`, semantique(valeurDocument)],
				[`donnees.reperes.${repere}.reference.valeur`, REFERENCE]
			])
		),
		'donnees.caracteristiques[].valeur': semantique(valeurDocument),
		'donnees.caracteristiques[].reference.valeur': REFERENCE,
		// The status line « En vigueur », « Expiré », « Remplacé selon l'ADEME »: validity of the document.
		'donnees.statut.code': semantique(document('dpe.document.date_fin_validite')),
		'donnees.statut.validite': METADONNEE,
		'donnees.statut.date': semantique(document('dpe.document.date_fin_validite')),
		'donnees.statut.remplace_par': TECHNIQUE,
		'donnees.statut.remplace': TECHNIQUE,
		'donnees.adresse_declaree': ABSENCE,
		'donnees.adresse_declaree.texte': semantique(document('dpe.document.adresse_declaree', (contexte) => objet(contexte.lire('donnees.adresse_declaree')).motif ?? null)),
		'donnees.adresse_divergente': semantique(document('dpe.document.adresse_declaree')),
		'donnees.adresse_ban': ABSENCE,
		'donnees.adresse_ban.ban_id': TECHNIQUE,
		// The BAN address the page reads out for the document: the result of the document ↔ BAN
		// relation (D-111), a value of the contract (final review FRONT WR-04), never `technique`.
		'donnees.adresse_ban.libelle': semantique(document('dpe.document.adresse_ban')),
		'donnees.adresse_ban.numero': TECHNIQUE,
		'donnees.adresse_ban.rep': TECHNIQUE,
		'donnees.adresse_ban.nom_voie': TECHNIQUE,
		'donnees.adresse_ban.commune': ABSENCE,
		'donnees.adresse_ban.commune.code_insee': TECHNIQUE,
		'donnees.adresse_ban.commune.nom': TECHNIQUE,
		'donnees.provenance.source': METADONNEE,
		'donnees.provenance.date_import': METADONNEE,
		// The nature of the relation between the document and the BAN address (D-111).
		'donnees.association': METADONNEE,
		'donnees.address_check': METADONNEE
	},
	'cmp-adresses': {
		statut: TECHNIQUE,
		'donnees.colonnes[].ban_id': TECHNIQUE,
		'donnees.colonnes[].libelle': TECHNIQUE,
		'donnees.colonnes[].numero_voie': TECHNIQUE,
		'donnees.colonnes[].code_postal': TECHNIQUE,
		'donnees.colonnes[].commune.code_insee': TECHNIQUE,
		'donnees.colonnes[].commune.nom': TECHNIQUE,
		'donnees.colonnes[].departement.code': TECHNIQUE,
		'donnees.colonnes[].departement.nom': TECHNIQUE,
		'donnees.colonnes[].identite_etat': METADONNEE,
		'donnees.colonnes[].couverture.etat': METADONNEE,
		'donnees.colonnes[].couverture.motif': METADONNEE,
		'donnees.colonnes[].designation': ABSENCE,
		'donnees.colonnes[].designation.numero': TECHNIQUE,
		'donnees.colonnes[].designation.etat': METADONNEE,
		'donnees.colonnes[].designation.motif': METADONNEE,
		'donnees.colonnes[].designation.classe_energie': semantique(designation('dpe.document.etiquette_dpe', 'designe_classe_energie')),
		'donnees.colonnes[].designation.date_etablissement': semantique(designation('dpe.document.date_etablissement', 'designe_etabli_le')),
		'donnees.designations_ecartees[].ban_id': TECHNIQUE,
		'donnees.designations_ecartees[].numero': TECHNIQUE,
		'donnees.ids_ecartes[]': TECHNIQUE,
		'donnees.groupes[].cle': TECHNIQUE,
		'donnees.groupes[].titre': TECHNIQUE,
		'donnees.groupes[].lignes[].cle': TECHNIQUE,
		'donnees.groupes[].lignes[].identiques': METADONNEE,
		'donnees.groupes[].lignes[].designe': METADONNEE,
		'donnees.groupes[].lignes[].cellules[].ban_id': TECHNIQUE,
		'donnees.groupes[].lignes[].cellules[].numero': TECHNIQUE,
		'donnees.groupes[].lignes[].cellules[].valeur': semantique((contexte) => celluleAdresse(contexte, false)),
		'donnees.groupes[].lignes[].cellules[].reference.valeur': REFERENCE,
		'donnees.groupes[].lignes[].cellules[].elements[].libelle': semantique((contexte) => celluleAdresse(contexte, false)),
		'donnees.groupes[].lignes[].cellules[].elements[].nature': semantique((contexte) => celluleAdresse(contexte, false)),
		'donnees.groupes[].lignes[].cellules[].elements[].distance_m': semantique((contexte) => celluleAdresse(contexte, false)),
		...SELECTION('donnees.groupes[].lignes[].cellules[]', cellule),
		'donnees.groupes[].lignes[].cellules[].ecart': ABSENCE,
		'donnees.groupes[].lignes[].cellules[].ecart.unite': METADONNEE,
		'donnees.groupes[].lignes[].cellules[].ecart.valeur': semantique((contexte) => celluleAdresse(contexte, true)),
		'donnees.groupes[].lignes[].cellules[].motif_non_calcul': semantique((contexte) => celluleAdresse(contexte, true)),
		'donnees.groupes[].lignes[].cellules[].verdict': semantique((contexte) => celluleAdresse(contexte, true)),
		'donnees.groupes[].lignes[].cellules[].identique_a_la_reference': semantique((contexte) => celluleAdresse(contexte, true))
	},
	'cmp-dpe': {
		statut: TECHNIQUE,
		'donnees.colonnes[].numero': TECHNIQUE,
		'donnees.colonnes[].disponibilite': METADONNEE,
		// The calculation method of the column: the rule under which its gaps are computed or refused.
		'donnees.colonnes[].methode': METADONNEE,
		'donnees.colonnes[].statut': METADONNEE,
		'donnees.colonnes[].validite': METADONNEE,
		'donnees.colonnes[].adresse_declaree': ABSENCE,
		'donnees.colonnes[].adresse_declaree.texte': semantique(colonneDpe),
		'donnees.numeros_ecartes[]': TECHNIQUE,
		'donnees.groupes[].cle': TECHNIQUE,
		'donnees.groupes[].titre': TECHNIQUE,
		'donnees.groupes[].lignes[].cle': TECHNIQUE,
		'donnees.groupes[].lignes[].identiques': METADONNEE,
		'donnees.groupes[].lignes[].cellules[].numero': TECHNIQUE,
		'donnees.groupes[].lignes[].cellules[].valeur': semantique((contexte) => celluleDpe(contexte, false)),
		'donnees.groupes[].lignes[].cellules[].reference.valeur': REFERENCE,
		'donnees.groupes[].lignes[].cellules[].ecart': ABSENCE,
		'donnees.groupes[].lignes[].cellules[].ecart.unite': METADONNEE,
		'donnees.groupes[].lignes[].cellules[].ecart.valeur': semantique((contexte) => celluleDpe(contexte, true)),
		'donnees.groupes[].lignes[].cellules[].motif_non_calcul': semantique((contexte) => celluleDpe(contexte, true)),
		'donnees.groupes[].lignes[].cellules[].verdict': semantique((contexte) => celluleDpe(contexte, true)),
		'donnees.groupes[].lignes[].cellules[].identique_a_la_reference': semantique((contexte) => celluleDpe(contexte, true))
	}
};

/** The rule of a normalized path: exact rule, then `meta.*`, then the metadata suffixes of a value. */
export function classer(surface: Surface, chemin: string): Regle | null {
	const exacte = REGLES[surface][chemin];
	if (exacte) return exacte;
	// Provenance and hosting of the whole answer (`MetaReponse`): metadata of every value of it.
	if (chemin === 'meta' || chemin.startsWith('meta.')) return METADONNEE;
	for (const suffixe of SUFFIXES_META) {
		if (chemin.endsWith(`.${suffixe}`)) return METADONNEE;
	}
	return null;
}

// --- Known gaps ----------------------------------------------------------------------------------

export interface EcartConnu {
	surface: Surface;
	/** The `field_id` of the values concerned: every leaf of this field on this surface. */
	champ: string;
	manque: Manque;
	/** Why the served answer lacks it, read in the code. */
	cause: string;
}

type Manque = 'definition' | 'source' | 'periode' | 'unite_ou_motif' | 'rattachement' | 'methode';

/**
 * Gaps of the served shape, observed on the synthetic source AND read in the production code (not
 * artifacts of a fixture), one entry per surface, field and missing metadata (every leaf of the
 * field: value, list elements, gap, verdict). Reported by the audit, never counted as conforming.
 *
 * Empty since decision C14 (task 261006-p4s): the 25 values served without period by 04-09
 * (landmark ①, buildings, parcel, condominium, RNB, stops and their copies in the address
 * comparator) carry the millésime or the import day of their source.
 */
export const ECARTS_CONNUS: readonly EcartConnu[] = [];

// --- Audit -----------------------------------------------------------------------------------------

export interface Reponse {
	surface: Surface;
	/** Request path (synthetic or real), never printed by the report. */
	url: string;
	corps: unknown;
}

export interface Ecart {
	surface: Surface;
	chemin: string;
	champ: string | null;
	manque: Manque;
	connu: boolean;
}

export interface RapportSurface {
	surface: Surface;
	reponses: number;
	feuilles: number;
	/** Denominator: semantic leaves counted by path, whatever their metadata. */
	semantiques: number;
	conformes: number;
	techniques: number;
	metadonnees: number;
	absences: number;
	/** Leaves served outside every rule (path and how many). */
	nonClassees: string[];
	/** Leaf paths of the schema outside every rule. */
	schemaNonClasse: string[];
	ecarts: Ecart[];
}

export interface Rapport {
	surfaces: RapportSurface[];
	ecartsConnusNonObserves: EcartConnu[];
	ok: boolean;
	/** Why the audit fails, one line per reason. */
	echecs: string[];
}

function present(valeur: Json): boolean {
	if (valeur === null || valeur === undefined || valeur === '') return false;
	if (Array.isArray(valeur)) return valeur.length > 0;
	if (typeof valeur === 'object') {
		const o = valeur as Objet;
		if ('nom' in o || 'cle' in o) return Boolean(o.nom) || Boolean(o.cle);
		if ('debut' in o || 'fin' in o) return o.debut !== null && o.debut !== undefined && o.fin !== null && o.fin !== undefined;
		return Object.keys(o).length > 0;
	}
	return true;
}

function manques(meta: Meta, valeur: Json, avecMethode: boolean): Manque[] {
	const sortie: Manque[] = [];
	const contrat = meta.champ ? CHAMPS.get(meta.champ) : undefined;
	if (!contrat || !contrat.description.trim()) sortie.push('definition');
	if (!present(meta.source)) sortie.push('source');
	// A period qualifies a value; an absence is read with its reason (« unité ou motif » below).
	if (valeur !== null && !present(meta.periode)) sortie.push('periode');
	// A value is read with the served unit or the unit of its contract; an absence with its reason.
	if (valeur === null ? !present(meta.motif) : !(present(meta.unite ?? null) || Boolean(contrat?.unit))) sortie.push('unite_ou_motif');
	const rattachement = meta.rattachement ?? (meta.rattachementDuContrat ? (contrat?.rattachement ?? null) : null);
	if (typeof rattachement !== 'string' || !RATTACHEMENTS.has(rattachement)) sortie.push('rattachement');
	if (avecMethode && !present(meta.methode ?? null)) sortie.push('methode');
	return sortie;
}

const ECARTS_DE_COMPARAISON = /(ecart\.valeur|motif_non_calcul|verdict|identique_a_la_reference)$/;

/**
 * A semantic path served without a value that is not itself a stated absence: a raw field left
 * null (a sale without built surface, a list element without distance), or the gap of a cell that
 * holds no value (the reference column, an absent value: the reason of the cell is the reading).
 * Counted as an absence, never as a conforming value. A `ValeurLieu` without value stays in the
 * denominator: it must carry its state or reason.
 */
function absenceBrute(racine: Json, feuille: Feuille): boolean {
	let parent: Json = racine;
	for (const cle of feuille.cles.slice(0, -1)) parent = Array.isArray(parent) ? parent[cle as number] : (parent as Objet)[cle as string];
	if (ECARTS_DE_COMPARAISON.test(feuille.chemin)) {
		const i = feuille.cles.indexOf('cellules');
		let cellule: Json = racine;
		for (const cle of feuille.cles.slice(0, i + 2)) cellule = Array.isArray(cellule) ? cellule[cle as number] : (cellule as Objet)[cle as string];
		const c = objet(cellule);
		if (c.etat !== 'value' || feuille.cles[i + 1] === 0) return true;
		// A served value of a compared column without gap, verdict nor reason: its comparison is
		// missing, never an absence (final review FRONT WR-04). Its leaves are read as values.
		if (c.ecart == null && c.verdict == null && c.motif_non_calcul == null) return false;
		return feuille.valeur === null;
	}
	return feuille.valeur === null && !('etat' in objet(parent));
}

/**
 * Audits the serialized answers, surface by surface. *mode* `exiger`: every declared known gap of an
 * audited surface must be observed (synthetic source); `signaler`: they are only reported (real data).
 */
export function auditSem02(reponses: readonly Reponse[], mode: 'exiger' | 'signaler' = 'exiger'): Rapport {
	const parSurface = new Map<Surface, Reponse[]>();
	for (const reponse of reponses) parSurface.set(reponse.surface, [...(parSurface.get(reponse.surface) ?? []), reponse]);
	const surfaces: RapportSurface[] = [];
	const echecs: string[] = [];
	const observes = new Set<string>();
	const cleConnue = (e: { surface: Surface; champ: string | null; manque: Manque }) => `${e.surface}|${e.champ}|${e.manque}`;
	const cleEcart = (e: { surface: Surface; chemin: string; champ: string | null; manque: Manque }) => `${e.surface}|${e.chemin}|${e.champ}|${e.manque}`;
	const connus = new Set(ECARTS_CONNUS.map(cleConnue));

	for (const [surface, lues] of parSurface) {
		const rapport: RapportSurface = {
			surface, reponses: lues.length, feuilles: 0, semantiques: 0, conformes: 0, techniques: 0, metadonnees: 0, absences: 0,
			nonClassees: [], schemaNonClasse: [], ecarts: []
		};
		rapport.schemaNonClasse = feuillesDuSchema(ROUTES[surface]).filter((chemin) => classer(surface, chemin) === null);
		const horsRegle = new Map<string, number>();
		for (const lue of lues) {
			const racine = lue.corps as Json;
			for (const feuille of inventaireFeuilles(racine)) {
				rapport.feuilles += 1;
				const regle = classer(surface, feuille.chemin);
				if (!regle) {
					horsRegle.set(feuille.chemin, (horsRegle.get(feuille.chemin) ?? 0) + 1);
					continue;
				}
				if (regle.classe === 'technique') rapport.techniques += 1;
				else if (regle.classe === 'metadonnee') rapport.metadonnees += 1;
				else if (regle.classe === 'absence' || absenceBrute(racine, feuille)) rapport.absences += 1;
				else if (regle.classe === 'semantique') {
					rapport.semantiques += 1;
					const contexte: Contexte = {
						racine,
						feuille,
						objet(n) {
							const objets: Objet[] = [];
							let noeud: Json = racine;
							for (const cle of feuille.cles.slice(0, -1)) {
								noeud = Array.isArray(noeud) ? noeud[cle as number] : (noeud as Objet)[cle as string];
								if (noeud !== null && typeof noeud === 'object' && !Array.isArray(noeud)) objets.push(noeud);
							}
							return objets[objets.length - n] ?? {};
						},
						lire(chemin) {
							let noeud: Json = racine;
							for (const cle of chemin.split('.')) noeud = objet(noeud)[cle] ?? null;
							return noeud;
						}
					};
					const meta = regle.meta(contexte);
					const manquants = manques(meta, feuille.valeur, ECARTS_DE_COMPARAISON.test(feuille.chemin));
					if (manquants.length === 0) rapport.conformes += 1;
					for (const manque of manquants) {
						const ecart = { surface, chemin: feuille.chemin, champ: meta.champ, manque, connu: false };
						ecart.connu = connus.has(cleConnue(ecart));
						if (ecart.connu) observes.add(cleConnue(ecart));
						if (!rapport.ecarts.some((e) => cleEcart(e) === cleEcart(ecart))) rapport.ecarts.push(ecart);
					}
				}
			}
		}
		rapport.nonClassees = [...horsRegle].map(([chemin, n]) => `${chemin} ×${n}`);
		if (rapport.semantiques === 0) echecs.push(`${surface} : dénominateur nul (aucune feuille sémantique lue)`);
		for (const chemin of rapport.nonClassees) echecs.push(`${surface} : feuille servie non classée ${chemin}`);
		for (const chemin of rapport.schemaNonClasse) echecs.push(`${surface} : feuille du schéma non classée ${chemin}`);
		for (const ecart of rapport.ecarts.filter((e) => !e.connu)) echecs.push(`${surface} : ${ecart.chemin} (${ecart.champ ?? 'sans champ'}) sans ${ecart.manque}`);
		surfaces.push(rapport);
	}

	const audites = new Set(parSurface.keys());
	const ecartsConnusNonObserves = ECARTS_CONNUS.filter((e) => audites.has(e.surface) && !observes.has(cleConnue(e)));
	if (mode === 'exiger') {
		for (const e of ecartsConnusNonObserves) echecs.push(`${e.surface} : écart connu non observé (${e.champ}) sans ${e.manque} — à retirer de ECARTS_CONNUS`);
	}
	return { surfaces, ecartsConnusNonObserves, ok: echecs.length === 0, echecs };
}

/** Totals of a register: semantic leaves conforming / counted, known gaps, over its surfaces. */
export function totalRegistre(rapport: Rapport, registre: keyof typeof REGISTRES) {
	const surfaces = rapport.surfaces.filter((s) => (REGISTRES[registre] as readonly Surface[]).includes(s.surface));
	return {
		conformes: surfaces.reduce((n, s) => n + s.conformes, 0),
		semantiques: surfaces.reduce((n, s) => n + s.semantiques, 0),
		ecartsConnus: surfaces.reduce((n, s) => n + s.ecarts.filter((e) => e.connu).length, 0)
	};
}

/**
 * The report lines, by surface and field path, without any address, key or number: paths, fields
 * and counts only (R06-09/8). *registre* names the source (`synthetique` or `reel`).
 */
export function lignesDuRapport(rapport: Rapport, registre: 'synthetique' | 'reel'): string[] {
	const adresse = totalRegistre(rapport, 'adresse');
	const dpe = totalRegistre(rapport, 'dpe');
	const lignes = [
		`RELEVE sem02 ${registre} adresse=${adresse.conformes}/${adresse.semantiques} dpe=${dpe.conformes}/${dpe.semantiques} ` +
			`ecarts_connus=${adresse.ecartsConnus + dpe.ecartsConnus} ok=${rapport.ok}`
	];
	for (const s of rapport.surfaces) {
		lignes.push(
			`RELEVE sem02 ${registre} surface=${s.surface} reponses=${s.reponses} feuilles=${s.feuilles} semantiques=${s.conformes}/${s.semantiques} ` +
				`techniques=${s.techniques} metadonnees=${s.metadonnees} absences=${s.absences} non_classees=${s.nonClassees.length} schema_non_classe=${s.schemaNonClasse.length}`
		);
		for (const e of s.ecarts) lignes.push(`RELEVE sem02 ${registre} ecart surface=${s.surface} chemin=${e.chemin} champ=${e.champ} manque=${e.manque} connu=${e.connu}`);
	}
	return lignes;
}

// --- Collection of every public answer of a set of keys ----------------------------------------

export interface Cles {
	banId: string;
	/** Query of the similar sales (`type`, `surface`, `tolerance`, `rayon`, `periode`). */
	criteres: Record<string, string>;
	numero: string;
	idsComparaison: string[];
	/** Designations `ban_id:numero` of the address comparison, if any. */
	designes?: string;
	numerosComparaison: string[];
}

/** Pause between two reads of the collector: 0 on the synthetic source, the key budget on real data. */
let pauseEntreLectures = 0;

/** One GET, 200 required; *entetes* carries the session key (the public one on the synthetic source). */
async function lire(request: APIRequestContext, base: string, chemin: string, entetes: Record<string, string>): Promise<unknown> {
	if (pauseEntreLectures > 0) await new Promise((fin) => setTimeout(fin, pauseEntreLectures));
	const reponse = await request.get(`${base}${chemin}`, { headers: entetes });
	expect(reponse.status(), `lecture d'audit ${chemin.split('?')[0]}`).toBe(200);
	return reponse.json();
}

/** Every page of a list, following its generation token (`g` for documents, `vg` for sales). */
async function toutesLesPages(
	request: APIRequestContext,
	base: string,
	chemin: string,
	query: Record<string, string>,
	jeton: 'g' | 'vg',
	surface: Surface,
	entetes: Record<string, string>
): Promise<Reponse[]> {
	const sortie: Reponse[] = [];
	let generation: string | null = null;
	for (let page = 1; ; page += 1) {
		const sp = new URLSearchParams({ ...query, page: String(page), ...(generation ? { [jeton]: generation } : {}) });
		const url = `${chemin}?${sp.toString()}`;
		const corps = (await lire(request, base, url, entetes)) as { donnees: { pages?: number; generation?: string } };
		sortie.push({ surface, url, corps });
		generation = corps.donnees.generation ?? null;
		if (page >= (corps.donnees.pages ?? 1)) break;
	}
	return sortie;
}

/**
 * Every public answer of the keys: the address page, every page of its documents, of its sales and
 * of its similar sales, the document, both comparators. Reads are sequential and *pauseMs* apart
 * (budget of the real key: 30 requests a minute; 2 100 ms keeps a margin).
 */
export async function collecterReponses(
	request: APIRequestContext,
	base: string,
	cles: Cles,
	entetes: Record<string, string> = {},
	pauseMs = 0
): Promise<Reponse[]> {
	pauseEntreLectures = pauseMs;
	const id = encodeURIComponent(cles.banId);
	const reponses: Reponse[] = [{ surface: 'fiche', url: `/v1/adresse/${id}`, corps: await lire(request, base, `/v1/adresse/${id}`, entetes) }];
	reponses.push(...(await toutesLesPages(request, base, `/v1/adresse/${id}/dpe`, {}, 'g', 'documents', entetes)));
	reponses.push(...(await toutesLesPages(request, base, `/v1/adresse/${id}/ventes`, {}, 'vg', 'ventes', entetes)));
	// C3 (04.5-08): both modes read by number and suffix, the rule of the page and of the comparator
	// (C40), which the definition of `adresse.dvf.ventes_adresse.nombre` describes; never the API default.
	reponses.push(...(await toutesLesPages(request, base, `/v1/adresse/${id}/ventes-declarees`, { regle: 'numero_et_suffixe' }, 'vg', 'declarees', entetes)));
	// C4 (04.5-07): the declared sales as the page reads them, with their VEFA.
	reponses.push(
		...(await toutesLesPages(request, base, `/v1/adresse/${id}/ventes-declarees`, { vefa: 'incluses', regle: 'numero_et_suffixe' }, 'vg', 'declarees-vefa', entetes))
	);
	reponses.push(...(await toutesLesPages(request, base, `/v1/adresse/${id}/ventes-ressemblantes`, cles.criteres, 'vg', 'ressemblantes', entetes)));
	const numero = `/v1/dpe/${encodeURIComponent(cles.numero)}`;
	reponses.push({ surface: 'document', url: numero, corps: await lire(request, base, numero, entetes) });
	const adresses = `/v1/adresses/comparaison?${new URLSearchParams({ ids: cles.idsComparaison.join(','), ...(cles.designes ? { designes: cles.designes } : {}) })}`;
	reponses.push({ surface: 'cmp-adresses', url: adresses, corps: await lire(request, base, adresses, entetes) });
	const documents = `/v1/dpe/comparaison?${new URLSearchParams({ numeros: cles.numerosComparaison.join(',') })}`;
	reponses.push({ surface: 'cmp-dpe', url: documents, corps: await lire(request, base, documents, entetes) });
	return reponses;
}
