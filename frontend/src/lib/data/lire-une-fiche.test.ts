// Legend « Lire une fiche » (04.8-03; UI-SPEC v4 § 3.12, § 7.6; amendment 9 of § 2.6): every level of the
// scale table and every key of the badges has a definition, and the legend draws one line for each.
// A table with one entry left out fails here, not on the page.
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import LireUneFiche from '$lib/components/ds/LireUneFiche.svelte';
import { LIBELLE_STATUT } from './adresse-rapport';
import { NIVEAUX } from './echelles';
import { DEFINITIONS_ECHELLES, DEFINITIONS_ETATS, lignesEchelles, lignesEtats } from './lire-une-fiche';

/** The keys of `table` that have no text (absent or blank): what an amputated table leaves out. */
const sansDefinition = (table: Readonly<Record<string, string>>, cles: readonly string[]): string[] =>
	cles.filter((cle) => !table[cle]?.trim());

/**
 * The keys of the legend: every lasting status of `LIBELLE_STATUT`, the outlined vigilance, and no badge.
 * « Lecture en cours » (R-07, 2026-11-10) lasts a few seconds and is said by the head of the fiche while it
 * lasts: it is the one status kept out of the legend (`CleLegende`), a choice left open to Ianis. Any other
 * status added without a definition still fails here.
 */
const TRANSITOIRES = ['en_cours'];
const CLES_ETATS = [...Object.keys(LIBELLE_STATUT).filter((cle) => !TRANSITOIRES.includes(cle)), 'vigilance_contour', 'sans_badge'];

describe('définitions des échelles — une par niveau de NIVEAUX', () => {
	it('every level of the scale table has a non-empty definition, and none is left over', () => {
		expect(sansDefinition(DEFINITIONS_ECHELLES, Object.keys(NIVEAUX))).toEqual([]);
		expect(new Set(Object.keys(DEFINITIONS_ECHELLES))).toEqual(new Set(Object.keys(NIVEAUX)));
		expect(Object.keys(NIVEAUX)).toHaveLength(9);
	});

	it('a table with one level left out fails (the check is not vacuous)', () => {
		const { epci: _epci, ...ampute } = DEFINITIONS_ECHELLES;
		expect(sansDefinition(ampute, Object.keys(NIVEAUX))).toEqual(['epci']);
		expect(sansDefinition({ ...DEFINITIONS_ECHELLES, iris: '  ' }, Object.keys(NIVEAUX))).toEqual(['iris']);
	});
});

describe('définitions des états — les 9 clés de la légende', () => {
	it('A4: outlined vigilance names the commune and a wider perimeter', () => {
		expect(DEFINITIONS_ETATS.vigilance_contour).toBe('Une vigilance qui vaut pour toute la commune, ou pour un périmètre plus large que l’adresse\u00a0: elle ne dit pas si l’adresse est concernée.');
	});
	it('every status, the outlined vigilance and the absence of badge have a non-empty definition', () => {
		expect(CLES_ETATS).toHaveLength(9);
		expect(sansDefinition(DEFINITIONS_ETATS, CLES_ETATS)).toEqual([]);
		expect(new Set(Object.keys(DEFINITIONS_ETATS))).toEqual(new Set(CLES_ETATS));
	});

	it('a table with one key left out fails (the check is not vacuous)', () => {
		const { zero: _zero, ...ampute } = DEFINITIONS_ETATS;
		expect(sansDefinition(ampute, CLES_ETATS)).toEqual(['zero']);
		expect(sansDefinition({ ...DEFINITIONS_ETATS, vigilance_contour: '' }, CLES_ETATS)).toEqual(['vigilance_contour']);
	});
});

