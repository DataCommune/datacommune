import { describe, it, expect } from 'vitest';
import {
	COVERED_FIELD_IDS,
	COMMUNE_META_SOURCE_NOMS,
	COMMUNE_META_SOURCE_ORDER,
	EXCLUDED_FIELD_IDS,
	EXPLICATIONS,
	datasetDuField,
	definitionPublique,
	getExplication,
	hasExplication,
	millesimesFromCommune,
	provenanceParDataset,
	sourcesCommuneParDataset,
	type CoveredFieldId
} from './explications';
import { getFieldContract, listFieldIds } from './formatters';
import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import type { DatasetProvenance } from '$lib/api/resolve';

// D-16 drift guard. The `satisfies Record<CoveredFieldId, ExplicationEntry>` in explications.ts
// already makes a MISSING entry a compile error; what the type system cannot see is the registry
// itself. These tests close that half: a field_id added to fiche-fields.v1.json without a decision
// (cover it / exclude it) turns the suite red instead of rendering a silently empty ℹ️.

describe('exhaustivité D-15/D-16 — COVERED ∪ EXCLUDED == le registre', () => {
	it('aucun field_id du registre ne reste sans décision (couvert ou exclu)', () => {
		const registre = new Set(listFieldIds());
		const decides = new Set<string>([...COVERED_FIELD_IDS, ...EXCLUDED_FIELD_IDS]);
		const manquants = [...registre].filter((id) => !decides.has(id));
		expect(manquants).toEqual([]);
	});

	it('aucun orphelin : chaque id couvert ou exclu existe bien dans le registre', () => {
		const registre = new Set(listFieldIds());
		const orphelins = [...COVERED_FIELD_IDS, ...EXCLUDED_FIELD_IDS].filter(
			(id) => !registre.has(id)
		);
		expect(orphelins).toEqual([]);
	});

	it('aucun doublon et aucun recouvrement entre les deux listes', () => {
		expect(new Set(COVERED_FIELD_IDS).size).toBe(COVERED_FIELD_IDS.length);
		expect(new Set(EXCLUDED_FIELD_IDS).size).toBe(EXCLUDED_FIELD_IDS.length);
		const couverts = new Set<string>(COVERED_FIELD_IDS);
		expect(EXCLUDED_FIELD_IDS.filter((id) => couverts.has(id))).toEqual([]);
	});

	it('la partition couvre exactement le registre (cardinalité)', () => {
		expect(COVERED_FIELD_IDS.length + EXCLUDED_FIELD_IDS.length).toBe(listFieldIds().length);
	});

	it('le dictionnaire a une entrée pour chaque id couvert, et rien d’autre', () => {
		expect(Object.keys(EXPLICATIONS).sort()).toEqual([...COVERED_FIELD_IDS].sort());
	});
});

describe('accès fail-closed (patron getFieldContract, T-43-07)', () => {
	it('getExplication throw en nommant le field_id inconnu', () => {
		expect(() => getExplication('does.not.exist' as CoveredFieldId)).toThrow(
			/does\.not\.exist/
		);
	});

	it('getExplication throw aussi sur un id EXCLU (une exclusion n’est pas un trou à combler)', () => {
		expect(() => getExplication('commune.code_insee' as CoveredFieldId)).toThrow(
			/commune\.code_insee/
		);
	});

	it('getExplication ne confond pas une clé héritée d’Object.prototype avec une entrée', () => {
		expect(() => getExplication('constructor' as CoveredFieldId)).toThrow(/constructor/);
	});

	it('hasExplication distingue couvert, exclu et absent', () => {
		expect(hasExplication('revenus.revenu_median')).toBe(true);
		expect(hasExplication('commune.code_insee')).toBe(false);
		expect(hasExplication('demographie.annee')).toBe(false);
		expect(hasExplication('does.not.exist')).toBe(false);
		expect(hasExplication(null)).toBe(false);
		expect(hasExplication(undefined)).toBe(false);
	});

	it('getExplication rend bien la définition attendue pour un id couvert', () => {
		expect(getExplication('revenus.revenu_median').definition).toContain(
			'Revenu déclaré médian'
		);
	});
});

