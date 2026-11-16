// Readable street of the fiche (04.8-03, UI-SPEC v4 § 7.5): the BAN files the article at the end of the
// name and writes the street type with a capital; the h1, the breadcrumb and the page title read it the
// way French writes it. Never applied to a datum that is re-exported (CSV, JSON).
import { describe, expect, it } from 'vitest';
import { voieLisible } from './voie-lisible';

describe('voieLisible — la voie telle que le français l’écrit (§ 7.5)', () => {
	it('puts the article back in place and lowers the street type', () => {
		expect(voieLisible('Chemin Puits Fictif (du)')).toBe('chemin du Puits Fictif');
	});

	it('lowers the street type of a name that has no filed article', () => {
		expect(voieLisible('Rue de la Paix')).toBe('rue de la Paix');
		expect(voieLisible('Boulevard Gambetta')).toBe('boulevard Gambetta');
	});

	it('an article ending with an apostrophe is glued to the name, with either apostrophe', () => {
		expect(voieLisible('Place Église (de l’)')).toBe('place de l’Église');
		expect(voieLisible("Impasse Orme (de l')")).toBe("impasse de l'Orme");
	});

	it('every filed article is put back: du, de la, des, de, d’', () => {
		expect(voieLisible('Rue Fontaine (de la)')).toBe('rue de la Fontaine');
		expect(voieLisible('Allée Tilleuls (des)')).toBe('allée des Tilleuls');
		expect(voieLisible('Route Nantes (de)')).toBe('route de Nantes');
		expect(voieLisible('Chemin Orme (d’)')).toBe('chemin d’Orme');
	});

	it('a street type that ends with an accent is lowered too (no ASCII-only word boundary)', () => {
		expect(voieLisible('Cité Jardin')).toBe('cité Jardin');
		expect(voieLisible('Allée des Roses')).toBe('allée des Roses');
		expect(voieLisible('Chaussée de la Mer')).toBe('chaussée de la Mer');
	});

	it('a name without a street type is returned as is', () => {
		expect(voieLisible('Les Terres Neuves')).toBe('Les Terres Neuves');
		expect(voieLisible('La Pierre Blanche')).toBe('La Pierre Blanche');
		expect(voieLisible('')).toBe('');
	});

	it('only the type at the start is lowered, a word of the name that looks like a type stays', () => {
		expect(voieLisible('Rue du Quai')).toBe('rue du Quai');
		expect(voieLisible('Quai de la Fosse')).toBe('quai de la Fosse');
		// A name that merely begins with the letters of a type is not that type.
		expect(voieLisible('Ruelle des Chats')).toBe('Ruelle des Chats');
	});

	it('is idempotent', () => {
		for (const voie of ['Chemin Puits Fictif (du)', 'Rue de la Paix', 'Les Terres Neuves', 'Place Église (de l’)']) {
			expect(voieLisible(voieLisible(voie))).toBe(voieLisible(voie));
		}
	});
});