describe('lignes de la légende', () => {
	it('draws the scales from the least to the widest, each with the pictogram and the word of the table', () => {
		const lignes = lignesEchelles();
		expect(lignes.map((l) => l.niveau)).toEqual(['adresse', 'batiment', 'parcelle', 'rayon', 'iris', 'arrondissement', 'commune', 'epci', 'departement']);
		const rangs = lignes.map((l) => NIVEAUX[l.niveau].rang);
		expect(rangs).toEqual([...rangs].sort((a, b) => a - b));
		for (const l of lignes) {
			expect(l.pictogramme).toBe(NIVEAUX[l.niveau].pictogramme);
			expect(l.definition).toBe(DEFINITIONS_ECHELLES[l.niveau]);
		}
		// A radius is shown with a distance, as the mockup does.
		expect(lignes.find((l) => l.niveau === 'rayon')?.mot).toBe('500 m autour');
	});

	it('draws the nine states in the order of the contract; the outlined vigilance in a tile, no badge for the last neutral', () => {
		const lignes = lignesEtats();
		expect(lignes.map((l) => l.cle)).toEqual([
			'vigilance',
			'vigilance_contour',
			'rassurant',
			'sans_badge',
			'zero',
			'non_concerne',
			'panne',
			'manque',
			'hors_couverture'
		]);
		const parCle = Object.fromEntries(lignes.map((l) => [l.cle, l]));
		expect(parCle.vigilance).toMatchObject({ zone: 'ligne', statut: { cle: 'vigilance', texte: LIBELLE_STATUT.vigilance } });
		expect(parCle.vigilance_contour).toMatchObject({ zone: 'tuile', statut: { cle: 'vigilance', texte: LIBELLE_STATUT.vigilance } });
		expect(parCle.sans_badge.statut).toBeNull();
		for (const cle of ['rassurant', 'zero', 'non_concerne', 'panne', 'manque', 'hors_couverture'] as const) {
			expect(parCle[cle].statut).toEqual({ cle, texte: LIBELLE_STATUT[cle] });
		}
		for (const l of lignes) expect(l.definition).toBe(DEFINITIONS_ETATS[l.cle]);
	});
});

describe('LireUneFiche — le panneau #lire', () => {
	it('is a part panel named « Lire une fiche », with its lead and the two subtitles', () => {
		const { container } = render(LireUneFiche);

		const section = container.querySelector('section#lire') as HTMLElement;
		expect(section).not.toBeNull();
		expect(section.querySelector('h2')?.textContent).toBe('Lire une fiche');
		expect(section.textContent).toContain('Chaque valeur porte son échelle et, s’il y a lieu, un état.');
		expect([...section.querySelectorAll('h3')].map((h) => h.textContent)).toEqual(['Les échelles', 'Les états']);
		expect(section.getAttribute('aria-labelledby')).toBe(section.querySelector('h2')?.id);
	});

	it('has one line for each level of the scale table, in the order of the ranks, with its definition', () => {
		const { container } = render(LireUneFiche);

		const [echelles] = [...container.querySelectorAll('#lire ul.lignes')];
		const lignes = [...echelles.querySelectorAll('li')];
		expect(lignes).toHaveLength(Object.keys(NIVEAUX).length);
		lignes.forEach((li, i) => {
			const attendu = lignesEchelles()[i];
			// The pictogram is always followed by its word (D-07), and drawn aria-hidden.
			expect(li.querySelector('.ech')?.textContent?.trim()).toBe(attendu.mot);
			expect(li.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
			expect(li.querySelector('p')?.textContent).toBe(attendu.definition);
		});
	});

	it('has one line for each key of the badges: the badge or « Sans badge », then its definition', () => {
		const { container } = render(LireUneFiche);

		const etats = container.querySelectorAll('#lire ul.lignes')[1];
		const lignes = [...etats.querySelectorAll('li')];
		expect(lignes).toHaveLength(9);
		lignes.forEach((li, i) => {
			const attendu = lignesEtats()[i];
			expect(li.querySelector('p')?.textContent).toBe(attendu.definition);
			if (attendu.statut) expect(li.querySelector('.badge')?.textContent).toBe(attendu.statut.texte);
			else {
				expect(li.querySelector('.badge')).toBeNull();
				expect(li.querySelector('.def-sans')?.textContent).toBe('Sans badge');
			}
		});
		expect(lignes[0].querySelector('.badge')?.classList.contains('badge-plein')).toBe(true);
		expect(lignes[1].querySelector('.badge')?.classList.contains('badge-contour')).toBe(true);
	});
});
