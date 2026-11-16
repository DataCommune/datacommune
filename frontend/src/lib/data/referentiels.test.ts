// Structure lock for the published national referentials (D-03) + behaviour lock for the
// comparison function. Modelled on semantic-contract.test.ts: the point is that an entry can
// never lose its provenance (source / millésime / publication URL) without a red test, and that
// the honest-silence cases (D-07) can never start emitting a chip.

import { describe, it, expect } from 'vitest';
import { REFERENTIELS, SANS_REFERENTIEL, comparerAuReferentiel } from './referentiels';

describe('REFERENTIELS — provenance structure lock (D-03)', () => {
	it('is non-empty and covers the three referentials of the phase', () => {
		expect(Object.keys(REFERENTIELS)).toEqual(
			expect.arrayContaining([
				'revenus.revenu_median',
				'zonage_abc.zone',
				'bien.dpe.etiquette_dpe'
			])
		);
	});

	it.each(Object.entries(REFERENTIELS))(
		'%s carries a non-empty source, millésime and https publication URL',
		(_fieldId, entree) => {
			expect(entree.source.trim()).not.toBe('');
			expect(entree.millesime.trim()).not.toBe('');
			expect(entree.url.trim()).not.toBe('');
			expect(entree.url.startsWith('https://')).toBe(true);
		}
	);

	it('every numeric referential exposes its visible tolerance band (D-06)', () => {
		for (const entree of Object.values(REFERENTIELS)) {
			if (entree.kind === 'numerique') {
				expect(entree.tolerancePct).toBeGreaterThan(0);
			}
		}
	});

	it('every categorical referential DECLARES its gradation, `null` included (D-16)', () => {
		let categoriels = 0;
		for (const [fieldId, entree] of Object.entries(REFERENTIELS)) {
			if (entree.kind !== 'categoriel') continue;
			categoriels += 1;
			// The KEY must exist: an entry that simply omits it would silently read as ungraded
			// while the type-check had no discriminant to reject a favourable verdict.
			expect(entree, `${fieldId} n'expose pas la clé gradation`).toHaveProperty('gradation');
		}
		// Non-vacuity: an empty loop would be green and prove nothing (T-49-11).
		expect(categoriels).toBeGreaterThan(0);
	});

	it('a graded referential grounds its order in a fragment of its own explication', () => {
		let gradues = 0;
		for (const [fieldId, entree] of Object.entries(REFERENTIELS)) {
			if (entree.kind !== 'categoriel' || entree.gradation == null) continue;
			gradues += 1;
			expect(
				entree.gradation.fondement.trim().length,
				`${fieldId}: fondement vide`
			).toBeGreaterThan(0);
			// The justification the code relies on IS the sentence the reader receives.
			expect(
				entree.explication,
				`${fieldId}: le fondement n'est pas une sous-chaîne de l'explication`
			).toContain(entree.gradation.fondement);
		}
		expect(gradues).toBeGreaterThan(0);
	});

	it('no field_id is both compared and declared without referential', () => {
		const chevauchement = Object.keys(REFERENTIELS).filter((id) => id in SANS_REFERENTIEL);
		expect(chevauchement).toEqual([]);
	});
});

describe('SANS_REFERENTIEL — honest silence (D-07)', () => {
	it('registers vacance, the two loyers and the two DVF price fields', () => {
		expect(Object.keys(SANS_REFERENTIEL).sort()).toEqual([
			'bien.dvf.prix_m2',
			'bien.dvf.valeur_fonciere',
			'loyers.loyer_m2_appartement',
			'loyers.loyer_m2_maison',
			'vacance.taux_vacance'
		]);
	});

	it.each(Object.keys(SANS_REFERENTIEL))('%s never yields a comparison', (fieldId) => {
		expect(comparerAuReferentiel(fieldId, 12)).toBeNull();
		expect(comparerAuReferentiel(fieldId, '12')).toBeNull();
	});

	it('the vacance reason names the LOVAC definitional mismatch', () => {
		expect(SANS_REFERENTIEL['vacance.taux_vacance']).toContain('vacance structurelle');
	});

	it.each(Object.entries(SANS_REFERENTIEL))('%s states a non-empty reason', (_id, raison) => {
		expect(raison.trim().length).toBeGreaterThan(20);
	});
});

