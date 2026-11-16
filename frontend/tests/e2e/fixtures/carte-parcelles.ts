// Routes of the map on the origin of the site (`/carte/…`, relayed by Caddy in production), answered in
// the browser for the specs of the interactive parcel map (04.10). The answers are the contract examples
// of `contracts/carte/exemples/`: the envelopes the API model reloads identically (04.10-02).
//
// Each route keeps the URLs it was asked and its answer is replaced per test (`repondre`), without
// editing this file. Any other path under `/carte/` gets a bare 404 and is recorded: a spec ends with
// `attendreAucuneCarteNonReconnue`, so a map request nobody planned fails it.
//
// The specs of the fiche that never open the map answer only the two pictures of its head
// (`repondreCarteStatique`, 04.10-10): a strict pattern, never a catch-all that would answer a picture to a
// JSON route of the map. Every other map path goes to the same record, and fails the same check.
//
// The pattern is anchored at the root of the origin: the components of the map live under
// `src/lib/components/adresse/carte/`, and the dev server serves them at a path that contains `/carte/`.
import { readFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';
import { expect, type Page } from '@playwright/test';

export type CleCarte = 'contexte' | 'parcelles' | 'fond' | 'ventes' | 'images';

export interface ReponseCarte {
	status: number;
	/** JSON body, serialised as is. */
	json?: unknown;
	/** Raw body (an image, or nothing). */
	body?: Buffer | string;
	contentType?: string;
	headers?: Record<string, string>;
}

type Repondeur = ReponseCarte | ((url: URL) => ReponseCarte);

export interface RoutesCarte {
	/** URLs asked on each route, in the order of the requests. */
	demandes: Record<CleCarte, string[]>;
	/** Replaces the answer of one route from now on. */
	repondre(cle: CleCarte, reponse: Repondeur): void;
}

/** Every path of the site under `/carte/`, and nothing served by the dev server under `/src/`. */
export const SOUS_CARTE = /^https?:\/\/[^/?#]+\/carte\//;

const CHEMINS: ReadonlyArray<[CleCarte, RegExp]> = [
	['parcelles', /^\/carte\/parcelles\/\d+\/\d+\/\d+$/],
	['fond', /^\/carte\/fond\/\d+\/\d+\/\d+\.png$/],
	['contexte', /^\/carte\/[^/]+\/contexte$/],
	['ventes', /^\/carte\/[^/]+\/parcelle\/[^/]+\/ventes$/],
	// The two pictures of the head of the fiche (04.7-17), not a route of the interactive map.
	['images', /^\/carte\/[^/]+\/(plan|parcelles)\.png$/]
];

const exemple = (nom: string): unknown =>
	JSON.parse(readFileSync(new URL(`../../../../contracts/carte/exemples/${nom}.json`, import.meta.url), 'utf8'));

export const EXEMPLE_CONTEXTE = exemple('contexte') as {
	donnees: { centre: { lon: number; lat: number }; parcelle_adresse: { id_parcelle: string } };
};
export const EXEMPLE_TUILE = exemple('tuile-parcelles') as {
	donnees: { collection: { features: Array<{ id: string; properties: { contenance: number | null; ventes: number | null } }> } };
};
export const EXEMPLE_VENTES = exemple('ventes-parcelle');

/** A plain grey PNG of *cote* × *cote* pixels. */
export function pngGris(cote: number): Buffer {
	const crc = (b: Buffer) => {
		let c = ~0;
		for (const x of b) {
			c ^= x;
			for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
		}
		return ~c >>> 0;
	};
	const bloc = (type: string, data: Buffer) => {
		const t = Buffer.concat([Buffer.from(type, 'ascii'), data]);
		const n = Buffer.alloc(4);
		n.writeUInt32BE(data.length);
		const c = Buffer.alloc(4);
		c.writeUInt32BE(crc(t));
		return Buffer.concat([n, t, c]);
	};
	const ihdr = Buffer.alloc(13);
	ihdr.writeUInt32BE(cote, 0);
	ihdr.writeUInt32BE(cote, 4);
	ihdr.set([8, 0, 0, 0, 0], 8); // 8-bit greyscale, not interlaced
	const lignes = Buffer.alloc(cote * (cote + 1), 0xc8);
	for (let y = 0; y < cote; y++) lignes[y * (cote + 1)] = 0; // filter byte of each row
	return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), bloc('IHDR', ihdr), bloc('IDAT', deflateSync(lignes)), bloc('IEND', Buffer.alloc(0))]);
}