describe('qualité des définitions (D-12/D-13)', () => {
	const entrees = Object.entries(EXPLICATIONS) as [string, { definition: string }][];

	it('chaque définition est non vide et rédigée (pas un mot isolé)', () => {
		for (const [fieldId, entree] of entrees) {
			expect(entree.definition.trim().length, fieldId).toBeGreaterThan(40);
		}
	});

	// D-11 — aucun jeton interne ni code d'état technique ne doit atteindre une surface publique.
	it('aucune définition ne laisse fuir un jeton interne ou un code d’état', () => {
		const interdits = [/\bL[012]\b/, /out_of_coverage/, /not_reported/, /temporary_error/];
		for (const [fieldId, entree] of entrees) {
			for (const motif of interdits) {
				expect(motif.test(entree.definition), `${fieldId} / ${motif}`).toBe(false);
			}
		}
	});

	// D-13 — un millésime figé ici deviendrait faux au premier refresh ETL : il se lit live.
	it('aucune définition ne fige un millésime', () => {
		for (const [fieldId, entree] of entrees) {
			expect(/millésime\s*:?\s*\d{4}/i.test(entree.definition), fieldId).toBe(false);
		}
	});

	it('le cas déclencheur UI-07 nomme le piège du revenu médian (Pitfall 3)', () => {
		const definition = getExplication('revenus.revenu_median').definition;
		expect(definition).toContain('unité de consommation');
		expect(definition).toContain('avant redistribution');
		expect(definition).toContain('ne mesure pas le niveau de vie');
	});
});

