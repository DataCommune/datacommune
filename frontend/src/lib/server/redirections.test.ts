import { describe, expect, it } from 'vitest';
import { redirectionPermanente } from './redirections';

describe('redirectionPermanente: routes removed in phase 3 (03-02)', () => {
	it.each([
		['/docs', '/developpeurs'],
		['/docs/quickstart', '/developpeurs'],
		['/playground', '/developpeurs'],
		['/playground/', '/developpeurs'],
		['/reference', '/developpeurs'],
		['/reference/communes', '/developpeurs'],
		['/guides', '/developpeurs'],
		['/guides/achat-immobilier', '/developpeurs'],
		['/guides/renovation-pme', '/developpeurs'],
		['/guides/quickstart', '/developpeurs'],
		['/roadmap', '/couverture'],
		['/roadmap/2027', '/couverture']
	])('%s → %s', (chemin, cible) => {
		expect(redirectionPermanente(chemin)).toBe(cible);
	});

	it('does not catch a page whose name only starts like a removed one', () => {
		for (const chemin of ['/docsx', '/guidesavoir', '/references', '/roadmaps', '/developpeurs', '/couverture']) {
			expect(redirectionPermanente(chemin), chemin).toBeNull();
		}
	});
});

describe('redirectionPermanente: canonical upper-case code', () => {
	it.each([
		['/departement/2a', '/departement/2A'],
		['/departement/2b', '/departement/2B'],
		['/commune/2a004', '/commune/2A004'],
		['/commune/2b033', '/commune/2B033']
	])('%s → %s', (chemin, cible) => {
		expect(redirectionPermanente(chemin)).toBe(cible);
	});

	it('leaves the canonical forms alone, so they keep answering themselves', () => {
		for (const chemin of ['/departement/2A', '/departement/44', '/departement/974', '/commune/44109', '/commune/2A004']) {
			expect(redirectionPermanente(chemin), chemin).toBeNull();
		}
	});

	it('never redirects a code the route refuses: that one keeps its own 404', () => {
		for (const chemin of ['/departement/2c', '/departement/abc', '/commune/2a00', '/commune/2a004.json', '/commune/x']) {
			expect(redirectionPermanente(chemin), chemin).toBeNull();
		}
	});

	it('only reads the code segment, never a deeper path', () => {
		expect(redirectionPermanente('/departement/2a/communes')).toBeNull();
	});
});
