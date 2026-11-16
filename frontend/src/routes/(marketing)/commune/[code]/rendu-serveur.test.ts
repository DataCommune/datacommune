// @vitest-environment node
// What the server sends for a commune fiche (Search Console of 2026-11-16: 486 fiches seen as duplicates of
// another one, the texts shared by every fiche outweighing its figures). The panels, the blocks of paper
// and the definitions are the same on every commune: none of them is in the server HTML, and a reader
// without JavaScript reaches the definitions through one link. `scripts/mesure-doublons-communes.mjs`
// measures the resemblance of two served fiches; this test locks the cause.
import { describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';

// The test setup resolves `svelte` to its browser build; a server render needs the server build.
// @ts-expect-error the server entry of svelte has no declaration file of its own
vi.mock('svelte', () => import('../../../../../node_modules/svelte/src/index-server.js'));
vi.mock('$app/environment', () => ({ browser: false }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), afterNavigate: vi.fn(), invalidateAll: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/stores', async () => {
	const { readable } = await import('svelte/store');
	return { navigating: readable(null), page: readable({ url: new URL('https://site.invalid/commune/85191'), state: {} }) };
});

import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from '$lib/data/__fixtures__/commune-85191-20261012';
import { communeKpis, reperesConnus, TENDANCE_NON_PUBLIEE } from '$lib/data/commune-kpi';
import { getExplication } from '$lib/data/explications';
import type { FicheCommuneData } from './+page';
import Page from './+page.svelte';
import Definitions from './definitions/+page.svelte';

const synthese = COMMUNE_85191_OBSERVED as CommuneSynthese;
const sources = [...COMMUNE_85191_SOURCES] as SourceAttribution[];
const fiche: FicheCommuneData = { etat: 'fiche', code: '85191', synthese, sources, consulteLe: '16 novembre 2026' };
const premiere = () => reperesConnus(communeKpis(synthese, sources))[0];

describe('/commune/[code] — server render', () => {
	const { body } = render(Page, { props: { data: fiche } });

	it('witness: the fiche is served with its rows and their panels, closed', () => {
		expect(body).toContain('id="essentiel"');
		expect(body).toContain('data-field-id="revenus.revenu_median"');
		expect(body).toMatch(/<dialog[^>]*class="source-panel/);
	});

	it('serves no text of a panel, no definition and no block of paper', () => {
		expect(body).not.toContain('source-panel-entry');
		expect(body).not.toContain(getExplication('revenus.revenu_median').definition);
		expect(body).not.toContain(premiere().definition);
		expect(body).not.toContain(TENDANCE_NON_PUBLIEE);
		expect(body).not.toContain('definitions-impression');
		expect(body).not.toContain('bloc-impression');
	});

	it('links the definitions for a reader without JavaScript', () => {
		const noscript = body.match(/<noscript>([\s\S]*?)<\/noscript>/)?.[1] ?? '';
		expect(noscript).toContain('href="/commune/85191/definitions"');
	});
});

describe('/commune/[code]/definitions — server render', () => {
	it('writes every definition of the fiche, the sentence of an absence included, and is not indexed', () => {
		const { head, body } = render(Definitions, { props: { data: fiche } });
		expect(head).toContain('<meta name="robots" content="noindex, follow"');
		expect(body).toContain('Définitions de la fiche La Roche-sur-Yon');
		expect(body).toContain('href="/commune/85191"');
		expect(body).toContain(premiere().definition);
		expect(body).toContain(TENDANCE_NON_PUBLIEE);
	});

	it('says that a fiche without values has nothing to define, and links it', () => {
		const panne = { etat: 'panne', code: '85191', identite: null, detail: { statut: 503, titre: null, message: null }, retryAfter: null } as FicheCommuneData;
		const { body } = render(Definitions, { props: { data: panne } });
		expect(body).toContain('Aucune définition à afficher');
		expect(body).toContain('href="/commune/85191"');
		expect(body).not.toContain('<dl');
	});
});
