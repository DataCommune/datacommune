// `/llms.txt` and the shared list of sources (04.7-14): every URL pattern is served by a route of the
// repository, no address page is presented as indexed, and every main source has the producer and
// the licence that the source catalogue of the API declares.
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { EXTRAITS_EXPORT_COMMUNE } from './exemple-export-commune';
import { ATTRIBUTION, CODE_SOURCE_URL, MODELES_URL, SOURCES_PRINCIPALES, modeleUrl, texteLlms } from './ouverture';
import { GET } from '../../routes/llms.txt/+server';

const ROUTES = resolve(__dirname, '../../routes');
const CATALOGUE = readFileSync(resolve(__dirname, '../../../../api/backend/app/common/source_catalog.py'), 'utf8');

/** Producer and licence of *cle* in the catalogue, read from its `DatasetCatalogEntry`. */
function entreeCatalogue(cle: string): { producteur?: string; licence?: string } {
	const debut = CATALOGUE.indexOf(`"${cle}": DatasetCatalogEntry(`);
	if (debut < 0) return {};
	// Every entry writes its producer and its licence before its `licence_url=`.
	const bloc = CATALOGUE.slice(debut, CATALOGUE.indexOf('licence_url=', debut));
	return { producteur: /producteur="([^"]+)"/.exec(bloc)?.[1], licence: /licence="([^"]+)"/.exec(bloc)?.[1] };
}

/** Whether a route of the repository serves this directory (a page or an endpoint). */
function routeServie(route: string): boolean {
	const dossier = resolve(ROUTES, route);
	return ['+page.svelte', '+server.ts'].some((f) => existsSync(resolve(dossier, f)));
}

describe('modèles d’URL', () => {
	it('chaque modèle est servi par une route du dépôt', () => {
		for (const modele of MODELES_URL) {
			expect(routeServie(modele.route), modele.modele).toBe(true);
		}
	});

	it('la page d’un département a son modèle, avec un exemple et sa condition d’indexation', () => {
		const modele = MODELES_URL.find((m) => m.modele === '/departement/{code}');
		expect(modele).toMatchObject({ route: '(marketing)/departement/[code]', exemple: '/departement/44' });
		expect(modele?.description).toMatch(/prix/);
		expect(modele?.description).toMatch(/Indexée seulement si les prix du département sont servis/);
	});

	// Ianis, 11/11: the examples of the site follow the first example of the home page, Nantes no longer the default.
	it('les exemples de commune, d’adresse, de recherche et de comparaison sont ceux de l’accueil', () => {
		const exemple = (modele: string) => MODELES_URL.find((m) => m.modele === modele)?.exemple;
		expect(exemple('/commune/{code_insee}')).toBe('/commune/31555');
		expect(exemple('/commune/{code_insee}.json')).toBe('/commune/31555.json');
		expect(exemple('/adresse/{cle_ban}')).toBe('/adresse/31555_5904_00008');
		expect(exemple('/communes?q={texte}')).toBe('/communes?q=Toulouse');
		expect(exemple('/comparer?communes={code_insee},{code_insee}')).toBe('/comparer?communes=29232,31555');
		const texte = texteLlms('https://exemple.fr', 'https://api.exemple.fr');
		expect(texte).toContain('https://exemple.fr/adresse/31555_5904_00008');
		expect(texte).not.toMatch(/44109|Nantes/);
	});

	it('aucune page d’adresse ni de document n’est dite indexée', () => {
		for (const modele of MODELES_URL.filter((m) => /adresse|dpe/.test(m.modele))) {
			expect(modele.indexee, modele.modele).toBe(false);
		}
	});
});

describe('sources principales', () => {
	it('reprennent le producteur et la licence du catalogue des sources de l’API', () => {
		for (const source of SOURCES_PRINCIPALES) {
			expect(entreeCatalogue(source.cle), source.cle).toEqual({ producteur: source.producteur, licence: source.licence });
		}
	});

	it('la carte des loyers porte la mention que l’ANIL demande', () => {
		expect(SOURCES_PRINCIPALES.find((s) => s.cle === 'loyers')?.mention).toBe(
			'Estimations ANIL, à partir des données du Groupe SeLoger et de leboncoin'
		);
	});
});

