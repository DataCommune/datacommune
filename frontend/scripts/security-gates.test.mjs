// SEC-02 (Phase 46-04): sentinel tests for the two fail-closed public-key gates.
// - validate-public-api-key.mjs : pre-build prefix gate (dc_anon_ only, fail-closed)
// - scan-public-bundle.mjs      : post-build redacted secret scan of the real artifact
// D-10/D-11: the anon key is public by design but never echoed; NO test may print a
// detected secret value. Every assertion below proves redaction on each secret class.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { validatePublicApiKey, ALLOWED_PREFIX } from './validate-public-api-key.mjs';
import { scanContent, scanBundle, formatFindings, SECRET_RULES } from './scan-public-bundle.mjs';
import { SECURITY_HEADERS } from '../src/lib/utils/security-headers.js';

// ---------------------------------------------------------------------------
// Sentinel secret values — synthetic, never real credentials.
// ---------------------------------------------------------------------------
const SENTINEL_ANON = ALLOWED_PREFIX + 'PUBLICVISITORKEY0000';
const SENTINEL_LIVE = 'dc_live_' + 'DEADBEEFCAFEBABE1234';
const SENTINEL_PEM = '-----BEGIN RSA PRIVATE KEY-----';
const SENTINEL_STRIPE = 'sk_live_' + 'A1b2C3d4E5f6G7h8I9j0';
const SENTINEL_AWS = 'AKIA' + 'IOSFODNN7EXAMPLE';
const SENTINEL_GH = 'ghp_' + 'abcdefghijklmnopqrstuvwxyz0123456789';

const ALL_SENTINELS = [
	SENTINEL_LIVE,
	SENTINEL_PEM,
	SENTINEL_STRIPE,
	SENTINEL_AWS,
	SENTINEL_GH
];

function assertNoSentinel(text, label) {
	for (const s of ALL_SENTINELS) {
		assert.ok(!text.includes(s), `${label} leaked a sentinel secret value`);
	}
}

// ---------------------------------------------------------------------------
// Prefix gate: validate-public-api-key.mjs
// ---------------------------------------------------------------------------
test('prefix gate: dc_anon_ value passes', () => {
	assert.equal(validatePublicApiKey(SENTINEL_ANON, { allowEmpty: false }).ok, true);
});

test('prefix gate: empty value in deploy mode fails (fail-closed)', () => {
	assert.equal(validatePublicApiKey('', { allowEmpty: false }).ok, false);
});

test('prefix gate: empty value with --allow-empty passes (local only)', () => {
	assert.equal(validatePublicApiKey('', { allowEmpty: true }).ok, true);
});

test('prefix gate: whitespace-only value in deploy mode fails', () => {
	assert.equal(validatePublicApiKey('   ', { allowEmpty: false }).ok, false);
});

test('prefix gate: dc_live_ server key fails', () => {
	assert.equal(validatePublicApiKey(SENTINEL_LIVE, { allowEmpty: false }).ok, false);
});

test('prefix gate: any other prefix fails', () => {
	assert.equal(validatePublicApiKey(SENTINEL_STRIPE, { allowEmpty: false }).ok, false);
});

test('prefix gate: result never echoes the credential value', () => {
	const r = validatePublicApiKey(SENTINEL_LIVE, { allowEmpty: false });
	assertNoSentinel(JSON.stringify(r), 'validatePublicApiKey result');
});

// ---------------------------------------------------------------------------
// Bundle scan: scan-public-bundle.mjs
// ---------------------------------------------------------------------------
test('bundle scan: dc_anon_ public key is allowed (no finding)', () => {
	assert.equal(scanContent(`const k="${SENTINEL_ANON}";`).length, 0);
});

test('bundle scan: dc_live_ / PEM / stripe / aws / github sentinels each flag', () => {
	for (const s of ALL_SENTINELS) {
		const findings = scanContent(`payload ${s} tail`);
		assert.ok(findings.length >= 1, `expected a finding for a sentinel class`);
	}
});

test('bundle scan: scanContent findings never contain the matched value', () => {
	for (const s of ALL_SENTINELS) {
		const findings = scanContent(`payload ${s} tail`);
		assertNoSentinel(JSON.stringify(findings), 'scanContent findings');
	}
});

test('bundle scan: formatFindings emits file/rule/count only, never the value', () => {
	const findings = [
		{ file: 'app/immutable/chunk.js', rule: 'dc_live_key', count: 2 },
		{ file: 'app/immutable/key.txt', rule: 'pem_private_key', count: 1 }
	];
	const out = formatFindings(findings);
	assert.ok(out.includes('dc_live_key'));
	assert.ok(out.includes('app/immutable/chunk.js'));
	assert.ok(out.includes('2'));
	assertNoSentinel(out, 'formatFindings output');
});

