import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, within } from '@testing-library/svelte';
import { tick } from 'svelte';
import type { ReponseApiComparaison } from '$lib/api/commune';
import type { CommuneSearchResult } from '$lib/api/communes';
import { composeComparaison } from '$lib/data/commune-comparison';
import { lireSelection, messageCodeHorsCouverture } from '$lib/data/commune-comparison-selection';
import {
	MESSAGE_ACCES_REFUSE,
	messageLimite,
	TITRE_ACCES_REFUSE,
	TITRE_DEMANDE_REFUSEE,
	TITRE_LIMITE
} from '$lib/data/panne';
import comparaisons from '$lib/data/__fixtures__/commune-comparaison.json';
import adressesServies from '$lib/data/__fixtures__/adresse-comparaison.json';
import { composeComparaisonAdresses, composeComparaisonDocuments } from '$lib/data/commune-comparison';
import { projeterComparaisonAdresses } from '$lib/api/adresses';
import { projeterComparaisonDocuments } from '$lib/api/documents-dpe';
import documentsServis from '$lib/data/__fixtures__/dpe-comparaison.json';
import { lireSelectionNature } from '$lib/data/commune-comparison-selection';

// `/comparer` (D-48, D-49): the load reads the link — ONE call to /v1/communes/comparaison for 2 to 4
// communes, the referential for one, nothing for none — and the page acts only by writing a new
// link. Invariants kept from the former loader: an outage is one state for the whole comparison,
// nothing partial is shown, and a reload has one call in flight at most.

const runtime = vi.hoisted(() => ({ goto: vi.fn(), invalidateAll: vi.fn(), replaceState: vi.fn(), search: vi.fn(), apres: vi.fn() }));
vi.mock('$app/environment', () => ({ browser: true }));
vi.mock('$app/navigation', () => ({
	goto: (...args: unknown[]) => runtime.goto(...args),
	afterNavigate: (callback: (navigation: unknown) => void) => runtime.apres(callback),
	invalidateAll: (...args: unknown[]) => runtime.invalidateAll(...args),
	replaceState: (...args: unknown[]) => runtime.replaceState(...args)
}));
vi.mock('$lib/api/communes', async (original) => ({
	...(await original<typeof import('$lib/api/communes')>()),
	searchCommunes: (...args: unknown[]) => runtime.search(...args)
}));

import { load, type ComparerData } from './+page';
import Page from './+page.svelte';
import { COLONNES_APERCU, EXEMPLES_COMMUNES, LIGNES_APERCU, PROMESSE_COMMUNES, TITRE_APERCU, TITRE_EXEMPLES } from '$lib/data/comparateur-textes';
import pageSource from './+page.svelte?raw';
import {
	addressComparisonSelection,
	comparisonSelection,
	derniereNature,
	documentComparisonSelection
} from '$lib/stores/comparison-selection.svelte';

const REPONSES = comparaisons as unknown as Record<string, ReponseApiComparaison>;

// ---------------------------------------------------------------------------------------------
// Load
// ---------------------------------------------------------------------------------------------

type Reponse = { status: number; body: unknown; headers?: Record<string, string> };

function source(reponses: Record<string, Reponse>) {
	const appels: URL[] = [];
	const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
		const url = new URL(String(input), 'http://api.test');
		appels.push(url);
		const reponse = reponses[url.pathname];
		if (!reponse) throw new TypeError(`unexpected request ${url.pathname}`);
		return new Response(JSON.stringify(reponse.body), {
			status: reponse.status,
			headers: { 'Content-Type': 'application/json', ...reponse.headers }
		});
	});
	return { fetchImpl: fetchImpl as unknown as typeof fetch, appels };
}

async function charger(query: string, reponses: Record<string, Reponse> = {}) {
	const { fetchImpl, appels } = source(reponses);
	const url = new URL(`http://localhost/comparer${query}`);
	const data = (await load({ url, fetch: fetchImpl } as never)) as ComparerData;
	return { data, appels };
}

const comparaison = (body: unknown): Record<string, Reponse> => ({ '/v1/communes/comparaison': { status: 200, body } });

describe('/comparer load', () => {
	it('0 commune: the empty comparator, without any request', async () => {
		const { data, appels } = await charger('');
		expect(data).toMatchObject({ etat: 'vide', selection: { codes: [], messages: [] } });
		expect(appels).toEqual([]);
	});

	it('1 commune: its name from the referential, never a comparison call', async () => {
		const { data, appels } = await charger('?communes=44109', {
			'/v1/communes': { status: 200, body: { statut: 'ok', donnees: { results: [{ code_insee: '44109', nom: 'Nantes', departement: '44' }] } } }
		});
		expect(data).toMatchObject({ etat: 'une', commune: { code: '44109', nom: 'Nantes' } });
		expect(appels.map((url) => url.pathname)).toEqual(['/v1/communes']);
		expect(appels[0].searchParams.get('code_insee')).toBe('44109');
	});

	// 04.7-09 review: the referential lists an arrondissement only on request.
	it('1 arrondissement alone: the referential is read with inclure_arrondissements and names it', async () => {
		const { data, appels } = await charger('?communes=75112', {
			'/v1/communes': { status: 200, body: { statut: 'ok', donnees: { results: [{ code_insee: '75112', nom: 'Paris 12e Arrondissement', departement: '75', type_zone: 'arrondissement', commune_parent: '75056' }] } } }
		});
		expect(appels[0].searchParams.get('inclure_arrondissements')).toBe('true');
		expect(data).toMatchObject({ etat: 'une', commune: { code: '75112', nom: 'Paris 12e Arrondissement' } });
	});

	it('1 plain commune: the referential request has no inclure_arrondissements', async () => {
		const { appels } = await charger('?communes=44109', {
			'/v1/communes': { status: 200, body: { statut: 'ok', donnees: { results: [{ code_insee: '44109', nom: 'Nantes', departement: '44' }] } } }
		});
		expect(appels[0].searchParams.has('inclure_arrondissements')).toBe(false);
	});

	it('1 commune while the referential is down: the commune stays, named by its code', async () => {
		const { data } = await charger('?communes=44109', { '/v1/communes': { status: 503, body: {} } });
		expect(data).toMatchObject({ etat: 'une', commune: { code: '44109', nom: null } });
	});

	it('1 commune unknown to the referential: « Lien altéré », nothing left to compare', async () => {
		const { data } = await charger('?communes=44999', {
			'/v1/communes': { status: 200, body: { statut: 'ok', donnees: { results: [] } } }
		});
		expect(data).toMatchObject({ etat: 'vide', selection: { codes: [], messages: ['Le lien contenait un code INSEE inconnu (44999) ; il a été écarté.'] } });
	});

	// 04.1-06 report, UI-031 (D-123): a collectivity out of coverage is named apart from an altered link.
	it('a collectivity out of coverage is said « hors couverture », never « Lien altéré », and leaves the link', async () => {
		const reponse = structuredClone(REPONSES.trois_communes_dont_hors_couverture);
		reponse.donnees.codes_hors_couverture = [
			{ code_insee: '97701', hebergement: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: ['44', '85'] } }
		];
		const { data } = await charger('?communes=44991,44992,85991,97701', comparaison(reponse));
		if (data.etat !== 'prete') throw new Error(`expected prete, got ${data.etat}`);
		expect(data.modele.colonnes.map((colonne) => colonne.code)).toEqual(['44991', '44992', '85991']);
		expect(data.selection.codes).toEqual(['44991', '44992', '85991']);
		expect(data.selection.messages).toEqual(['Le lien contenait un code INSEE inconnu (44999) ; il a été écarté.']);
		expect(data.selection.horsCouverture).toEqual([
			"97701 : collectivité d'outre-mer, hors du périmètre couvert par ce site ; elle n'a pas de colonne."
		]);
	});

	it('2 to 4 communes: exactly one comparison call, in link order, composed without computation', async () => {
		const reponse = REPONSES.trois_communes_dont_hors_couverture;
		const { data, appels } = await charger('?communes=44991,44992,85991,44999&diff=1', comparaison(reponse));
		expect(appels).toHaveLength(1);
		expect(appels[0].pathname).toBe('/v1/communes/comparaison');
		expect(appels[0].searchParams.get('codes')).toBe('44991,44992,85991,44999');
		if (data.etat !== 'prete') throw new Error(`expected prete, got ${data.etat}`);
		expect(data.modele.colonnes.map((colonne) => colonne.code)).toEqual(['44991', '44992', '85991']);
		// The code the referential does not know comes back from the API, worded « Lien altéré ».
		expect(data.selection).toMatchObject({
			statut: 'prete', codes: ['44991', '44992', '85991'], differences: true,
			messages: ['Le lien contenait un code INSEE inconnu (44999) ; il a été écarté.']
		});
		const servi = reponse.donnees.criteres.find((critere) => critere.cle === 'revenu_median')!;
		const ligne = data.modele.groupes.flatMap((groupe) => groupe.lignes).find((item) => item.cle === 'revenu_median')!;
		expect(ligne.cellules[1].ecart).toEqual({ kind: 'ecart', texte: `+${servi.cellules[1].ecart!.valeur.toLocaleString('fr-FR')} €/UC/an par rapport à Ancienne` });
	});

	it('the altered link messages come before the call, which receives only the kept codes', async () => {
		const { data, appels } = await charger('?communes=44991,44991,abc,44992', comparaison(REPONSES.deux_communes_hebergees));
		expect(appels[0].searchParams.get('codes')).toBe('44991,44992');
		expect(data.selection.messages).toEqual([
			'Le lien contenait deux fois la même commune (44991) ; le doublon a été écarté.',
			'Le lien contenait un code INSEE inconnu (ABC) ; il a été écarté.'
		]);
	});

	it.each([503, 500, 502])('HTTP %i: one outage state, no partial column', async (status) => {
		const { data, appels } = await charger('?communes=44991,44992', { '/v1/communes/comparaison': { status, body: { detail: 'x' } } });
		expect(data).toEqual(
			expect.objectContaining({
				etat: 'panne',
				selection: expect.objectContaining({ codes: ['44991', '44992'] }),
				detail: { statut: status, titre: null, message: null }
			})
		);
		expect(appels).toHaveLength(1);
	});

	it.each([409, 422])('HTTP %i: the service answered, a refused request with its status (03-17 B C1)', async (status) => {
		const { data } = await charger('?communes=44991,44992', { '/v1/communes/comparaison': { status, body: { detail: 'texte serveur' } } });
		if (data.etat !== 'panne') throw new Error(`expected panne, got ${data.etat}`);
		expect(data.detail).toEqual({
			statut: status,
			titre: TITRE_DEMANDE_REFUSEE,
			message: `La demande n'a pas pu être traitée (erreur ${status}).`
		});
	});

	// 04.7-09: an arrondissement is accepted; a commune against its own arrondissement is a 422 of the API.
	it('two arrondissements are compared: one call, both codes sent as typed', async () => {
		const reponse = structuredClone(REPONSES.deux_communes_hebergees);
		const { data, appels } = await charger('?communes=75112,75113', comparaison(reponse));
		expect(appels).toHaveLength(1);
		expect(appels[0].searchParams.get('codes')).toBe('75112,75113');
		expect(data.etat).toBe('prete');
	});

	it('a commune against its own arrondissement (422): said clearly, with the two zones to choose from', async () => {
		const { data } = await charger('?communes=75056,75112', {
			'/v1/communes/comparaison': { status: 422, body: { detail: 'texte serveur' } }
		});
		if (data.etat !== 'panne') throw new Error(`expected panne, got ${data.etat}`);
		expect(data.detail.statut).toBe(422);
		expect(data.detail.titre).toBe('Une commune et son arrondissement ne se comparent pas');
		expect(data.detail.message).toContain('Paris et son 12e arrondissement ne se comparent pas');
		expect(data.detail.message).not.toContain('texte serveur');
		expect(data.conflit).toEqual({ commune: '75056', arrondissement: '75112' });
	});

	it('HTTP 429 of the shared key (Retry-After): the rate-limit sentence, not an outage (WR-01)', async () => {
		const { data } = await charger('?communes=44991,44992', {
			'/v1/communes/comparaison': { status: 429, body: { detail: 'texte serveur' }, headers: { 'Retry-After': '60' } }
		});
		if (data.etat !== 'panne') throw new Error(`expected panne, got ${data.etat}`);
		expect(data.detail).toEqual({ statut: 429, titre: TITRE_LIMITE, message: messageLimite(60) });
	});

	it('HTTP 401: a refused access, worded by the client (WR-01)', async () => {
		const { data } = await charger('?communes=44991,44992', {
			'/v1/communes/comparaison': { status: 401, body: { detail: 'texte serveur' } }
		});
		if (data.etat !== 'panne') throw new Error(`expected panne, got ${data.etat}`);
		expect(data.detail).toEqual({ statut: 401, titre: TITRE_ACCES_REFUSE, message: MESSAGE_ACCES_REFUSE });
	});

	it('a response that cannot be composed is an outage, never a half table', async () => {
		const casse = structuredClone(REPONSES.deux_communes_hebergees);
		casse.donnees.criteres[0].cellules.reverse();
		const { data } = await charger('?communes=44991,44992', comparaison(casse));
		expect(data.etat).toBe('panne');
	});
});

