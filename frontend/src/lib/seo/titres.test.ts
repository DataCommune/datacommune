import { describe, expect, it } from 'vitest';
import { titreCommune, titreDepartement, titrePage } from './titres';

describe('titres des pages de territoire', () => {
	it('names the commune, its departement and what the fiche serves', () => {
		expect(titreCommune('Nantes', '44', true)).toBe('Nantes (44) : prix immobilier, loyers, DPE et services — DataCommune');
		expect(titreCommune('Ajaccio', '2A', true)).toBe('Ajaccio (2A) : prix immobilier, loyers, DPE et services — DataCommune');
	});

	it('keeps the plain name when the fiche serves no figure, or without a departement', () => {
		expect(titreCommune('Nantes', '44', false)).toBe('Nantes — DataCommune');
		expect(titreCommune('Commune INSEE 99999', null, true)).toBe('Commune INSEE 99999 : prix immobilier, loyers, DPE et services — DataCommune');
	});

	it('keeps the departement title as served before', () => {
		expect(titreDepartement('Loire-Atlantique', '44')).toBe('Loire-Atlantique (44), chiffres clés du département — DataCommune');
		expect(titrePage('Comparer des communes')).toBe('Comparer des communes — DataCommune');
	});
});
