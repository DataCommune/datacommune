// The versioned public stack keeps the promises of the audience measurement (04.7-14b): no
// third-party source in the CSP, nothing that identifies the visitor reaches GoatCounter, the count
// stays closed until the site is set, its image is pinned, its limit fits a single address, its
// dashboard is never published, and only the public build turns the count on. These are checks of
// the configuration text: the behaviour of Caddy itself is proven by the Docker test of 14b.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { cheminProvenance } from './mesure-audience';

const RACINE = resolve(__dirname, '../../..');
const caddy = readFileSync(resolve(RACINE, 'api/Caddyfile.public'), 'utf8').replace(/\r\n/g, '\n');
const compose = readFileSync(resolve(RACINE, 'api/docker-compose.public.yml'), 'utf8').replace(/\r\n/g, '\n');

/** The block of *ouverture* (`http://datacommune.fr {`), up to its closing brace at column 0. */
function bloc(texte: string, ouverture: string): string {
	const debut = texte.indexOf(ouverture);
	expect(debut, ouverture).toBeGreaterThanOrEqual(0);
	return texte.slice(debut, texte.indexOf('\n}\n', debut));
}

/** The service *nom* of the compose file, up to the next service or top-level key. */
function service(nom: string): string {
	const debut = compose.indexOf(`\n  ${nom}:\n`);
	expect(debut, nom).toBeGreaterThanOrEqual(0);
	const suite = compose.slice(debut + 1).search(/\n {2}[a-z_-]+:\n|\n[a-z]+:\n/);
	return compose.slice(debut, debut + 1 + suite);
}

const site = bloc(caddy, 'http://datacommune.fr {');
const count = /handle \/count \{([\s\S]*?)\n\t\}/.exec(site)?.[1] ?? '';

describe('Caddy — datacommune.fr', () => {
	// 05-07 lot 5: the CSP of the pages is written by the frontend (kit.csp of svelte.config.js), with
	// the API it is built for as its only other source; Caddy sets none, it would replace it.
	it('la CSP ne cite aucune source tierce (beacon Cloudflare retiré, AUD-01)', async () => {
		expect(site).not.toMatch(/Content-Security-Policy/);
		const { default: config } = await import('../../svelte.config.js');
		const sources = Object.values(config.kit?.csp?.directives ?? {}).flat() as string[];
		expect(sources.length).toBeGreaterThan(0);
		expect(sources.join(' ')).not.toMatch(/cloudflareinsights/);
		const apiCompose = /VITE_API_URL: (\S+)/.exec(service('frontend'))?.[1];
		expect(apiCompose).toBe('https://api.datacommune.fr');
		const tiers = sources.filter((s) => /^https?:/.test(s));
		expect(tiers).toEqual([new URL(process.env.VITE_API_URL || 'http://127.0.0.1:8000').origin]);
	});

	it('seul /count va à GoatCounter, sans adresse IP, référent ni cookie du visiteur', () => {
		expect(count).toContain('reverse_proxy goatcounter:8080');
		// Every header the RealIP middleware of GoatCounter reads (zgo.at/zhttp mware/realip.go), the
		// two other address carriers, the referrer and the cookies (Codex 14b n° 2).
		const retires = [
			'CF-Connecting-IP',
			'Fly-Client-IP',
			'X-Azure-SocketIP',
			'X-Real-IP',
			'X-Forwarded-For',
			'Forwarded',
			'True-Client-IP',
			'Referer',
			'Cookie'
		];
		for (const entete of retires) expect(count, entete).toContain(`header_up -${entete}`);
		// Nothing is put back: the only header set upstream is the vhost (Codex 14b n° 6).
		const poses = (count.match(/header_up [^-\s][^\n]*/g) ?? []).map((ligne) => ligne.trim());
		expect(poses).toEqual(['header_up Host localhost']);
		expect(count).not.toContain('import visitor_ip');
		// No other route of GoatCounter (dashboard, count.js) is published, on any host.
		expect(caddy.match(/reverse_proxy goatcounter/g)).toHaveLength(1);
	});

	// Provenance by channel (2026-11-11): `/venu-de/<canal>` is one more value of `p`. Caddy routes on
	// the path `/count` alone and neither reads nor rewrites the query, so no change of configuration
	// is needed for it; the Docker test proves it with the real Caddy.
	it('le comptage est routé sur le seul chemin /count : la requête (p=/venu-de/<canal>) passe sans être lue ni réécrite', () => {
		expect(site.match(/handle \/count\b[^\n]*/g)).toEqual(['handle /count {']);
		expect(count).not.toMatch(/\b(rewrite|uri|query|path|path_regexp|method|header_regexp)\b/);
		const filtres = (count.match(/@\w+[^\n]*/g) ?? []).map((ligne) => ligne.trim());
		expect(filtres).toEqual(['@collecte_fermee expression `"{$MESURE_AUDIENCE_ACTIVE:non}" != "oui"`', '@collecte_fermee 204']);
		const essai = readFileSync(resolve(RACINE, 'infra/public/tests/test-mesure-audience.sh'), 'utf8');
		expect(essai).toContain(`requete '/count?p=${encodeURIComponent(cheminProvenance('reddit'))}'`);
	});

	it('porte : tant que MESURE_AUDIENCE_ACTIVE ne vaut pas « oui », Caddy répond lui-même 204 (Codex 14b n° 4)', () => {
		expect(count).toContain('@collecte_fermee expression `"{$MESURE_AUDIENCE_ACTIVE:non}" != "oui"`');
		expect(count).toContain('respond @collecte_fermee 204');
		expect(service('caddy')).toContain('MESURE_AUDIENCE_ACTIVE: ${MESURE_AUDIENCE_ACTIVE:-non}');
	});
});

describe('Compose — goatcounter', () => {
	const goatcounter = service('goatcounter');

	it('image officielle épinglée par digest', () => {
		expect(goatcounter).toMatch(/image: arp242\/goatcounter:[\d.]+@sha256:[0-9a-f]{64}\n/);
	});

	it('une limite de comptage faite pour une seule adresse, celle de Caddy (Codex 14b n° 5)', () => {
		const commande = /command: (\[[^\]]+\])/.exec(goatcounter)?.[1] ?? '[]';
		const args = JSON.parse(commande) as string[];
		expect(args.slice(0, 2)).toEqual(['serve', '-automigrate']);
		const limite = /^count:(\d+)\/1$/.exec(args[args.indexOf('-ratelimit') + 1] ?? '')?.[1];
		expect(Number(limite)).toBeGreaterThan(4);
	});

	it('tableau de bord publié sur la seule boucle locale du CT', () => {
		const ports = goatcounter.match(/- "([^"]+)"/g) ?? [];
		expect(ports.length).toBeGreaterThan(0);
		for (const port of ports) expect(port, port).toMatch(/^- "127\.0\.0\.1:/);
	});

	it('seule la construction publique active la mesure', () => {
		expect(service('frontend')).toMatch(/VITE_MESURE_AUDIENCE: "true"/);
		const dockerfile = readFileSync(resolve(RACINE, 'frontend/Dockerfile'), 'utf8');
		expect(dockerfile).toMatch(/^ARG VITE_MESURE_AUDIENCE=false$/m);
	});
});
