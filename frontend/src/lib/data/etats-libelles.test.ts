// Copy lock of the state glossary (UI-SPEC « Les quatre états servis », glossary table) and
// non-regression lock of the historical wording still used by the screens not yet redesigned.
// Every label is asserted as an EXACT string: a rewording must show up as a red test, never as a
// silent copy change on a public surface.

import { describe, it, expect } from 'vitest';
import {
	EXPLICATIONS_ETATS,
	LIBELLE_HORS_COUVERTURE,
	LIBELLES_ECARTS_HISTORIQUES,
	LIBELLE_NON_AFFICHABLE,
	LIBELLES_ETATS,
	LIBELLES_MOTIFS,
	LIBELLES_MOTIFS_ABSENCE,
	MOTIFS_HORS_COUVERTURE,
	PHRASES_ETATS,
	PHRASES_MOTIFS_NOT_QUALIFIED,
	libelleEtat,
	libelleMotifAbsence,
	phraseEtat
} from './etats-libelles';
import { formatField, SemanticContractError, type SemanticState } from './formatters';

describe('LIBELLES_ETATS — the UI-SPEC glossary, exact', () => {
	it.each([
		['not_reported', 'Non renseigné'],
		['insufficient_sample', 'Effectif insuffisant'],
		['suppressed', 'Secret statistique'],
		['not_available', 'Non publiée'],
		['zero_denominator', 'Sans base de calcul'],
		['not_applicable', 'Sans objet'],
		['not_qualified', 'Non qualifié'],
		['out_of_coverage', 'Hors couverture'],
		['temporary_error', 'Indisponible']
	])('%s → « %s »', (cle, libelle) => {
		expect(LIBELLES_ETATS[cle]).toBe(libelle);
	});

	it('no longer words out of coverage nor outage as « non disponible »', () => {
		expect(LIBELLES_ETATS.out_of_coverage).not.toMatch(/non disponible/i);
		expect(LIBELLES_ETATS.temporary_error).not.toMatch(/non disponible/i);
		expect(LIBELLES_ETATS.out_of_coverage).not.toBe(LIBELLES_ETATS.temporary_error);
	});

	it('never uses a technical key as a label', () => {
		for (const [cle, libelle] of Object.entries(LIBELLES_ETATS)) {
			expect(libelle, cle).not.toBe(cle);
			expect(libelle, cle).not.toMatch(/^[a-z]+(?:_[a-z]+)+$/);
		}
	});
});

describe('PHRASES_ETATS — the UI-SPEC sentences, exact', () => {
	it.each([
		['not_reported', 'La source ne fournit pas cette valeur pour cette commune.'],
		['insufficient_sample', "Trop peu d'observations ({n}) pour publier une valeur."],
		['suppressed', 'Le producteur ne publie pas cette valeur pour protéger les personnes.'],
		['not_available', 'La source ne publie pas cette valeur pour ce territoire.'],
		['zero_denominator', 'Aucun élément ne permet de calculer ce taux.'],
		['not_applicable', "Cette information ne s'applique pas ici."],
		['not_qualified', 'Ce jeu est en quarantaine : ses valeurs ne sont pas présentées comme fiables.'],
		['temporary_error', "Le service n'a pas répondu. Réessayez dans un instant."]
	])('%s → exact sentence', (cle, phrase) => {
		expect(PHRASES_ETATS[cle]).toBe(phrase);
	});

	it('every labelled absence has its sentence, except out of coverage which is worded by its reason', () => {
		for (const cle of Object.keys(LIBELLES_ETATS)) {
			if (cle === 'out_of_coverage') continue;
			expect(PHRASES_ETATS[cle], cle).toBeTruthy();
		}
	});
});

describe('LIBELLES_MOTIFS — the three reasons of out of coverage (D-24)', () => {
	it.each([
		['dataset_non_heberge', 'Hors couverture · jeu non installé', 'Ce site ne propose pas ce jeu de données.'],
		['territoire_hors_perimetre', 'Hors couverture · département non hébergé', 'Ce site ne couvre pas ce département.'],
		['source_sans_couverture', 'Hors couverture · non couvert par la source', 'La source ne couvre pas ce territoire.']
	] as const)('%s', (motif, libelle, phrase) => {
		expect(LIBELLES_MOTIFS[motif]).toEqual({ libelle, phrase });
	});

	it('is the table the search results already read (one wording per reason)', () => {
		expect(MOTIFS_HORS_COUVERTURE).toBe(LIBELLES_MOTIFS);
		expect(LIBELLE_HORS_COUVERTURE).toBe('Hors couverture');
	});
});

