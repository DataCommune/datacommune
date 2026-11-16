// @vitest-environment node
// What the server renders for `/comparer` before any script runs (review of 2026-11-10, R-14): an
// effect never runs on the server, so a state the page only aligns in an effect is served at its
// initial value. The page is rendered here as SvelteKit renders it for the first request.
import { describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';

// The test setup resolves `svelte` to its browser build (components are mounted in jsdom elsewhere);
// a server render needs the server build of the lifecycle functions (`onMount` is a no-op there).
// @ts-expect-error the server entry of svelte has no declaration file of its own
vi.mock('svelte', () => import('../../../../node_modules/svelte/src/index-server.js'));
vi.mock('$app/environment', () => ({ browser: false }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), afterNavigate: vi.fn(), invalidateAll: vi.fn(), replaceState: vi.fn() }));

import type { ReponseApiComparaison } from '$lib/api/commune';
import { composeComparaison } from '$lib/data/commune-comparison';
import { lireSelection } from '$lib/data/commune-comparison-selection';
import comparaisons from '$lib/data/__fixtures__/commune-comparaison.json';
import type { ComparerData } from './+page';
import Page from './+page.svelte';

const REPONSES = comparaisons as unknown as Record<string, ReponseApiComparaison>;

function rendu(query: string, cle = 'deux_communes_hebergees'): string {
	const reponse = REPONSES[cle];
	const modele = composeComparaison(reponse.donnees, reponse.meta?.source ?? null);
	const selection = { ...lireSelection(new URLSearchParams(query)), codes: modele.colonnes.map((colonne) => colonne.code) };
	const data: ComparerData = { nature: 'commune', etat: 'prete', selection: { ...selection, statut: 'prete' }, modele };
	return render(Page, { props: { data } }).body;
}

const caseDuFiltre = (html: string) => /<input[^>]*type="checkbox"[^>]*>(?=\s*<span[^>]*>Différences seulement)/.exec(html)?.[0] ?? null;

describe('/comparer — server render', () => {
	it('witness: without `diff`, the filter is served unchecked and every row is served', () => {
		const html = rendu('communes=44991,44992');
		expect(caseDuFiltre(html)).not.toBeNull();
		expect(caseDuFiltre(html)).not.toMatch(/\schecked/);
		expect(html).toContain('data-identiques="true"');
	});

	it('R-14: `diff=1` is served checked, without the rows the API served as identical', () => {
		const html = rendu('communes=44991,44992&diff=1');
		expect(caseDuFiltre(html)).toMatch(/\schecked/);
		expect(html).not.toContain('data-identiques="true"');
		expect(html).toContain('data-identiques="false"');
	});
});
