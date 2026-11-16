// Guard of the server <title> (production bug of 2026-11-12, /commune/44109 served `<title>DataCommune</title>`).
//
// A route component holding a `bind:` on a child component is compiled by Svelte into a settle loop: the
// server renders it again on a copied renderer until the bound values stop moving. That copy is not in the
// tree of its parent, so the path of its <title> is wrong and the default <title> of the root layout wins in
// the server HTML (Svelte 5.56, `Renderer.copy` / `set_title`). The dev server did not show it, the browser
// title was right after hydration: only the built server HTML was wrong.
//
// So no route component may compile to that loop. A page passes the value down with a callback instead
// (Categorie: `ouverte` + `onbascule`); a child component may still bind inside itself, it holds no <title>.
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { compile } from 'svelte/compiler';
import { describe, expect, it } from 'vitest';

const ROUTES = join(process.cwd(), 'src/routes');

function composantsDeRoute(dossier: string): string[] {
	return readdirSync(dossier, { withFileTypes: true }).flatMap((entree) => {
		const chemin = join(dossier, entree.name);
		if (entree.isDirectory()) return composantsDeRoute(chemin);
		return /^\+(page|layout|error)\.svelte$/.test(entree.name) ? [chemin] : [];
	});
}

describe('titre au rendu serveur', () => {
	const composants = composantsDeRoute(ROUTES);

	it('finds the route components', () => {
		expect(composants.length).toBeGreaterThan(30);
	});

	it.each(composants.map((chemin) => [relative(ROUTES, chemin).replaceAll('\\', '/'), chemin]))(
		'%s binds no child component (its <title> would lose to the layout at the server)',
		(_nom, chemin) => {
			const { js } = compile(readFileSync(chemin, 'utf8'), { generate: 'server', runes: true, filename: chemin });
			expect(js.code).not.toContain('$$settled');
		}
	);
});