describe('libelleEtat / phraseEtat — the wording of one served absence', () => {
	it('words out of coverage by its reason, or plainly when the reason is unknown', () => {
		expect(libelleEtat('out_of_coverage', 'territoire_hors_perimetre')).toBe('Hors couverture · département non hébergé');
		expect(phraseEtat('out_of_coverage', 'territoire_hors_perimetre', null)).toBe('Ce site ne couvre pas ce département.');
		expect(libelleEtat('out_of_coverage', 'Bloc DPE hors couverture.')).toBe('Hors couverture');
		expect(phraseEtat('out_of_coverage', 'Bloc DPE hors couverture.', null)).toBeNull();
	});

	it('writes the served size in the insufficient sample sentence, and drops it when not served', () => {
		expect(phraseEtat('insufficient_sample', '3 ventes, seuil : 5.', 3)).toBe("Trop peu d'observations (3) pour publier une valeur.");
		expect(phraseEtat('insufficient_sample', 'Moins de 5 ventes.', null)).toBe("Trop peu d'observations pour publier une valeur.");
		expect(phraseEtat('insufficient_sample', '', 1250)).toBe("Trop peu d'observations (1\u202f250) pour publier une valeur.");
	});

	it('words every other state from the glossary', () => {
		expect(libelleEtat('not_available', 'x')).toBe('Non publiée');
		expect(phraseEtat('zero_denominator', 'x', null)).toBe('Aucun élément ne permet de calculer ce taux.');
	});

	it('words a quarantine by its reason, never by its whole dataset (03-17 F55)', () => {
		expect(phraseEtat('not_qualified', 'indicator_policy_unqualified', null)).toBe(
			"Cet indicateur n'est pas encore publié : sa règle de calcul n'a pas été validée."
		);
		expect(phraseEtat('not_qualified', 'territorial_attribution_unqualified', null)).toBe(
			"Cet indicateur n'est pas publié : son rattachement à la commune n'est pas vérifié."
		);
		expect(phraseEtat('not_qualified', 'indicator_policy_unqualified', null)).not.toContain('quarantaine');
		// Any other reason keeps the generic sentence (also read by /datasets).
		expect(phraseEtat('not_qualified', 'autre_motif', null)).toBe(PHRASES_ETATS.not_qualified);
		expect(Object.keys(PHRASES_MOTIFS_NOT_QUALIFIED)).toEqual([
			'indicator_policy_unqualified',
			'territorial_attribution_unqualified',
			'commune_hors_build_qualifie'
		]);
	});

	// 04.1-09 report, UI-032: a commune outside the qualified build is not a quarantined dataset.
	it('words a commune outside the qualified build by the commune, never as a quarantine', () => {
		const phrase = phraseEtat('not_qualified', 'commune_hors_build_qualifie', null);
		expect(phrase).toBe('Ces indicateurs ne sont pas encore qualifiés pour cette commune.');
		expect(phrase).not.toContain('quarantaine');
		expect(libelleEtat('not_qualified', 'commune_hors_build_qualifie')).toBe('Non qualifié');
	});

	// 04-DECISIONS-TEXTES rows 44, 45, 49 to 51 and 56 (in place of row 52): the sentence of a served
	// reason of a value not published; the label stays « Non publiée ».
	it.each([
		['adresse_non_verifiable', "L'adresse publiée par l'Arcep ne correspond pas exactement à celle de la BAN ; l'éligibilité n'est pas affichée."],
		['aucun_document_publie', "Aucun document d'urbanisme n'est publié sur le GPU pour cette commune."],
		['batiment_du_point_non_unique', "Plusieurs bâtiments couvrent le point de l'adresse ; aucun n'est retenu."],
		// Row 75 (C52, B3), validated as is (row 77, C54).
		['batiment_rnb_non_unique', "Le RNB rattache plusieurs bâtiments à cette adresse ; aucun n'est retenu."],
		['aucun_document', 'Aucun DPE depuis juillet 2021 à cette adresse ; les diagnostics plus anciens ne sont plus valables.'],
		['aucun_document_date', 'Aucun DPE daté à cette adresse ; le plus récent ne peut pas être déterminé.'],
		['population_incomplete', 'Plus de 1 000 documents sont déclarés à cette adresse ; le plus récent n\'est pas déterminé.']
	])('not_available, motif %s : phrase décidée, libellé « Non publiée »', (motif, phrase) => {
		expect(phraseEtat('not_available', motif, null)).toBe(phrase);
		expect(libelleEtat('not_available', motif)).toBe('Non publiée');
	});

	it('a decided reason sentence is read for `not_available` only; a reason without one keeps the generic one', () => {
		expect(phraseEtat('temporary_error', 'adresse_non_verifiable', null)).toBe(PHRASES_ETATS.temporary_error);
		// Row 56 replaces row 52: `population_incomplete` now has its sentence, never the outage one.
		expect(phraseEtat('temporary_error', 'population_incomplete', null)).toBe(PHRASES_ETATS.temporary_error);
		expect(phraseEtat('not_available', 'aucun_batiment_au_point', null)).toBe(PHRASES_ETATS.not_available);
	});
});