describe('comparerAuReferentiel — revenu (numeric, ±5 % band)', () => {
	it('above the band → good « Au-dessus de la médiane »', () => {
		const c = comparerAuReferentiel('revenus.revenu_median', 25000, { annee: 2021, codeInsee: '44109' });
		expect(c).not.toBeNull();
		expect(c!.verdict).toBe('good');
		expect(c!.label).toBe('Au-dessus de la médiane');
		// Intl fr-FR groups with a NARROW no-break space (U+202F), not a plain space.
		expect(c!.ecartTexte).toContain('22 900 €');
		expect(c!.ecartTexte).toContain('au-dessus');
	});

	it('inside the band → neutral « Proche de la médiane », band stated in the sentence', () => {
		const c = comparerAuReferentiel('revenus.revenu_median', 23772, { annee: 2021, codeInsee: '44109' });
		expect(c!.verdict).toBe('neutral');
		expect(c!.label).toBe('Proche de la médiane');
		expect(c!.ecartTexte).toContain('±5 %');
	});

	it('exactly at the band edge stays neutral (<= tolerance)', () => {
		expect(comparerAuReferentiel('revenus.revenu_median', 22900 * 1.05, { annee: 2021, codeInsee: '44109' })!.verdict).toBe('neutral');
		expect(comparerAuReferentiel('revenus.revenu_median', 22900 * 0.95, { annee: 2021, codeInsee: '44109' })!.verdict).toBe('neutral');
	});

	it('below the band → watch « En dessous de la médiane »', () => {
		const c = comparerAuReferentiel('revenus.revenu_median', 19000, { annee: 2021, codeInsee: '44109' });
		expect(c!.verdict).toBe('watch');
		expect(c!.label).toBe('En dessous de la médiane');
		expect(c!.ecartTexte).toContain('en dessous');
	});

	it('null → no comparison at all', () => {
		expect(comparerAuReferentiel('revenus.revenu_median', null, { annee: 2021, codeInsee: '44109' })).toBeNull();
	});

	it('an unparsable numeric string → no comparison (never NaN in the copy)', () => {
		expect(comparerAuReferentiel('revenus.revenu_median', 'n.d.', { annee: 2021, codeInsee: '44109' })).toBeNull();
	});

	it('a numeric string is compared like its number (API numeric-as-string)', () => {
		expect(comparerAuReferentiel('revenus.revenu_median', '25000', { annee: 2021, codeInsee: '44109' })!.verdict).toBe('good');
	});
});

describe('comparerAuReferentiel — zonage ABC (categorical, DESCRIPTIF — A-01)', () => {
	// A-01: the cited arrêté classifies market tension and publishes NO order from favourable to
	// unfavourable. Every class is therefore rendered as `info` — the zone and its explanation
	// still reach the reader, only the colour stops deciding for them. Labels unchanged.
	it.each([
		['A', 'info', 'Marché tendu (zone A)'],
		['B1', 'info', 'Marché tendu (zone B1)'],
		['B2', 'info', 'Marché intermédiaire (zone B2)'],
		['C', 'info', 'Marché détendu (zone C)']
	])('zone %s → %s / %s', (zone, verdict, label) => {
		const c = comparerAuReferentiel('zonage_abc.zone', zone);
		expect(c!.verdict).toBe(verdict);
		expect(c!.label).toBe(label);
		expect(c!.ecartTexte).toContain('2026');
	});

	it('the A bis spelling of the source resolves and is displayed verbatim', () => {
		const c = comparerAuReferentiel('zonage_abc.zone', 'A bis');
		expect(c!.verdict).toBe('info');
		expect(c!.label).toBe('Marché tendu (zone A bis)');
	});

	it('null and an unknown zone → no comparison', () => {
		expect(comparerAuReferentiel('zonage_abc.zone', null)).toBeNull();
		expect(comparerAuReferentiel('zonage_abc.zone', 'Z9')).toBeNull();
		expect(comparerAuReferentiel('zonage_abc.zone', '')).toBeNull();
	});
});

describe('comparerAuReferentiel — étiquette DPE (categorical)', () => {
	it.each([
		['C', 'good', 'Bonne performance (classe C)'],
		['E', 'neutral', 'Performance moyenne (classe E)'],
		['G', 'watch', 'Passoire énergétique (classe G)']
	])('classe %s → %s / %s', (classe, verdict, label) => {
		const c = comparerAuReferentiel('bien.dpe.etiquette_dpe', classe);
		expect(c!.verdict).toBe(verdict);
		expect(c!.label).toBe(label);
		expect(c!.ecartTexte).toContain('2021');
	});

	it('a lowercase label from the source still resolves', () => {
		expect(comparerAuReferentiel('bien.dpe.etiquette_dpe', 'g')!.verdict).toBe('watch');
	});

	it('null and an unknown class → no comparison', () => {
		expect(comparerAuReferentiel('bien.dpe.etiquette_dpe', null)).toBeNull();
		expect(comparerAuReferentiel('bien.dpe.etiquette_dpe', 'H')).toBeNull();
	});
});

describe('comparerAuReferentiel — unknown field', () => {
	it('a field with no referential is not an error, it simply has no chip', () => {
		expect(comparerAuReferentiel('demographie.population', 12345)).toBeNull();
		expect(comparerAuReferentiel('this.field.does.not.exist', 1)).toBeNull();
	});
});
