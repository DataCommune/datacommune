// HTTP proof of the headers the production server actually sends (KI-P3, KI-P12).
//
// Runs against the built artifact (`npm run build` first), by starting the same
// entry the image starts (Dockerfile CMD) on a free loopback port and fetching it.
// No browser and no API: `/`, `/developpeurs` and `/robots.txt` are served by
// sirv from `build/`, `/bien` is a 308 rendered by SvelteKit without any API call.
//
// `DC_SERVER_ENTRY` points the test at another entry, e.g. `build/index.js` (the
// bare adapter-node server) to show what the wrapper adds.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SECURITY_HEADERS } from '../src/lib/utils/security-headers.js';

const FRONTEND = fileURLToPath(new URL('..', import.meta.url));
const ENTRY = process.env.DC_SERVER_ENTRY ?? 'server.js';

/** @returns {Promise<number>} */
function freePort() {
	return new Promise((resolve, reject) => {
		const srv = createServer();
		srv.unref();
		srv.on('error', reject);
		srv.listen(0, '127.0.0.1', () => {
			const { port } = /** @type {import('node:net').AddressInfo} */ (srv.address());
			srv.close(() => resolve(port));
		});
	});
}

let child;
let origin;

before(async () => {
	assert.ok(existsSync(new URL('../build/handler.js', import.meta.url)), 'build/ missing: run `npm run build` first');
	const port = await freePort();
	origin = `http://127.0.0.1:${port}`;
	child = spawn(process.execPath, [ENTRY], {
		cwd: FRONTEND,
		env: { ...process.env, HOST: '127.0.0.1', PORT: String(port), ORIGIN: origin },
		stdio: ['ignore', 'ignore', 'inherit']
	});
	const deadline = Date.now() + 15_000;
	for (;;) {
		try {
			await fetch(`${origin}/robots.txt`);
			return;
		} catch {
			if (child.exitCode !== null) throw new Error(`${ENTRY} exited with ${child.exitCode}`);
			if (Date.now() > deadline) throw new Error(`${ENTRY} did not listen on ${origin}`);
			await new Promise((r) => setTimeout(r, 100));
		}
	}
});

after(() => {
	child?.kill();
});

function get(path) {
	return fetch(`${origin}${path}`, { redirect: 'manual' });
}

function assertSecurityHeaders(res, path) {
	for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
		assert.equal(res.headers.get(name), value, `${path}: ${name}`);
	}
}

for (const path of ['/', '/developpeurs', '/robots.txt', '/favicon.svg']) {
	test(`KI-P3: ${path} (static or prerendered, served by sirv) carries every S4 header`, async () => {
		const res = await get(path);
		assert.equal(res.status, 200, path);
		assertSecurityHeaders(res, path);
	});
}

// 05-07 lot 5: CSP without 'unsafe-inline' for scripts. A prerendered page carries it in a <meta>
// tag with the hash of each inline script; a page SvelteKit renders at the request carries it in the
// header with a nonce on each inline script.
import { createHash } from 'node:crypto';

/** Inline <script> bodies of an HTML page, with their opening tag; data blocks (JSON-LD) are
 * never executed, so the CSP does not apply to them. */
function inlineScripts(html) {
	return [...html.matchAll(/<script(?![^>]*\ssrc=)([^>]*)>([\s\S]*?)<\/script>/g)].map((m) => ({ attrs: m[1], body: m[2] }))
		.filter(({ attrs }) => !/type="application\/(ld\+)?json"/.test(attrs));
}

test('lot 5: a prerendered page lists the hash of each inline script, never unsafe-inline', async () => {
	const html = await (await get('/')).text();
	const meta = /<meta http-equiv="content-security-policy" content="([^"]+)"/i.exec(html)?.[1];
	assert.ok(meta, 'no CSP <meta> on /');
	const scriptSrc = /script-src ([^;]+)/.exec(meta)?.[1] ?? '';
	assert.doesNotMatch(scriptSrc, /unsafe-inline/);
	const scripts = inlineScripts(html);
	assert.ok(scripts.length > 0, 'no inline script on / (the check would pass vacuously)');
	for (const { body } of scripts) {
		const hash = createHash('sha256').update(body).digest('base64');
		assert.ok(scriptSrc.includes(`'sha256-${hash}'`), `inline script not hashed: ${body.slice(0, 60)}`);
	}
});

test('lot 5: a page rendered at the request carries the CSP header with a nonce', async () => {
	const res = await get('/une-page-qui-n-existe-pas');
	assert.equal(res.status, 404);
	const csp = res.headers.get('content-security-policy') ?? '';
	const nonce = /'nonce-([^']+)'/.exec(/script-src ([^;]+)/.exec(csp)?.[1] ?? '')?.[1];
	assert.ok(nonce, `no script nonce in ${csp}`);
	assert.doesNotMatch(csp, /script-src[^;]*unsafe-inline/);
	assert.match(csp, /frame-ancestors 'none'/);
	const scripts = inlineScripts(await res.text());
	assert.ok(scripts.length > 0);
	for (const { attrs } of scripts) assert.ok(attrs.includes(`nonce="${nonce}"`), `inline script without the nonce: ${attrs}`);
});

// 05-07 lot 5: security.txt (RFC 9116) is served by sirv from static/.well-known, with a contact and
// an expiry still to come (it must be renewed within the year: this test fails the day it lapses).
test('lot 5: /.well-known/security.txt names a contact and has not expired', async () => {
	const res = await get('/.well-known/security.txt');
	assert.equal(res.status, 200);
	assert.match(res.headers.get('content-type') ?? '', /^text\/plain/);
	const body = await res.text();
	assert.match(body, /^Contact: mailto:security@datacommune\.fr$/m);
	const expires = /^Expires: (\S+)$/m.exec(body)?.[1];
	assert.ok(expires && Date.parse(expires) > Date.now(), `security.txt expired: ${expires}`);
	assert.ok(Date.parse(expires) - Date.now() <= 366 * 24 * 3600 * 1000, 'Expires more than a year away (RFC 9116)');
	assert.match(body, /^Canonical: https:\/\/datacommune\.fr\/\.well-known\/security\.txt$/m);
});

