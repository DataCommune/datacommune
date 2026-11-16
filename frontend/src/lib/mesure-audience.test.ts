// Audience measurement (04.7-14b, audit A9): the path sent is built from the route the page matched,
// never from the URL typed; an address, a document, a search or a query string never leaves the
// page; the request carries no title, referrer nor credential, and a driven browser sends nothing.
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CANAUX, canalProvenance, cheminMesure, cheminProvenance, compter, rechercheSansCanal, urlComptage } from './mesure-audience';

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
});

/** A page answered 200 for route *id*, at *pathname*, with *params*. */
const vue = (id: string | null, pathname: string, params: Record<string, string> = {}, statut = 200) =>
	cheminMesure({ id, params, pathname, statut });

describe('cheminMesure — construit depuis la route, jamais depuis l’URL', () => {
	it.each([
		['/(marketing)', '/', '/'],
		['/(marketing)/communes', '/communes', '/communes'],
		['/(marketing)/comparer', '/comparer', '/comparer'],
		['/(marketing)/ce-que-nous-ne-montrons-pas', '/ce-que-nous-ne-montrons-pas', '/ce-que-nous-ne-montrons-pas']
	])('%s → %s', (id, pathname, attendu) => {
		expect(vue(id, pathname)).toBe(attendu);
	});

	it('une fiche commune garde son code INSEE, s’il en a la forme', () => {
		expect(vue('/(marketing)/commune/[code]', '/commune/44109', { code: '44109' })).toBe('/commune/44109');
		expect(vue('/(marketing)/commune/[code]', '/commune/2A004', { code: '2A004' })).toBe('/commune/2A004');
		expect(vue('/(marketing)/commune/[code]', '/commune/x', { code: '12 rue' })).toBe('/commune');
	});

	it('une adresse ou un document est un seul chemin, quel que soit l’identifiant', () => {
		expect(vue('/(marketing)/adresse/[id]', '/adresse/44109_zz04_00004', { id: '44109_zz04_00004' })).toBe('/adresse');
		expect(vue('/(marketing)/dpe/[numero]', '/dpe/2644E8000030X', { numero: '2644E8000030X' })).toBe('/dpe');
	});

	it('une URL encodée qui atteint la fiche d’adresse ou de document ne transmet rien d’autre (Codex 14b n° 1)', () => {
		expect(vue('/(marketing)/adresse/[id]', '/%61dresse/44109_zz01_00002', { id: '44109_zz01_00002' })).toBe('/adresse');
		expect(vue('/(marketing)/dpe/[numero]', '/d%70e/2644E8000030X', { numero: '2644E8000030X' })).toBe('/dpe');
	});

	it('une page d’erreur est comptée par son genre, jamais par l’URL saisie', () => {
		expect(vue(null, '/12-rue-de-la-paix', {}, 404)).toBe('/erreur-404');
		expect(vue('/(marketing)/adresse/[id]', '/adresse/44109_zz04_00004', { id: '44109_zz04_00004' }, 503)).toBe('/erreur');
	});

	it.each([
		['/(app)/settings', '/settings', 200],
		['/(app)/admin/users', '/admin/users', 200],
		['/(auth)/login', '/login', 200],
		// The public build switches the accounts off: these routes answer 404 there (Codex 14b n° 7).
		[null, '/login', 404],
		[null, '/l%6Fgin', 404],
		[null, '/admin/users', 404],
		[null, '/mon-espace', 404]
	])('%s (%s, %i) : l’espace des comptes n’est jamais compté', (id, pathname, statut) => {
		expect(vue(id, pathname, {}, statut)).toBeNull();
	});
});

