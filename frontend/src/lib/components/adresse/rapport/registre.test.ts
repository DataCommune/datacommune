// Every theme of the backend registry renders on the fiche (04.7-03; rewritten against LigneCritere in
// 04.8-03, D-35): the front knows no list of themes, so a theme added by a later plan
// must render through the generic path, as a row. The registry is read
// from `api/backend/app/rapport/themes/*.py` (one `THEME = DefinitionTheme(...)` per file); the themes
// served as examples (`contracts/rapport/exemples/<theme_id>[-<variante>].json`) are used as they are,
// the others take a served shape cycling through the states, so that each state is rendered by some theme.
import { readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import { projeterTheme, type ThemeRapport } from '$lib/api/rapport';
import { aucunZeroFabrique, statutDe, verdictCoherent } from '$lib/data/adresse-rapport';
import { lignesExportRapport } from '$lib/data/adresse-export';
import { niveauDe } from '$lib/data/echelles';
import { placerThemes } from '$lib/data/questions';
import LigneCritere from '$lib/components/adresse/question/LigneCritere.svelte';

const RACINE = resolve(process.cwd(), '..');
const DOSSIER_THEMES = join(RACINE, 'api/backend/app/rapport/themes');
const DOSSIER_EXEMPLES = join(RACINE, 'contracts/rapport/exemples');

interface Definition {
	id: string;
	section: string;
	titre: string;
	niveau: string;
}

/** The registry as the backend declares it, one definition per theme file. */
function registre(): Definition[] {
	return readdirSync(DOSSIER_THEMES)
		.filter((f) => f.endsWith('.py') && f !== '__init__.py')
		.map((f) => {
			const source = readFileSync(join(DOSSIER_THEMES, f), 'utf8');
			const bloc = source.slice(source.indexOf('THEME = DefinitionTheme('));
			const champ = (nom: string) => new RegExp(`${nom}="([^"]+)"`).exec(bloc)?.[1] ?? '';
			return { id: champ('id'), section: champ('section'), titre: champ('titre'), niveau: /granularite\("([a-z_]+)"/.exec(bloc)?.[1] ?? '' };
		});
}

/** The themes served as examples, by id. */
function exemples(): Map<string, unknown> {
	const fichiers = readdirSync(DOSSIER_EXEMPLES).filter((f) => f.endsWith('.json'));
	return new Map(
		fichiers.map((f) => {
			const brut = JSON.parse(readFileSync(join(DOSSIER_EXEMPLES, f), 'utf8')) as { id: string };
			return [brut.id, brut];
		})
	);
}

/** Spacing of the served sentences is set at render time (D-37): compare the words, not the spaces. */
const sansInsecables = (texte: string | null | undefined): string => (texte ?? '').replace(/[\u00a0\u202f]/g, ' ');

const SOURCE = { cle: 'georisques', nom: 'Géorisques', producteur: 'BRGM', url: 'https://www.georisques.gouv.fr', licence: 'Licence Ouverte 2.0', millesime: null, mode: 'direct' };
const ETATS = [
	{ etat: 'ok', ton: 'vigilance', chiffres: [{ id: 'n', libelle: 'Sites', valeur: 2, unite: 'count', millesime: null }] },
	{ etat: 'ok', ton: 'rassurant', chiffres: [{ id: 'd', libelle: 'Distance', valeur: 1200, unite: 'metre', millesime: null }] },
	{ etat: 'zero', ton: 'neutre', chiffres: [{ id: 'n', libelle: 'Sites', valeur: 0, unite: 'count', millesime: null }] },
	{ etat: 'manque', ton: 'info', chiffres: [], motif: 'not_available' },
	{ etat: 'panne', ton: 'info', chiffres: [], motif: 'amont_indisponible' },
	{ etat: 'non_concerne', ton: 'neutre', chiffres: [] }
] as const;

function themeDe(d: Definition, i: number, servis: Map<string, unknown>): ThemeRapport | null {
	const forme = ETATS[i % ETATS.length];
	const brut = servis.get(d.id) ?? {
		id: d.id,
		section: d.section,
		titre: d.titre,
		granularite: { niveau: d.niveau, rayon_m: d.niveau === 'rayon' ? 500 : null },
		verdict: `Lecture de ${d.titre.toLowerCase()} à cette adresse.`,
		details: { mesure: { valeur: 3, unite: 'cm' } },
		// A building or a parcel is read through its link; the API serves a favourable tone only on a
		// sure one (04.7-17), so the synthetic theme carries one, as a served theme would.
		rattachement: ['batiment', 'parcelle'].includes(d.niveau) ? { entite: d.niveau, confiance: 'sure', maillons: [{ lien: d.niveau, confiance: 'sure' }] } : null,
		sources: [SOURCE],
		pannes_partielles: [],
		...forme
	};
	return projeterTheme(brut);
}

describe('registre des thèmes du rapport (04.7-03)', () => {
	const definitions = registre();
	const servis = exemples();
	const projetes = definitions.map((d, i) => ({ id: d.id, theme: themeDe(d, i, servis) }));
	const themes = projetes.flatMap((p) => (p.theme ? [p.theme] : []));

	it('the registry is read whole: 37 themes at least, each with its id, part, title and scale', () => {
		expect(definitions.length).toBeGreaterThanOrEqual(37);
		for (const d of definitions) expect([d.id, d.section, d.titre, d.niveau].every(Boolean), JSON.stringify(d)).toBe(true);
		// The served examples of 01, 04 and 05 are among them.
		expect([...servis.keys()].every((id) => definitions.some((d) => d.id === id))).toBe(true);
		expect(servis.size).toBeGreaterThanOrEqual(6);
		// A theme the front does not know is projected like the others, never dropped.
		expect(projetes.filter((p) => !p.theme).map((p) => p.id)).toEqual([]);
	});

	it('every theme renders as a row: title, verdict, scale word, coherent status, no fabricated zero', () => {
		for (const theme of themes) {
			const { container, unmount } = render(LigneCritere, { props: { theme, niveauTitre: 4 } });
			const critere = container.querySelector(`li#critere-${theme.id}`) as HTMLElement;
			expect(critere, theme.id).not.toBeNull();
			expect(critere.querySelector('details'), theme.id).not.toBeNull();
			expect(sansInsecables(critere.querySelector('.ligne-titre')?.textContent)).toBe(sansInsecables(theme.titre));
			expect(sansInsecables(critere.querySelector('.ligne-phrase')?.textContent)).toBe(sansInsecables(theme.verdict));
			const echelle = critere.querySelector('.ech-mot')?.textContent?.trim() ?? '';
			expect(echelle, theme.id).toBe(niveauDe(theme.granularite).mot);
			expect(echelle, theme.id).not.toBe('');
			expect(critere.querySelector('.badge')?.textContent?.trim() ?? null, theme.id).toBe(statutDe(theme)?.texte ?? null);
			expect(verdictCoherent(theme), theme.id).toBe(true);
			expect(aucunZeroFabrique(theme), theme.id).toBe(true);
			unmount();
		}
	});

	it('every theme falls in one category of the page (none in « autres »), and the CSV has one row per theme', () => {
		// The page places the themes through the table of the classement; the CSV keeps one row per theme.
		const { parties, autres } = placerThemes(themes);
		expect(autres.map((t) => t.id)).toEqual([]);
		const places = parties.flatMap((p) => [...p.premiers, ...p.autres]).map((t) => t.id);
		expect([...places].sort()).toEqual(themes.map((t) => t.id).sort());
		expect(new Set(places).size).toBe(themes.length);
		expect(lignesExportRapport(themes, 'Nantes').flatMap((g) => g.rows)).toHaveLength(themes.length);
	});
});
