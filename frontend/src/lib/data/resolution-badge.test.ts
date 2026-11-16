import { describe, it, expect } from 'vitest';
import { deriveResolutionBadge } from './resolution-badge';
import type { ResultatMultiResolution } from '$lib/api/resolve';

/**
 * Minimal ResultatMultiResolution builder — only the 3 fields the badge reads
 * (adresse / code_insee / id_parcelle) vary per test; the rest is filler so the
 * literal satisfies the full interface (RESEARCH Priority Question 6 fields).
 */
function multi(overrides: Partial<ResultatMultiResolution> = {}): ResultatMultiResolution {
	return {
		type_detecte: 'adresse',
		latitude: 47.2184,
		longitude: -1.5536,
		adresse: null,
		id_parcelle: null,
		commune: null,
		code_postal: null,
		score: null,
		code_insee: null,
		datasets: null,
		erreurs: null,
		avertissements: [],
		...overrides
	};
}

describe('deriveResolutionBadge() — honest precision badge (D-07 / FICHE-03)', () => {
	it('issue=out_of_coverage → échec "Hors périmètre", niveau none, aucune granularité positive', () => {
		const badge = deriveResolutionBadge(multi(), 'out_of_coverage');
		expect(badge.niveau).toBe('none');
		expect(badge.label).toBe('Hors périmètre');
		expect(badge.ton).toBe('watch');
		expect(badge.label).not.toContain('Adresse normalisée');
		expect(badge.label).not.toContain('Commune');
	});

	it('issue=not_reported → "Aucune correspondance", niveau none, jamais un numéro de voie', () => {
		const badge = deriveResolutionBadge(multi(), 'not_reported');
		expect(badge.niveau).toBe('none');
		expect(badge.label).toBe('Aucune correspondance');
		expect(badge.ton).toBe('watch');
		expect(badge.label).not.toMatch(/\d/);
	});

	it('issue=ambiguous → "Plusieurs parcelles candidates", niveau partiel, ne prétend pas une parcelle exacte', () => {
		const badge = deriveResolutionBadge(
			multi({ parcelles_candidates: ['44109000EN0179', '44109000EN0180'] }),
			'ambiguous'
		);
		expect(badge.niveau).toBe('partiel');
		expect(badge.label).toBe('Plusieurs parcelles candidates');
		expect(badge.ton).toBe('watch');
	});

	it('issue=exact + adresse avec rue + code_insee + id_parcelle → "Adresse normalisée · parcelle identifiée", niveau adresse, ton info', () => {
		const badge = deriveResolutionBadge(
			multi({
				adresse: '12 Rue de la Paix, 44000 Nantes',
				code_insee: '44109',
				id_parcelle: '44109000EN0179'
			}),
			'exact'
		);
		expect(badge.niveau).toBe('adresse');
		expect(badge.ton).toBe('info');
		expect(badge.label).toBe('Adresse normalisée · parcelle identifiée');
	});

	it('issue=exact + adresse SANS rue (commune seule) + code_insee + id_parcelle=null → "Commune", niveau commune, jamais "Adresse normalisée" ni numéro de voie', () => {
		const badge = deriveResolutionBadge(
			multi({ adresse: '44000 Nantes', code_insee: '44109', id_parcelle: null }),
			'exact'
		);
		expect(badge.niveau).toBe('commune');
		expect(badge.ton).toBe('neutral');
		expect(badge.label).toBe('Commune');
		expect(badge.label).not.toContain('Adresse normalisée');
		expect(badge.label).not.toMatch(/\d/);
	});

	it('issue=exact + id_parcelle présent mais adresse sans rue → mentionne "parcelle identifiée" sans prétendre le niveau adresse', () => {
		const badge = deriveResolutionBadge(
			multi({ adresse: '44000 Nantes', code_insee: '44109', id_parcelle: '44109000EN0179' }),
			'exact'
		);
		expect(badge.niveau).toBe('commune');
		expect(badge.label).toBe('Commune · parcelle identifiée');
		expect(badge.label).not.toContain('Adresse normalisée');
	});

	it('issue=exact + adresse avec segment rue vide avant la virgule (", 44000 Nantes") → traité comme commune, pas adresse', () => {
		const badge = deriveResolutionBadge(
			multi({ adresse: ', 44000 Nantes', code_insee: '44109' }),
			'exact'
		);
		expect(badge.niveau).toBe('commune');
		expect(badge.label).not.toContain('Adresse normalisée');
	});

	it('meta.resolution absent (issue=undefined) → non concluant, niveau none, ne crashe pas', () => {
		const badge = deriveResolutionBadge(multi(), undefined);
		expect(badge.niveau).toBe('none');
		expect(badge.label).toBeTruthy();
	});

	it('multi=null (aucune donnée de résolution) → non concluant, niveau none, ne crashe pas', () => {
		const badge = deriveResolutionBadge(null, undefined);
		expect(badge.niveau).toBe('none');
		expect(badge.label).toBeTruthy();
	});

	it("ne lit jamais avertissements/ADRESSE_INCOMPLETE (signal dataset-scopé, pas un signal de résolution globale)", () => {
		const badge = deriveResolutionBadge(
			multi({
				adresse: '44000 Nantes',
				code_insee: '44109',
				avertissements: [{ dataset: 'connectivite', code: 'ADRESSE_INCOMPLETE', message: 'x' }]
			}),
			'exact'
		);
		// Le résultat ne doit dépendre que de adresse/code_insee/id_parcelle, jamais des avertissements.
		expect(badge.niveau).toBe('commune');
	});
});
