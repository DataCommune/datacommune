// The home page lists the categories and shows status badges, nothing more of the address report. Both live in
// leaf modules (2026-11-13): when `categories.ts` or `statut.ts` imports a module, or the home page reads them
// through `questions.ts` or `adresse-rapport.ts` again, the home page loads the shared chunk of the address
// report back (`explications-simples.ts` inside it, 57 KB of JS measured on the built home page).
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const lire = (chemin: string) => readFileSync(chemin, 'utf8');
const imports = (source: string) => [...source.matchAll(/^\s*import\s[^;]*?from\s+['"]([^'"]+)['"]/gm)].map((m) => m[1]);

describe("modules feuilles de l'accueil", () => {
	it.each(['src/lib/data/categories.ts', 'src/lib/data/statut.ts'])('%s n’importe aucun module', (chemin) => {
		expect(imports(lire(chemin))).toEqual([]);
	});

	it('l’accueil lit les catégories et les statuts dans les modules feuilles', () => {
		const accueil = imports(lire('src/routes/(marketing)/+page.svelte'));
		expect(accueil).toContain('$lib/data/categories');
		expect(accueil).toContain('$lib/data/statut');
		expect(accueil).not.toContain('$lib/data/questions');
		expect(accueil).not.toContain('$lib/data/adresse-rapport');
	});
});