// R-21 (review of 2026-11-10; plan 04.7-10, decision 11): a comparison whose source failed is rendered
// whole but must not be answered 200. The load posts the outage marker through the `setHeaders` of its
// event, and `hooks.server.ts` answers 503 with `Retry-After`. A refusal the visitor has to solve by
// changing the selection (a commune against its own arrondissement) is not an outage.
describe('/comparer load — an outage is marked for the 503 (R-21)', () => {
	async function chargerAvecEntetes(query: string, reponses: Record<string, Reponse>) {
		const { fetchImpl } = source(reponses);
		const setHeaders = vi.fn();
		const data = (await load({ url: new URL(`http://localhost/comparer${query}`), fetch: fetchImpl, setHeaders } as never)) as ComparerData;
		return { data, setHeaders };
	}
	const PANNE = { 'x-dc-etat-page': 'panne' };

	it.each([
		['communes', '?communes=44991,44992', '/v1/communes/comparaison'],
		['addresses', '?adresses=85191_0120_00012,85191_0120_00014', '/v1/adresses/comparaison'],
		['DPE documents', '?dpe=2644E8000030X,2385E3344905Q', '/v1/dpe/comparaison']
	])('%s: a 503 posts the marker once', async (_nature, query, chemin) => {
		const { data, setHeaders } = await chargerAvecEntetes(query, { [chemin]: { status: 503, body: { detail: 'x' } } });
		expect(data.etat).toBe('panne');
		expect(setHeaders).toHaveBeenCalledTimes(1);
		expect(setHeaders).toHaveBeenCalledWith(PANNE);
	});

	it('the served Retry-After travels with the marker (rate limit of the shared key)', async () => {
		const { setHeaders } = await chargerAvecEntetes('?communes=44991,44992', {
			'/v1/communes/comparaison': { status: 429, body: { detail: 'x' }, headers: { 'Retry-After': '45' } }
		});
		expect(setHeaders).toHaveBeenCalledWith({ ...PANNE, 'x-dc-retry-after': '45' });
	});

	it('a response that cannot be composed is marked too', async () => {
		const casse = structuredClone(REPONSES.deux_communes_hebergees);
		casse.donnees.criteres[0].cellules.reverse();
		const { setHeaders } = await chargerAvecEntetes('?communes=44991,44992', comparaison(casse));
		expect(setHeaders).toHaveBeenCalledWith(PANNE);
	});

	it('a commune against its own arrondissement is a refusal, never marked as an outage', async () => {
		const { data, setHeaders } = await chargerAvecEntetes('?communes=75056,75112', {
			'/v1/communes/comparaison': { status: 422, body: { detail: 'x' } }
		});
		expect(data.etat).toBe('panne');
		expect(setHeaders).not.toHaveBeenCalled();
	});

	it('a served comparison, an empty comparator and a single commune post nothing', async () => {
		const servie = await chargerAvecEntetes('?communes=44991,44992', comparaison(structuredClone(REPONSES.deux_communes_hebergees)));
		expect(servie.data.etat).toBe('prete');
		expect(servie.setHeaders).not.toHaveBeenCalled();
		expect((await chargerAvecEntetes('', {})).setHeaders).not.toHaveBeenCalled();
		// The referential down: the commune stays, named by its code; the page is usable.
		const seule = await chargerAvecEntetes('?communes=44109', { '/v1/communes': { status: 503, body: {} } });
		expect(seule.data.etat).toBe('une');
		expect(seule.setHeaders).not.toHaveBeenCalled();
	});
});

// ---------------------------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------------------------

const NOMS: Record<string, string> = { '44991': 'Ancienne', '44992': 'Courante', '85991': 'Couverture inconnue' };

/**
 * The frozen three-commune response, columns taken in `codes` order. SYNTHETIC for the page only:
 * the page never reads a gap it did not receive, so the verdicts of a reordered column are
 * replaced by a served reason (the API would serve new gaps for the new first column).
 */
function reponsePour(codes: string[]): ReponseApiComparaison {
	const base = structuredClone(REPONSES.trois_communes_dont_hors_couverture);
	const index = codes.map((code) => base.donnees.communes.findIndex((commune) => commune.code_insee === code));
	base.donnees.communes = index.map((i) => base.donnees.communes[i]);
	base.donnees.criteres = base.donnees.criteres.map((critere) => ({
		...critere,
		cellules: index.map((i, position) => {
			const cellule = critere.cellules[i];
			if (position === 0) return { ...cellule, ecart: null, motif_non_calcul: null, identique_a_la_reference: null };
			if (cellule.ecart || cellule.motif_non_calcul) return cellule;
			return { ...cellule, motif_non_calcul: 'donnee_absente' as const, identique_a_la_reference: false };
		})
	}));
	base.donnees.codes_inconnus = [];
	return base;
}

function donnees(lien: string, extra: Partial<ComparerData> = {}): ComparerData {
	const selection = lireSelection(new URL(lien, 'http://localhost').searchParams);
	// A rendering fixture may mount the outage without `detail`: the page reads it optionally.
	if (extra.etat === 'panne') return { etat: 'panne', selection, detail: extra.detail } as ComparerData;
	if (selection.statut === 'vide') return { etat: 'vide', selection, ...extra } as ComparerData;
	if (selection.statut === 'une') {
		return { etat: 'une', selection, commune: { code: selection.codes[0], nom: NOMS[selection.codes[0]] ?? null }, ...extra } as ComparerData;
	}
	const reponse = selection.codes.join(',') === '44991,44992' ? structuredClone(REPONSES.deux_communes_hebergees) : reponsePour(selection.codes);
	return { etat: 'prete', selection, modele: composeComparaison(reponse.donnees, reponse.meta?.source ?? null), ...extra } as ComparerData;
}

/** Renders the page; `goto` loads the new link and re-renders, like the router would. */
function ouvrir(lien: string, extra: Partial<ComparerData> = {}) {
	const vue = render(Page, { data: donnees(lien, extra) });
	runtime.goto.mockImplementation(async (suivant: string) => {
		await vue.rerender({ data: donnees(suivant) });
	});
	return vue;
}

/** What the router does after a navigation to `lien`: it calls the afterNavigate callbacks. */
async function apresNavigation(lien: string) {
	const navigation = { to: { url: new URL(lien, 'http://localhost') } };
	for (const [callback] of runtime.apres.mock.calls) (callback as (navigation: unknown) => void)(navigation);
	await Promise.resolve();
}

const annonce = (vue: { container: HTMLElement }) =>
	vue.container.querySelector('.comparer-annonce')?.textContent?.trim() ?? '';

function commune(code: string, nom: string): CommuneSearchResult {
	return {
		code_insee: code, nom, code_postal: null, departement: code.slice(0, 2), population: 100,
		population_annee: null, population_source: 'geo_communes', hebergement: { etat: 'heberge', motif: null, departements: [] }
	};
}

beforeEach(() => {
	sessionStorage.clear();
	comparisonSelection.replace([]);
	for (const mock of Object.values(runtime)) mock.mockReset();
	runtime.invalidateAll.mockResolvedValue(undefined);
	runtime.search.mockResolvedValue({ donnees: [] });
});
afterEach(() => {
	vi.useRealTimers();
	vi.restoreAllMocks();
});

