import { describe, expect, it } from 'vitest';
import { CATEGORIES_COMMUNE, categorieDeAncre, categoriesCommune, groupeDuChamp } from './commune-categories';
import { THEME_DU_CHAMP, themesCommune } from './commune-grid';
import { QUESTIONS } from './questions';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from './__fixtures__/commune-85191-20261012';
import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';

const SOURCES_85191 = [...COMMUNE_85191_SOURCES] as SourceAttribution[];
const OBSERVEE_85191 = COMMUNE_85191_OBSERVED as CommuneSynthese;

describe('categories of the commune fiche (4.9)', () => {
	it('names its categories as the address fiche, in its order', () => {
		const ordre = QUESTIONS.map((q) => q.id);
		expect(CATEGORIES_COMMUNE.map((c) => ordre.indexOf(c.id))).toEqual([0, 1, 2]);
		expect(CATEGORIES_COMMUNE.slice(0, 2).map((c) => c.titre)).toEqual(QUESTIONS.slice(0, 2).map((q) => q.titre));
	});

	it('gives every field the page renders a group', () => {
		expect(Object.keys(THEME_DU_CHAMP).filter((champ) => groupeDuChamp(champ) === null)).toEqual([]);
		expect(groupeDuChamp('dpe.annee_construction_mediane')).toBe('construction');
		expect(groupeDuChamp('dpe.dpe_median')).toBe('dpe');
		expect(groupeDuChamp('demographie.nb_logements_vacants_recensement')).toBe('parc');
		expect(groupeDuChamp('demographie.population')).toBe('population');
	});

	it.each([
		['85047', COMMUNE_85047_CHALLANS, null],
		['85191', OBSERVEE_85191, SOURCES_85191]
	] as const)('places each row and note of %s once, in the served order of its theme', (_code, commune, sources) => {
		const themes = themesCommune(commune, sources);
		const categories = categoriesCommune(themes);
		const lignes = categories.flatMap((c) => c.groupes.flatMap((g) => g.lignes.map((l) => l.id)));
		expect([...lignes].sort()).toEqual(themes.flatMap((t) => t.lignes.map((l) => l.id)).sort());
		expect(new Set(lignes).size).toBe(lignes.length);
		const notes = categories.flatMap((c) => c.groupes.flatMap((g) => g.notes.map((n) => n.id)));
		expect(notes.sort()).toEqual(themes.flatMap((t) => t.notes.map((n) => n.id)).sort());
		// Prices first: the first row read is a sale price.
		expect(categories[0].groupes[0].lignes[0].fieldId).toMatch(/^dvf\./);
	});

	it('announces served values on the closed row, never a value it does not have', () => {
		const categories = categoriesCommune(themesCommune(OBSERVEE_85191, SOURCES_85191));
		for (const c of categories) expect(c.annonce.length).toBeGreaterThan(0);
		expect(categories.flatMap((c) => c.annonce).join(' ')).not.toMatch(/null|undefined|NaN/);
	});

	it('opens the category an anchor points to, former theme anchors included', () => {
		const categories = categoriesCommune(themesCommune(OBSERVEE_85191, SOURCES_85191));
		expect(categorieDeAncre('#q-logement', categories)).toBe('q-logement');
		expect(categorieDeAncre('#critere-loyers', categories)).toBe('q-prix');
		expect(categorieDeAncre('#energie-bati', categories)).toBe('q-logement');
		expect(categorieDeAncre('#contexte-communal', categories)).toBe('q-quotidien');
		expect(categorieDeAncre('#questions', categories)).toBeNull();
		expect(categorieDeAncre('', categories)).toBeNull();
	});
});