// Provenance by channel (2026-11-11): a link the publisher spreads carries `?src=<canal>`; the entry page
// counts one more virtual page, `/venu-de/<canal>`. The referrer is never read, and what is sent is a
// name of the closed list, never the value typed in the URL.
describe('canalProvenance — liste fermée, comparaison exacte', () => {
	const canal = (recherche: string) => canalProvenance(new URL(`https://datacommune.fr/${recherche}`));

	it('la liste des canaux est celle validée par l’éditeur le 2026-11-11', () => {
		expect([...CANAUX]).toEqual(['reddit', 'linkedin', 'mail', 'presse', 'forum', 'mastodon', 'bluesky', 'datagouv', 'linuxfr', 'hn', 'papier']);
		for (const nom of CANAUX) expect(nom).toMatch(/^[a-z]+$/);
	});

	it.each([...CANAUX])('canal connu : ?src=%s', (nom) => {
		expect(canal(`?src=${nom}`)).toBe(nom);
		expect(canal(`adresse/44109_zz04_00004?page=2&src=${nom}#documents`)).toBe(nom);
		expect(cheminProvenance(nom)).toBe(`/venu-de/${nom}`);
	});

	it.each([
		['canal inconnu', '?src=tiktok'],
		['aucun paramètre', ''],
		['valeur vide', '?src='],
		['majuscules', '?src=Reddit'],
		['tout en majuscules', '?src=REDDIT'],
		['chemin piégé', '?src=reddit/../x'],
		['chemin piégé encodé', '?src=reddit%2F..%2Fx'],
		['suffixe', '?src=reddit-frenchtech'],
		['espace', '?src=reddit%20'],
		['espace par « + »', '?src=+reddit'],
		['saut de ligne encodé', '?src=reddit%0A'],
		['deux paramètres src', '?src=reddit&src=linkedin'],
		['deux fois le même canal', '?src=reddit&src=reddit'],
		['un connu puis un piégé', '?src=reddit&src=x%2F..'],
		['autre nom de paramètre', '?source=reddit&utm_source=reddit&SRC=reddit'],
		['propriété héritée d’un objet', '?src=constructor'],
		['dans l’ancre seulement', '#src=reddit']
	])('%s (%s) : rien n’est compté', (_cas, recherche) => {
		expect(canal(recherche)).toBeNull();
	});

	it('une écriture encodée d’un nom connu rend le nom de la liste, jamais la valeur de l’URL', () => {
		const rendu = canal('?src=%72eddit');
		expect(rendu).toBe('reddit');
		expect(CANAUX).toContain(rendu);
	});
});

describe('rechercheSansCanal — adresse canonique et carte de partage', () => {
	const sans = (recherche: string) => rechercheSansCanal(new URL(`https://datacommune.fr/communes${recherche}`));

	it('retire le seul paramètre de canal, quelle que soit sa valeur', () => {
		expect(sans('?src=reddit')).toBe('');
		expect(sans('?q=nantes&src=reddit')).toBe('?q=nantes');
		expect(sans('?src=x%2F..&departement=44&page=2&src=reddit')).toBe('?departement=44&page=2');
	});

	it('sans paramètre de canal, la requête est rendue telle qu’écrite', () => {
		expect(sans('')).toBe('');
		expect(sans('?q=4%20rue%20Jean%20Jaur%C3%A8s')).toBe('?q=4%20rue%20Jean%20Jaur%C3%A8s');
	});
});

describe('urlComptage', () => {
	it('envoie le seul chemin agrégé', () => {
		expect(urlComptage('/adresse')).toBe('/count?p=%2Fadresse');
		expect(urlComptage('/commune/44109')).toBe('/count?p=%2Fcommune%2F44109');
		expect(urlComptage(cheminProvenance('reddit'))).toBe('/count?p=%2Fvenu-de%2Freddit');
	});
});

describe('compter', () => {
	it('appelle le point de collecte du site sans titre, sans référent, sans identifiant', async () => {
		const fetch = vi.fn(async () => new Response(null, { status: 204 }));
		vi.stubGlobal('fetch', fetch);
		vi.stubGlobal('navigator', { webdriver: false });
		await compter('/adresse');
		expect(fetch).toHaveBeenCalledTimes(1);
		const [url, options] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('/count?p=%2Fadresse');
		expect(options).toMatchObject({ method: 'GET', credentials: 'omit', referrerPolicy: 'no-referrer', cache: 'no-store' });
	});

	it('un navigateur piloté n’envoie rien : GoatCounter garderait un robot avec son User-Agent (Codex 14b n° 3)', async () => {
		const fetch = vi.fn(async () => new Response(null, { status: 204 }));
		vi.stubGlobal('fetch', fetch);
		vi.stubGlobal('navigator', { webdriver: true });
		await compter('/adresse');
		expect(fetch).not.toHaveBeenCalled();
	});

	it('une collecte qui échoue ne casse rien et ne se signale pas', async () => {
		vi.stubGlobal('navigator', { webdriver: false });
		vi.stubGlobal('fetch', vi.fn(async () => {
			throw new TypeError('réseau coupé');
		}));
		await expect(compter('/')).resolves.toBeUndefined();
	});
});