describe('/comparer page — selection kept in the link and the tab storage', () => {
	it('the description of the page names the three natures (04-DECISIONS-TEXTES row 54)', () => {
		ouvrir('/comparer?communes=44992,44991');
		expect(document.head.querySelector('meta[name="description"]')?.getAttribute('content')).toBe(
			'Comparer des communes, des adresses ou des documents DPE : chaque valeur garde sa période, sa définition et sa source.'
		);
	});

	// T1 (inventory of 2026-11-10): a shared link of a comparison had no card. The page mounts the
	// `MetaPartage` of the socle: the card carries the link of the selection; the page stays out of the
	// index, so it has no canonical.
	it('T1: a shared comparison has its card, with the link of the selection, and stays out of the index', () => {
		ouvrir('/comparer?communes=44992,44991&diff=1');
		const meta = (selecteur: string) => document.head.querySelector(selecteur)?.getAttribute('content') ?? null;
		expect(meta('meta[property="og:title"]')).toBe(document.title);
		expect(meta('meta[name="twitter:card"]')).toBe('summary_large_image');
		expect(meta('meta[property="og:image"]')).toMatch(/^https:\/\//);
		expect(meta('meta[property="og:url"]')).toMatch(/\/comparer\?communes=44992,44991$/);
		expect(meta('meta[name="robots"]')).toBe('noindex, follow');
		expect(document.head.querySelectorAll('meta[name="robots"]')).toHaveLength(1);
		expect(document.head.querySelector('link[rel="canonical"]')).toBeNull();
	});

	it('the link comes first: the tab storage takes its codes, in its order', async () => {
		comparisonSelection.replace(['75056']);
		ouvrir('/comparer?communes=44992,44991');
		await apresNavigation('/comparer?communes=44992,44991');
		expect(JSON.parse(sessionStorage.getItem('dc:comparaison')!)).toEqual(['44992', '44991']);
		expect(runtime.goto).not.toHaveBeenCalled();
	});

	it('opened without `communes` (header link, typed address), the page reads the tab selection back', async () => {
		comparisonSelection.replace(['44991', '44992']);
		ouvrir('/comparer');
		await apresNavigation('/comparer');
		expect(runtime.goto).toHaveBeenCalledWith('/comparer?communes=44991,44992', { replaceState: true });
		expect(comparisonSelection.codes).toEqual(['44991', '44992']);
	});

	it('opened without `communes` and an empty tab selection, the empty comparator stays', async () => {
		ouvrir('/comparer');
		await apresNavigation('/comparer');
		expect(runtime.goto).not.toHaveBeenCalled();
	});

	it('removing the last commune empties the tab selection: the empty comparator does not come back to it', async () => {
		comparisonSelection.replace(['44991']);
		const vue = ouvrir('/comparer?communes=44991');
		await fireEvent.click(vue.getByRole('button', { name: 'Retirer Ancienne' }));
		await vi.waitFor(() => expect(runtime.goto).toHaveBeenCalledWith('/comparer', { noScroll: true, keepFocus: true }));
		expect(comparisonSelection.codes).toEqual([]);
		await apresNavigation('/comparer');
		expect(runtime.goto).toHaveBeenCalledTimes(1);
	});

	it('every action writes the tab selection with the link', async () => {
		const vue = ouvrir('/comparer?communes=44991,44992,85991');
		await fireEvent.click(vue.getByRole('button', { name: 'Placer Couverture inconnue en premier' }));
		await vi.waitFor(() => expect(comparisonSelection.codes).toEqual(['85991', '44991', '44992']));
	});

	it('an altered link is announced and rewritten in place, without a new load', async () => {
		const vue = ouvrir('/comparer?communes=44991,44991,44992');
		await tick();
		// The router calls the afterNavigate callbacks of the first navigation, then starts.
		expect(runtime.replaceState).not.toHaveBeenCalled();
		await apresNavigation('/comparer?communes=44991,44991,44992');
		const lien = within(vue.getByRole('heading', { name: 'Lien altéré' }).parentElement!);
		expect(lien.getByText('Le lien contenait deux fois la même commune (44991) ; le doublon a été écarté.')).toBeTruthy();
		expect(runtime.replaceState).toHaveBeenCalledWith('/comparer?communes=44991,44992', {});
		expect(runtime.goto).not.toHaveBeenCalled();
	});

	it('a collectivity out of coverage gets its own notice, not « Lien altéré », and leaves the link', async () => {
		const horsCouverture = [messageCodeHorsCouverture('97701')];
		// The load sets 97701 aside: the page receives the two columns and the notice.
		const lue = donnees('/comparer?communes=44991,44992');
		const vue = render(Page, { data: { ...lue, selection: { ...lue.selection, horsCouverture } } });
		await apresNavigation('/comparer?communes=44991,44992,97701');
		const notice = vue.getByRole('heading', { name: 'Hors couverture' }).parentElement!;
		// The exact text, its no-break spaces included (getByText would collapse them).
		expect([...notice.querySelectorAll('p')].map((p) => p.textContent)).toEqual(horsCouverture);
		expect(vue.queryByRole('heading', { name: 'Lien altéré' })).toBeNull();
		expect(runtime.replaceState).toHaveBeenCalledWith('/comparer?communes=44991,44992', {});
	});

	it('renders the contract of the empty comparator and of a single commune, without a table', async () => {
		const vide = ouvrir('/comparer');
		expect(vide.getByRole('heading', { level: 2, name: 'Choisissez deux communes à comparer' })).toBeTruthy();
		expect(vide.getByText('Ajoutez-les ici ou depuis une fiche.')).toBeTruthy();
		expect(vide.queryByRole('region', { name: 'Tableau de comparaison' })).toBeNull();
		vide.unmount();
		const une = ouvrir('/comparer?communes=44991');
		expect(une.getByRole('link', { name: 'Ancienne' })).toHaveAttribute('href', '/commune/44991');
		expect(une.getByText('Ajoutez une deuxième commune pour lire les écarts.')).toBeTruthy();
		expect(une.queryByRole('region', { name: 'Tableau de comparaison' })).toBeNull();
	});

	// C8 (inventory of 2026-11-10), then the readable comparator (direction A, 2026-11-10): the empty
	// comparator of communes offers ready comparisons under its add control. Their wording lives in
	// `comparateur-textes.ts` (proposed, to be reread by Ianis). The other natures and a single commune have none.
	it('C8, T3: the empty comparator of communes offers the ready comparisons of the wording file', () => {
		const exemples = (vue: { container: HTMLElement }) => [...vue.container.querySelectorAll<HTMLAnchorElement>('.comparer-exemples a')];
		const vide = ouvrir('/comparer');
		expect(exemples(vide).map((lien) => [lien.textContent?.trim(), lien.getAttribute('href')])).toEqual(
			EXEMPLES_COMMUNES.map((exemple) => [exemple.libelle, exemple.lien])
		);
		expect(EXEMPLES_COMMUNES.length).toBeGreaterThanOrEqual(2);
		expect(vide.getByText(TITRE_EXEMPLES)).toBeTruthy();
		// No commune with arrondissements: its rents and vacancy are « Non renseigné » at the commune code.
		for (const exemple of EXEMPLES_COMMUNES) expect(exemple.lien).not.toMatch(/\b(75056|69123|13055)\b/);
		vide.unmount();
		const une = ouvrir('/comparer?communes=44991');
		expect(exemples(une)).toHaveLength(0);
		une.unmount();
		const adresses = render(Page, { data: { nature: 'adresse', etat: 'vide', selection: lireSelection(new URLSearchParams()) } as ComparerData });
		expect(exemples(adresses)).toHaveLength(0);
	});

	// T1, T2, T4 (proposed on 2026-11-10): the empty comparator of communes says what one gets in one line, then
	// shows the shape of the result beside the add control; the sentence of method becomes the caption of that
	// preview and the surtitle leaves this state. The other natures keep their header as it was.
	it('T1, T2, T4: the empty comparator of communes promises, then previews the shape of the result', () => {
		const vide = ouvrir('/comparer');
		// Like every sentence of the main, the promise spells out its first acronym (D-31).
		expect(vide.container.querySelector('.comparer-promesse')?.textContent?.replace(' (diagnostic de performance énergétique)', '')).toBe(PROMESSE_COMMUNES);
		expect(vide.queryByText('Comparer sans changer le sens des données')).toBeNull();
		const apercu = vide.container.querySelector('.comparer-apercu')!;
		expect(apercu.querySelector('.comparer-apercu-titre')?.textContent).toBe(TITRE_APERCU);
		expect([...apercu.querySelectorAll('thead th')].map((th) => th.textContent?.trim()).filter(Boolean)).toEqual([...COLONNES_APERCU]);
		expect([...apercu.querySelectorAll('tbody th')].map((th) => th.textContent?.trim())).toEqual([...LIGNES_APERCU]);
		// A shape, never a value: no figure is written in the preview.
		expect(apercu.querySelector('table')?.textContent).not.toMatch(/\d/);
		expect(apercu.querySelector('table')?.getAttribute('aria-hidden')).toBe('true');
		expect(apercu.textContent).toContain('Les valeurs gardent leur période, leur définition et leur source. Aucun score global.');
		vide.unmount();
		const une = ouvrir('/comparer?communes=44991');
		expect(une.container.querySelector('.comparer-apercu')).toBeNull();
		expect(une.getByText('Comparer sans changer le sens des données')).toBeTruthy();
		une.unmount();
		const adresses = render(Page, { data: { nature: 'adresse', etat: 'vide', selection: lireSelection(new URLSearchParams()) } as ComparerData });
		expect(adresses.container.querySelector('.comparer-apercu')).toBeNull();
		expect(adresses.getByText('Comparer sans changer le sens des données')).toBeTruthy();
		expect(adresses.queryByText(PROMESSE_COMMUNES)).toBeNull();
	});

	// T6: beyond 720 px the title of a comparison of communes already names them; the line that repeats them
	// is marked, and drawn on a phone only (where the title is the short one).
	it('T6: the line naming the compared communes is marked as a repetition of the title, for communes only', () => {
		const communes = ouvrir('/comparer?communes=44991,44992');
		expect(communes.container.querySelector('.comparer-objets')?.classList.contains('comparer-objets-redite')).toBe(true);
		expect(pageSource).toMatch(/@media \(min-width: 721px\) \{\s*\.comparer-objets-redite \{\s*display: none;/);
	});

	it('shows the header, the reading note and the table for 2 to 4 communes', () => {
		const vue = ouvrir('/comparer?communes=44991,44992,85991');
		expect(vue.getByText('Comparer sans changer le sens des données')).toBeTruthy();
		// UI-SPEC v4 § 6.4 (04.8-05): a comparison of communes is titled by their names, the sentence of
		// the mock-up under it; the method sentences stay, folded with the reading note.
		expect(vue.container.querySelector('h1 .titre-large')?.textContent).toBe('Comparer Ancienne, Courante et Couverture inconnue');
		expect(vue.getByText('Les valeurs gardent leur période, leur définition et leur source. Aucun score global.')).toBeTruthy();
		expect(vue.getByRole('heading', { name: 'Lire les écarts' })).toBeTruthy();
		expect(vue.getByRole('region', { name: 'Tableau de comparaison' })).toBeTruthy();
	});

	it('B5 order: title, compared objects, « Modifier la sélection » folded, the table, then the folded reference', () => {
		const vue = ouvrir('/comparer?communes=44991,44992,85991');
		const titre = vue.container.querySelector('h1')!;
		expect(titre.querySelector('.titre-large')?.textContent).toBe('Comparer Ancienne, Courante et Couverture inconnue');
		const objets = vue.container.querySelector('.comparer-objets')!;
		expect(objets.textContent?.replace(/\s+/g, ' ').trim()).toBe('3 communes : Ancienne, Courante, Couverture inconnue');
		const modifier = vue.getByText('Modifier la sélection').closest('summary')!;
		const pli = modifier.closest('details') as HTMLDetailsElement;
		expect(pli.tagName).toBe('DETAILS');
		expect(pli.open).toBe(false);
		const region = vue.getByRole('region', { name: 'Tableau de comparaison' });
		const reference = vue.getByRole('heading', { level: 2, name: 'Référence des écarts : Ancienne' });
		const ordre = [titre, objets, modifier, region, reference];
		for (let i = 1; i < ordre.length; i++) {
			expect(ordre[i - 1].compareDocumentPosition(ordre[i]) & Node.DOCUMENT_POSITION_FOLLOWING, `block ${i}`).toBeTruthy();
		}
		// The add control and the filter live in the folded panel; sharing the link stays in view.
		expect(pli.contains(vue.getByRole('button', { name: 'Ajouter une commune' }))).toBe(true);
		expect(pli.contains(vue.getByRole('checkbox', { name: 'Différences seulement' }))).toBe(true);
		expect(pli.contains(vue.getByRole('button', { name: 'Copier le lien' }))).toBe(false);
		// The nature switch stays before the table, outside the panel.
		const nav = vue.getByRole('navigation', { name: 'Nature des objets comparés' });
		expect(nav.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(pli.contains(nav)).toBe(false);
	});

	// Same shape as R-06 (review of 2026-11-10): `bind:open` writes the initial state on the server-rendered
	// `details` when the page hydrates, closing a fold the reader opened while the scripts were loading.
	// The page reads the state of the served element first. The behaviour itself (scripts withheld, click,
	// scripts released) is locked by `comparateur-revue.spec.ts`; this case locks the reading.
	it('a fold the reader opened before the hydration starts open', () => {
		const servis = ['comparer-modifier', 'comparer-lecture'].map((id) => {
			const pli = document.createElement('details');
			pli.id = id;
			pli.open = true;
			document.body.prepend(pli);
			return pli;
		});
		try {
			const vue = ouvrir('/comparer?communes=44991,44992,85991');
			expect((vue.getByText('Modifier la sélection').closest('details') as HTMLDetailsElement).open).toBe(true);
			expect((vue.getByRole('heading', { level: 2, name: 'Référence des écarts : Ancienne' }).closest('details') as HTMLDetailsElement).open).toBe(true);
		} finally {
			for (const pli of servis) pli.remove();
		}
	});

	it('B5: with fewer than two objects the panel « Modifier la sélection » is open, so adding is in view', () => {
		const vide = ouvrir('/comparer');
		expect((vide.getByText('Modifier la sélection').closest('details') as HTMLDetailsElement).open).toBe(true);
		vide.unmount();
		const une = ouvrir('/comparer?communes=44991');
		expect((une.getByText('Modifier la sélection').closest('details') as HTMLDetailsElement).open).toBe(true);
		expect(une.container.querySelector('.comparer-objets')).toBeNull();
	});

	// Readability (Ianis, 2026-11-10; diagnosis in the internal note 2026-11-10-comparateur-lisibilite): with
	// fewer than two objects nothing is selected to « modify ». The fold stays in the page, open, but is marked
	// alone (its summary and its panel are not drawn), and what to do is said BEFORE the add control.
	it('readability: with fewer than two objects the fold is marked alone and the state is said before the add control', () => {
		const avant = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
		const vide = ouvrir('/comparer');
		expect(vide.container.querySelector('details.comparer-modifier')?.classList.contains('comparer-modifier-seul')).toBe(true);
		expect(avant(vide.getByRole('heading', { level: 2, name: 'Choisissez deux communes à comparer' }), vide.getByRole('button', { name: 'Ajouter une commune' }))).toBe(true);
		vide.unmount();
		const une = ouvrir('/comparer?communes=44991');
		expect(une.container.querySelector('details.comparer-modifier')?.classList.contains('comparer-modifier-seul')).toBe(true);
		expect(avant(une.getByText('Ajoutez une deuxième commune pour lire les écarts.'), une.getByRole('button', { name: 'Ajouter une commune' }))).toBe(true);
		expect(avant(une.getByRole('button', { name: 'Ajouter une commune' }), une.getByRole('button', { name: 'Copier le lien' }))).toBe(true);
		une.unmount();
		const deux = ouvrir('/comparer?communes=44991,44992');
		expect(deux.container.querySelector('details.comparer-modifier')?.classList.contains('comparer-modifier-seul')).toBe(false);
	});

	// 04.5-09 (B5, UI-012): the reference and the method are folded AFTER the values, their text
	// unchanged; this replaces the former presentation assertion « the reference precedes the table ».
	it('names the first column as the gap reference, folded after the table, and links only to displayed groups', async () => {
		const vue = ouvrir('/comparer?communes=44991,44992,85991');
		const region = vue.getByRole('region', { name: 'Tableau de comparaison' });
		const reference = vue.getByRole('heading', { level: 2, name: 'Référence des écarts : Ancienne' });
		expect(region.compareDocumentPosition(reference) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		const pli = reference.closest('details')!;
		expect(pli.open).toBe(false);
		expect(reference.closest('summary')?.parentElement).toBe(pli);
		pli.open = true;
		const aide = vue.getByText(/change la référence utilisée pour lire les écarts\. L’ordre ne constitue pas un classement\./);
		expect(aide.textContent).toBe('« Placer en premier » change la référence utilisée pour lire les écarts. L’ordre ne constitue pas un classement.');
		expect(pli.contains(aide)).toBe(true);
		expect(pli.contains(vue.getByRole('heading', { name: 'Lire les écarts' }))).toBe(true);
		expect(vue.getByRole('button', { name: 'Placer Courante en premier' })).toHaveAccessibleDescription(aide.textContent!);

		const sommaire = vue.getByRole('navigation', { name: 'Accès aux groupes de comparaison' });
		const liens = within(sommaire).getAllByRole('link');
		const groupes = [...vue.container.querySelectorAll('tbody[data-groupe]')];
		const titres = groupes.map((groupe) => groupe.querySelector('th[scope="rowgroup"]'));
		expect(liens.map((lien) => lien.getAttribute('href'))).toEqual(titres.map((titre) => `#${titre?.id}`));
		await fireEvent.click(liens[0]);
		expect(document.activeElement).toBe(titres[0]);
	});

	it('omits a group shortcut when Differences only leaves that group empty', () => {
		const lien = '/comparer?communes=44991,44992&diff=1';
		const data = donnees(lien);
		if (data.etat !== 'prete') throw new Error('comparison fixture must be ready');
		const [premier, ...autres] = data.modele.groupes;
		const titreMasque = premier.titre;
		data.modele = {
			...data.modele,
			groupes: [{ ...premier, lignes: premier.lignes.map((ligne) => ({ ...ligne, identiques: true })) }, ...autres]
		};
		const vue = render(Page, { data });
		const sommaire = vue.getByRole('navigation', { name: 'Accès aux groupes de comparaison' });

		expect(within(sommaire).queryByRole('link', { name: titreMasque })).toBeNull();
		expect(vue.container.querySelector(`tbody[data-groupe="${premier.cle}"]`)).toBeNull();
	});
});

describe('/comparer page — column actions', () => {
	it('« Retirer » writes the link without the commune and puts focus on the neighbouring column', async () => {
		const vue = ouvrir('/comparer?communes=44991,44992,85991');
		await fireEvent.click(vue.getByRole('button', { name: 'Retirer Courante' }));
		await vi.waitFor(() => expect(annonce(vue)).toBe('Courante retirée de la comparaison.'));
		expect(runtime.goto).toHaveBeenCalledWith('/comparer?communes=44991,85991', { noScroll: true, keepFocus: true });
		expect(document.activeElement).toBe(vue.container.querySelector('th[data-code="85991"]'));
		expect(vue.queryByRole('button', { name: 'Retirer Courante' })).toBeNull();
	});

	it('removing down to one commune puts focus on « Ajouter une commune »', async () => {
		const vue = ouvrir('/comparer?communes=44991,44992');
		await fireEvent.click(vue.getByRole('button', { name: 'Retirer Ancienne' }));
		await vi.waitFor(() => expect(annonce(vue)).toBe('Ancienne retirée de la comparaison.'));
		expect(runtime.goto).toHaveBeenCalledWith('/comparer?communes=44992', expect.anything());
		const ajouter = vue.getByRole('button', { name: 'Ajouter une commune' });
		expect(document.activeElement).toBe(ajouter);
		// 04.5-09 (amendment 3): the add control lives in the folded panel, opened before the focus moves.
		expect(ajouter.closest('details')?.open).toBe(true);
	});

	it('B5: removing down to one from a closed panel opens it before the focus moves (amendment 3)', async () => {
		const vue = ouvrir('/comparer?communes=44991,44992');
		const pli = vue.getByText('Modifier la sélection').closest('details') as HTMLDetailsElement;
		expect(pli.open).toBe(false);
		await fireEvent.click(vue.getByRole('button', { name: 'Retirer Courante' }));
		await vi.waitFor(() => expect(annonce(vue)).toBe('Courante retirée de la comparaison.'));
		expect(pli.open).toBe(true);
		expect(document.activeElement).toBe(vue.getByRole('button', { name: 'Ajouter une commune' }));
	});

	it('« Placer en premier » writes the new order: the API is read again for the new first column', async () => {
		const vue = ouvrir('/comparer?communes=44991,44992,85991');
		await fireEvent.click(vue.getByRole('button', { name: 'Placer Couverture inconnue en premier' }));
		await vi.waitFor(() => expect(document.activeElement).toBe(vue.container.querySelector('th[data-code="85991"]')));
		expect(runtime.goto).toHaveBeenCalledWith('/comparer?communes=85991,44991,44992', { noScroll: true, keepFocus: true });
		expect([...vue.container.querySelectorAll('thead th[data-code]')].map((th) => th.getAttribute('data-code'))).toEqual(['85991', '44991', '44992']);
	});

	it('a navigation that fails is announced and gives the controls back', async () => {
		const vue = ouvrir('/comparer?communes=44991,44992');
		runtime.goto.mockRejectedValueOnce(new Error('network'));
		await fireEvent.click(vue.getByRole('button', { name: 'Retirer Ancienne' }));
		await vi.waitFor(() => expect(annonce(vue)).toBe('La sélection n’a pas pu être chargée. Réessayez.'));
		expect(vue.getByRole('button', { name: 'Retirer Ancienne' })).not.toBeDisabled();
	});
});

describe('/comparer page — add, filter, share', () => {
	it('adds a commune found by the search: link written, confirmation announced', async () => {
		vi.useFakeTimers();
		runtime.search.mockResolvedValue({ donnees: [commune('85991', 'Couverture inconnue')] });
		const vue = ouvrir('/comparer?communes=44991,44992');
		await fireEvent.click(vue.getByRole('button', { name: 'Ajouter une commune' }));
		const champ = vue.getByRole('combobox', { name: 'Rechercher une commune' });
		await fireEvent.input(champ, { target: { value: 'Couv' } });
		await vi.advanceTimersByTimeAsync(201);
		await fireEvent.keyDown(champ, { key: 'ArrowDown' });
		await fireEvent.keyDown(champ, { key: 'Enter' });
		await vi.advanceTimersByTimeAsync(0);
		expect(runtime.goto).toHaveBeenCalledWith('/comparer?communes=44991,44992,85991', { noScroll: true, keepFocus: true });
		await vi.waitFor(() => expect(annonce(vue)).toBe('Couverture inconnue ajoutée à la comparaison (3/4).'));
		expect(vue.container.querySelectorAll('thead th[data-code]')).toHaveLength(3);
	});

	it('B5: the fourth commune added keeps the panel open and puts the focus on the sentence of the limit', async () => {
		vi.useFakeTimers();
		runtime.search.mockResolvedValue({ donnees: [commune('44001', 'Quatrième')] });
		const vue = ouvrir('/comparer?communes=44991,44992,85991');
		// The frozen answer has no fourth commune: the new link renders the outage state (no table needed here).
		runtime.goto.mockImplementation(async (suivant: string) => {
			await vue.rerender({ data: donnees(suivant, { etat: 'panne' } as Partial<ComparerData>) });
		});
		const pli = vue.getByText('Modifier la sélection').closest('details') as HTMLDetailsElement;
		pli.open = true;
		await fireEvent.click(vue.getByRole('button', { name: 'Ajouter une commune' }));
		const champ = vue.getByRole('combobox', { name: 'Rechercher une commune' });
		await fireEvent.input(champ, { target: { value: 'Quat' } });
		await vi.advanceTimersByTimeAsync(201);
		await fireEvent.keyDown(champ, { key: 'ArrowDown' });
		await fireEvent.keyDown(champ, { key: 'Enter' });
		await vi.advanceTimersByTimeAsync(0);
		expect(runtime.goto).toHaveBeenCalledWith('/comparer?communes=44991,44992,85991,44001', { noScroll: true, keepFocus: true });
		await vi.waitFor(() => expect(document.activeElement?.id).toBe('comparer-plein'));
		expect(pli.open).toBe(true);
		expect(pli.contains(document.activeElement)).toBe(true);
		const bouton = vue.getByRole('button', { name: 'Ajouter une commune' });
		expect(bouton).toBeDisabled();
		expect(bouton).toHaveAccessibleDescription('Quatre communes au maximum : retirez-en une pour en ajouter une autre.');
	});

	it('a commune already compared is not added twice', async () => {
		vi.useFakeTimers();
		runtime.search.mockResolvedValue({ donnees: [commune('44992', 'Courante')] });
		const vue = ouvrir('/comparer?communes=44991,44992');
		await fireEvent.click(vue.getByRole('button', { name: 'Ajouter une commune' }));
		const champ = vue.getByRole('combobox', { name: 'Rechercher une commune' });
		await fireEvent.input(champ, { target: { value: 'Cour' } });
		await vi.advanceTimersByTimeAsync(201);
		await fireEvent.keyDown(champ, { key: 'ArrowDown' });
		await fireEvent.keyDown(champ, { key: 'Enter' });
		expect(runtime.goto).not.toHaveBeenCalled();
		expect(annonce(vue)).toBe('Courante est déjà dans la comparaison (2/4).');
	});

	it('four communes disable « Ajouter une commune », with the contract sentence', () => {
		const vue = ouvrir('/comparer?communes=44991,44992,85991,44001', { etat: 'panne' } as Partial<ComparerData>);
		const bouton = vue.getByRole('button', { name: 'Ajouter une commune' });
		expect(bouton).toBeDisabled();
		expect(bouton).toHaveAccessibleDescription('Quatre communes au maximum : retirez-en une pour en ajouter une autre.');
	});

	it('« Différences seulement » hides what the API served as identical, rewriting the link only', async () => {
		const vue = ouvrir('/comparer?communes=44991,44992');
		const avant = vue.container.querySelectorAll('tr[data-cle]').length;
		await fireEvent.click(vue.getByRole('checkbox', { name: 'Différences seulement' }));
		expect(runtime.replaceState).toHaveBeenCalledWith('/comparer?communes=44991,44992&diff=1', {});
		expect(runtime.goto).not.toHaveBeenCalled();
		const apres = [...vue.container.querySelectorAll('tr[data-cle]')];
		expect(apres.length).toBeLessThan(avant);
		for (const tr of apres) expect(tr.getAttribute('data-identiques')).toBe('false');
	});

	it('the filter read from the link is applied at opening, and carried by the next action', async () => {
		const vue = ouvrir('/comparer?communes=44991,44992,85991&diff=1');
		expect(vue.getByRole('checkbox', { name: 'Différences seulement' })).toBeChecked();
		await fireEvent.click(vue.getByRole('button', { name: 'Retirer Couverture inconnue' }));
		expect(runtime.goto).toHaveBeenCalledWith('/comparer?communes=44991,44992&diff=1', expect.anything());
	});

	it('« Copier le lien » copies the current address and says what it carries', async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
		const vue = ouvrir('/comparer?communes=44991,44992');
		await fireEvent.click(vue.getByRole('button', { name: 'Copier le lien' }));
		await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith(window.location.href));
		expect(annonce(vue)).toBe('Lien copié. Il reprend les communes, leur ordre et le filtre\u202f; les valeurs sont relues à l’ouverture.');
	});

	it('a refused clipboard is said, never a false confirmation', async () => {
		Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) }, configurable: true });
		const vue = ouvrir('/comparer?communes=44991,44992');
		await fireEvent.click(vue.getByRole('button', { name: 'Copier le lien' }));
		await vi.waitFor(() => expect(annonce(vue)).toBe('Le lien n’a pas pu être copié. Copiez l’adresse de cette page.'));
	});
});