describe('/llms.txt', () => {
	it('annonce le modèle /departement/{code} avec un lien absolu vers son exemple', () => {
		const texte = texteLlms('https://exemple.fr', 'https://api.exemple.fr');
		expect(texte).toContain('[/departement/{code}](https://exemple.fr/departement/44)');
		const ligne = texte.split(String.fromCharCode(10)).find((l) => l.includes('/departement/{code}'));
		expect(ligne).toContain('Indexée seulement si les prix du département sont servis');
		expect(ligne).not.toMatch(/ Indexée[.]$/);
	});

	it('est servi en texte, avec les liens absolus du site et de l’API', async () => {
		const res = await (GET as unknown as () => Response)();
		expect(res.headers.get('content-type')).toBe('text/plain; charset=utf-8');
		const texte = await res.text();
		expect(texte.startsWith('# DataCommune\n')).toBe(true);
		for (const modele of MODELES_URL) expect(texte).toContain(modele.modele);
	});

	it('dit de l’API ce qui est vrai sur l’instance : une clé, et qu’on ne peut pas encore en obtenir sans comptes', () => {
		const ligne = (texte: string) => texte.split(String.fromCharCode(10)).find((l) => l.includes('/developpeurs'));
		expect(ligne(texteLlms('https://exemple.fr', 'https://api.exemple.fr'))).toBe(
			'- [Développeurs](https://exemple.fr/developpeurs) : export JSON sans clé, contrat de l’API. L’API demande une clé ; les clés ne sont pas encore ouvertes.'
		);
		expect(ligne(texteLlms('https://exemple.fr', 'https://api.exemple.fr', true))).toBe(
			'- [Développeurs](https://exemple.fr/developpeurs) : export JSON sans clé, contrat de l’API. L’API demande une clé.'
		);
	});

	it('nomme les limites, l’attribution et les sources, sans URL relative', () => {
		const texte = texteLlms('https://exemple.fr', 'https://api.exemple.fr');
		expect(texte).toContain('https://exemple.fr/ce-que-nous-ne-montrons-pas');
		expect(texte).toContain('https://exemple.fr/datasets');
		expect(texte).toContain('https://api.exemple.fr/openapi.json');
		expect(texte).toContain('## Attribution');
		for (const source of SOURCES_PRINCIPALES) expect(texte).toContain(source.producteur);
		expect(texte).not.toMatch(/\]\(\//);
	});
});

describe('ce que la page développeurs lit ici', () => {
	const SERVEUR = readFileSync(resolve(ROUTES, '(marketing)/commune/[code].json/+server.ts'), 'utf8');

	it('le dépôt du code est le dépôt public DataCommune/datacommune (D-137)', () => {
		expect(CODE_SOURCE_URL).toBe('https://github.com/DataCommune/datacommune');
	});

	it('l’exemple de l’export a son extrait de réponse, au format et avec la phrase de licence que le code sert', () => {
		const exemple = modeleUrl('/commune/{code_insee}.json').exemple ?? '';
		const code = /\/commune\/([^/]+)\.json$/.exec(exemple)?.[1] ?? '';
		const extrait = EXTRAITS_EXPORT_COMMUNE[code];
		expect(extrait, `extrait de ${code}`).toBeDefined();
		expect(extrait).toContain(`"code_insee": "${code}"`);
		const format = /FORMAT_EXPORT_COMMUNE = '([^']+)'/.exec(SERVEUR)?.[1];
		const licence = /const LICENCE =\s*'([^']+)'/.exec(SERVEUR)?.[1];
		expect(format).toBe('datacommune.commune.v1');
		expect(licence).toBeTruthy();
		for (const texte of Object.values(EXTRAITS_EXPORT_COMMUNE)) {
			expect(texte).toContain(`"format": "${format}"`);
			expect(texte).toContain(`"licence": "${licence}"`);
		}
	});

	it('la phrase de licence de l’export ne fait pas de la mention de DataCommune une condition, comme l’attribution', () => {
		expect(SERVEUR).toContain('est bienvenu sans être une condition');
		expect(ATTRIBUTION).toContain('est bienvenu sans être une condition');
	});
});