/**
 * The two pictures of the head of a fiche (04.7-17): `/carte/{BAN key}/plan.png` and `…/parcelles.png`. The
 * key is that of `BAN_ID_PATTERN` (api/backend/app/adresse/router.py), percent-encoded in the path
 * (`rapport/carte.py`); none of the paths of the interactive map has this shape.
 */
export const IMAGE_DE_TETE = /^\/carte\/[0-9][0-9AB][0-9]{3}_[0-9A-Za-z]{4,6}(?:_[0-9]{5}(?:_[^/?#]{1,30})?)?\/(?:plan|parcelles)\.png$/;

const nonReconnues = new WeakMap<Page, string[]>();

/** The record of the map requests that no installed route knows, one per page, shared by the installers. */
function releve(page: Page): string[] {
	let inconnues = nonReconnues.get(page);
	if (!inconnues) {
		inconnues = [];
		nonReconnues.set(page, inconnues);
	}
	return inconnues;
}

/**
 * Answers the two pictures of the head of the fiche for *page*, with a grey PNG of *cote* pixels or the 503
 * of a failing IGN, and returns the URLs asked. Any other path under `/carte/` (the routes of the
 * interactive map included) gets a bare 404 and is recorded for `attendreAucuneCarteNonReconnue`. To
 * register after `installLocalNetworkGuard`; registered again on the same page, the last one answers.
 */
export async function repondreCarteStatique(page: Page, options: { statut?: 200 | 503; cote?: number } = {}): Promise<string[]> {
	const { statut = 200, cote = 448 } = options;
	const image = pngGris(cote);
	const demandees: string[] = [];
	const inconnues = releve(page);
	await page.route(SOUS_CARTE, async (route) => {
		const url = new URL(route.request().url());
		if (!IMAGE_DE_TETE.test(url.pathname)) {
			inconnues.push(`${route.request().method()} ${url.pathname}`);
			await route.fulfill({ status: 404, body: '' });
			return;
		}
		demandees.push(url.href);
		await (statut === 200 ? route.fulfill({ status: 200, contentType: 'image/png', body: image }) : route.fulfill({ status: 503, body: '' }));
	});
	return demandees;
}

/**
 * Answers the routes of the map for *page*. To register after `installLocalNetworkGuard`, so that it
 * takes precedence over it for these paths.
 */
export async function installerRoutesCarte(page: Page): Promise<RoutesCarte> {
	const reponses: Record<CleCarte, Repondeur> = {
		contexte: { status: 200, json: EXEMPLE_CONTEXTE, headers: { 'Cache-Control': 'no-store' } },
		parcelles: { status: 200, json: EXEMPLE_TUILE, headers: { 'Cache-Control': 'public, max-age=3600' } },
		fond: { status: 200, body: pngGris(256), contentType: 'image/png' },
		ventes: { status: 200, json: EXEMPLE_VENTES, headers: { 'Cache-Control': 'no-store' } },
		images: { status: 200, body: pngGris(448), contentType: 'image/png' }
	};
	const demandes: Record<CleCarte, string[]> = { contexte: [], parcelles: [], fond: [], ventes: [], images: [] };
	const inconnues = releve(page);

	await page.route(SOUS_CARTE, async (route) => {
		const url = new URL(route.request().url());
		const cle = CHEMINS.find(([, motif]) => motif.test(url.pathname))?.[0];
		if (!cle) {
			// Last resort: never a picture, never a body a reader could take for data.
			inconnues.push(`${route.request().method()} ${url.pathname}`);
			await route.fulfill({ status: 404, body: '' });
			return;
		}
		demandes[cle].push(url.pathname);
		const repondeur = reponses[cle];
		const reponse = typeof repondeur === 'function' ? repondeur(url) : repondeur;
		await route.fulfill({
			status: reponse.status,
			headers: reponse.headers,
			...(reponse.json !== undefined
				? { contentType: 'application/json', body: JSON.stringify(reponse.json) }
				: { contentType: reponse.contentType, body: reponse.body ?? '' })
		});
	});

	return {
		demandes,
		repondre(cle, reponse) {
			reponses[cle] = reponse;
		}
	};
}

/** No request under `/carte/` went to a path that no installed route knows. */
export function attendreAucuneCarteNonReconnue(page: Page): void {
	const inconnues = nonReconnues.get(page);
	expect(inconnues, 'installerRoutesCarte ou repondreCarteStatique doit être appelé sur cette page').toBeDefined();
	expect(inconnues, `requêtes de carte qu’aucune route ne reconnaît : ${JSON.stringify(inconnues)}`).toEqual([]);
}