describe('/comparer page — outage', () => {
	it('shows one warn notice, no value, and reloads with one call in flight at most', async () => {
		let terminer!: () => void;
		runtime.invalidateAll.mockImplementation(() => new Promise<void>((resolve) => { terminer = resolve; }));
		const vue = ouvrir('/comparer?communes=44991,44992', { etat: 'panne' } as Partial<ComparerData>);
		const alerte = vue.getByRole('alert');
		expect(within(alerte).getByRole('heading', { name: 'Les données de comparaison sont momentanément indisponibles.' })).toBeTruthy();
		expect(vue.queryByRole('region', { name: 'Tableau de comparaison' })).toBeNull();
		const recharger = vue.getByRole('button', { name: 'Recharger la comparaison' });
		await fireEvent.click(recharger);
		await fireEvent.click(recharger);
		expect(runtime.invalidateAll).toHaveBeenCalledTimes(1);
		expect(recharger).toHaveAttribute('aria-busy', 'true');
		expect(recharger).toBeDisabled();
		terminer();
		await vi.waitFor(() => expect(recharger).not.toBeDisabled());
		await fireEvent.click(recharger);
		expect(runtime.invalidateAll).toHaveBeenCalledTimes(2);
	});

	it('a commune against its arrondissement: the sentence, and the way out (remove one), not a useless reload', async () => {
		const { data } = await charger('?communes=75056,75112', {
			'/v1/communes/comparaison': { status: 422, body: { detail: 'texte serveur' } }
		});
		const vue = render(Page, { data });
		const alerte = vue.getByRole('alert');
		expect(within(alerte).getByText('Une commune et son arrondissement ne se comparent pas')).toBeTruthy();
		expect(alerte.textContent).toContain('Paris et son 12e arrondissement ne se comparent pas');
		expect(alerte.textContent).not.toContain('erreur 422');
		expect(within(alerte).queryByRole('button', { name: 'Recharger la comparaison' })).toBeNull();
		expect(within(alerte).getByRole('button', { name: 'Retirer Paris' })).toBeTruthy();
		expect(within(alerte).getByRole('button', { name: 'Retirer 12e arrondissement de Paris' })).toBeTruthy();
	});

	it('« Retirer Paris » leaves the arrondissement alone: the new link reads it as known, not unknown', async () => {
		const { data } = await charger('?communes=75056,75112', {
			'/v1/communes/comparaison': { status: 422, body: { detail: 'texte serveur' } }
		});
		const vue = render(Page, { data });
		await fireEvent.click(within(vue.getByRole('alert')).getByRole('button', { name: 'Retirer Paris' }));
		const cible = new URL(String(runtime.goto.mock.calls.at(-1)?.[0]), 'http://localhost');
		expect(cible.searchParams.get('communes')).toBe('75112');
		const apres = await charger(cible.search, {
			'/v1/communes': { status: 200, body: { statut: 'ok', donnees: { results: [{ code_insee: '75112', nom: 'Paris 12e Arrondissement', departement: '75', type_zone: 'arrondissement', commune_parent: '75056' }] } } }
		});
		expect(apres.appels[0].searchParams.get('inclure_arrondissements')).toBe('true');
		expect(apres.data.etat).toBe('une');
	});

	it('a rate limit renders its own sentence in the notice, not « Le service n’a pas répondu » (WR-01)', () => {
		const vue = ouvrir('/comparer?communes=44991,44992', {
			etat: 'panne',
			detail: { statut: 429, titre: TITRE_LIMITE, message: messageLimite(60) }
		} as Partial<ComparerData>);
		const alerte = vue.getByRole('alert');
		// textContent, not getByText: its default normalizer turns the no-break space of « 1 minute » into a space.
		expect(alerte.textContent).toContain(messageLimite(60));
		expect(within(alerte).getByText(TITRE_LIMITE)).toBeTruthy();
		expect(alerte.textContent).not.toContain("Le service n'a pas répondu");
		expect(alerte.textContent).not.toContain('indisponible');
		expect(within(alerte).getByRole('button', { name: 'Recharger la comparaison' })).toBeTruthy();
	});

	it('a 409 is rendered as a refused request titled by the client, with « erreur 409 » (03-17 B C1)', async () => {
		const { data } = await charger('?communes=44991,44992', {
			'/v1/communes/comparaison': { status: 409, body: { detail: 'texte serveur' } }
		});
		const vue = render(Page, { data });
		const alerte = vue.getByRole('alert');
		expect(within(alerte).getByText(TITRE_DEMANDE_REFUSEE)).toBeTruthy();
		expect(alerte.textContent).toContain('erreur 409');
		expect(alerte.textContent).not.toContain('texte serveur');
		expect(alerte.textContent).not.toContain("Le service n'a pas répondu");
		expect(alerte.textContent).not.toContain('indisponible');
	});

	it('a 503 keeps the generic outage title and text', async () => {
		const { data } = await charger('?communes=44991,44992', {
			'/v1/communes/comparaison': { status: 503, body: { detail: 'texte serveur' } }
		});
		const vue = render(Page, { data });
		const alerte = vue.getByRole('alert');
		expect(within(alerte).getByText('Les données de comparaison sont momentanément indisponibles.')).toBeTruthy();
		expect(alerte.textContent).toContain("Le service n'a pas répondu");
		expect(alerte.textContent).not.toContain('texte serveur');
	});
});

