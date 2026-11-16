// Open JSON export of a commune fiche (04.7-14): the same reading as the page, the four outcomes kept
// apart (200 with `etat`, 404 for an unknown code, 503 for an outage), and the headers of an open,
// non-indexed, cacheable document.
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TITRE_ACCES_REFUSE, TITRE_LIMITE, TITRE_QUOTA, messageLimite } from '$lib/data/panne';
import { GET } from './+server';

type Corps = Record<string, unknown>;

function reponse(status: number, body: unknown) {
	return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const SYNTHESE = { code_insee: '44109', nom_commune: 'Nantes', code_departement: '44', population: 320732 };
const SOURCE = { nom: 'Recensement', producteur: 'INSEE', licence: 'Licence Ouverte 2.0', dataset: 'demographie' };

/** A fetch that answers the synthesis with *synthese* and the referential with *referentiel*. */
function api(synthese: Response | (() => Response), referentiel: Response = reponse(200, { donnees: [] })) {
	return vi.fn(async (input: RequestInfo | URL) => {
		const url = String(input);
		if (url.includes('/v1/commune?')) return typeof synthese === 'function' ? synthese() : synthese;
		return referentiel;
	}) as unknown as typeof fetch;
}

async function exporter(fetch: typeof globalThis.fetch, code = '44109') {
	const event = { params: { code }, fetch, url: new URL(`https://datacommune.fr/commune/${code}.json`) };
	return (GET as unknown as (e: typeof event) => Promise<Response>)(event);
}

afterEach(() => vi.restoreAllMocks());

describe('/commune/[code].json', () => {
	it('sert la synthèse lue pour la page, ses sources, sa licence et son lien, sans indexation', async () => {
		const res = await exporter(api(reponse(200, { donnees: [SYNTHESE], meta: { source: [SOURCE] } })));
		expect(res.status).toBe(200);
		const corps = (await res.json()) as Corps;
		expect(corps).toMatchObject({
			format: 'datacommune.commune.v1',
			code_insee: '44109',
			etat: 'fiche',
			page: 'https://datacommune.fr/commune/44109',
			sources: [SOURCE],
			donnees: SYNTHESE
		});
		expect(String(corps.licence)).toContain('licence de son producteur');
		expect(Number.isNaN(Date.parse(String(corps.lu_le)))).toBe(false);
		expect(res.headers.get('x-robots-tag')).toBe('noindex');
		expect(res.headers.get('access-control-allow-origin')).toBe('*');
		expect(res.headers.get('cache-control')).toBe('public, max-age=3600');
	});

	// Todo 2026-11-01: blocks the instance does not publish are exported as served, never as a bare
	// null (the page alone reads them as no block).
	it('sert les blocs non publiés avec leur état, jamais un null sans état', async () => {
		const nonPublies = { etat: 'not_qualified', motif: 'indicateurs_communaux_non_publies' };
		const synthese = { ...SYNTHESE, indicateurs_dvf: nonPublies, indicateurs_dpe: nonPublies };
		const res = await exporter(api(reponse(200, { donnees: [synthese], meta: { source: [SOURCE] } })));
		const corps = (await res.json()) as Corps;
		expect(corps.donnees).toEqual(synthese);
	});

	it('hors couverture : 200, l’état et son motif, aucune donnée — jamais un zéro', async () => {
		const hors = reponse(200, {
			donnees: [],
			meta: { hebergement: { commune: { etat: 'hors_couverture', motif: 'departement_non_servi' } } }
		});
		const referentiel = reponse(200, { donnees: [{ code_insee: '75056', nom: 'Paris', departement: '75', code_postal: '75001' }] });
		const res = await exporter(api(hors, referentiel), '75056');
		expect(res.status).toBe(200);
		const corps = (await res.json()) as Corps;
		expect(corps.etat).toBe('hors_couverture');
		expect(corps.donnees).toBeNull();
		expect(corps.commune).toMatchObject({ nom: expect.any(String) });
	});

	it('panne de la synthèse : 503 problem+json, jamais un objet vide ni mis en cache', async () => {
		const res = await exporter(api(() => reponse(503, { detail: 'texte serveur' })));
		expect(res.status).toBe(503);
		expect(res.headers.get('content-type')).toContain('application/problem+json');
		expect(res.headers.get('cache-control')).toBe('no-store');
		const corps = (await res.json()) as Corps;
		expect(corps).toMatchObject({ status: 503, title: 'Source indisponible' });
		expect(corps).not.toHaveProperty('donnees');
	});

	it('limite de requêtes : 429 typé, avec le délai servi par l’API, jamais 60 secondes inventées', async () => {
		const limite = () =>
			new Response(JSON.stringify({ title: 'Too Many Requests' }), {
				status: 429,
				headers: { 'Content-Type': 'application/json', 'Retry-After': '120' }
			});
		const res = await exporter(api(limite));
		expect(res.status).toBe(429);
		expect(res.headers.get('retry-after')).toBe('120');
		expect(await res.json()).toMatchObject({ status: 429, title: TITRE_LIMITE, detail: messageLimite(120) });
	});

	it('quota mensuel : 429 avec son titre, et aucun délai quand l’API n’en sert pas', async () => {
		const quota = () =>
			new Response(JSON.stringify({ title: 'Quota mensuel epuise' }), { status: 429, headers: { 'Content-Type': 'application/json' } });
		const res = await exporter(api(quota));
		expect(res.status).toBe(429);
		expect(res.headers.get('retry-after')).toBeNull();
		expect(await res.json()).toMatchObject({ status: 429, title: TITRE_QUOTA });
	});

	it('accès refusé : 503 avec son titre typé, sans promesse de reprise', async () => {
		const res = await exporter(api(() => reponse(403, { detail: 'refusé' })));
		expect(res.status).toBe(503);
		expect(res.headers.get('retry-after')).toBeNull();
		expect(await res.json()).toMatchObject({ status: 503, title: TITRE_ACCES_REFUSE });
	});

	it('code mal formé : 404 problem+json lisible de partout, avant toute requête', async () => {
		const fetch = api(reponse(200, { donnees: [SYNTHESE] }));
		const res = await exporter(fetch, 'abc');
		expect(res.status).toBe(404);
		expect(res.headers.get('content-type')).toContain('application/problem+json');
		expect(res.headers.get('access-control-allow-origin')).toBe('*');
		expect(res.headers.get('x-robots-tag')).toBe('noindex');
		expect(res.headers.get('cache-control')).toBe('no-store');
		expect(await res.json()).toMatchObject({ status: 404, title: 'Commune inconnue' });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('une panne réseau reste une panne (503), jamais un 404', async () => {
		const fetch = vi.fn(async () => {
			throw new TypeError('réseau coupé');
		}) as unknown as typeof globalThis.fetch;
		const res = await exporter(fetch);
		expect(res.status).toBe(503);
	});
});