describe('audit septembre 2026 — définitions compatibles avec la source', () => {
	it('décrit une consommation annuelle totale sans la présenter par mètre carré', () => {
		const copy = getExplication('bien.dpe.conso_5_usages_ep').definition;
		expect(copy).toContain('annuelle totale');
		expect(copy).toContain('kWhEP/an');
		expect(copy).not.toMatch(/par mètre carré|supérieur à l'énergie facturée/);
	});

	it('ne transforme pas le libellé séisme en niveau notable de sismicité', () => {
		const copy = getExplication('bien.risques.sismique').definition;
		expect(copy).toContain('liste des risques recensés');
		expect(copy).toContain('commune');
		expect(copy).toContain('ne précise pas le niveau de sismicité');
		expect(copy).not.toContain('sismicité notable');
	});

	it('décrit les argiles et le PLU au point consulté, sans couverture de toute la parcelle', () => {
		for (const fieldId of ['bien.risques.expo_argiles', 'bien.urbanisme.zonage_libelle'] as const) {
			expect(getExplication(fieldId).definition).toContain('point géographique consulté');
		}
	});
});

// M-15 (03-17 F66-F69): the « Définition » of a source panel is this public text, so it carries no
// decision number, no SQL, no `null`, no snake_case key, no verdict, and never calls a DPE a dwelling.
describe('définitions publiques — garde M-15', () => {
	const INTERDITS: ReadonlyArray<[string, RegExp]> = [
		['numéro de décision', /\bD-\d/],
		['null', /\bNULL\b|\bnull\b/],
		['fonction SQL', /\bST_|percentile_/],
		// Digits included (plan 04.5-12, M1): `conso_5_usages_par_m2_ep` is a key, never plain French.
		['clé snake_case', /\b[a-z0-9]+_[a-z0-9_]+\b/],
		['verdict « passoire »', /passoire/i],
		['DPE compté comme logement', /logements diagnostiqu/i],
		// « servi » is the API jargon of the state glossary; « service » is plain French.
		['jargon « servi »', /\bservie?s?\b/i]
	];

	it.each(COVERED_FIELD_IDS.filter((id) => !id.startsWith('bien.')))('%s', (id) => {
		const texte = getExplication(id).definition;
		for (const [nom, motif] of INTERDITS) expect(texte, nom).not.toMatch(motif);
	});

	it('a garde détecte une définition fautive (contre-épreuve)', () => {
		const fautive = 'Part des diagnostics (D-01).';
		expect(INTERDITS.some(([, motif]) => motif.test(fautive))).toBe(true);
	});

	// Plan 04.5-12 (counter-review M1): the definitions proposed by report 4.1-12 name the ADEME columns
	// in parentheses; a column name with digits must be caught too, never read as plain French.
	it.each(['conso_5_usages_par_m2_ep', 'emission_ges_5_usages_par_m2'])('a garde détecte le nom de colonne réel %s (contre-épreuve)', (colonne) => {
		const fautive = `Valeur publiée par l'ADEME pour le document DPE (colonne ${colonne}), en kWhEP/m²/an.`;
		expect(INTERDITS.filter(([, motif]) => motif.test(fautive)).map(([nom]) => nom)).toEqual(['clé snake_case']);
	});

	it('« Le plus récent » du comparateur d’adresses : définition décidée (04-DECISIONS-TEXTES ligne 53), plus exclue', () => {
		expect(definitionPublique('adresse.dpe.plus_recent')).toBe(
			"Diagnostic de performance énergétique (DPE) dont la date d'établissement est la plus récente parmi ceux déclarés à cette adresse. Il n'est rattaché à aucun logement."
		);
		expect((EXCLUDED_FIELD_IDS as readonly string[]).includes('adresse.dpe.plus_recent')).toBe(false);
	});

	it('definitionPublique lit EXPLICATIONS, jamais le contrat ; null hors couverture', () => {
		expect(definitionPublique('dpe.dpe_passoires_pct')).toBe(getExplication('dpe.dpe_passoires_pct').definition);
		expect(definitionPublique('commune.risques.risques_naturels')).toBeNull();
		expect(definitionPublique('champ.inconnu')).toBeNull();
		expect(definitionPublique(null)).toBeNull();
	});
});

// Plan 04.5-12 (C1, report 4.1-12, D45-04): the definitions of the report, taken as proposed but for
// the ADEME column name in parentheses (counter-review M1); to be read again as a block (R10).
describe('valeurs par m² d’un document DPE : couvertes, définition du report sans nom de colonne', () => {
	it('dpe.document.conso_ep_m2', () => {
		expect((EXCLUDED_FIELD_IDS as readonly string[]).includes('dpe.document.conso_ep_m2')).toBe(false);
		expect(definitionPublique('dpe.document.conso_ep_m2')).toBe(
			"Consommation d'énergie primaire des cinq usages par mètre carré et par an, publiée par l'ADEME pour le document DPE, en kWhEP/m²/an : c'est la valeur qu'on rapproche de la classe énergie. Elle n'est jamais recalculée à partir du total et de la surface ; absente de la source, elle est non renseignée."
		);
	});

	it('dpe.document.emissions_ges_m2', () => {
		expect((EXCLUDED_FIELD_IDS as readonly string[]).includes('dpe.document.emissions_ges_m2')).toBe(false);
		expect(definitionPublique('dpe.document.emissions_ges_m2')).toBe(
			"Émissions de gaz à effet de serre des cinq usages par mètre carré et par an, publiées par l'ADEME pour le document DPE, en kg CO₂ éq/m²/an : c'est la valeur qu'on rapproche de la classe climat. Elles ne sont jamais recalculées à partir du total et de la surface ; absentes de la source, elles sont non renseignées."
		);
	});
});

describe('datasetDuField', () => {
	it('retire le préfixe bien. puis prend le premier segment', () => {
		expect(datasetDuField('revenus.revenu_median')).toBe('revenus');
		expect(datasetDuField('bien.dpe.etiquette_dpe')).toBe('dpe');
		expect(datasetDuField('bien.cadastre.contenance')).toBe('cadastre');
		expect(datasetDuField('zonage_abc.zone')).toBe('zonage_abc');
		expect(datasetDuField('foncier_pm.surface_pm_ha')).toBe('foncier_pm');
	});

	it('chaque id couvert produit un dataset non vide', () => {
		for (const fieldId of COVERED_FIELD_IDS) {
			expect(datasetDuField(fieldId).length, fieldId).toBeGreaterThan(0);
		}
	});
});

describe('millesimesFromCommune (D-13 — millésime live only)', () => {
	// Partial fixture: the helper reads 5 `annee_*` columns; casting keeps the test honest about
	// what it actually exercises instead of fabricating 30 unrelated nulls.
	const commune = {
		code_insee: '85047',
		annee_demographie: 2021,
		annee_revenus: 2021,
		annee_vacance: null,
		annee_fiscalite: 2024,
		annee_loyers: null
	} as unknown as CommuneSynthese;

	it('convertit les années présentes en chaînes et préserve les absences', () => {
		expect(millesimesFromCommune(commune)).toEqual({
			demographie: '2021',
			revenus: '2021',
			vacance: null,
			fiscalite: '2024',
			loyers: null,
			equipements: null
		});
	});

	it('n’invente aucun millésime pour les 7 datasets commune sans colonne annee_* (décision de périmètre)', () => {
		const millesimes = millesimesFromCommune(commune);
		for (const dataset of [
			'dvf',
			'dpe',
			'zonage_abc',
			'monuments',
			'scolaire',
			'foncier_pm',
			'connectivite'
		]) {
			expect(millesimes[dataset], dataset).toBeUndefined();
		}
	});
});

describe('provenanceParDataset (/v1/resolve — Phase 44 déjà sur le fil)', () => {
	const provenance = (dataset: string, nom: string): DatasetProvenance => ({
		nom,
		licence: 'Licence ouverte',
		url: 'https://example.gouv.fr',
		producteur: 'Producteur',
		dataset,
		periode: { statut: 'connue', label: '2024', debut: null, fin: null },
		derniere_importation_reussie: null,
		couverture: { type: 'departements', departements: ['44', '85'] },
		cadence: {
			statut: 'non_publiee_ou_irreguliere',
			label: null,
			evidence_url: null,
			intervalle_jours: null
		},
		etat: 'actuel'
	});

	it('indexe la liste par nom de dataset', () => {
		const parDataset = provenanceParDataset([
			provenance('dpe', 'DPE ADEME'),
			provenance('dvf', 'Demandes de valeurs foncières')
		]);
		expect(Object.keys(parDataset).sort()).toEqual(['dpe', 'dvf']);
		expect(parDataset.dvf.nom).toBe('Demandes de valeurs foncières');
		expect(parDataset.dpe.periode.label).toBe('2024');
	});

	it('tolère null, undefined et liste vide', () => {
		expect(provenanceParDataset(null)).toEqual({});
		expect(provenanceParDataset(undefined)).toEqual({});
		expect(provenanceParDataset([])).toEqual({});
	});
});

describe('sourcesCommuneParDataset (/v1/commune — attribution par NOM, D-21 #4)', () => {
	// Phase 50: the fixture now carries the REAL publication names served by the API
	// (`DATASET_CATALOG[key].nom`). A fixture with invented names could not distinguish a correct
	// attribution from a shifted one — that is exactly how the héritage #4 defect survived.
	const treize: SourceAttribution[] = COMMUNE_META_SOURCE_ORDER.map((dataset) => ({
		nom: COMMUNE_META_SOURCE_NOMS[dataset],
		licence: 'Licence ouverte',
		url: 'https://example.gouv.fr',
		producteur: 'Producteur'
	}));

	it('le miroir compte bien les 13 datasets de COMMUNE_SOURCE_KEYS', () => {
		expect(COMMUNE_META_SOURCE_ORDER.length).toBe(13);
	});

	it('le miroir des NOMS couvre exactement les 13 datasets de l’ordre', () => {
		expect(Object.keys(COMMUNE_META_SOURCE_NOMS).sort()).toEqual(
			[...COMMUNE_META_SOURCE_ORDER].sort()
		);
		expect(Object.values(COMMUNE_META_SOURCE_NOMS).every((n) => n.length > 0)).toBe(true);
		// Publication names are unique — otherwise a name could not identify a dataset.
		expect(new Set(Object.values(COMMUNE_META_SOURCE_NOMS)).size).toBe(13);
	});

	it('attribue les 13 sources, chacune au dataset dont elle porte le nom', () => {
		const sources = sourcesCommuneParDataset(treize);
		expect(Object.keys(sources).length).toBe(13);
		expect(sources.revenus.nom).toBe('Filosofi (revenus et pauvreté)');
		expect(sources.demographie.nom).toBe('Recensement de la population');
		expect(sources.connectivite.nom).toBe('Ma Connexion Internet (Arcep)');
		// Every key is attributed the entry that NAMES it, never a neighbour's.
		for (const dataset of COMMUNE_META_SOURCE_ORDER) {
			expect(sources[dataset].nom).toBe(COMMUNE_META_SOURCE_NOMS[dataset]);
		}
	});

	it('FAIL-CLOSED : DEUX entrées permutées rendent {} — jamais une attribution décalée', () => {
		const permute = [...treize];
		[permute[0], permute[1]] = [permute[1], permute[0]];
		// Pre-fix behaviour was: 13 keys, `sources.demographie.nom` = the Filosofi name — a lie the
		// reader cannot detect. Post-fix: honest absence.
		expect(sourcesCommuneParDataset(permute)).toEqual({});
		// Also true further down the list (not just the head).
		const permuteFin = [...treize];
		[permuteFin[11], permuteFin[12]] = [permuteFin[12], permuteFin[11]];
		expect(sourcesCommuneParDataset(permuteFin)).toEqual({});
	});

	it('FAIL-CLOSED : une longueur inattendue rend {} plutôt qu’une attribution décalée', () => {
		expect(sourcesCommuneParDataset(treize.slice(0, 3))).toEqual({});
		expect(sourcesCommuneParDataset([...treize, treize[0]])).toEqual({});
	});

	it('FAIL-CLOSED : null / undefined / liste vide rendent {}', () => {
		expect(sourcesCommuneParDataset(null)).toEqual({});
		expect(sourcesCommuneParDataset(undefined)).toEqual({});
		expect(sourcesCommuneParDataset([])).toEqual({});
	});

	it('FAIL-CLOSED : 13 entrées dont un nom inconnu du miroir rendent {}', () => {
		const inconnu = [...treize];
		inconnu[4] = { ...inconnu[4], nom: 'DGFiP — Taux de fiscalité locale' };
		expect(sourcesCommuneParDataset(inconnu)).toEqual({});
		// A renamed publication (backend catalog drift) is also caught.
		const renomme = [...treize];
		renomme[0] = { ...renomme[0], nom: 'Recensement de la Population' }; // casse différente (ancienne graphie)
		expect(sourcesCommuneParDataset(renomme)).toEqual({});
		// `nom` is optional on the wire: a missing one is not attributed to anybody.
		const sansNom = [...treize];
		sansNom[7] = { licence: 'Licence ouverte' };
		expect(sourcesCommuneParDataset(sansNom)).toEqual({});
	});
});

// C55 (fix 7, IN-07): the four definitions of decision n° 63 are pinned word for word. Expected
// text: row 76 of 04-DECISIONS-TEXTES.md (validated by the product owner, C54), never read from
// explications.ts. Non-breaking spaces (U+00A0, U+202F) compare as plain spaces: the test pins the
// words and punctuation; the typographic space before « : » or « ; » stays a rendering choice.
const DEFINITIONS_N63_LIGNE_76: Record<string, string> = {
	'dpe.document.date_etablissement':
		"Date à laquelle le diagnostiqueur a établi ce diagnostic de performance énergétique (DPE), telle que publiée par l'ADEME. Elle fixe la méthode de calcul du document (avant ou depuis juillet 2021).",
	'dpe.document.date_fin_validite':
		"Date de fin de validité déclarée par ce diagnostic de performance énergétique (DPE), telle que publiée par l'ADEME. Elle n'est jamais calculée à partir de la date d'établissement ; un document dont la date est dépassée reste listé avec sa date.",
	'dpe.document.methode':
		"Méthode de calcul de ce diagnostic de performance énergétique (DPE), lue sur sa date d'établissement : ancienne méthode avant juillet 2021, méthode actuelle depuis. Les classes, consommations et émissions de deux DPE de méthodes différentes ne se comparent pas.",
	'dpe.document.adresse_ban':
		"Adresse de la Base Adresse Nationale (BAN) dont l'identifiant est déclaré par l'ADEME pour ce diagnostic de performance énergétique (DPE). DataCommune reprend cette déclaration sans la vérifier ; elle ne désigne aucun logement."
};

const espacesSimples = (texte: string | null) => (texte ?? '').replace(/[\u00a0\u202f]/g, ' ');

describe('décision n° 63 — définitions validées mot pour mot (ligne 76)', () => {
	it.each(Object.entries(DEFINITIONS_N63_LIGNE_76))('%s', (fieldId, attendue) => {
		expect(espacesSimples(definitionPublique(fieldId))).toBe(espacesSimples(attendue));
	});
});

// 04.1-13 (D-127), plan 04.5-05 C2: the group of the documents attached by their exact raw address is
// displayed, so its field leaves the « served but not displayed yet » exclusions with an entry whose
// definition is the description of the contract, word for word.
describe('adresse.dpe.documents_adresse_brute — couvert, définition du contrat (04.5-05)', () => {
	const ID = 'adresse.dpe.documents_adresse_brute';

	it('covered, no longer excluded', () => {
		expect((COVERED_FIELD_IDS as readonly string[]).includes(ID)).toBe(true);
		expect((EXCLUDED_FIELD_IDS as readonly string[]).includes(ID)).toBe(false);
	});

	it('its public definition is the description of the contract', () => {
		const description = getFieldContract(ID).description;
		expect(description).toBeTruthy();
		expect(definitionPublique(ID)).toBe(description);
	});
});

// C4 (plan 04.5-07, report 4.1-15; D-12): the VEFA declared at the address are listed and marked, never
// counted in `adresse.dvf.ventes_adresse.nombre`. The addition of the report is applied as written
// (read in a block by Ianis, R10); the contract says it too, in its three copies.
describe('adresse.dvf.ventes_adresse.nombre — VEFA listées, pas comptées (04.5-07, C4)', () => {
	it('la définition publique porte l’ajout du report 4.1-15, mot pour mot', () => {
		expect(getExplication('adresse.dvf.ventes_adresse.nombre').definition).toContain(
			"Les ventes en l'état futur d'achèvement (VEFA) déclarées à l'adresse sont listées et marquées, mais ne sont pas comptées."
		);
	});

	it('la description du contrat dit « les VEFA ne sont pas comptées »', () => {
		expect(getFieldContract('adresse.dvf.ventes_adresse.nombre').description).toContain('les VEFA ne sont pas comptées');
	});
});

// C3 (plan 04.5-08, report 4.1-14): the sale is attached by the declared number and suffix. The sentence
// of the report replaces the one it withdraws, at its place (read in a block by Ianis, R10); the
// contract describes the rule served to the pages and to the comparator.
describe('adresse.dvf.ventes_adresse.nombre — numéro et suffixe (04.5-08, C3)', () => {
	const sansEspacesFines = (texte: string) => texte.replace(/[  ]/g, ' ');
	const definition = () => sansEspacesFines(getExplication('adresse.dvf.ventes_adresse.nombre').definition);

	it('la phrase du report 4.1-14 remplace la phrase retirée, à sa place, mot pour mot', () => {
		const ajoutee =
			'La vente est rattachée par le numéro et le suffixe déclarés : une vente déclarée au « 2 » sans suffixe ne figure que sur la page du « 2 », une vente déclarée au « 2 B » sur celle du « 2 bis ». ' +
			"Quand un suffixe ne correspond à aucune adresse, ou à plusieurs, la vente n'est montrée sur aucune.";
		expect(definition()).toContain(ajoutee);
		expect(definition()).not.toContain('Les données DVF importées ne gardent pas le suffixe (bis, ter) : une vente déclarée au « 2 bis » peut apparaître sur la page du « 2 ».');
		// At its place: after the sentence on the BAN, before the VEFA addition of 04.5-07.
		expect(definition()).toContain(`ne retrouve aucune adresse de la Base Adresse Nationale. ${ajoutee} Les ventes en l'état futur d'achèvement (VEFA)`);
	});

	it('la description du contrat décrit la règle numéro et suffixe servie aux pages et au comparateur', () => {
		const description = getFieldContract('adresse.dvf.ventes_adresse.nombre').description;
		expect(description).toContain('regle=numero_et_suffixe');
		expect(description).not.toContain('numéro sans suffixe');
		expect(description).not.toContain('une vente déclarée au bis peut figurer sur l’adresse sans suffixe');
		expect(description).not.toContain("une vente déclarée au bis peut figurer sur l'adresse sans suffixe");
	});
});