test('bundle scan: scanBundle walks a fake artifact, flags and redacts', () => {
	const dir = mkdtempSync(join(tmpdir(), 'dc-bundle-'));
	try {
		mkdirSync(join(dir, 'app', 'immutable'), { recursive: true });
		writeFileSync(join(dir, 'app', 'immutable', 'chunk.js'), `const k="${SENTINEL_LIVE}";`);
		writeFileSync(join(dir, 'app', 'immutable', 'key.txt'), SENTINEL_PEM);
		writeFileSync(join(dir, 'safe.js'), `const pub="${SENTINEL_ANON}";`);

		const findings = scanBundle(dir);
		assert.ok(findings.length >= 2, 'expected at least two findings in the fake bundle');
		const out = formatFindings(findings);
		assertNoSentinel(out, 'scanBundle formatted output');
		// The clean file with the public anon key must not appear as a finding.
		assert.ok(!findings.some((f) => f.file.endsWith('safe.js')));
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
});

test('bundle scan: a secret embedded in a .wasm binary is still flagged (latin1)', () => {
	const dir = mkdtempSync(join(tmpdir(), 'dc-bundle-wasm-'));
	try {
		// A binary blob with high bytes surrounding an embedded live-key sentinel.
		const buf = Buffer.concat([
			Buffer.from([0x00, 0xfa, 0xce, 0x01, 0xff, 0x80]),
			Buffer.from(SENTINEL_LIVE, 'latin1'),
			Buffer.from([0x00, 0x80, 0xff])
		]);
		writeFileSync(join(dir, 'module.wasm'), buf);
		const findings = scanBundle(dir);
		assert.ok(
			findings.some((f) => f.rule === 'dc_live_key'),
			'a secret in a .wasm binary must be flagged'
		);
		assertNoSentinel(formatFindings(findings), 'wasm scan output');
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
});

test('bundle scan: rule set covers each required secret class', () => {
	const names = SECRET_RULES.map((r) => r.name);
	assert.ok(names.includes('dc_live_key'));
	assert.ok(names.some((n) => n.includes('pem')));
});

// ---------------------------------------------------------------------------
// S4 security headers.
//
// Until plan 01-06 there were TWO delivery paths and this block checked that they
// agreed: `_headers` (the Cloudflare Workers asset router, for static assets and
// prerendered pages) had to mirror src/lib/utils/security-headers.js byte for byte.
//
// `_headers` is a Cloudflare mechanism. It left with the Worker adapter, and
// `@sveltejs/adapter-node` serves those same responses from sirv without ever
// entering `src/hooks.server.ts`. So the sentinel is TURNED, not deleted: the file
// that can no longer be applied must not come back as a silent second source of
// truth, and the one path that IS applied must keep reading the canonical list.
// The half that lost coverage — headers on static/prerendered responses — came
// back through `server.js` (KI-P3, closed); scripts/served-headers.test.mjs proves
// it over HTTP on the built artifact (`npm run test:served`), and
// src/hooks.server.test.ts covers the hook.
// ---------------------------------------------------------------------------
const HEADERS_FILE = fileURLToPath(new URL('../_headers', import.meta.url));
const SERVER_HOOK_FILE = fileURLToPath(new URL('../src/hooks.server.ts', import.meta.url));

test('S4: no `_headers` file — a Cloudflare-only mechanism adapter-node never applies', () => {
	assert.ok(
		!existsSync(HEADERS_FILE),
		'`_headers` is back: adapter-node ignores it, so it would be an unapplied second source of security headers'
	);
});

test('S4: the server hook still applies the canonical list (the one live path)', () => {
	const src = readFileSync(SERVER_HOOK_FILE, 'utf8');
	assert.ok(
		src.includes('import { SECURITY_HEADERS }'),
		'hooks.server.ts must read the canonical list, not a local copy'
	);
	assert.ok(
		src.includes('Object.entries(SECURITY_HEADERS)'),
		'hooks.server.ts must iterate the whole canonical list, not a subset'
	);
});

test('S4: the canonical list is non-empty (a cleared list would pass vacuously)', () => {
	assert.ok(Object.keys(SECURITY_HEADERS).length >= 5);
});

// 05-07 lot 5: one year, still without includeSubDomains (api.datacommune.fr keeps its own) nor
// preload; the same value in the two Caddy site blocks, which set it on every answer.
const CADDY_FILES = ['../../api/Caddyfile.public', '../../api/Caddyfile.prod'].map((f) =>
	readFileSync(fileURLToPath(new URL(f, import.meta.url)), 'utf8').replace(/\r\n/g, '\n')
);
// The headers of the site: its block in Caddyfile.public, the snippet its hosts import in .prod.
const SITE_BLOCKS = [[CADDY_FILES[0], 'http://datacommune.fr'], [CADDY_FILES[1], '(frontend_headers)']];
/** A top-level block (from its opening line up to the next top-level `}`) of a Caddyfile. */
function siteBlock(caddy, opener) {
	const start = caddy.indexOf(`${opener} {`);
	assert.ok(start >= 0, `no block ${opener}`);
	return caddy.slice(start, caddy.indexOf('\n}\n', start));
}

test('S4: the frontend HSTS is one year, in the app and in both Caddy site blocks', () => {
	assert.equal(SECURITY_HEADERS['Strict-Transport-Security'], 'max-age=31536000');
	for (const [caddy, host] of SITE_BLOCKS) {
		assert.match(siteBlock(caddy, host), />Strict-Transport-Security "max-age=31536000"/, host);
	}
});

// 05-07 lot 5: the page CSP is SvelteKit's (kit.csp: hashes on prerendered pages, nonces on the
// others), so inline scripts are allowed one by one and 'unsafe-inline' leaves script-src. A
// proxy that set its own CSP on the site would replace that header and break every page.
test('S4: kit.csp allows no inline script and no third-party source', async () => {
	const { default: config } = await import('../svelte.config.js');
	const csp = config.kit.csp;
	assert.equal(csp.mode, 'auto');
	const d = csp.directives;
	for (const directive of ['script-src', 'default-src']) {
		assert.ok(!d[directive].includes('unsafe-inline'), `${directive} allows inline scripts`);
	}
	// The only exception: the hash of the inline event handler Svelte writes (`this.__e=event`).
	const { SVELTE_EVENT_HANDLER } = await import('../svelte.config.js');
	const { createHash } = await import('node:crypto');
	const handlerHash = 'sha256-' + createHash('sha256').update(SVELTE_EVENT_HANDLER).digest('base64');
	assert.deepEqual(d['script-src'], ['self', 'unsafe-hashes', handlerHash]);
	const compiler = readFileSync(
		fileURLToPath(new URL('../node_modules/svelte/src/compiler/phases/3-transform/server/visitors/shared/element.js', import.meta.url)),
		'utf8'
	);
	assert.ok(compiler.includes(`="${SVELTE_EVENT_HANDLER}"`), 'Svelte writes another inline handler: update its hash');
	assert.deepEqual(d['object-src'], ['none']);
	assert.deepEqual(d['frame-ancestors'], ['none']);
	const thirdParties = Object.values(d).flat().filter((s) => /^https?:/.test(s));
	assert.ok(thirdParties.length <= 1, `third-party sources: ${thirdParties}`);
	assert.ok(!thirdParties.some((s) => /cloudflareinsights/.test(s)));
});

test('S4: no Caddy site block sets a CSP over the one of SvelteKit', () => {
	for (const [caddy, host] of SITE_BLOCKS) {
		assert.doesNotMatch(siteBlock(caddy, host), /Content-Security-Policy/, host);
	}
});

test('S4: the fallback CSP of the canonical list frames nothing and restricts no script', () => {
	const fallback = SECURITY_HEADERS['Content-Security-Policy'];
	assert.match(fallback, /frame-ancestors 'none'/);
	// Combined with the <meta> CSP of a prerendered page, a script-src here would block its hashes.
	assert.doesNotMatch(fallback, /script-src|default-src/);
});

test('S4: the frontend HSTS carries neither includeSubDomains nor preload', () => {
	const hsts = SECURITY_HEADERS['Strict-Transport-Security'];
	assert.ok(!/includeSubDomains/i.test(hsts), 'frontend HSTS must not include subdomains');
	assert.ok(!/preload/i.test(hsts), 'frontend HSTS must never be preloaded');
});

// R-17 (review 2026-11-10): a link opened in a new tab without `rel="noopener"` hands the opened
// page `window.opener`. Modern engines imply noopener, but the explicit attribute is the contract
// every other link of the site keeps; one admin link escaped it. Scans every `.svelte` source.
const SRC_DIR = fileURLToPath(new URL('../src', import.meta.url));

function svelteFiles(dir) {
	const out = [];
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const full = join(dir, entry.name);
		if (entry.isDirectory()) out.push(...svelteFiles(full));
		else if (entry.name.endsWith('.svelte')) out.push(full);
	}
	return out;
}

test('R-17: every link opened in a new tab carries rel="noopener"', () => {
	const files = svelteFiles(SRC_DIR);
	assert.ok(files.length > 50, 'the scan must see the sources (a wrong path would pass vacuously)');
	const offenders = [];
	let seen = 0;
	for (const file of files) {
		const src = readFileSync(file, 'utf8');
		for (const m of src.matchAll(/<a\b[^>]*target="_blank"[^>]*>/gs)) {
			seen += 1;
			if (!/\brel="[^"]*\bnoopener\b[^"]*"/.test(m[0])) offenders.push(file.slice(SRC_DIR.length));
		}
	}
	assert.ok(seen > 0, 'no new-tab link found: the pattern no longer matches the sources');
	assert.deepEqual(offenders, []);
});