describe('libelleMotifAbsence — a served reason, never a technical key (03-17 F57, F58)', () => {
	it('reads the table first', () => {
		expect(LIBELLES_MOTIFS_ABSENCE).toEqual({
			indicator_policy_unqualified: 'Indicateur non encore validé',
			territorial_attribution_unqualified: 'Rattachement à la commune non vérifié',
			empty_sample: 'Aucune vente retenue sur la période',
			known_class_denominator_zero: 'Aucun diagnostic avec une classe connue',
			commune_hors_build_qualifie: 'Indicateurs non qualifiés pour cette commune'
		});
		expect(libelleMotifAbsence('empty_sample')).toBe('Aucune vente retenue sur la période');
		expect(libelleMotifAbsence('commune_hors_build_qualifie')).toBe('Indicateurs non qualifiés pour cette commune');
	});

	it('keeps a reason that is already a sentence', () => {
		expect(libelleMotifAbsence('Moins de 5 ventes.')).toBe('Moins de 5 ventes.');
	});

	it('returns null for a technical key, an empty or a missing reason', () => {
		expect(libelleMotifAbsence('some_unknown_key')).toBeNull();
		expect(libelleMotifAbsence('clé')).toBeNull();
		expect(libelleMotifAbsence('')).toBeNull();
		expect(libelleMotifAbsence(null)).toBeNull();
	});
});

describe('LIBELLE_NON_AFFICHABLE — a value served without a usable state (03-17 C6)', () => {
	it('is worded as the UI-SPEC glossary, never « Non servi »', () => {
		expect(LIBELLE_NON_AFFICHABLE).toBe('Valeur non affichable');
	});
});

/** The closed D-04 set mirrored from the backend (`FieldValue['state']`). */
const ETATS_FICHE: SemanticState[] = ['value', 'not_reported', 'out_of_coverage', 'temporary_error', 'not_applicable'];

// The themes (until 03-08) and the Fiche Bien (phase 4) still render the D-04 states of
// `formatField`, where `out_of_coverage` also stands for « dataset empty or not requested ».
// Worded « Hors couverture » there, it would tell a reader that the instance does not cover a
// place it does cover. They keep their historical wording until they move to StateValue.
describe('LIBELLES_ECARTS_HISTORIQUES — wording of the screens not yet redesigned, unchanged', () => {
	it.each([
		['value', 'Données présentes'],
		['not_reported', 'Non renseigné'],
		['out_of_coverage', 'non disponible'],
		['temporary_error', 'non disponible'],
		['not_applicable', 'non applicable']
	])('%s → « %s »', (cle, libelle) => {
		expect(LIBELLES_ECARTS_HISTORIQUES[cle as SemanticState]).toBe(libelle);
	});

	it('covers every Fiche FieldValue state', () => {
		for (const etat of ETATS_FICHE) expect(LIBELLES_ECARTS_HISTORIQUES[etat], etat).toBeTruthy();
	});
});

describe('EXPLICATIONS_ETATS — sentences of the former ℹ️ panels, unchanged', () => {
	it.each([
		['not_reported', "La source publique n'a pas renseigné cette information pour ce point précis."],
		['out_of_coverage', "Cette donnée n'est pas couverte ici (hors périmètre de la démonstration ou non collectée pour cette zone)."],
		['temporary_error', 'Donnée temporairement indisponible — nouvelle tentative au prochain rafraîchissement.'],
		['not_applicable', "Cette information ne s'applique pas à ce bien ou à cette commune."]
	])('%s → exact sentence', (cle, phrase) => {
		expect(EXPLICATIONS_ETATS[cle]).toBe(phrase);
	});

	// 04-12 (UI-SPEC « Glossaire — deltas », C34): `perime` is retired — « preuve d'existence du
	// logement » is false — and no reader is left; a document says « Expiré » (LIBELLES_DOCUMENT_DPE).
	it('perime is retired from every glossary, none speaks of a proof of the dwelling', () => {
		for (const glossaire of [LIBELLES_ETATS, PHRASES_ETATS, EXPLICATIONS_ETATS]) {
			expect(Object.hasOwn(glossaire, 'perime')).toBe(false);
			expect(Object.values(glossaire).join(' ')).not.toMatch(/existence du logement|Diagnostic périmé/);
		}
	});
});

describe('formatField — historical wording of the D-04 rows, unchanged', () => {
	const FIELD = 'demographie.population';

	it('not_reported renders the soft muted row', () => {
		expect(formatField(FIELD, 'Population', { state: 'not_reported' })).toEqual({
			label: 'Population', value: 'Non renseigné', muted: true, fieldId: FIELD,
			state: 'not_reported', presentation: { state: 'not_reported' }
		});
	});

	it.each(['out_of_coverage', 'temporary_error'] as const)('%s renders the hard gap row', (etat) => {
		expect(formatField(FIELD, 'Population', { state: etat })).toEqual({
			label: 'Population', value: 'non disponible', available: false, fieldId: FIELD,
			state: etat, presentation: { state: etat }
		});
	});

	it('not_applicable is out of contract on this field (D-20 guard)', () => {
		expect(() => formatField(FIELD, 'Population', { state: 'not_applicable' })).toThrow(SemanticContractError);
	});

	it('a real value is untouched by the glossary', () => {
		expect(formatField(FIELD, 'Population', { state: 'value', value: 12345 })).toEqual({
			label: 'Population', value: '12\u202f345 hab.', fieldId: FIELD, state: 'value',
			presentation: { state: 'value', measuredZero: false }
		});
	});
});
