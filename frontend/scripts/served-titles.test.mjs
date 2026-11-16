// HTTP proof of the <title> the production server sends (bug seen in production on 2026-11-12:
// /commune/44109 served `<title>DataCommune</title>` while its og:title named Nantes).
//
// Runs against the built artifact (`npm run build` first), through the entry the image starts (server.js),
// because only the built server showed the defect: the dev server and the browser had the right title. The
// API is a local stub answering 503 to every read, so each page renders its outage state; the title of a
// page does not depend on its figures being served, and nothing here reaches a real API.
//
// What it locks, for each public page: one <title> in the server HTML, never the bare default of the root
// layout, and the same text as the page's og:title. The rule that words the titles is unit-tested
// (src/lib/seo/titres.test.ts); the cause is guarded at compile time (src/routes/titre-serveur.test.ts).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createServer as createHttpServer } from 'node:http';
import { createServer } from 'node:net';
import { fileURLToPath } from 'node:url';

const FRONTEND = fileURLToPath(new URL('..', import.meta.url));

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
let api;
let origin;

before(async () => {
	assert.ok(existsSync(new URL('../build/handler.js', import.meta.url)), 'build/ missing: run `npm run build` first');
	api = createHttpServer((_req, res) => {
		res.writeHead(503, { 'content-type': 'application/json' });
		res.end('{"detail":"Service indisponible (stub of served-titles.test.mjs)."}');
	});
	const apiPort = await freePort();
	await new Promise((resolve) => api.listen(apiPort, '127.0.0.1', resolve));
	const port = await freePort();
	origin = `http://127.0.0.1:${port}`;
	child = spawn(process.execPath, ['server.js'], {
		cwd: FRONTEND,
		env: {
			...process.env,
			HOST: '127.0.0.1',
			PORT: String(port),
			ORIGIN: origin,
			// Every server-side API read goes to the stub, whatever VITE_API_URL the build was given.
			API_INTERNAL_URL: `http://127.0.0.1:${apiPort}`
		},
		stdio: ['ignore', 'ignore', 'inherit']
	});
	const deadline = Date.now() + 15_000;
	for (;;) {
		try {
			await fetch(`${origin}/robots.txt`);
			return;
		} catch {
			if (child.exitCode !== null) throw new Error(`server.js exited with ${child.exitCode}`);
			if (Date.now() > deadline) throw new Error(`server.js did not listen on ${origin}`);
			await new Promise((r) => setTimeout(r, 100));
		}
	}
});

after(() => {
	child?.kill();
	api?.close();
});

const titres = (html) => [...html.matchAll(/<title>([^<]*)<\/title>/g)].map((m) => m[1]);
const ogTitle = (html) => html.match(/<meta property="og:title" content="([^"]*)"/)?.[1] ?? null;

for (const chemin of ['/', '/commune/44109', '/departement/44', '/comparer?communes=44109,35238']) {
	test(`${chemin}: the server HTML carries the page's own <title>, once, as its og:title`, async () => {
		const html = await (await fetch(`${origin}${chemin}`)).text();
		const lus = titres(html);
		assert.equal(lus.length, 1, `${chemin}: ${JSON.stringify(lus)}`);
		assert.notEqual(lus[0], 'DataCommune', `${chemin}: the default title of the root layout won`);
		const og = ogTitle(html);
		if (og !== null) assert.equal(lus[0], og, `${chemin}: <title> and og:title differ`);
	});
}

// Search-result metadata of every indexable page (SEO audit of 2026-11-14): /couverture and
// /ce-que-nous-ne-montrons-pas had no canonical nor card, /datasets no card, /departements a description
// Google cuts. The static pages are read from the sitemap the server sends, so a page added to it is checked
// too; a commune and a departement stand for the fiches (their outage state here: the stub answers 503).
const attribut = (html, motif) => html.match(motif)?.[1] ?? null;
const decoder = (texte) =>
	texte.replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>');
const DESCRIPTION_MAX = 160;

test('every indexable page of the sitemap carries canonical, og:title, og:description and a description of at most 160 characters', async () => {
	const xml = await (await fetch(`${origin}/sitemaps/pages.xml`)).text();
	const chemins = [...xml.matchAll(/<loc>([^<]*)<\/loc>/g)].map((m) => new URL(m[1]).pathname);
	assert.ok(chemins.length >= 5, `sitemap pages: ${JSON.stringify(chemins)}`);
	const defauts = [];
	for (const chemin of [...chemins, '/commune/44109', '/departement/44']) {
		const html = await (await fetch(`${origin}${chemin}`)).text();
		const description = attribut(html, /<meta name="description" content="([^"]*)"/);
		if (!/<link rel="canonical" href="[^"]+"/.test(html)) defauts.push(`${chemin}: no canonical`);
		if (attribut(html, /<meta property="og:title" content="([^"]*)"/) === null) defauts.push(`${chemin}: no og:title`);
		if (attribut(html, /<meta property="og:description" content="([^"]*)"/) === null) defauts.push(`${chemin}: no og:description`);
		if (description === null) defauts.push(`${chemin}: no description`);
		else if (decoder(description).length > DESCRIPTION_MAX) {
			defauts.push(`${chemin}: description of ${decoder(description).length} characters`);
		}
		const titre = titres(html)[0] ?? '';
		if (titre.includes(' | ')) defauts.push(`${chemin}: title separator « | » (${titre})`);
	}
	assert.deepEqual(defauts, []);
});
