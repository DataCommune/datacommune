// HTTP proof of the permanent redirects of the public site (SEO audit, 2026-11-14): each removed route of
// phase 3 and each lower-case Corsican code answers one 301 to its replacement, and the replacement and
// canonical pages are not redirected themselves. Rules: src/lib/server/redirections.ts, applied by
// src/hooks.server.ts.
//
// Runs against the built artifact (`npm run build` first), through server.js like the image, because the
// removed routes are not prerendered files: this is what proves that sirv lets them reach the hook. The
// API is a local stub answering 503 to every read, so the canonical pages render their outage state (503,
// 04.7-10); what is locked for them is that they are answered by their own route, never by a redirect.
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
		res.end('{"detail":"Service indisponible (stub of served-redirections.test.mjs)."}');
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

const get = (chemin) => fetch(`${origin}${chemin}`, { redirect: 'manual' });

const REDIRECTIONS = [
	['/docs', '/developpeurs'],
	['/playground', '/developpeurs'],
	['/reference', '/developpeurs'],
	['/reference/communes', '/developpeurs'],
	['/guides', '/developpeurs'],
	['/guides/achat-immobilier', '/developpeurs'],
	['/roadmap', '/couverture'],
	['/departement/2a', '/departement/2A'],
	['/departement/2b', '/departement/2B'],
	['/commune/2a004', '/commune/2A004']
];

for (const [chemin, cible] of REDIRECTIONS) {
	test(`${chemin} answers one 301 to ${cible}`, async () => {
		const res = await get(chemin);
		assert.equal(res.status, 301, chemin);
		assert.equal(res.headers.get('location'), cible, chemin);
		assert.equal(res.headers.get('x-content-type-options'), 'nosniff', `${chemin}: security headers missing`);
	});
}

test('/developpeurs and /couverture are served as they are (200)', async () => {
	for (const chemin of ['/developpeurs', '/couverture']) assert.equal((await get(chemin)).status, 200, chemin);
});

test('the canonical pages are answered by their own route, never redirected', async () => {
	for (const chemin of ['/commune/44109', '/departement/2A', '/departement/44']) {
		const res = await get(chemin);
		assert.ok([200, 503].includes(res.status), `${chemin}: ${res.status}`);
		assert.equal(res.headers.get('location'), null, chemin);
	}
});

test('a malformed code keeps the 404 of its route instead of a redirect', async () => {
	assert.equal((await get('/departement/2c')).status, 404);
});