// ---------------------------------------------------------------------------------------------
// 04-07: addresses (D-102, CMP-01), with their own link and tab selection
// ---------------------------------------------------------------------------------------------

const A = '85191_0420_00012';
const B = '85191_0601_00001';
const ADRESSES = adressesServies as unknown as Record<string, { statut: string; donnees: unknown; meta: null }>;

describe('/comparer load — addresses', () => {
	it('2 to 4 addresses: exactly one call to /v1/adresses/comparaison, in link order', async () => {
		const { data, appels } = await charger(`?adresses=${A},${B}&diff=1`, {
			'/v1/adresses/comparaison': { status: 200, body: ADRESSES.deux_adresses }
		});
		expect(appels.map((url) => `${url.pathname}?${url.searchParams}`)).toEqual([
			`/v1/adresses/comparaison?ids=${encodeURIComponent(`${A},${B}`)}`
		]);
		expect(data).toMatchObject({ nature: 'adresse', etat: 'prete', selection: { codes: [A, B], differences: true } });
		if (data.etat !== 'prete') throw new Error('prete expected');
		expect(data.modele.groupes.map((groupe) => groupe.cle)).toEqual(['documents', 'ventes', 'lieu', 'commune_reference']);
	});

	it('1 address: named by the identity of its page, never a comparison call', async () => {
		const fiche = {
			statut: 'ok',
			donnees: {
				identite: {
					etat: 'etablie', ban_id: A, libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon', numero: '12', rep: null,
					nom_voie: 'Rue des Lilas', code_postal: '85000', commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
					departement: { code: '85', nom: 'Vendée' }, parcelle: null
				},
				couverture: { etat: 'heberge', motif: null }
			}
		};
		const { data, appels } = await charger(`?adresses=${A}`, { [`/v1/adresse/${A}`]: { status: 200, body: fiche } });
		expect(appels.map((url) => url.pathname)).toEqual([`/v1/adresse/${A}`]);
		expect(data).toMatchObject({
			nature: 'adresse',
			etat: 'une',
			commune: { code: A, nom: '12 Rue des Lilas', lien: `/adresse/${A}`, identite: '85000 La Roche-sur-Yon' }
		});
		const inconnue = await charger(`?adresses=${A}`, {
			[`/v1/adresse/${A}`]: { status: 404, body: { title: 'Adresse inconnue', status: 404, code: 'adresse_inconnue' } }
		});
		expect(inconnue.data).toMatchObject({ nature: 'adresse', etat: 'vide', selection: { codes: [] } });
		expect(inconnue.data.selection.messages).toEqual([`Le lien contenait un identifiant d'adresse inconnu (${A}) ; il a été écarté.`]);
	});

	it('a key the BAN does not know is worded « Lien altéré », the others are compared', async () => {
		const { data } = await charger(`?adresses=${A},85191_9999_00001,${B}`, {
			'/v1/adresses/comparaison': { status: 200, body: { ...ADRESSES.deux_adresses, donnees: { ...(ADRESSES.deux_adresses.donnees as object), ids_ecartes: ['85191_9999_00001'] } } }
		});
		expect(data.selection.codes).toEqual([A, B]);
		expect(data.selection.messages).toEqual(["Le lien contenait un identifiant d'adresse inconnu (85191_9999_00001) ; il a été écarté."]);
	});

	it('an outage of the comparison is one state, nothing partial', async () => {
		const { data } = await charger(`?adresses=${A},${B}`, { '/v1/adresses/comparaison': { status: 503, body: { title: 'x' } } });
		expect(data).toMatchObject({ nature: 'adresse', etat: 'panne' });
	});

	// 04-11 (D-102): designations travel with the comparison call, in column order.
	it('sends the designations of the link with the one comparison call and keeps what the API kept', async () => {
		const { data, appels } = await charger(`?adresses=${A},${B}&designes=${B}:2385E0123456A,${A}:2185E0000001A`, {
			'/v1/adresses/comparaison': { status: 200, body: ADRESSES.deux_designations }
		});
		expect(appels).toHaveLength(1);
		expect(appels[0].searchParams.get('designes')).toBe(`${A}:2185E0000001A,${B}:2385E0123456A`);
		expect(data.selection.designations).toEqual([
			{ ban_id: A, numero: '2185E0000001A' },
			{ ban_id: B, numero: '2385E0123456A' }
		]);
		expect(data.selection.messages).toEqual([]);
	});

	it('a designation the API sets aside, or a malformed one, is said « Lien altéré » with its address named', async () => {
		const { data } = await charger(`?adresses=${A},${B}&designes=${A}:2185E0000001A,${B}:2485E0000099Z`, {
			'/v1/adresses/comparaison': { status: 200, body: ADRESSES.une_designation_une_ecartee }
		});
		expect(data.selection.designations).toEqual([{ ban_id: A, numero: '2185E0000001A' }]);
		expect(data.selection.messages).toEqual([
			"Le DPE désigné pour 1 Rue du Lieu n'est pas déclaré à cette adresse ; la désignation a été retirée."
		]);
		const malforme = await charger(`?adresses=${A},${B}&designes=${A}:2185e0000001a`, {
			'/v1/adresses/comparaison': { status: 200, body: ADRESSES.deux_adresses }
		});
		// The malformed number never reaches the API.
		expect(malforme.appels[0].searchParams.has('designes')).toBe(false);
		expect(malforme.data.selection.messages).toEqual([
			"Le DPE désigné pour 12 Rue des Lilas n'est pas déclaré à cette adresse ; la désignation a été retirée."
		]);
		expect(malforme.data.selection).not.toHaveProperty('designations');
	});
});

