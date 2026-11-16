// The page of the error report on the server: closed unless the build opens it, the context read
// from the link or from the page the visitor comes from, and the form relayed to the API over the
// internal network with the visitor's address. The API is a stand-in: what matters here is what the
// site sends, where, and what it answers to each outcome.
import { beforeEach, describe, expect, it, vi } from 'vitest';

const etat = vi.hoisted(() => ({ actif: true, interne: 'http://api:8000' as string | undefined }));
vi.mock('$lib/signalement/reglage', () => ({
	get SIGNALEMENT() {
		return etat.actif;
	}
}));
vi.mock('$env/dynamic/private', () => ({
	env: {
		get API_INTERNAL_URL() {
			return etat.interne;
		}
	}
}));
vi.mock('$lib/config', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/config')>()),
	API_URL: 'https://api.datacommune.fr',
	DEPLOYMENT_SHA: 'aaaa1111',
	IS_PREPRODUCTION: false
}));

import type { ContexteSignalement } from '$lib/signalement/contexte';
import { actions, load, prerender } from './+page.server';

const appelApi = vi.fn<typeof fetch>();

function reponse(status: number, corps: unknown = {}): Response {
	return new Response(JSON.stringify(corps), { status, headers: { 'content-type': 'application/json' } });
}

function formulaire(champs: Record<string, string>): Request {
	return new Request('https://datacommune.fr/signaler', {
		method: 'POST',
		headers: { 'user-agent': 'Mozilla/5.0 (test)' },
		body: new URLSearchParams(champs)
	});
}

const SAISIE = {
	commentaire: 'Le prix médian des maisons paraît dix fois trop bas.',
	email: '',
	site_web: '',
	page: '/commune/44109',
	titre: 'Prix médian des maisons',
	lignes: 'dvf.prix_m2_median_maison=312',
	source: 'DVF',
	version: '81d3926f',
	largeur: '1280'
};

async function envoyer(champs: Record<string, string> = SAISIE) {
	return actions.default({
		request: formulaire(champs),
		getClientAddress: () => '203.0.113.7'
	} as never);
}

function charger(chemin: string, entetes: Record<string, string> = {}) {
	const setHeaders = vi.fn();
	const data = load({
		url: new URL(`https://datacommune.fr${chemin}`),
		request: new Request(`https://datacommune.fr${chemin}`, { headers: entetes }),
		setHeaders
	} as never) as { contexte: ContexteSignalement; navigateur: string | null };
	return { data, setHeaders };
}

beforeEach(() => {
	etat.actif = true;
	etat.interne = 'http://api:8000';
	appelApi.mockReset();
	vi.stubGlobal('fetch', appelApi);
});

describe('/signaler — closed unless the build opens it', () => {
	it('is never prerendered: it carries a form action', () => {
		expect(prerender).toBe(false);
	});

	it('answers 404 to the page and to the form, and calls nothing', async () => {
		etat.actif = false;

		expect(() => charger('/signaler')).toThrowError(expect.objectContaining({ status: 404 }));
		await expect(envoyer()).rejects.toMatchObject({ status: 404 });
		expect(appelApi).not.toHaveBeenCalled();
	});
});

describe('/signaler — the context shown before sending', () => {
	it('comes from the link of the panel, with the browser read on the server', () => {
		const { data, setHeaders } = charger('/signaler?de=%2Fcommune%2F44109&titre=Prix&lignes=a%3D1&source=DVF&v=81d3926f', {
			'user-agent': 'Mozilla/5.0 (test)'
		});

		expect(data).toEqual({
			contexte: { page: '/commune/44109', titre: 'Prix', lignes: 'a=1', source: 'DVF', version: '81d3926f' },
			navigateur: 'Mozilla/5.0 (test)'
		});
		expect(setHeaders).toHaveBeenCalledWith({ 'cache-control': 'no-store', 'x-robots-tag': 'noindex, nofollow' });
	});

	it('comes from the page left behind when the visitor used the footer, without JavaScript', () => {
		const { data } = charger('/signaler', { referer: 'https://datacommune.fr/adresse/44109_1234_00012' });

		expect(data.contexte).toEqual({ page: '/adresse/44109_1234_00012', titre: null, lignes: null, source: null, version: 'aaaa1111' });
		expect(data.navigateur).toBeNull();
	});
});

