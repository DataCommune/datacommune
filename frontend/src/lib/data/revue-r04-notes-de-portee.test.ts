import { describe, expect, it } from 'vitest';
import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import { categoriesCommune } from './commune-categories';
import { themesCommune, themesPourExport } from './commune-grid';
import exportRochefourchat from './__fixtures__/revue-commune-26274.json';

// Review 2026-11-10, finding R-04 (red on purpose, no fix here).
//
// `mapCommuneToGrid` appends « Portée du revenu déclaré » and « Portée de la vacance » to the former
// themes `environnement` and `propriete` (commune-grid.ts:146-147). `themesCommune` then gives a note
// the theme of the row above it: the last row of `environnement` is a school count, the last row of
// `propriete` a land-register one. So the income caveat lands in « Services & transports » and the
// vacancy caveat in « Contexte communal », in the CSV export and on the fiche (groups « Équipements »
// and « Population »). They only show when the value they qualify is absent, which is the case of
// every small commune (income under statistical secrecy) and of Paris, Lyon and Marseille (no
// communal vacancy row). Fixture: the export of Rochefourchat (26274) as served by datacommune.fr on
// 2026-11-09, where the fiche shows « Portée du revenu déclaré » under « Équipements ».

const ROCHEFOURCHAT = exportRochefourchat.donnees as unknown as CommuneSynthese;
const SOURCES = exportRochefourchat.sources as unknown as SourceAttribution[];
const themes = themesCommune(ROCHEFOURCHAT, SOURCES);

const themeDeLaLigne = (fieldId: string) => themes.find((t) => t.lignes.some((l) => l.fieldId === fieldId))?.cle;
const themeDeLaNote = (libelle: string) => themes.find((t) => t.notes.some((n) => n.libelle === libelle))?.cle;

describe('R-04 — une note de portée suit la ligne qu’elle qualifie', () => {
	it('témoin : les deux notes sont affichées quand la valeur manque', () => {
		const notes = themes.flatMap((t) => t.notes.map((n) => n.libelle));
		expect(notes).toContain('Portée du revenu déclaré');
		expect(notes).toContain('Portée de la vacance');
	});

	it('« Portée du revenu déclaré » est dans le thème du revenu', () => {
		expect(themeDeLaNote('Portée du revenu déclaré')).toBe(themeDeLaLigne('revenus.revenu_median'));
	});

	it('« Portée de la vacance » est dans le thème de la vacance', () => {
		expect(themeDeLaNote('Portée de la vacance')).toBe(themeDeLaLigne('vacance.taux_vacance'));
	});

	it('sur la fiche, chaque note est dans le groupe de sa ligne', () => {
		const groupes = categoriesCommune(themes).flatMap((c) => c.groupes);
		const groupeDeLaNote = (libelle: string) => groupes.find((g) => g.notes.some((n) => n.libelle === libelle))?.id;
		expect(groupeDeLaNote('Portée du revenu déclaré')).toBe('revenus');
		expect(groupeDeLaNote('Portée de la vacance')).toBe('parc');
	});

	it('dans le CSV, chaque note est dans le thème de sa ligne', () => {
		const exportes = themesPourExport(themes);
		const themeCsv = (label: string) => exportes.find((g) => g.rows.some((r) => r.label === label))?.name;
		expect(themeCsv('Portée du revenu déclaré')).toBe(themeCsv('Revenu déclaré médian'));
		expect(themeCsv('Portée de la vacance')).toBe(themeCsv('Taux de vacance du parc privé'));
	});
});