describe('/comparer page — addresses', () => {
	function donneesAdresses(lien: string): ComparerData {
		const { nature, ...selection } = lireSelectionNature(new URL(lien, 'http://localhost').searchParams);
		if (selection.statut === 'vide') return { nature, etat: 'vide', selection };
		const modele = composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone(ADRESSES.deux_adresses.donnees) as never));
		return { nature, etat: 'prete', selection, modele };
	}

	it('h1 « Comparer des adresses », the reading note of the commune kept apart, the table', async () => {
		const vue = render(Page, { data: donneesAdresses(`/comparer?adresses=${A},${B}`) });
		expect(vue.getByRole('heading', { level: 1, name: 'Comparer des adresses' })).toBeTruthy();
		expect(vue.getByText("Les valeurs de la commune sont rangées à part : elles ne décrivent pas l'adresse.")).toBeTruthy();
		expect(vue.getByRole('button', { name: 'Ajouter une adresse' })).toBeTruthy();
		expect(vue.getByText("Ces valeurs décrivent la commune, pas l'adresse.")).toBeTruthy();
		expect(document.title).toBe('12 Rue des Lilas, 1 Rue du Lieu — Comparer des adresses — DataCommune');
	});

	// Codex 04.8-05-1, P1: « Point hors parcelle » is a reason of attachment, not a source that does not
	// publish: it never enters the sentence on absences, though the table shows it.
	it('the sentence on absences quotes only what a source does not publish, never a reason of attachment', () => {
		const vue = render(Page, { data: donneesAdresses(`/comparer?adresses=${A},${B}`) });
		const badges = [...vue.container.querySelectorAll('td .state-badge')].map((badge) => badge.textContent?.trim());
		expect(badges).toContain('Point hors parcelle');
		expect(vue.container.querySelector('.cmp-note')?.textContent ?? '').not.toContain('Point hors parcelle');
	});

	it('the link of addresses fills the address selection of the tab, never the communes one', async () => {
		comparisonSelection.replace(['44991']);
		render(Page, { data: donneesAdresses(`/comparer?adresses=${B},${A}`) });
		await apresNavigation(`/comparer?adresses=${B},${A}`);
		expect(addressComparisonSelection.codes).toEqual([B, A]);
		expect(comparisonSelection.codes).toEqual(['44991']);
		expect(runtime.goto).not.toHaveBeenCalled();
	});

	it('renders the empty comparator of addresses', () => {
		const vue = render(Page, { data: { nature: 'adresse', etat: 'vide', selection: { statut: 'vide', codes: [], differences: false, messages: [] } } });
		expect(vue.getByRole('heading', { level: 2, name: 'Choisissez deux adresses à comparer' })).toBeTruthy();
		expect(vue.getByText("Ajoutez-les ici ou depuis la page d'une adresse.")).toBeTruthy();
	});

	// 04-11 (D-102): the designation lives in the link only.
	it('« Désigner ce DPE » and « Retirer la désignation » write the link only, never the storage', async () => {
		addressComparisonSelection.replace([A, B]);
		const avant = { ...sessionStorage };
		const vue = render(Page, { data: donneesAdresses(`/comparer?adresses=${A},${B}&designes=${B}:2385E0123456A&diff=1`) });
		runtime.goto.mockResolvedValue(undefined);
		await fireEvent.click(vue.getByRole('button', { name: 'Retirer 12 Rue des Lilas, 85000 La Roche-sur-Yon' }));
		// An address removed takes its designation away, the other one keeps its own.
		await vi.waitFor(() =>
			expect(runtime.goto).toHaveBeenCalledWith(`/comparer?adresses=${B}&designes=${B}:2385E0123456A&diff=1`, { noScroll: true, keepFocus: true })
		);
		expect({ ...sessionStorage }).toEqual({ ...avant, 'dc:comparaison:adresses': JSON.stringify([B]) });
		expect(JSON.stringify({ ...localStorage })).not.toContain('2385E0123456A');
	});

	it('D-15 FRONT IN-04: the link of the active nature keeps its designations and its filter', () => {
		const vue = render(Page, { data: donneesAdresses(`/comparer?adresses=${A},${B}&designes=${B}:2385E0123456A&diff=1`) });
		const nav = vue.getByRole('navigation', { name: 'Nature des objets comparés' });
		expect(within(nav).getByRole('link', { name: 'Adresses (2)' })).toHaveAttribute(
			'href',
			`/comparer?adresses=${A},${B}&designes=${B}:2385E0123456A&diff=1`
		);
	});

	it('D-15 FRONT IN-02: a link whose every address is set aside is rewritten with its nature', async () => {
		const selection = { statut: 'vide' as const, codes: [], differences: false, messages: ["Le lien contenait une adresse inconnue (85191_9999_00001) ; elle a été écartée."] };
		render(Page, { data: { nature: 'adresse', etat: 'vide', selection } });
		await apresNavigation('/comparer?adresses=85191_9999_00001');
		await new Promise((resolve) => queueMicrotask(() => resolve(null)));
		expect(runtime.replaceState).toHaveBeenCalledWith('/comparer?adresses=', {});
	});

	it('B5: removing down to one address from a closed panel opens it and focuses « Ajouter une adresse »', async () => {
		addressComparisonSelection.replace([A, B]);
		const vue = render(Page, { data: donneesAdresses(`/comparer?adresses=${A},${B}`) });
		runtime.goto.mockImplementation(async () => {
			await vue.rerender({
				data: {
					nature: 'adresse', etat: 'une', selection: { statut: 'une', codes: [A], differences: false, messages: [] },
					commune: { code: A, nom: '12 Rue des Lilas', lien: `/adresse/${A}` }
				} as ComparerData
			});
		});
		const pli = vue.getByText('Modifier la sélection').closest('details') as HTMLDetailsElement;
		expect(pli.open).toBe(false);
		await fireEvent.click(vue.getByRole('button', { name: 'Retirer 1 Rue du Lieu, 85000 La Roche-sur-Yon' }));
		await vi.waitFor(() => expect(document.activeElement).toBe(vue.getByRole('button', { name: 'Ajouter une adresse' })));
		expect(pli.open).toBe(true);
	});

	it('« Retirer » writes the link of addresses with the remaining one', async () => {
		addressComparisonSelection.replace([A, B]);
		const vue = render(Page, { data: donneesAdresses(`/comparer?adresses=${A},${B}`) });
		runtime.goto.mockResolvedValue(undefined);
		await fireEvent.click(vue.getByRole('button', { name: 'Retirer 1 Rue du Lieu, 85000 La Roche-sur-Yon' }));
		await vi.waitFor(() => expect(runtime.goto).toHaveBeenCalledWith(`/comparer?adresses=${A}`, { noScroll: true, keepFocus: true }));
		expect(addressComparisonSelection.codes).toEqual([A]);
	});
});