describe('/signaler — the form relayed to the API', () => {
	it('posts the message and its context to the internal address of the API, with the address of the visitor', async () => {
		appelApi.mockResolvedValue(reponse(201, { reference: '0A1B2C3D4E' }));

		const resultat = await envoyer({ ...SAISIE, email: ' Lecteur@Example.fr ' });

		expect(resultat).toEqual({ recu: { reference: '0A1B2C3D4E', retour: '/commune/44109' } });
		expect(appelApi).toHaveBeenCalledTimes(1);
		const [url, init] = appelApi.mock.calls[0];
		expect(url).toBe('http://api:8000/v1/signalements');
		expect(init?.method).toBe('POST');
		const entetes = new Headers(init?.headers);
		expect(entetes.get('content-type')).toBe('application/json');
		expect(entetes.get('cf-connecting-ip')).toBe('203.0.113.7');
		expect(entetes.get('x-forwarded-for')).toBe('203.0.113.7');
		expect(JSON.parse(init?.body as string)).toEqual({
			commentaire: SAISIE.commentaire,
			email: 'Lecteur@Example.fr',
			page: '/commune/44109',
			titre: 'Prix médian des maisons',
			lignes: 'dvf.prix_m2_median_maison=312',
			source: 'DVF',
			version_site: '81d3926f',
			largeur: 1280,
			user_agent: 'Mozilla/5.0 (test)',
			piege: null
		});
	});

	it('hands the honeypot to the API, which answers a robot like anyone else', async () => {
		appelApi.mockResolvedValue(reponse(201, { reference: '0A1B2C3D4E' }));

		const resultat = await envoyer({ ...SAISIE, site_web: 'https://spam.example' });

		expect(JSON.parse(appelApi.mock.calls[0][1]?.body as string).piege).toBe('https://spam.example');
		expect(resultat).toEqual({ recu: { reference: '0A1B2C3D4E', retour: '/commune/44109' } });
	});

	it('sends nothing the form was not given: no width without JavaScript, no forged page', async () => {
		appelApi.mockResolvedValue(reponse(201, { reference: '0A1B2C3D4E' }));

		await envoyer({ commentaire: SAISIE.commentaire, page: 'https://ailleurs.example/', largeur: 'abc', version: '<script>' });

		expect(JSON.parse(appelApi.mock.calls[0][1]?.body as string)).toMatchObject({
			email: null,
			page: null,
			titre: null,
			lignes: null,
			source: null,
			version_site: 'aaaa1111',
			largeur: null
		});
	});

	it.each([
		['a message too short', { commentaire: 'court' }, { commentaire: 'court' }],
		['a message too long', { commentaire: 'x'.repeat(2001) }, { commentaire: 'long' }],
		['an e-mail address that is not one', { email: 'pas-un-email' }, { email: 'invalide' }]
	])('refuses %s without calling the API, and gives back what was typed', async (_nom, champs, erreurs) => {
		const saisie = { ...SAISIE, ...champs };

		const resultat = await envoyer(saisie);

		expect(appelApi).not.toHaveBeenCalled();
		expect(resultat).toMatchObject({
			status: 400,
			data: {
				erreurs,
				valeurs: { commentaire: saisie.commentaire, email: saisie.email },
				contexte: { page: '/commune/44109', titre: 'Prix médian des maisons', version: '81d3926f' }
			}
		});
	});

	it.each([
		['a refusal of the rate limit', () => Promise.resolve(reponse(429)), 429, 'debit'],
		['an outage of the API', () => Promise.resolve(reponse(503)), 503, 'envoi'],
		['a route that does not exist (setting off on the API)', () => Promise.resolve(reponse(404)), 503, 'envoi'],
		['a refusal of the schema', () => Promise.resolve(reponse(422)), 503, 'envoi'],
		['a network failure', () => Promise.reject(new Error('ECONNREFUSED api:8000')), 503, 'envoi']
	])('turns %s into a worded failure that keeps the message', async (_nom, appel, status, panne) => {
		appelApi.mockImplementation(appel);

		const resultat = await envoyer();

		expect(resultat).toMatchObject({ status, data: { panne, valeurs: { commentaire: SAISIE.commentaire } } });
		expect(JSON.stringify(resultat)).not.toContain('ECONNREFUSED');
	});

	it('does not show a reference the API did not shape as one', async () => {
		appelApi.mockResolvedValue(reponse(201, { reference: '<b>x</b>' }));

		expect(await envoyer()).toEqual({ recu: { reference: null, retour: '/commune/44109' } });
	});
});
