import { describe, expect, it } from 'vitest';
import { BORNES, cheminDuSite, lienSignalement, lireContexte, validerSaisie } from './contexte';

describe('cheminDuSite — only a path of the site is ever joined', () => {
	it('keeps the path alone, without query string or fragment', () => {
		expect(cheminDuSite('/adresse/44109_1234_00012?x=1#ventes')).toBe('/adresse/44109_1234_00012');
		expect(cheminDuSite('/')).toBe('/');
	});

	it.each([
		['another origin', 'https://ailleurs.example/commune/44109'],
		['a protocol-relative URL', '//ailleurs.example/x'],
		['a backslash trick', '/\\ailleurs.example'],
		['a relative path', 'commune/44109'],
		['a script URL', 'javascript:alert(1)'],
		['blank', '   '],
		['too long', '/' + 'a'.repeat(BORNES.page)],
		['nothing', null]
	])('refuses %s', (_nom, valeur) => {
		expect(cheminDuSite(valeur)).toBeNull();
	});
});

describe('lienSignalement — the link of the information panel', () => {
	it('carries the page, the title, each served field with its raw value, the source and the version', () => {
		const lien = lienSignalement({
			page: '/commune/44109',
			titre: 'Prix médian des maisons',
			brut: [
				{ fieldId: 'dvf.prix_m2_median_maison', valeur: '3120' },
				{ fieldId: 'dvf.nb_ventes_maison', valeur: 'null' }
			],
			source: 'DVF',
			version: '81d3926f'
		});

		const url = new URL(lien, 'https://datacommune.fr');
		expect(url.pathname).toBe('/signaler');
		expect(Object.fromEntries(url.searchParams)).toEqual({
			de: '/commune/44109',
			titre: 'Prix médian des maisons',
			lignes: 'dvf.prix_m2_median_maison=3120 ; dvf.nb_ventes_maison=null',
			source: 'DVF',
			v: '81d3926f'
		});
	});

	it('leaves out what the panel does not have and stays within the bounds', () => {
		const lien = lienSignalement({
			page: '/adresse/44109_1234_00012?x=1',
			titre: 't'.repeat(500),
			brut: Array.from({ length: 40 }, (_, i) => ({ fieldId: `champ.numero_${i}`, valeur: 'x'.repeat(30) })),
			source: null,
			version: 'unknown'
		});

		const params = new URL(lien, 'https://datacommune.fr').searchParams;
		expect(params.get('de')).toBe('/adresse/44109_1234_00012');
		expect(params.get('titre')).toHaveLength(BORNES.titre);
		expect(params.get('lignes')!.length).toBeLessThanOrEqual(BORNES.lignes);
		expect(params.has('source')).toBe(false);
	});
});

describe('lireContexte — what the page joins, read on the server', () => {
	const serveur = 'aaaa1111';

	it('reads the link of the panel', () => {
		const url = new URL('https://datacommune.fr/signaler?de=%2Fcommune%2F44109&titre=Prix&lignes=a%3D1&source=DVF&v=81d3926f');

		expect(lireContexte(url, null, serveur)).toEqual({
			page: '/commune/44109',
			titre: 'Prix',
			lignes: 'a=1',
			source: 'DVF',
			version: '81d3926f'
		});
	});

	it('falls back on the page the visitor comes from, same origin only, and on the version of the server', () => {
		const url = new URL('https://datacommune.fr/signaler');

		expect(lireContexte(url, 'https://datacommune.fr/adresse/44109_1234_00012?x=1', serveur)).toEqual({
			page: '/adresse/44109_1234_00012',
			titre: null,
			lignes: null,
			source: null,
			version: serveur
		});
		expect(lireContexte(url, 'https://ailleurs.example/commune/44109', serveur).page).toBeNull();
		expect(lireContexte(url, 'pas une url', serveur).page).toBeNull();
		// Coming back to the form after a refusal is not a page to report.
		expect(lireContexte(url, 'https://datacommune.fr/signaler?de=%2Fx', serveur).page).toBeNull();
	});

	it('never trusts what the address bar carries: bounds, path of the site, shape of a version', () => {
		const url = new URL('https://datacommune.fr/signaler');
		url.searchParams.set('de', 'https://ailleurs.example/');
		url.searchParams.set('titre', 't'.repeat(900));
		url.searchParams.set('lignes', 'l'.repeat(900));
		url.searchParams.set('source', 's'.repeat(900));
		url.searchParams.set('v', '<script>');

		const contexte = lireContexte(url, null, serveur);

		expect(contexte.page).toBeNull();
		expect(contexte.titre).toHaveLength(BORNES.titre);
		expect(contexte.lignes).toHaveLength(BORNES.lignes);
		expect(contexte.source).toHaveLength(BORNES.source);
		expect(contexte.version).toBe(serveur);
	});
});

describe('validerSaisie — the two typed fields', () => {
	it('accepts a message alone, and with an e-mail address', () => {
		expect(validerSaisie('Le prix est faux.', '')).toEqual({});
		expect(validerSaisie('Le prix est faux.', 'lecteur@example.fr')).toEqual({});
	});

	it('names what is wrong', () => {
		expect(validerSaisie('court', '')).toEqual({ commentaire: 'court' });
		expect(validerSaisie('x'.repeat(BORNES.commentaireMax + 1), '')).toEqual({ commentaire: 'long' });
		expect(validerSaisie('Le prix est faux.', 'pas-un-email')).toEqual({ email: 'invalide' });
		expect(validerSaisie('Le prix est faux.', `a@${'b'.repeat(250)}.fr`)).toEqual({ email: 'invalide' });
	});
});