// ---------------------------------------------------------------------------------------------
// 04-10: DPE documents (CMP-02, D-116), nature switch and nature modified last (UI-SPEC Q3)
// ---------------------------------------------------------------------------------------------

const ANCIEN = '2185E0000001A';
const ACTUEL = '2385E0123456A';
const DOCUMENTS = documentsServis as unknown as Record<string, { statut: string; donnees: { numeros_ecartes: string[] }; meta: null }>;

describe('/comparer load — DPE documents', () => {
	it('2 to 4 numbers: exactly one call to /v1/dpe/comparaison, in link order', async () => {
		const { data, appels } = await charger(`?dpe=${ANCIEN},${ACTUEL}&diff=1`, {
			'/v1/dpe/comparaison': { status: 200, body: DOCUMENTS.ancien_et_actuel }
		});
		expect(appels.map((url) => `${url.pathname}?${url.searchParams}`)).toEqual([
			`/v1/dpe/comparaison?numeros=${encodeURIComponent(`${ANCIEN},${ACTUEL}`)}`
		]);
		expect(data).toMatchObject({ nature: 'dpe', etat: 'prete', selection: { codes: [ANCIEN, ACTUEL], differences: true } });
		if (data.etat !== 'prete') throw new Error('prete expected');
		expect(data.modele.groupes.map((groupe) => groupe.cle)).toEqual(['etiquettes', 'logement', 'cout_validite', 'adresse']);
	});

	it('a number the instance does not know is worded « Lien altéré », the others are compared', async () => {
		const corps = structuredClone(DOCUMENTS.meme_methode_trois);
		const { data } = await charger(`?dpe=${ACTUEL},2485E0000003C,2385E9999999X,2485E0000004C`, {
			'/v1/dpe/comparaison': { status: 200, body: corps }
		});
		expect(data.selection.codes).toEqual([ACTUEL, '2485E0000003C', '2485E0000004C']);
		expect(data.selection.messages).toEqual(['Le lien contenait un numéro DPE inconnu (2385E9999999X) ; il a été écarté.']);
	});

	it('1 number: its document read once to know it, never a comparison call', async () => {
		const { data, appels } = await charger(`?dpe=${ACTUEL}`, {
			[`/v1/dpe/${ACTUEL}`]: { status: 200, body: { statut: 'ok', donnees: { numero: ACTUEL } } }
		});
		expect(appels.map((url) => url.pathname)).toEqual([`/v1/dpe/${ACTUEL}`]);
		expect(data).toMatchObject({ nature: 'dpe', etat: 'une', commune: { code: ACTUEL, nom: 'DPE n° 2385E0123456A', lien: `/dpe/${ACTUEL}` } });
		const inconnu = await charger(`?dpe=${ACTUEL}`, {
			[`/v1/dpe/${ACTUEL}`]: { status: 404, body: { title: 'DPE inconnu', status: 404, code: 'dpe_inconnu', couverture: { departements: ['85'] } } }
		});
		expect(inconnu.data).toMatchObject({ nature: 'dpe', etat: 'vide', selection: { codes: [] } });
		expect(inconnu.data.selection.messages).toEqual([`Le lien contenait un numéro DPE inconnu (${ACTUEL}) ; il a été écarté.`]);
	});

	it('a refused publication (503) is one outage state, nothing partial', async () => {
		const { data } = await charger(`?dpe=${ANCIEN},${ACTUEL}`, {
			'/v1/dpe/comparaison': { status: 503, body: { detail: 'Donnees temporairement indisponibles' } }
		});
		expect(data).toMatchObject({ nature: 'dpe', etat: 'panne' });
	});

	it('a malformed number of the link never reaches the API', async () => {
		const { data, appels } = await charger(`?dpe=${ACTUEL},2385e0123456a`, {
			[`/v1/dpe/${ACTUEL}`]: { status: 200, body: { statut: 'ok', donnees: { numero: ACTUEL } } }
		});
		expect(appels.map((url) => url.pathname)).toEqual([`/v1/dpe/${ACTUEL}`]);
		expect(data.selection.messages).toEqual(['Le lien contenait un numéro DPE inconnu (2385e0123456a) ; il a été écarté.']);
	});
});

describe('/comparer page — DPE documents', () => {
	function donneesDocuments(lien: string): ComparerData {
		const { nature, ...selection } = lireSelectionNature(new URL(lien, 'http://localhost').searchParams);
		if (selection.statut === 'vide') return { nature, etat: 'vide', selection };
		if (selection.statut === 'une') {
			return { nature, etat: 'une', selection, commune: { code: selection.codes[0], nom: `DPE n° ${selection.codes[0]}`, lien: `/dpe/${selection.codes[0]}` } };
		}
		const modele = composeComparaisonDocuments(projeterComparaisonDocuments(structuredClone(DOCUMENTS.ancien_et_actuel.donnees) as never));
		return { nature, etat: 'prete', selection, modele };
	}

	function ouvrirDocuments(lien: string) {
		const vue = render(Page, { data: donneesDocuments(lien) });
		runtime.goto.mockImplementation(async (suivant: string) => {
			await vue.rerender({ data: donneesDocuments(suivant) });
		});
		return vue;
	}

	beforeEach(() => {
		documentComparisonSelection.replace([]);
		addressComparisonSelection.replace([]);
		// The communes are the nature modified last before each test (the phase 3 default).
		comparisonSelection.replace(['44991']);
		comparisonSelection.replace([]);
	});

	it('h1 « Comparer des documents DPE », the reading note of the methods, the nature switch, the table', () => {
		const vue = ouvrirDocuments(`/comparer?dpe=${ANCIEN},${ACTUEL}`);
		// 04.8-08 (D-31): the h1 is the first prose of the main, its DPE is spelled out there.
		expect(vue.getByRole('heading', { level: 1, name: 'Comparer des documents DPE (diagnostic de performance énergétique)' })).toBeTruthy();
		expect(vue.getByText('Les valeurs gardent leur période, leur définition et leur source. Aucun score global.')).toBeTruthy();
		expect(
			vue.getByText('Deux DPE établis avec des méthodes différentes (avant et après juillet 2021) ne se comparent pas : la ligne le dit.')
		).toBeTruthy();
		const nav = vue.getByRole('navigation', { name: 'Nature des objets comparés' });
		expect(within(nav).getByRole('link', { name: 'Documents DPE (2)' })).toHaveAttribute('aria-current', 'page');
		expect(within(nav).getByRole('link', { name: 'Communes' })).toHaveAttribute('href', '/comparer');
		expect(within(nav).getByRole('link', { name: 'Adresses' })).toHaveAttribute('href', '/comparer?adresses=');
		expect(vue.getByRole('region', { name: 'Tableau de comparaison' })).toBeTruthy();
		expect(document.title).toBe('DPE n° 2185E0000001A, DPE n° 2385E0123456A — Comparer des documents DPE — DataCommune');
	});

	it('renders the empty comparator of documents with the number field, its help and its button', () => {
		const vue = ouvrirDocuments('/comparer?dpe=');
		expect(vue.getByRole('heading', { level: 2, name: 'Choisissez deux documents DPE à comparer' })).toBeTruthy();
		expect(vue.getByText("Ajoutez-les ici par leur numéro ou depuis la page d'un DPE.")).toBeTruthy();
		expect(vue.getByRole('textbox', { name: 'Numéro du DPE' })).toHaveAccessibleDescription(
			'Le numéro figure sur le document et sur la page de chaque adresse.'
		);
		// C7: the plain action of the page wears the shared class of the socle, not a class of its own.
		expect(vue.getByRole('button', { name: 'Ajouter un DPE' })).toHaveClass('bouton-primaire');
	});

	it('a malformed input is said under the field and never sent', async () => {
		const vue = ouvrirDocuments(`/comparer?dpe=${ACTUEL}`);
		await fireEvent.input(vue.getByRole('textbox', { name: 'Numéro du DPE' }), { target: { value: '2385-E0123' } });
		await fireEvent.click(vue.getByRole('button', { name: 'Ajouter un DPE' }));
		expect(vue.getByRole('alert').textContent).toBe('« 2385-E0123 » n\'a pas la forme d\'un numéro DPE de l\'ADEME (Agence de la transition écologique).'); // 04.8-08 (D-31): ADEME first said here; the quoted input is untouched.
		expect(runtime.goto).not.toHaveBeenCalled();
	});

	it('a well-formed number joins the link and the DPE selection of the tab, never another nature', async () => {
		documentComparisonSelection.replace([ACTUEL]);
		const vue = ouvrirDocuments(`/comparer?dpe=${ACTUEL}`);
		expect(vue.getByText('Ajoutez un deuxième document DPE pour lire les écarts.')).toBeTruthy();
		await fireEvent.input(vue.getByRole('textbox', { name: 'Numéro du DPE' }), { target: { value: ` ${ANCIEN} ` } });
		await fireEvent.click(vue.getByRole('button', { name: 'Ajouter un DPE' }));
		await vi.waitFor(() => expect(runtime.goto).toHaveBeenCalledWith(`/comparer?dpe=${ACTUEL},${ANCIEN}`, { noScroll: true, keepFocus: true }));
		expect(documentComparisonSelection.codes).toEqual([ACTUEL, ANCIEN]);
		expect(comparisonSelection.codes).toEqual([]);
		await vi.waitFor(() => expect(annonce(vue)).toBe(`DPE n° ${ANCIEN} ajouté à la comparaison (2/4).`));
	});

	it('a number already compared is not added twice; four documents disable the field with the sentence', async () => {
		const vue = ouvrirDocuments(`/comparer?dpe=${ANCIEN},${ACTUEL}`);
		await fireEvent.input(vue.getByRole('textbox', { name: 'Numéro du DPE' }), { target: { value: ACTUEL } });
		await fireEvent.click(vue.getByRole('button', { name: 'Ajouter un DPE' }));
		expect(annonce(vue)).toBe(`DPE n° ${ACTUEL} est déjà dans la comparaison (2/4).`);
		vue.unmount();
		const plein = render(Page, {
			data: { nature: 'dpe', etat: 'vide', selection: { statut: 'prete', codes: [ANCIEN, ACTUEL, '2485E0000003C', '2485E0000004C'], differences: false, messages: [] } }
		});
		expect(plein.getByRole('button', { name: 'Ajouter un DPE' })).toBeDisabled();
		expect(plein.getByText('Quatre documents DPE au maximum : retirez-en un pour en ajouter un autre.')).toBeTruthy();
	});

	it('B5: removing down to one document from a closed panel opens it and focuses the number field', async () => {
		documentComparisonSelection.replace([ANCIEN, ACTUEL]);
		const vue = ouvrirDocuments(`/comparer?dpe=${ANCIEN},${ACTUEL}`);
		const pli = vue.getByText('Modifier la sélection').closest('details') as HTMLDetailsElement;
		expect(pli.open).toBe(false);
		// The column name carries a no-break space after « n° » (titreDocument).
		await fireEvent.click(vue.getByRole('button', { name: new RegExp(`^Retirer DPE n°\\s${ACTUEL}$`) }));
		await vi.waitFor(() => expect(document.activeElement).toBe(vue.getByRole('textbox', { name: 'Numéro du DPE' })));
		expect(pli.open).toBe(true);
	});

	it('D-15 FRONT IN-01: a document placed first is « lui », never « elle »', async () => {
		const vue = ouvrirDocuments(`/comparer?dpe=${ANCIEN},${ACTUEL}`);
		const placer = vue.getAllByRole('button').find((bouton) => bouton.textContent?.trim() === 'Placer en premier')!;
		await fireEvent.click(placer);
		await vi.waitFor(() => expect(annonce(vue)).toMatch(/est en première colonne : les écarts sont lus par rapport à lui\.$/));
	});

	it('D-15 FRONT IN-05: the fourth document added puts the focus on the sentence of the limit', async () => {
		const trois = [ANCIEN, ACTUEL, '2485E0000003C'];
		documentComparisonSelection.replace(trois);
		const vue = ouvrirDocuments(`/comparer?dpe=${trois.join(',')}`);
		await fireEvent.input(vue.getByRole('textbox', { name: 'Numéro du DPE' }), { target: { value: '2485E0000004C' } });
		await fireEvent.click(vue.getByRole('button', { name: 'Ajouter un DPE' }));
		await vi.waitFor(() => expect(document.activeElement?.id).toBe('comparer-plein'));
		expect(document.activeElement?.textContent?.replace(/\s/g, ' ')).toBe('Quatre documents DPE au maximum : retirez-en un pour en ajouter un autre.');
	});

	it('the link of documents fills the DPE selection of the tab, the nature modified last', async () => {
		comparisonSelection.replace(['44991']);
		ouvrirDocuments(`/comparer?dpe=${ACTUEL},${ANCIEN}`);
		await apresNavigation(`/comparer?dpe=${ACTUEL},${ANCIEN}`);
		expect(documentComparisonSelection.codes).toEqual([ACTUEL, ANCIEN]);
		expect(comparisonSelection.codes).toEqual(['44991']);
		expect(derniereNature.nature).toBe('dpe');
		expect(runtime.goto).not.toHaveBeenCalled();
	});

	it('opened without parameter, the page opens the nature modified last with its selection', async () => {
		comparisonSelection.replace(['44991', '44992']);
		documentComparisonSelection.replace([ACTUEL, ANCIEN]);
		ouvrir('/comparer');
		await apresNavigation('/comparer');
		expect(runtime.goto).toHaveBeenCalledWith(`/comparer?dpe=${ACTUEL},${ANCIEN}`, { replaceState: true });
		runtime.goto.mockReset();
		// The DPE selection emptied: the nature stays the documents', its empty comparator.
		documentComparisonSelection.replace([]);
		await apresNavigation('/comparer');
		expect(runtime.goto).toHaveBeenCalledWith('/comparer?dpe=', { replaceState: true });
	});

	it('a nature named without any object reads its own tab selection back', async () => {
		documentComparisonSelection.replace([ACTUEL, ANCIEN]);
		ouvrirDocuments('/comparer?dpe=');
		await apresNavigation('/comparer?dpe=');
		expect(runtime.goto).toHaveBeenCalledWith(`/comparer?dpe=${ACTUEL},${ANCIEN}`, { replaceState: true });
		expect(documentComparisonSelection.codes).toEqual([ACTUEL, ANCIEN]);
	});

	it('« Retirer » down to none keeps the nature: its own empty comparator', async () => {
		documentComparisonSelection.replace([ACTUEL]);
		const vue = ouvrirDocuments(`/comparer?dpe=${ACTUEL}`);
		await fireEvent.click(vue.getByRole('button', { name: `Retirer DPE n° ${ACTUEL}` }));
		await vi.waitFor(() => expect(runtime.goto).toHaveBeenCalledWith('/comparer?dpe=', { noScroll: true, keepFocus: true }));
		expect(documentComparisonSelection.codes).toEqual([]);
		await vi.waitFor(() => expect(annonce(vue)).toBe(`DPE n° ${ACTUEL} retiré de la comparaison.`));
	});
});

