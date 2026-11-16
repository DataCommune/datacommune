// The server bought for the end of November runs the infra/prod stack: docker-compose.public.yml,
// then the docker-compose.prod.yml overlay, which mounts api/Caddyfile.prod in place of
// api/Caddyfile.public. The two files are standalone (Caddy snippets do not cross files), so every
// route added to the public one after 2026-10-24 must exist in the prod one too, or the switch
// silently drops it. Found missing on 2026-11-13: the audience count (/count), the address maps
// (/carte/*), closed sign-up and closed error reports on the API hosts. These are checks of the
// configuration text; `caddy validate` of both files is the Docker test of infra/*/tests.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const RACINE = resolve(__dirname, '../../..');
const lire = (f: string) => readFileSync(resolve(RACINE, f), 'utf8').replace(/\r\n/g, '\n');
const publicCf = lire('api/Caddyfile.public');
const prodCf = lire('api/Caddyfile.prod');

/** The block opened by *ouverture* at column 0, up to its closing brace at column 0. */
function bloc(texte: string, ouverture: string): string {
	const debut = texte.indexOf(`\n${ouverture}\n`);
	expect(debut, ouverture).toBeGreaterThanOrEqual(0);
	return texte.slice(debut + 1, texte.indexOf('\n}\n', debut + 1) + 2);
}

/** The site block of *hote*, its snippet imports expanded (one level, as the files use them). */
function site(texte: string, hote: string): string {
	return bloc(texte, `http://${hote} {`).replace(/^\s*import (\S+)\s*$/gm, (ligne, nom: string) => {
		const debut = texte.indexOf(`\n(${nom}) {\n`);
		return debut < 0 ? ligne : bloc(texte, `(${nom}) {`);
	});
}

/** The inner block `handle <chemin> {`, indentation and comments removed, for comparison. */
function handle(texte: string, chemin: string): string {
	const m = new RegExp(`\\n(\\t+)handle ${chemin.replace(/[*/]/g, '\\$&')} \\{\\n([\\s\\S]*?)\\n\\1\\}`).exec(texte);
	return (m?.[2] ?? '')
		.split('\n')
		.map((l) => l.trim())
		.filter((l) => l && !l.startsWith('#'))
		.join('\n');
}

/** Matcher lines and the `respond` lines that use them, comments removed. */
function fermetures(texte: string): string[] {
	return texte
		.split('\n')
		.map((l) => l.trim())
		.filter((l) => /^@(inscription|signalement) path |^respond @(inscription|signalement) 404$/.test(l));
}

const apexPublic = site(publicCf, 'datacommune.fr');
const apiPublic = site(publicCf, 'api.datacommune.fr');

describe('Caddyfile.prod garde les routes de Caddyfile.public', () => {
	it('apex : /count va à GoatCounter exactement comme sur le conteneur public (mesure d’audience)', () => {
		const attendu = handle(apexPublic, '/count');
		expect(attendu).toContain('reverse_proxy goatcounter:8080');
		expect(handle(site(prodCf, 'datacommune.fr'), '/count')).toBe(attendu);
	});

	it('apex : /carte/* est relayé à l’API comme sur le conteneur public (cartes de la fiche)', () => {
		const attendu = handle(apexPublic, '/carte/*');
		expect(attendu).toContain('rewrite * /v1{uri}');
		expect(handle(site(prodCf, 'datacommune.fr'), '/carte/*')).toBe(attendu);
	});

	it.each(['api.datacommune.fr', 'api-prod.datacommune.fr'])(
		'%s : inscription et signalements fermés depuis Internet (404)',
		(hote) => {
			const attendu = fermetures(apiPublic);
			expect(attendu).toHaveLength(4);
			expect(fermetures(site(prodCf, hote))).toEqual(attendu);
		}
	);

	it('préproduction : la recette ne compte aucune visite (204 sans GoatCounter) et garde ses cartes', () => {
		const recette = site(prodCf, 'prod.datacommune.fr');
		expect(handle(recette, '/count')).toBe('respond 204');
		expect(handle(recette, '/carte/*')).toBe(handle(apexPublic, '/carte/*'));
	});

	it('un seul chemin publie GoatCounter, sur le seul apex', () => {
		expect(prodCf.match(/reverse_proxy goatcounter/g)).toHaveLength(1);
	});
});