test('KI-P3: the image starts the entry this test exercises', () => {
	const dockerfile = readFileSync(new URL('../Dockerfile', import.meta.url), 'utf8');
	assert.match(dockerfile, /^CMD \["node", "server\.js"\]$/m);
});

for (const path of ['/bien', '/bien/47.5,-1.5']) {
	test(`KI-P12: ${path} answers with x-robots-tag noindex`, async () => {
		const res = await get(path);
		assert.ok(res.status >= 300 && res.status < 400, `${path}: status ${res.status}`);
		assert.equal(res.headers.get('x-robots-tag'), 'noindex, nofollow', path);
		assertSecurityHeaders(res, path);
	});
}

test('KI-P12: robots.txt does not hide /bien from crawlers', async () => {
	const body = await (await get('/robots.txt')).text();
	const disallows = body
		.split('\n')
		.map((line) => line.replace(/#.*/, '').trim())
		.filter((line) => /^disallow:/i.test(line))
		.map((line) => line.slice('disallow:'.length).trim());
	for (const rule of disallows) {
		assert.ok(rule === '' || !'/bien/x'.startsWith(rule), `robots.txt disallows ${rule}, which masks the noindex`);
	}
});

test('the image healthcheck route still answers through server.js', async () => {
	assert.equal((await get('/__version')).status, 200);
});

test('KI-P12: pages outside /bien are not marked noindex by the header', async () => {
	const res = await get('/');
	assert.equal(res.headers.get('x-robots-tag'), null);
});

// 04.7-10 (decision 5): the sitemap is served by SvelteKit at the request, from the database served.
// sirv serves the static files BEFORE SvelteKit, so a `sitemap*.xml` left in `static/` (or copied
// into the build) would hide the served one and freeze its URLs. None may ship, whatever its name:
// neither a file named `sitemap*.xml` nor any XML under a `sitemaps/` folder, the names of the served
// files (`sitemaps/pages.xml`, `sitemaps/communes-44.xml`...) being the ones that would be hidden.
/** Relative paths of the sitemap files under *racine*, however deep; none when it does not exist. */
function sitemapsStatiques(racine, relatif = '') {
	const courant = join(racine, relatif);
	if (!existsSync(courant)) return [];
	return readdirSync(courant, { withFileTypes: true }).flatMap((entree) => {
		const chemin = relatif ? `${relatif}/${entree.name}` : entree.name;
		if (entree.isDirectory()) return sitemapsStatiques(racine, chemin);
		const dansSitemaps = relatif.split('/').some((segment) => /^sitemaps$/i.test(segment));
		return /^sitemap.*\.xml$/i.test(entree.name) || (dansSitemaps && /\.xml$/i.test(entree.name)) ? [chemin] : [];
	});
}

for (const dossier of ['static', 'build/client', 'build/prerendered']) {
	test(`04.7-10: no static sitemap*.xml nor sitemaps/*.xml in ${dossier} (the served sitemap would be hidden)`, () => {
		assert.deepEqual(sitemapsStatiques(join(FRONTEND, dossier)), []);
	});
}

test('04.7-10: the check sees every XML of sitemaps/ and every sitemap*.xml, at any depth, and nothing else', () => {
	const racine = mkdtempSync(join(tmpdir(), 'dc-sitemap-static-'));
	try {
		mkdirSync(join(racine, 'sitemaps', 'plus-profond'), { recursive: true });
		// The names the served sitemap really has, which would hide the routes if they were files (sirv first).
		const refuses = [
			'sitemap.xml',
			'Sitemap-index.XML',
			'sitemaps/pages.xml',
			'sitemaps/departements.xml',
			'sitemaps/communes-44.xml',
			'sitemaps/plus-profond/communes-2A.xml'
		];
		const permis = ['robots.txt', 'sitemap.txt', 'plan.xml', 'sitemaps/lisez-moi.txt', 'sitemaps/pages.xml.map'];
		for (const fichier of [...refuses, ...permis]) writeFileSync(join(racine, fichier), '');
		assert.deepEqual(sitemapsStatiques(racine).sort(), [...refuses].sort());
		assert.deepEqual(sitemapsStatiques(join(racine, 'absent')), []);
	} finally {
		rmSync(racine, { recursive: true, force: true });
	}
});

// Audit of 2026-11-10, P-4: the two woff2 (107 KB, 30 to 45 % of a light page) went out without any
// Cache-Control from the origin, so Cloudflare kept them four hours and a visitor of the next day
// downloaded them again. Their names carry no hash: thirty days, and a new name if a file changes.
for (const path of ['/fonts/inter-latin.woff2', '/fonts/newsreader-latin.woff2']) {
	test(`P-4: ${path} is kept thirty days by the browser`, async () => {
		const res = await get(path);
		assert.equal(res.status, 200, path);
		assert.equal(res.headers.get('cache-control'), 'public, max-age=2592000', path);
		assertSecurityHeaders(res, path);
	});
}

test('P-4: a page and a file outside /fonts/ get no long cache from that rule', async () => {
	for (const path of ['/', '/robots.txt', '/favicon.svg']) {
		assert.doesNotMatch((await get(path)).headers.get('cache-control') ?? '', /max-age=2592000/, path);
	}
});