// 04.8-05 (UI-SPEC v4 § 6.4): the form of the direction A. The three natures, the selection and the
// behaviours above are unchanged; these lock what the port adds.
describe('/comparer page — forme direction A (04.8-05)', () => {
	it('breadcrumb « Accueil › Comparer », h1 « Comparer A et B » and the sentence of the mock-up for communes', () => {
		const vue = ouvrir('/comparer?communes=44991,44992');
		const fil = vue.getByRole('navigation', { name: "Fil d'Ariane" });
		expect(within(fil).getByRole('link', { name: 'Accueil' })).toHaveAttribute('href', '/');
		expect(fil.querySelector('[aria-current="page"]')?.textContent).toBe('Comparer');
		// Beyond 720 px the names; under 720 px the short title of the nature (gap decided on 05/11 to keep
		// UI-012): one span shown at a time, by CSS (jsdom renders both).
		const h1 = vue.getByRole('heading', { level: 1 });
		expect(h1.querySelector('.titre-large')?.textContent).toBe('Comparer Ancienne et Courante');
		expect(h1.querySelector('.titre-court')?.textContent).toBe('Comparer des communes');
		expect(pageSource).toMatch(/\.titre-court \{\s*display: none;/);
		expect(pageSource).toMatch(/@media \(max-width: 720px\) \{[\s\S]*\.titre-large \{\s*display: none;[\s\S]*\.titre-court \{\s*display: inline;/);
		expect(vue.container.querySelector('.fiche-sous')?.textContent).toBe(
			'Valeur par valeur, sans note globale. Ce sont des références communales, pas l’estimation d’une adresse.'
		);
	});

	it('keeps the title of the nature while no comparison is shown', () => {
		const vide = ouvrir('/comparer');
		expect(vide.getByRole('heading', { level: 1 }).textContent).toBe('Comparer des communes');
		expect(vide.container.querySelector('.fiche-sous')).toBeNull();
		expect(vide.container.querySelectorAll('.cmp-liens a')).toHaveLength(0);
	});

	it('links each compared commune to its page (→), in column order', () => {
		const vue = ouvrir('/comparer?communes=44991,44992,85991');
		const liens = [...vue.container.querySelectorAll('.cmp-liens ul.liste-liens a')];
		expect(liens.map((a) => [a.querySelector('.lien-t')?.textContent, a.getAttribute('href')])).toEqual([
			['La fiche de Ancienne', '/commune/44991'],
			['La fiche de Courante', '/commune/44992'],
			['La fiche de Couverture inconnue', '/commune/85991']
		]);
		for (const lien of liens) expect(lien.querySelector('.fleche[aria-hidden="true"]')?.textContent).toBe('→');
	});

	// Codex 04.8-05-2, P1: the sentence never says the source does not publish: a generic served
	// « not reported » does not establish it.
	it('says that an absent value stays its served word, never zero, without blaming the source', () => {
		const vue = ouvrir('/comparer?communes=44991,44992,85991');
		const note = vue.container.querySelector('.cmp-note')?.textContent ?? '';
		expect(note).not.toMatch(/ne publie pas|ne fournit pas/);
		expect(note).toMatch(/^Une valeur absente pour une commune reste «\s[^»]+\s»( ou «\s[^»]+\s»)*, jamais zéro\.$/);
		// Each word quoted is one the table shows on a badge: read from the served states, never written here.
		const badges = new Set([...vue.container.querySelectorAll('td .state-badge')].map((badge) => badge.textContent?.trim()));
		for (const [, mot] of note.matchAll(/«\s([^»]+)\s»/g)) expect(badges.has(mot), mot).toBe(true);
	});

	// R-09: a value an arrondissement holds from its commune is marked « Commune » in its cell; the page says
	// once what the mark means, naming the commune, and only when the table shows such a value.
	describe('the note of the values inherited from the commune (R-09)', () => {
		function avecHeritage(lien: string) {
			const data = donnees(lien);
			if (data.etat !== 'prete') throw new Error('expected a comparison');
			data.modele.colonnes[0] = { ...data.modele.colonnes[0], code: '75111', nom: 'Paris 11e Arrondissement' };
			const zone = data.modele.groupes.flatMap((groupe) => groupe.lignes).find((ligne) => ligne.cle === 'zone_abc')!;
			zone.identiques = true;
			zone.cellules[0] = {
				...zone.cellules[0],
				code: '75111',
				etat: { kind: 'valeur', texte: 'Abis', unite: null },
				echelle: { pictogramme: 'commune', mot: 'Commune', enClair: 'Commune de Paris' }
			};
			return render(Page, { data });
		}
		const note = (vue: { container: HTMLElement }) =>
			vue.container.querySelector('.cmp-note-echelle')?.textContent?.replace(/[  ]/g, ' ').trim() ?? null;

		it('names the commune the marked values hold for', () => {
			expect(note(avecHeritage('/comparer?communes=44991,44992'))).toBe(
				'Les valeurs marquées « Commune » valent pour tout Paris, pas pour le seul arrondissement.'
			);
		});

		it('is absent when the table shows no inherited value', () => {
			expect(note(ouvrir('/comparer?communes=44991,44992'))).toBeNull();
			// The only marked row is served identical: « Différences seulement » hides it, and the note with it.
			expect(note(avecHeritage('/comparer?communes=44991,44992&diff=1'))).toBeNull();
		});
	});

	it('folds « Modifier la sélection » and the reference in the `.det` form, without a drawn icon', () => {
		const vue = ouvrir('/comparer?communes=44991,44992');
		const plis = [...vue.container.querySelectorAll('details.det')];
		expect(plis.map((pli) => pli.classList.contains('comparer-modifier') || pli.classList.contains('comparer-lecture'))).toEqual([true, true]);
		for (const pli of plis) expect(pli.querySelector(':scope > summary svg')).toBeNull();
	});
});
