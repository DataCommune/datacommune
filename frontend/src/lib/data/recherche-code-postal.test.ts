import { describe, it, expect } from 'vitest';
import type { DatasetHosting } from '$lib/api/admin';
import { LIBELLES_MOTIFS } from './etats-libelles';
import {
	ANNONCE_CODE_POSTAL_HORS_COUVERTURE,
	LIEN_COUVERTURE,
	TITRE_CODE_POSTAL_HORS_COUVERTURE,
	couvertureCodePostal,
	estCodePostal,
	phraseRechercheBornee
} from './recherche-code-postal';

// Departements are arbitrary here: the module names what the API serves, nothing more (D-34).
const OUTSIDE: DatasetHosting = {
	etat: 'hors_couverture',
	motif: 'territoire_hors_perimetre',
	departements: []
};
const HOSTED: DatasetHosting = { etat: 'heberge', motif: null, departements: ['35', '56'] };

const meta = (ban: DatasetHosting) => ({ hebergement: { ban } });

describe('estCodePostal()', () => {
	it('is true for five digits, spaces around them ignored', () => {
		expect(estCodePostal('33000')).toBe(true);
		expect(estCodePostal(' 33000 ')).toBe(true);
	});

	it('is false for anything else', () => {
		expect(estCodePostal('3300')).toBe(false);
		expect(estCodePostal('330000')).toBe(false);
		expect(estCodePostal('nantes')).toBe(false);
		expect(estCodePostal('')).toBe(false);
	});
});

describe('couvertureCodePostal()', () => {
	it('is null for a search by name, whatever the meta says', () => {
		expect(couvertureCodePostal('le pin', meta(OUTSIDE))).toBeNull();
	});

	it('is null without meta or without a served BAN coverage', () => {
		expect(couvertureCodePostal('33000', undefined)).toBeNull();
		expect(couvertureCodePostal('33000', null)).toBeNull();
		expect(couvertureCodePostal('33000', { hebergement: null })).toBeNull();
		expect(
			couvertureCodePostal('33000', {
				hebergement: { demographie: OUTSIDE }
			})
		).toBeNull();
	});

	it('out of coverage: the phrase of the served reason, never a zero', () => {
		for (const motif of Object.keys(LIBELLES_MOTIFS) as (keyof typeof LIBELLES_MOTIFS)[]) {
			expect(couvertureCodePostal('33000', meta({ ...OUTSIDE, motif }))).toEqual({
				kind: 'hors-couverture',
				phrase: LIBELLES_MOTIFS[motif].phrase
			});
		}
	});

	it('out of coverage with a reason outside the contract: the state, no phrase', () => {
		const inconnu = { ...OUTSIDE, motif: 'motif_futur' } as unknown as DatasetHosting;

		expect(couvertureCodePostal('33000', meta(inconnu))).toEqual({
			kind: 'hors-couverture',
			phrase: null
		});
	});

	it('hosted: a zero bounded to the served departements, copied in the served order', () => {
		const served = meta(HOSTED);

		const couverture = couvertureCodePostal('35999', served);

		expect(couverture).toEqual({ kind: 'bornee', departements: ['35', '56'] });
		if (couverture?.kind !== 'bornee') throw new Error('bornee attendue');
		expect(couverture.departements).not.toBe(HOSTED.departements);
	});

	it('hosted without any departement to name: no bounded statement', () => {
		expect(couvertureCodePostal('35999', meta({ ...HOSTED, departements: [] }))).toBeNull();
	});

	it('reads the meta only: a neighbouring prefix out of coverage stays out of coverage', () => {
		// The API may serve a hosted commune for this code with this meta; the screens consult the
		// coverage only when no result is listed, so the result is never hidden.
		expect(couvertureCodePostal('49730', meta(OUTSIDE))).toEqual({
			kind: 'hors-couverture',
			phrase: LIBELLES_MOTIFS.territoire_hors_perimetre.phrase
		});
	});
});

// The wording itself is asserted on the screens (CommuneSearch.test.ts, e2e), not copied here.
describe('phraseRechercheBornee()', () => {
	it('names the served departements with the caller wording, in order', () => {
		const nom = (code: string) => `Département ${code}`;

		expect(phraseRechercheBornee(['35', '56'], nom)).toBe(
			'La recherche par code postal porte sur\u00a0: Département 35, Département 56.'
		);
	});
});

// 03-17 T-7, V14, A12: out of coverage is worded with the glossary, never with the outage vocabulary.
describe('out-of-coverage wording', () => {
	it('title, announcement and link never say « disponible » nor « indisponible »', () => {
		for (const texte of [TITRE_CODE_POSTAL_HORS_COUVERTURE, ANNONCE_CODE_POSTAL_HORS_COUVERTURE, LIEN_COUVERTURE]) {
			expect(texte).not.toMatch(/disponible/i);
		}
	});

	it('the title names the out-of-coverage state, and the announcement reads it', () => {
		expect(TITRE_CODE_POSTAL_HORS_COUVERTURE).toBe('Code postal hors couverture');
		expect(ANNONCE_CODE_POSTAL_HORS_COUVERTURE).toBe(TITRE_CODE_POSTAL_HORS_COUVERTURE);
		expect(LIEN_COUVERTURE).toBe('Voir la couverture des données');
	});
});
