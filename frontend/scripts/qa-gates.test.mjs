// Phase 46-05 (T46-05-FG/T46-05-NET/T46-05-LOG) — sentinel tests for the QA gates added in
// this plan: the E2E manifest gate, the strict build wrapper, and the quality-guard fixture's
// pure redaction/classification helpers. Every gate is exercised against temporary
// directories/processes (never the real manifest, the real build, or a real browser) — same
// discipline as scripts/security-gates.test.mjs.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync, copyFileSync, symlinkSync, unlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

import {
	collectSpecFiles,
	validateManifestCoverage,
	scanForbiddenTokens,
	runManifestGate,
	VALID_CLASSIFICATIONS
} from './check-e2e-manifest.mjs';
import { scanBuildOutput, runBuildStrict, WARNING_RULES, ALLOWLIST } from './run-build-strict.mjs';
import {
	redactUrl,
	redactMessage,
	isForbiddenHost,
	isApiPath,
	classifyRequest
} from '../tests/e2e/fixtures/quality.ts';
import {
	scannerFichier,
	evaluerDette,
	runDesignGate,
	REGLES,
	PORTEE_PUBLIQUE,
	JETONS_CONTRAT,
	ALIAS_DE_TRANSITION,
	ALLOWLIST as DESIGN_ALLOWLIST,
	DETTE as DESIGN_DETTE
} from './check-design-gate.mjs';
// Namespace import for the ratchet API (D-29): each test that needs it fails on its own when an
// export is missing, instead of the whole file failing to link.
import * as designGate from './check-design-gate.mjs';
import { verifierAccueil } from './check-marketing-prerender.mjs';

// ---------------------------------------------------------------------------
// check-e2e-manifest.mjs
// ---------------------------------------------------------------------------

function withTempE2eDir(specNames, fn) {
	const dir = mkdtempSync(join(tmpdir(), 'dc-e2e-manifest-'));
	try {
		for (const name of specNames) {
			writeFileSync(join(dir, name), `import { test } from '@playwright/test';\ntest('x', async () => {});\n`);
		}
		return fn(dir);
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
}

test('collectSpecFiles: finds *.spec.ts recursively, ignores non-spec files', () => {
	const dir = mkdtempSync(join(tmpdir(), 'dc-e2e-collect-'));
	try {
		mkdirSync(join(dir, 'fixtures'), { recursive: true });
		writeFileSync(join(dir, 'a.spec.ts'), '');
		writeFileSync(join(dir, 'fixtures', 'helper.ts'), '');
		writeFileSync(join(dir, 'README.md'), '');
		const found = collectSpecFiles(dir);
		assert.deepEqual(found, ['a.spec.ts']);
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
});

test('collectSpecFiles: missing directory returns empty array (no throw)', () => {
	const missing = join(tmpdir(), 'dc-e2e-does-not-exist-' + Date.now());
	assert.deepEqual(collectSpecFiles(missing), []);
});

test('validateManifestCoverage: exhaustive + valid manifest passes', () => {
	const manifest = {
		specs: {
			'a.spec.ts': { classification: 'critical' },
			'b.spec.ts': { classification: 'manual', reason: 'live smoke', target: 'manual pre-release' }
		}
	};
	const result = validateManifestCoverage(manifest, ['a.spec.ts', 'b.spec.ts']);
	assert.equal(result.ok, true);
	assert.deepEqual(result.errors, []);
});

test('validateManifestCoverage: spec on disk missing from manifest -> FAIL', () => {
	const manifest = { specs: { 'a.spec.ts': { classification: 'critical' } } };
	const result = validateManifestCoverage(manifest, ['a.spec.ts', 'unclassified.spec.ts']);
	assert.equal(result.ok, false);
	assert.ok(result.errors.some((e) => e.includes('unclassified spec on disk: unclassified.spec.ts')));
});

test('validateManifestCoverage: stale manifest entry (spec removed from disk) -> FAIL', () => {
	const manifest = { specs: { 'a.spec.ts': { classification: 'critical' }, 'gone.spec.ts': { classification: 'critical' } } };
	const result = validateManifestCoverage(manifest, ['a.spec.ts']);
	assert.equal(result.ok, false);
	assert.ok(result.errors.some((e) => e.includes('stale manifest entry')));
});

test('validateManifestCoverage: invalid classification value -> FAIL', () => {
	const manifest = { specs: { 'a.spec.ts': { classification: 'sometimes' } } };
	const result = validateManifestCoverage(manifest, ['a.spec.ts']);
	assert.equal(result.ok, false);
	assert.ok(result.errors.some((e) => e.includes('must be one of')));
});

test('validateManifestCoverage: manual/legacy without reason+target -> FAIL', () => {
	const manifest = { specs: { 'a.spec.ts': { classification: 'legacy' } } };
	const result = validateManifestCoverage(manifest, ['a.spec.ts']);
	assert.equal(result.ok, false);
	assert.ok(result.errors.some((e) => e.includes('requires a non-empty "reason"')));
	assert.ok(result.errors.some((e) => e.includes('requires a non-empty "target"')));
});

test('validateManifestCoverage: missing "specs" object is fail-closed, never throws', () => {
	const result = validateManifestCoverage({}, ['a.spec.ts']);
	assert.equal(result.ok, false);
	assert.ok(result.errors.some((e) => e.includes('missing a "specs" object')));
});

test('scanForbiddenTokens: flags test.only/describe.only/test.skip/describe.skip/test.fixme/waitForTimeout', () => {
	const content = [
		"test.only('x', async () => {});",
		"test.describe.only('y', () => {});",
		"test.skip('z', async () => {});",
		"test.describe.skip('w', () => {});",
		"test.fixme('v', async () => {});",
		'await page.waitForTimeout(500);'
	].join('\n');
	const hits = scanForbiddenTokens(content);
	const names = hits.map((h) => h.name).sort();
	assert.deepEqual(names, [
		'describe.only',
		'describe.skip',
		'test.fixme',
		'test.only',
		'test.skip',
		'waitForTimeout'
	]);
});

test('scanForbiddenTokens: reports the correct line number', () => {
	const content = "test('ok', async () => {});\ntest.only('bad', async () => {});\n";
	const hits = scanForbiddenTokens(content);
	assert.equal(hits.length, 1);
	assert.equal(hits[0].name, 'test.only');
	assert.equal(hits[0].line, 2);
});

test('scanForbiddenTokens: a clean spec (no forbidden token) yields zero hits', () => {
	const content = "test.fail();\ntest('ok', async () => {});\n";
	assert.deepEqual(scanForbiddenTokens(content), []);
});

test('runManifestGate: unclassified critical spec containing test.only is caught end-to-end', () => {
	withTempE2eDir([], () => {
		const manifest = {
			specs: {
				'good.spec.ts': { classification: 'critical' },
				'bad.spec.ts': { classification: 'critical' }
			}
		};
		const content = {
			'good.spec.ts': "test('ok', async () => {});",
			'bad.spec.ts': "test.only('bad', async () => {});"
		};
		const result = runManifestGate(manifest, ['good.spec.ts', 'bad.spec.ts'], (rel) => content[rel]);
		assert.equal(result.ok, false);
		assert.ok(result.errors.some((e) => e.includes('bad.spec.ts:1: forbidden "test.only"')));
	});
});

test('runManifestGate: forbidden token in a MANUAL spec is tolerated (only critical is scanned)', () => {
	const manifest = { specs: { 'live.spec.ts': { classification: 'manual', reason: 'live smoke', target: 'manual' } } };
	const content = { 'live.spec.ts': "test.skip(!KEY, 'no key');\ntest('ok', async () => {});" };
	const result = runManifestGate(manifest, ['live.spec.ts'], (rel) => content[rel]);
	assert.equal(result.ok, true);
});

test('VALID_CLASSIFICATIONS is exactly critical/manual/legacy', () => {
	assert.deepEqual([...VALID_CLASSIFICATIONS].sort(), ['critical', 'legacy', 'manual']);
});

// ---------------------------------------------------------------------------
// run-build-strict.mjs
// ---------------------------------------------------------------------------

test('scanBuildOutput: clean vite output yields zero findings', () => {
	const clean = "vite v7.3.1 building for production...\n✓ 120 modules transformed.\n✓ built in 3.21s\n";
	assert.deepEqual(scanBuildOutput(clean), []);
});

test('scanBuildOutput: rollup "(!)" marker is flagged', () => {
	const out = '(!) Some chunks are larger than 500 KiB after minification.';
	assert.ok(scanBuildOutput(out).includes('rollup-generic-warning'));
});

test('scanBuildOutput: vite-plugin-svelte warning is flagged', () => {
	const out = '[vite-plugin-svelte] warn: something unexpected in Foo.svelte';
	assert.ok(scanBuildOutput(out).includes('vite-plugin-svelte-warning'));
});

test('scanBuildOutput: svelte a11y warning is flagged', () => {
	const out = 'src/routes/+page.svelte:12:3 a11y-click-events-have-key-events';
	assert.ok(scanBuildOutput(out).includes('svelte-a11y-warning'));
});

test('scanBuildOutput: "Unused CSS selector" is flagged', () => {
	const out = "src/lib/components/Foo.svelte:4:1 Unused CSS selector \".bar\"";
	assert.ok(scanBuildOutput(out).includes('svelte-unused-css-selector'));
});

test('scanBuildOutput: [SvelteKit] warning is flagged', () => {
	const out = '[SvelteKit] Warning: deprecated config option';
	assert.ok(scanBuildOutput(out).includes('sveltekit-warning'));
});

test('scanBuildOutput: generic WARN/WARNING line marker is flagged', () => {
	assert.ok(scanBuildOutput('WARN: something happened').includes('generic-warn-marker'));
	assert.ok(scanBuildOutput('WARNING: something else').includes('generic-warn-marker'));
});

test('scanBuildOutput: default ALLOWLIST is empty (no silent exceptions)', () => {
	assert.deepEqual(ALLOWLIST, []);
});

test('scanBuildOutput: an explicit allowlist entry suppresses that one rule only', () => {
	const out = '(!) Some chunks are larger than 500 KiB after minification.';
	assert.deepEqual(scanBuildOutput(out, ['rollup-generic-warning']), []);
});

test('WARNING_RULES: every rule has a name and a pattern', () => {
	for (const rule of WARNING_RULES) {
		assert.equal(typeof rule.name, 'string');
		assert.ok(rule.pattern instanceof RegExp);
	}
});

test('runBuildStrict: propagates a non-zero exit code from the child process', async () => {
	const dir = mkdtempSync(join(tmpdir(), 'dc-build-strict-'));
	try {
		const script = join(dir, 'fail.mjs');
		writeFileSync(script, "console.log('building...'); process.exit(3);\n");
		const { code, findings } = await runBuildStrict({ cmd: process.execPath, args: [script] });
		assert.equal(code, 3);
		assert.deepEqual(findings, []);
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
});

test('runBuildStrict: exit 0 but forbidden warning marker in output -> findings non-empty', async () => {
	const dir = mkdtempSync(join(tmpdir(), 'dc-build-strict-'));
	try {
		const script = join(dir, 'warn.mjs');
		writeFileSync(script, "console.log('(!) Some chunks are larger than 500 KiB after minification.'); process.exit(0);\n");
		const { code, findings } = await runBuildStrict({ cmd: process.execPath, args: [script] });
		assert.equal(code, 0);
		assert.ok(findings.includes('rollup-generic-warning'));
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
});

test('runBuildStrict: clean exit 0, zero output -> zero findings', async () => {
	const dir = mkdtempSync(join(tmpdir(), 'dc-build-strict-'));
	try {
		const script = join(dir, 'clean.mjs');
		writeFileSync(script, "console.log('✓ built in 1.00s'); process.exit(0);\n");
		const { code, findings } = await runBuildStrict({ cmd: process.execPath, args: [script] });
		assert.equal(code, 0);
		assert.deepEqual(findings, []);
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
});

// ---------------------------------------------------------------------------
// tests/e2e/fixtures/quality.ts — pure redaction/classification helpers
// ---------------------------------------------------------------------------

const SENTINEL_KEY = 'dc_live_' + 'DEADBEEFCAFEBABE1234';
const SENTINEL_BEARER = 'Bearer ' + 'abc123.def456.ghi789';

test('redactUrl: strips the query string and hash, keeps origin+pathname', () => {
	const out = redactUrl('http://127.0.0.1:5173/v1/resolve?q=46.83,-1.9&type=gps#frag');
	assert.equal(out, 'http://127.0.0.1:5173/v1/resolve');
	assert.ok(!out.includes('46.83'));
	assert.ok(!out.includes('?'));
});

test('redactUrl: falls back to a simple split on an unparsable URL, still strips the query', () => {
	const out = redactUrl('not-a-real-url?secret=1234');
	assert.ok(!out.includes('secret=1234'));
});

test('redactMessage: strips a query-string-shaped key=value pair', () => {
	const out = redactMessage('fetch failed: /v1/resolve?q=46.83,-1.9&apikey=SUPERSECRET');
	assert.ok(!out.includes('SUPERSECRET'));
	assert.ok(out.includes('[redacted]'));
});

test('redactMessage: redacts a dc_live_/dc_anon_ key even embedded in free text', () => {
	const out = redactMessage(`unexpected credential ${SENTINEL_KEY} in header`);
	assert.ok(!out.includes(SENTINEL_KEY));
	assert.ok(out.includes('dc_live_[redacted]'));
});

test('redactMessage: redacts a Bearer token', () => {
	const out = redactMessage(`Authorization: ${SENTINEL_BEARER}`);
	assert.ok(!out.includes(SENTINEL_BEARER));
	assert.ok(out.includes('Bearer [redacted]'));
});

test('redactMessage: truncates an overly long message', () => {
	const out = redactMessage('x'.repeat(500));
	assert.ok(out.length < 500);
	assert.ok(out.endsWith('…'));
});

test('isForbiddenHost: flags datacommune.fr and api-adresse.data.gouv.fr, allows localhost', () => {
	assert.equal(isForbiddenHost('https://api.datacommune.fr/v1/resolve'), true);
	assert.equal(isForbiddenHost('https://datacommune.fr/'), true);
	assert.equal(isForbiddenHost('https://api-adresse.data.gouv.fr/search'), true);
	assert.equal(isForbiddenHost('http://127.0.0.1:5173/v1/resolve'), false);
	assert.equal(isForbiddenHost('http://localhost:8001/v1/resolve'), false);
});

test('isApiPath: flags /v1|/auth|/billing regardless of host, ignores static assets', () => {
	assert.equal(isApiPath('http://127.0.0.1:5173/v1/resolve?q=1'), true);
	assert.equal(isApiPath('http://127.0.0.1:5173/auth/me'), true);
	assert.equal(isApiPath('http://127.0.0.1:5173/billing/checkout'), true);
	assert.equal(isApiPath('http://127.0.0.1:5173/_app/immutable/chunk.js'), false);
	assert.equal(isApiPath('http://127.0.0.1:5173/'), false);
});

test('classifyRequest: forbidden host wins over API-path classification', () => {
	assert.equal(classifyRequest('https://api.datacommune.fr/v1/resolve'), 'forbidden-host');
});

test('classifyRequest: an unmocked local API path is classified unmocked-api', () => {
	assert.equal(classifyRequest('http://127.0.0.1:5173/v1/resolve?q=1'), 'unmocked-api');
});

test('classifyRequest: a static asset request is not classified (null)', () => {
	assert.equal(classifyRequest('http://127.0.0.1:5173/_app/immutable/chunk.js'), null);
});

test('classifyRequest / redactUrl never leak the query string together', () => {
	const url = 'http://127.0.0.1:5173/v1/resolve?apikey=SUPERSECRET';
	assert.equal(classifyRequest(url), 'unmocked-api');
	assert.ok(!redactUrl(url).includes('SUPERSECRET'));
});

// ---------------------------------------------------------------------------
// check-design-gate.mjs (rewritten in 03-03 on the contract tokens of 03-UI-SPEC.md) —
// sentinels for the pure functions. Every scan runs against synthetic content or temporary
// directories, never the real src/ (except the app.css / DETTE invariants, which read the repo).
// ---------------------------------------------------------------------------

function withTempFrontendRoot(fichiers, fn) {
	const root = mkdtempSync(join(tmpdir(), 'dc-design-gate-'));
	try {
		for (const [rel, contenu] of Object.entries(fichiers)) {
			const dest = join(root, rel);
			mkdirSync(dirname(dest), { recursive: true });
			writeFileSync(dest, contenu);
		}
		return fn(root);
	} finally {
		rmSync(root, { recursive: true, force: true });
	}
}

const PUBLIC_FILE = 'src/routes/(marketing)/+page.svelte';
const APP_FILE = 'src/routes/(app)/mon-espace/+page.svelte';
// A stylesheet of the public scope: read as CSS, without the Svelte parser.
const FICHIER_CSS_PUBLIC = 'src/lib/components/ds/fixture.css';

function regles(chemin, contenu) {
	return scannerFichier(chemin, contenu).map((c) => c.regle);
}

// One violation case and one clean case per rule. `site` rules are exercised outside the public
// scope to prove they apply site-wide; `public` rules inside it.
const CAS_REGLES = [
	{
		regle: 'hex-en-dur',
		chemin: APP_FILE,
		violation: '<p style="color:#355b50">x</p>\n',
		propre: '<p style="color: var(--color-accent)">x</p>\n'
	},
	{
		regle: 'police-hors-token',
		chemin: APP_FILE,
		violation: '<style>h2 { font-family: Georgia, serif; }</style>\n',
		propre: '<style>h2 { font-family: var(--font-sans); }</style>\n'
	},
	{
		regle: 'couleur-hors-palette',
		chemin: PUBLIC_FILE,
		violation: '<a class="text-bronze hover:bg-marble-dark">x</a>\n',
		propre: '<a class="text-ink bg-paper border-line-strong hover:bg-soft">x</a>\n'
	},
	{
		regle: 'typo-hors-echelle',
		chemin: PUBLIC_FILE,
		violation: '<style>p { font-size: 13px; }</style>\n',
		propre: '<style>p { font-size: 14px; } small { font-size: inherit; }</style>\n'
	},
	{
		regle: 'graisse-hors-contrat',
		chemin: PUBLIC_FILE,
		violation: '<style>strong { font-weight: 700; }</style>\n',
		propre: '<style>strong { font-weight: 600; } h2 { font-weight: 500; }</style>\n'
	},
	{
		regle: 'rayon-hors-jeton',
		chemin: PUBLIC_FILE,
		violation: '<style>.panneau { border-radius: 10px; }</style>\n',
		propre: '<style>.panneau { border-radius: var(--radius-panneau); }</style>\n'
	},
	{
		regle: 'espacement-hors-jeton',
		chemin: PUBLIC_FILE,
		violation: '<style>.panneau { padding: 10px; }</style>\n',
		propre: '<style>.panneau { padding: var(--s5); margin: 0 auto; }</style>\n'
	},
	{
		regle: 'emoji-ui',
		chemin: PUBLIC_FILE,
		violation: `<p>Attention ${String.fromCodePoint(0x26a0)}</p>\n`,
		propre: '<p>Attention</p>\n'
	},
	{
		regle: 'infobulle-native',
		chemin: PUBLIC_FILE,
		violation: '<button\n\tonclick={() => (open = !open)}\n\ttitle="Source">i</button>\n',
		propre: '<button onclick={() => (open = !open)} aria-label="Source et définition : Prix">i</button>\n'
	},
	{
		regle: 'perimetre-en-dur',
		chemin: PUBLIC_FILE,
		violation: '<p>Démo · Loire-Atlantique &amp; Vendée</p>\n',
		propre: '<p>{hebergement.departements.length} départements hébergés</p>\n'
	}
];

test('check-design-gate: REGLES carries exactly the ten rules of the contract', () => {
	assert.deepEqual(
		REGLES.map((r) => [r.nom, r.portee]),
		[
			['hex-en-dur', 'site'],
			['police-hors-token', 'site'],
			['couleur-hors-palette', 'public'],
			['typo-hors-echelle', 'public'],
			['emoji-ui', 'public'],
			['infobulle-native', 'public'],
			['perimetre-en-dur', 'public'],
			['graisse-hors-contrat', 'public'],
			['rayon-hors-jeton', 'public'],
			['espacement-hors-jeton', 'public']
		]
	);
});

for (const cas of CAS_REGLES) {
	test(`scannerFichier: ${cas.regle} flags its violation`, () => {
		assert.ok(regles(cas.chemin, cas.violation).includes(cas.regle), `${cas.regle} should be flagged`);
	});

	test(`scannerFichier: ${cas.regle} leaves the clean case alone`, () => {
		assert.deepEqual(regles(cas.chemin, cas.propre), []);
	});
}

test('scannerFichier: reports the line of the offending attribute, never the value', () => {
	const constats = scannerFichier(PUBLIC_FILE, '<div>\n<a href="/"\n  title="x">y</a>\n</div>\n');
	assert.deepEqual(constats, [{ regle: 'infobulle-native', ligne: 3 }]);
	for (const constat of scannerFichier(PUBLIC_FILE, '<p style="color:#123abc">x</p>\n')) {
		assert.deepEqual(Object.keys(constat).sort(), ['ligne', 'regle']);
	}
});

test('scannerFichier: public rules do not fire under (app)/ or admin/, but fire under (marketing)/', () => {
	const contenu = '<div class="text-red-500 text-sm font-bold" title="x">44 &amp; 85</div>\n';
	assert.deepEqual(regles(APP_FILE, contenu), []);
	assert.deepEqual(regles('src/lib/components/admin/OverviewCards.svelte', contenu), []);
	assert.deepEqual(regles(PUBLIC_FILE, contenu).sort(), [
		'couleur-hors-palette',
		'graisse-hors-contrat',
		'perimetre-en-dur',
		'typo-hors-echelle'
	]);
});

test('scannerFichier: couleur-hors-palette refuses every transition alias and off-contract var()', () => {
	for (const alias of ALIAS_DE_TRANSITION) {
		assert.ok(regles(PUBLIC_FILE, `<p class="bg-${alias}">x</p>`).includes('couleur-hors-palette'), alias);
		assert.ok(
			regles(PUBLIC_FILE, `<style>p { color: var(--color-${alias}); }</style>`).includes(
				'couleur-hors-palette'
			),
			alias
		);
	}
	for (const jeton of JETONS_CONTRAT) {
		assert.deepEqual(regles(PUBLIC_FILE, `<p class="bg-${jeton} text-${jeton}">x</p>`), [], jeton);
	}
	assert.ok(regles(PUBLIC_FILE, '<p class="text-white">x</p>').includes('couleur-hors-palette'));
	assert.ok(regles(PUBLIC_FILE, '<p style="color: rgb(0 0 0)">x</p>').includes('couleur-hors-palette'));
});

// 04.8-01 (D-03, D-03c, D-28, UI-SPEC § 2.2 and § 2.6 n° 2): closed v4 scale with a 14 px floor,
// px of the scale or `inherit` only, every writing form read.
const ECHELLE_V4 = [14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 38, 44, 48, 52, 64];

test('scannerFichier: typo-hors-echelle accepts the closed v4 scale, refuses other sizes', () => {
	for (const px of ECHELLE_V4) {
		assert.deepEqual(regles(PUBLIC_FILE, `<p class="text-[${px}px]">x</p>`), [], `text-[${px}px]`);
		assert.deepEqual(regles(PUBLIC_FILE, `<style>p { font-size: ${px}px; }</style>`), [], `${px}px`);
	}
	for (const contenu of [
		'<p class="text-[12px]">x</p>',
		'<p class="text-[13px]">x</p>',
		'<p class="text-[15px]">x</p>',
		'<p class="text-[17px]">x</p>',
		'<p class="text-[36px]">x</p>',
		'<p class="text-[42px]">x</p>',
		'<p class="text-[1rem]">x</p>',
		'<p class="text-[0.9em]">x</p>',
		'<p class="text-sm">x</p>',
		'<p class="lg:text-4xl">x</p>',
		'<text font-size="60">D</text>',
		'<text font-size="13">D</text>'
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes('typo-hors-echelle'), contenu);
	}
	assert.deepEqual(regles(PUBLIC_FILE, '<p class="text-meta">x</p><p class="text-display">y</p>'), []);
	assert.deepEqual(regles(PUBLIC_FILE, '<text font-size="14">D</text>'), []);
});

test('scannerFichier: typo-hors-echelle refuses 13, 15 and 42 px, relative sizes and keywords', () => {
	for (const valeur of ['13px', '15px', '42px', '0.9em', '1rem', '0.875rem', '90%', '2vw', 'smaller', 'small', 'var(--text-meta)']) {
		assert.ok(
			regles(PUBLIC_FILE, `<style>p { font-size: ${valeur}; }</style>`).includes('typo-hors-echelle'),
			valeur
		);
		assert.ok(
			regles(PUBLIC_FILE, `<p style="font-size: ${valeur}">x</p>`).includes('typo-hors-echelle'),
			`style= ${valeur}`
		);
	}
	for (const valeur of ['14px', '18px', '22px', '52px', '64px', 'inherit', '16px !important']) {
		assert.deepEqual(regles(PUBLIC_FILE, `<style>p { font-size: ${valeur}; }</style>`), [], valeur);
	}
});

test('scannerFichier: typo-hors-echelle refuses every font: shorthand, whatever its value', () => {
	for (const contenu of [
		'<style>p { font: 500 21px/1.25 Inter; }</style>',
		'<style>p { font: 13px/1.4 X; }</style>',
		'<style>button { font: inherit; }</style>',
		'<style>\n\tbutton {\n\t\tfont: inherit;\n\t}\n</style>',
		'<p style="font: inherit">x</p>'
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes('typo-hors-echelle'), contenu);
	}
	// The long properties are not the shorthand.
	assert.deepEqual(
		regles(
			PUBLIC_FILE,
			'<style>button { font-family: inherit; font-size: inherit; font-weight: inherit; font-style: inherit; line-height: inherit; }</style>'
		),
		[]
	);
});

test('scannerFichier: typo-hors-echelle reads the style: directive and a value written on the next line', () => {
	for (const contenu of [
		'<p style:font-size="13px">x</p>',
		"<p style:font-size={'13px'}>x</p>",
		'<p style:font-size={taille}>x</p>',
		'<style>\n\tp {\n\t\tfont-size:\n\t\t\t13px;\n\t}\n</style>',
		'<style>\n\tp {\n\t\tfont-size:\n\t\t\t1rem\n\t}\n</style>'
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes('typo-hors-echelle'), contenu);
	}
	assert.deepEqual(regles(PUBLIC_FILE, '<p style:font-size="14px">x</p>'), []);
	assert.deepEqual(regles(PUBLIC_FILE, '<style>\n\tp {\n\t\tfont-size:\n\t\t\t14px;\n\t}\n</style>'), []);
	// The finding is located on the line of the property, not on the line of its value.
	assert.deepEqual(scannerFichier(PUBLIC_FILE, '<style>\n\tp {\n\t\tfont-size:\n\t\t\t13px;\n\t}\n</style>'), [
		{ regle: 'typo-hors-echelle', ligne: 3 }
	]);
});

// 04.8-01 (D-03b, D-28, UI-SPEC § 2.6 n° 10): three weights, read in every writing form.
test('scannerFichier: graisse-hors-contrat accepts 400, 500, 600 and inherit, refuses every other weight', () => {
	for (const valeur of ['200', '300', '700', '800', '900', 'bold', 'bolder', 'lighter', 'normal']) {
		assert.ok(
			regles(PUBLIC_FILE, `<style>b { font-weight: ${valeur}; }</style>`).includes('graisse-hors-contrat'),
			valeur
		);
		assert.ok(
			regles(PUBLIC_FILE, `<b style="font-weight:${valeur}">x</b>`).includes('graisse-hors-contrat'),
			`style= ${valeur}`
		);
	}
	for (const valeur of ['400', '500', '600', 'inherit', '600 !important']) {
		assert.deepEqual(regles(PUBLIC_FILE, `<style>b { font-weight: ${valeur}; }</style>`), [], valeur);
	}
});

test('scannerFichier: graisse-hors-contrat reads Tailwind utilities, style: directives and multi-line values', () => {
	for (const contenu of [
		'<p class="font-bold">x</p>',
		'<p class="font-light">x</p>',
		'<p class="font-thin">x</p>',
		'<p class="font-extralight">x</p>',
		'<p class="font-extrabold">x</p>',
		'<p class="md:font-black">x</p>',
		'<p class="font-[700]">x</p>',
		'<p class="font-[300]">x</p>',
		'<p style:font-weight="700">x</p>',
		'<p style:font-weight={700}>x</p>',
		'<p style:font-weight={poids}>x</p>',
		'<style>\n\tb {\n\t\tfont-weight:\n\t\t\t700;\n\t}\n</style>'
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes('graisse-hors-contrat'), contenu);
	}
	for (const contenu of [
		'<p class="font-normal font-medium font-semibold">x</p>',
		'<p class="font-[600]">x</p>',
		'<p class="font-[family-name:var(--font-display)]">x</p>',
		'<p style:font-weight="600">x</p>',
		'<p style:font-weight={500}>x</p>',
		'<style>p { font-family: var(--font-sans); }</style>'
	]) {
		assert.deepEqual(regles(PUBLIC_FILE, contenu), [], contenu);
	}
});

// 04.8-01 (D-04, D-28, UI-SPEC § 2.4 and § 2.6 n° 3): a radius is 0, 50 %, inherit or a contract
// radius, in every writing form. No generic pattern on token names: an unknown name fails.
const RAYONS_048 = ['panneau', 'bande', 'tuile', 'ligne', 'badge', 'control'];

test('scannerFichier: rayon-hors-jeton refuses a literal radius, an unknown token and Tailwind radii', () => {
	for (const contenu of [
		'<style>.p { border-radius: 10px; }</style>',
		'<style>.p { border-radius: 9999px; }</style>',
		'<style>.p { border-radius: 0px; }</style>',
		'<style>.p { border-radius: var(--radius-inconnu); }</style>',
		'<style>.p { border-radius: var(--radius-panel); }</style>',
		'<style>.p { border-radius: var(--radius-panneau, 10px); }</style>',
		'<style>.p { border-radius: var(--radius-panneau) 6px; }</style>',
		'<style>.p { border-radius: calc(var(--radius-panneau) - 1px); }</style>',
		'<style>.p { border-top-left-radius: 4px; }</style>',
		'<style>.p { border-end-start-radius: 4px; }</style>',
		'<style>.p { --radius-ligne: 2px; }</style>',
		'<p class="rounded">x</p>',
		'<p class="rounded-lg">x</p>',
		'<p class="md:rounded-t-md">x</p>',
		'<p class="rounded-[4px]">x</p>',
		'<p class="rounded-[var(--radius-inconnu)]">x</p>',
		'<p class="rounded-panel">x</p>',
		'<p class="rounded-[var(--radius-panel)]">x</p>',
		'<p style="border-radius: 6px">x</p>',
		'<p style:border-radius="6px">x</p>',
		'<p style:border-radius={rayon}>x</p>',
		'<style>\n\t.p {\n\t\tborder-radius:\n\t\t\t10px;\n\t}\n</style>'
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes('rayon-hors-jeton'), contenu);
	}
	for (const contenu of [
		'<style>.p { border-radius: 0; }</style>',
		'<style>.p { border-radius: 50%; }</style>',
		'<style>.p { border-radius: inherit; }</style>',
		...RAYONS_048.map((nom) => `<style>.p { border-radius: var(--radius-${nom}); }</style>`),
		'<style>.p { border-radius: var(--radius-panneau) var(--radius-panneau) 0 0; }</style>',
		'<style>.p { border-top-left-radius: var(--radius-bande) !important; }</style>',
		'<p class="rounded-none">x</p>',
		'<p class="rounded-full">x</p>',
		'<p class="rounded-[var(--radius-tuile)]">x</p>',
		'<p class="rounded-t-none sm:rounded-b-[var(--radius-panneau)]">x</p>',
		'<p style:border-radius="var(--radius-badge)">x</p>'
	]) {
		assert.deepEqual(regles(PUBLIC_FILE, contenu), [], contenu);
	}
	// The finding of a value written on the next line stays on the property's line.
	assert.deepEqual(scannerFichier(PUBLIC_FILE, '<style>\n\t.p {\n\t\tborder-radius:\n\t\t\t10px;\n\t}\n</style>'), [
		{ regle: 'rayon-hors-jeton', ligne: 3 }
	]);
});

// 04.8-01 (D-04, D-28, UI-SPEC § 2.3 and § 2.6 n° 4): a spacing is 0, auto or a name of the closed
// list, alone or in calc(-1 * var(…)), in every writing form. No `--s*` pattern: --s9 fails.
const ESPACEMENTS_048 = ['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's-fin', 's-demi'];

test('scannerFichier: espacement-hors-jeton refuses a literal spacing, an unknown token and Tailwind spacing', () => {
	for (const contenu of [
		'<style>.p { padding: 10px; }</style>',
		'<style>.p { padding: 0px; }</style>',
		'<style>.p { padding: var(--s-inconnu); }</style>',
		'<style>.p { gap: var(--s9); }</style>',
		'<style>.p { padding: var(--s3, 12px); }</style>',
		'<style>.p { margin: calc(2 * var(--s2)); }</style>',
		'<style>.p { margin: calc(var(--s2) * -1); }</style>',
		'<style>.p { margin: 0 4px; }</style>',
		'<style>.p { margin-inline: -4px; }</style>',
		'<style>.p { padding-block: 1rem; }</style>',
		'<style>.p { row-gap: 8px; }</style>',
		'<style>.p { column-gap: 2%; }</style>',
		'<style>.p { --s3: 10px; }</style>',
		'<p class="p-4">x</p>',
		'<p class="gap-2">x</p>',
		'<p class="space-y-6">x</p>',
		'<p class="md:px-3">x</p>',
		'<p class="-mt-2">x</p>',
		'<p class="mx-[10px]">x</p>',
		'<p class="p-[var(--s9)]">x</p>',
		'<p class="gap-x-[var(--s-inconnu)]">x</p>',
		'<p style="padding: 8px">x</p>',
		'<p style:gap="8px">x</p>',
		'<p style:padding-top={espace}>x</p>',
		'<style>\n\t.p {\n\t\tpadding:\n\t\t\t10px;\n\t}\n</style>'
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes('espacement-hors-jeton'), contenu);
	}
	for (const contenu of [
		'<style>.p { padding: var(--s3); }</style>',
		'<style>.p { gap: var(--s-demi); }</style>',
		'<style>.p { margin: 0 calc(-1 * var(--s2)); }</style>',
		'<style>.p { margin: 0 auto; }</style>',
		'<style>.p { margin-top: 0 !important; }</style>',
		'<style>.p { padding: var(--s-fin) var(--s8) var(--s1) var(--s4); }</style>',
		...ESPACEMENTS_048.map((nom) => `<style>.p { row-gap: var(--${nom}); column-gap: var(--${nom}); }</style>`),
		'<p class="mx-auto">x</p>',
		'<p class="p-[var(--s4)]">x</p>',
		'<p class="p-0 gap-0 space-y-[var(--s2)]">x</p>',
		'<p style:gap="var(--s2)">x</p>',
		'<p style="padding: var(--s2) 0">x</p>'
	]) {
		assert.deepEqual(regles(PUBLIC_FILE, contenu), [], contenu);
	}
	assert.deepEqual(scannerFichier(PUBLIC_FILE, '<style>\n\t.p {\n\t\tpadding:\n\t\t\t10px;\n\t}\n</style>'), [
		{ regle: 'espacement-hors-jeton', ligne: 3 }
	]);
});

// 04.8-01 Codex review 1, finding 2: a style: directive is read whatever its line breaks, and its
// shorthand form (`style:padding`, value taken from a variable of the same name) cannot be read
// statically, so it is refused for every guarded property.
const DIRECTIVES_GARDEES = [
	['font-size', 'typo-hors-echelle', '13px', '14px'],
	['font-weight', 'graisse-hors-contrat', '700', '600'],
	['border-radius', 'rayon-hors-jeton', '6px', 'var(--radius-tuile)'],
	['border-top-left-radius', 'rayon-hors-jeton', '6px', 'var(--radius-tuile)'],
	['padding', 'espacement-hors-jeton', '10px', 'var(--s2)'],
	['margin-inline', 'espacement-hors-jeton', '10px', 'var(--s2)'],
	['gap', 'espacement-hors-jeton', '10px', 'var(--s2)']
];

test('scannerFichier: a style: directive written over several lines or in shorthand is read (Codex 1, n° 2)', () => {
	for (const [propriete, regle, refusee, admise] of DIRECTIVES_GARDEES) {
		for (const contenu of [
			`<p style:${propriete}=\n"${refusee}">x</p>`,
			`<p\n\tstyle:${propriete}\n\t=\n\t"${refusee}"\n>x</p>`,
			`<p style:${propriete}="\n\t${refusee}\n">x</p>`,
			`<p style:${propriete}={\n\t'${refusee}'\n}>x</p>`,
			`<p style:${propriete}>x</p>`,
			`<p style:${propriete} class="x">x</p>`,
			`<p class="x" style:${propriete}\n>x</p>`,
			`<p style:${propriete}|important>x</p>`
		]) {
			assert.ok(regles(PUBLIC_FILE, contenu).includes(regle), contenu);
		}
		for (const contenu of [
			`<p style:${propriete}=\n"${admise}">x</p>`,
			`<p\n\tstyle:${propriete}\n\t=\n\t"${admise}"\n>x</p>`,
			`<p style:${propriete}|important=\n"${admise}">x</p>`
		]) {
			assert.deepEqual(regles(PUBLIC_FILE, contenu), [], contenu);
		}
	}
	// The finding stays on the line of the directive.
	assert.deepEqual(scannerFichier(PUBLIC_FILE, '<p\n\tstyle:padding=\n\t"10px">x</p>'), [
		{ regle: 'espacement-hors-jeton', ligne: 2 }
	]);
	// A directive on a property no rule guards stays free, shorthand included.
	assert.deepEqual(regles(PUBLIC_FILE, '<p style:width>x</p>\n<p style:opacity=\n"0.5">x</p>'), []);
});

// 04.8-01 Codex review 1, finding 3: a style: directive that sets a contract token redeclares it,
// like `--s4:` in CSS, whatever its value or its form.
test('scannerFichier: a style: directive cannot redeclare a spacing or radius token (Codex 1, n° 3)', () => {
	for (const [jeton, regle] of [
		['--s4', 'espacement-hors-jeton'],
		['--s-demi', 'espacement-hors-jeton'],
		['--radius-tuile', 'rayon-hors-jeton'],
		['--radius-control', 'rayon-hors-jeton']
	]) {
		for (const contenu of [
			`<p ${`style:${jeton}`}="100px">x</p>`,
			`<p style:${jeton}={'100px'}>x</p>`,
			`<p style:${jeton}|important="100px">x</p>`,
			`<p style:${jeton}>x</p>`,
			`<p style:${jeton}=\n"100px">x</p>`
		]) {
			assert.ok(regles(PUBLIC_FILE, contenu).includes(regle), contenu);
		}
	}
	assert.deepEqual(
		regles(PUBLIC_FILE, '<p style:--s4="100px" style:padding="var(--s4)">x</p>'),
		['espacement-hors-jeton']
	);
	assert.deepEqual(
		regles(PUBLIC_FILE, '<p style:--radius-tuile="100px" style:border-radius="var(--radius-tuile)">x</p>'),
		['rayon-hors-jeton']
	);
	// A custom property outside the contract lists stays free, and so does a longer name.
	assert.deepEqual(regles(PUBLIC_FILE, '<p style:--largeur="100px" style:--s4x="1" style:--radius-tuile-b="1">x</p>'), []);
});

// 04.8-01 Codex review 1, finding 4: a CSS property name is case-insensitive, a custom property name
// is not (`--S4` is another property than `--s4`).
test('scannerFichier: the case of a property name does not bypass the rules (Codex 1, n° 4)', () => {
	for (const [contenu, regle] of [
		['<style>.p { PADDING: 10px; }</style>', 'espacement-hors-jeton'],
		['<style>.p { Padding-Top: 10px; }</style>', 'espacement-hors-jeton'],
		['<style>.p { ROW-GAP: 10px; }</style>', 'espacement-hors-jeton'],
		['<p style="MARGIN: 8px">x</p>', 'espacement-hors-jeton'],
		['<p style:PADDING="10px">x</p>', 'espacement-hors-jeton'],
		['<p style:Padding>x</p>', 'espacement-hors-jeton'],
		['<style>\n\t.p {\n\t\tPADDING:\n\t\t\t10px;\n\t}\n</style>', 'espacement-hors-jeton'],
		['<style>.p { FONT-SIZE: 13px; }</style>', 'typo-hors-echelle'],
		['<p style:FONT-SIZE="13px">x</p>', 'typo-hors-echelle'],
		['<style>.p { FONT: 700 13px Inter; }</style>', 'typo-hors-echelle'],
		['<style>.p { Font: inherit; }</style>', 'typo-hors-echelle'],
		['<style>.p { FONT-WEIGHT: 700; }</style>', 'graisse-hors-contrat'],
		['<p style:Font-Weight="700">x</p>', 'graisse-hors-contrat'],
		['<style>.p { BORDER-RADIUS: 6px; }</style>', 'rayon-hors-jeton'],
		['<style>.p { Border-Top-Left-Radius: 6px; }</style>', 'rayon-hors-jeton'],
		['<p style:BORDER-RADIUS="6px">x</p>', 'rayon-hors-jeton'],
		['<style>.p { PADDING: var(--S4); }</style>', 'espacement-hors-jeton']
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes(regle), contenu);
	}
	for (const contenu of [
		'<style>.p { PADDING: var(--s4); FONT-SIZE: 14px; FONT-WEIGHT: 600; BORDER-RADIUS: var(--radius-tuile); }</style>',
		'<style>.p { --S4: 10px; --Radius-Tuile: 6px; }</style>',
		'<p class="P-4 Rounded-lg">x</p>'
	]) {
		assert.deepEqual(regles(PUBLIC_FILE, contenu), [], contenu);
	}
});

// 04.8-01 Codex review 1, finding 5: Tailwind 4.2.2 reads a size or a weight in more arbitrary forms
// than a bare number: type hints, math functions, keywords and the `(…)` variable shorthand. Each case
// below was compiled with the locked Tailwind (`@tailwindcss/node`): the comment gives its output.
test('scannerFichier: Tailwind arbitrary sizes and weights are read in every form (Codex 1, n° 5)', () => {
	for (const classe of [
		'text-[calc(13px)]', // font-size: calc(13px)
		'text-[calc(14px)]', // a function, never the bare px of the scale (D-03c)
		'text-[min(13px,2vw)]', // font-size: min(13px, 2vw)
		'text-[clamp(14px,2vw,16px)]',
		'text-[size:13px]', // font-size: 13px
		'text-[length:var(--x)]', // font-size: var(--x)
		'text-(length:--small)', // font-size: var(--small)
		'text-(size:--small)',
		'text-[absolute-size:large]', // font-size: large
		'text-[large]', // font-size: large
		'text-[larger]', // font-size: larger
		'text-[percentage:90%]', // font-size: 90%
		'md:text-[calc(13px)]'
	]) {
		assert.ok(regles(PUBLIC_FILE, `<p class="${classe}">x</p>`).includes('typo-hors-echelle'), classe);
	}
	for (const classe of [
		'font-[number:700]', // font-weight: 700
		'font-[weight:700]', // font-weight: 700
		'font-[weight:bold]', // font-weight: bold
		'font-[calc(700)]', // font-weight: calc(700)
		'font-[number:calc(600)]', // font-weight: calc(600)
		'font-[var(--x)]', // font-weight: var(--x)
		'font-(--heavy)', // font-weight: var(--heavy)
		'font-(weight:--heavy)', // font-weight: var(--heavy)
		'font-(number:--heavy)', // font-weight: var(--heavy)
		'hover:font-[number:700]'
	]) {
		assert.ok(regles(PUBLIC_FILE, `<p class="${classe}">x</p>`).includes('graisse-hors-contrat'), classe);
	}
	for (const classe of [
		'text-[14px]',
		'text-[length:16px]',
		'text-[size:18px]',
		'text-[var(--color-accent)]', // color: var(--color-accent), left to couleur-hors-palette
		'text-(--color-accent)', // color: var(--color-accent)
		'text-(color:--color-ink)',
		'font-[600]',
		'font-[number:600]',
		'font-[weight:500]',
		'font-[family-name:var(--font-display)]', // font-family
		'font-(family-name:--font-display)' // font-family
	]) {
		assert.deepEqual(regles(PUBLIC_FILE, `<p class="${classe}">x</p>`), [], classe);
	}
});

// 04.8-01 Codex review 1, finding 7: a closed CSS comment is not part of a value. An unclosed one (the
// value read stops at `;`) stays in the value and refuses it: never a silent pass.
test('scannerFichier: a CSS comment inside a value is ignored, an unclosed one refuses it (Codex 1, n° 7)', () => {
	for (const contenu of [
		'<style>.p { padding: var(--s4) /* reason */; }</style>',
		'<style>.p { padding: /* reason */ var(--s2) 0; }</style>',
		'<style>.p { margin: 0 /* a */ auto /* b */ !important; }</style>',
		'<style>.p { font-size: 14px /* floor */; }</style>',
		'<style>.p { font-weight: 600 /* Inter */; }</style>',
		'<style>.p { border-radius: var(--radius-tuile) /* tile */; }</style>',
		'<style>\n\t.p {\n\t\tpadding:\n\t\t\tvar(--s2) /* next line */;\n\t}\n</style>',
		'<p style="padding: var(--s2) /* inline */">x</p>'
	]) {
		assert.deepEqual(regles(PUBLIC_FILE, contenu), [], contenu);
	}
	for (const [contenu, regle] of [
		['<style>.p { padding: 10px /* reason */; }</style>', 'espacement-hors-jeton'],
		['<style>.p { padding: /* 10px */ 12px; }</style>', 'espacement-hors-jeton'],
		['<style>.p { padding: var(--s4) /* ; */ 10px; }</style>', 'espacement-hors-jeton'],
		['<style>.p { font-size: 13px /* floor */; }</style>', 'typo-hors-echelle'],
		['<style>.p { font-weight: 700 /* bold */; }</style>', 'graisse-hors-contrat'],
		['<style>.p { border-radius: 6px /* tile */; }</style>', 'rayon-hors-jeton']
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes(regle), contenu);
	}
	// A value made of a comment only: refused in a stylesheet; in a component, the Svelte parser refuses
	// the empty declaration and the scan fails closed.
	assert.ok(regles(FICHIER_CSS_PUBLIC, '.p { padding: /* var(--s4) */; }').includes('espacement-hors-jeton'));
	assert.throws(() => regles(PUBLIC_FILE, '<style>.p { padding: /* var(--s4) */; }</style>'), /analyse Svelte impossible/);
});

// 04.8-01 Codex review 2, finding 2: Svelte reads an unquoted directive value (`style:padding=10px`),
// across a line break after `=` too; the same closed lists apply to it.
test('scannerFichier: an unquoted style: directive value is read like a quoted one (Codex 2, n° 2)', () => {
	for (const [contenu, regle] of [
		['<p style:font-size=13px>x</p>', 'typo-hors-echelle'],
		['<p style:font-weight=700>x</p>', 'graisse-hors-contrat'],
		['<p style:padding=10px>x</p>', 'espacement-hors-jeton'],
		['<p style:border-radius=6px>x</p>', 'rayon-hors-jeton'],
		['<p style:padding=\n10px>x</p>', 'espacement-hors-jeton'],
		['<p style:font-size=\n\t13px>x</p>', 'typo-hors-echelle'],
		['<p style:padding = 10px>x</p>', 'espacement-hors-jeton'],
		['<p style:padding=10px/>', 'espacement-hors-jeton'],
		['<p style:margin-top=10px|>x</p>', 'espacement-hors-jeton'],
		// Text then expression: Svelte concatenates them, the value cannot be read statically.
		['<p style:padding=0{unite}>x</p>', 'espacement-hors-jeton'],
		['<p style:font-size|important=13px>x</p>', 'typo-hors-echelle']
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes(regle), contenu);
	}
	for (const contenu of [
		'<p style:padding=0>x</p>',
		'<p style:margin=auto>x</p>',
		'<p style:font-size=14px>x</p>',
		'<p style:font-weight=600>x</p>',
		'<p style:border-radius=0>x</p>',
		'<p style:padding=\n0 style:font-size=\n16px>x</p>'
	]) {
		assert.deepEqual(regles(PUBLIC_FILE, contenu), [], contenu);
	}
	// The finding stays on the line of the directive.
	assert.deepEqual(scannerFichier(PUBLIC_FILE, '<p\n\tstyle:padding=\n\t10px>x</p>'), [
		{ regle: 'espacement-hors-jeton', ligne: 2 }
	]);
});

// 04.8-01 Codex review 2, finding 3: the `font` shorthand is refused in a style: directive as in CSS,
// whatever its value, its modifier and its layout.
test('scannerFichier: every style:font directive is refused (Codex 2, n° 3)', () => {
	for (const contenu of [
		'<p style:font="700 13px Arial">x</p>',
		'<p style:font="inherit">x</p>',
		'<p style:font|important="700 13px Arial">x</p>',
		'<p style:font>x</p>',
		'<p style:font|important>x</p>',
		'<p style:font={police}>x</p>',
		"<p style:font={'inherit'}>x</p>",
		'<p style:font=inherit>x</p>',
		'<p style:FONT="inherit">x</p>',
		'<p\n\tstyle:font\n\t=\n\t"inherit"\n>x</p>'
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes('typo-hors-echelle'), contenu);
	}
	// The long properties stay free of the shorthand rule.
	assert.deepEqual(regles(PUBLIC_FILE, '<p style:font-style="italic" style:font-family="var(--font-sans)">x</p>'), []);
});

// 04.8-01 Codex review 2, finding 4: Tailwind 4.2.2 reads a signed number as a size or a weight
// (`text-[+13px]` → `font-size: +13px`, `font-[+700]` → `font-weight: +700`, checked with
// @tailwindcss/node). The sign is read, then the closed lists apply: `+14px` is 14 px, a negative
// size or weight is never in them.
test('scannerFichier: signed numbers are read as sizes and weights, then the closed lists apply (Codex 2, n° 4)', () => {
	for (const [classe, regle] of [
		['text-[+13px]', 'typo-hors-echelle'],
		['text-[-13px]', 'typo-hors-echelle'],
		['text-[-14px]', 'typo-hors-echelle'],
		['text-[+.8rem]', 'typo-hors-echelle'],
		['md:text-[+15px]', 'typo-hors-echelle'],
		['font-[+700]', 'graisse-hors-contrat'],
		['font-[-600]', 'graisse-hors-contrat'],
		['hover:font-[+300]', 'graisse-hors-contrat']
	]) {
		assert.ok(regles(PUBLIC_FILE, `<p class="${classe}">x</p>`).includes(regle), classe);
	}
	for (const [contenu, regle] of [
		['<style>.p { font-size: +13px; }</style>', 'typo-hors-echelle'],
		['<style>.p { font-size: -14px; }</style>', 'typo-hors-echelle'],
		['<style>.p { font-weight: +700; }</style>', 'graisse-hors-contrat'],
		['<style>.p { font-weight: -600; }</style>', 'graisse-hors-contrat']
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes(regle), contenu);
	}
	for (const contenu of [
		'<p class="text-[+14px] font-[+600]">x</p>',
		'<p class="text-[+16px] font-[+500] font-[+400]">x</p>',
		'<style>.p { font-size: +14px; font-weight: +600; }</style>'
	]) {
		assert.deepEqual(regles(PUBLIC_FILE, contenu), [], contenu);
	}
});

// 04.8-01 Codex review 2, finding 5: CSS comments are recognised before the separators are looked for,
// line numbers kept. A comment between a property and its colon hides nothing; a `;`, `}` or quote inside
// a comment cuts nothing; a `/*` inside a string opens no comment.
test('scannerFichier: CSS comments are removed before the declarations are read (Codex 2, n° 5)', () => {
	for (const [contenu, regle] of [
		['<style>.p { padding/**/: 10px; }</style>', 'espacement-hors-jeton'],
		['<style>.p { font-size/**/: 13px; }</style>', 'typo-hors-echelle'],
		['<style>.p { font-weight/**/: 700; }</style>', 'graisse-hors-contrat'],
		['<style>.p { border-radius/**/: 6px; }</style>', 'rayon-hors-jeton'],
		['<style>.p { padding /* why */ : 10px; }</style>', 'espacement-hors-jeton'],
		['<style>.p { --s4/**/: 10px; }</style>', 'espacement-hors-jeton'],
		['<style>.p { font/**/: inherit; }</style>', 'typo-hors-echelle'],
		['<p style="padding/**/: 10px">x</p>', 'espacement-hors-jeton'],
		['<style>.p { content: "/*"; padding: 10px; } .q { content: "*/"; }</style>', 'espacement-hors-jeton'],
		['<style>.p { content: \'/*\'; padding: 10px; } .q { content: \'*/\'; }</style>', 'espacement-hors-jeton']
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes(regle), contenu);
	}
	assert.ok(regles(FICHIER_CSS_PUBLIC, '.p { padding/**/: 10px; }').includes('espacement-hors-jeton'));
	// An unclosed comment in a stylesheet is not removed: what precedes it is still read.
	assert.ok(regles(FICHIER_CSS_PUBLIC, '.p { padding: 10px; } /* unclosed').includes('espacement-hors-jeton'));
	for (const contenu of [
		'<style>.p { padding: var(--s4) /* ; */; }</style>',
		'<style>.p { padding: var(--s4) /* } */; }</style>',
		'<style>.p { padding: var(--s4) /* " \' */; }</style>',
		'<style>.p {\n\tpadding: var(--s2) /* a\n\t; b } */;\n}</style>',
		'<style>/* .p { padding: 10px; } */</style>',
		'<p style="padding: var(--s2) /* ; */">x</p>',
		'<p style:padding="var(--s2) /* ; */">x</p>'
	]) {
		assert.deepEqual(regles(PUBLIC_FILE, contenu), [], contenu);
	}
	// Lines are kept: the finding is on the line of the property, after a comment over two lines.
	assert.deepEqual(scannerFichier(PUBLIC_FILE, '<style>\n.p {\n\t/* a\n\tb */ padding: 10px;\n}\n</style>'), [
		{ regle: 'espacement-hors-jeton', ligne: 4 }
	]);
	assert.deepEqual(scannerFichier(PUBLIC_FILE, '<style>\n.p {\n\tpadding/* a\n\tb */: 10px;\n}\n</style>'), [
		{ regle: 'espacement-hors-jeton', ligne: 3 }
	]);
});

// 04.8-01 Codex review 2, finding 6: CSS names, keywords and functions are case-insensitive, so the
// family and colour rules read them whatever their case; a custom property name and a Tailwind class
// keep theirs.
test('scannerFichier: the case of a name, a keyword or a function does not bypass the family and colour rules (Codex 2, n° 6)', () => {
	for (const [chemin, contenu, regle] of [
		[APP_FILE, '<style>.p { FONT-FAMILY: Arial; }</style>', 'police-hors-token'],
		[APP_FILE, '<style>.p { Font-Family: Georgia, serif; }</style>', 'police-hors-token'],
		[APP_FILE, '<p style="FONT-FAMILY: Arial">x</p>', 'police-hors-token'],
		[APP_FILE, '<text FONT-FAMILY="Arial">x</text>', 'police-hors-token'],
		[APP_FILE, '<p style:FONT-FAMILY="Arial">x</p>', 'police-hors-token'],
		[FICHIER_CSS_PUBLIC, '.p { FONT-FAMILY: Arial; }', 'police-hors-token'],
		[PUBLIC_FILE, '<style>.p { COLOR: white; }</style>', 'couleur-hors-palette'],
		[PUBLIC_FILE, '<style>.p { color: WHITE; }</style>', 'couleur-hors-palette'],
		[PUBLIC_FILE, '<style>.p { Background-Color: Black; }</style>', 'couleur-hors-palette'],
		[PUBLIC_FILE, '<style>.p { color: RGB(255 255 255); }</style>', 'couleur-hors-palette'],
		[PUBLIC_FILE, '<style>.p { color: Hsl(0 0% 100%); }</style>', 'couleur-hors-palette'],
		[PUBLIC_FILE, '<style>.p { color: VAR(--color-bronze); }</style>', 'couleur-hors-palette'],
		[PUBLIC_FILE, '<p style="COLOR: WHITE">x</p>', 'couleur-hors-palette'],
		[PUBLIC_FILE, '<svg><rect FILL="WHITE" /></svg>', 'couleur-hors-palette'],
		[PUBLIC_FILE, '<p style:color="WHITE">x</p>', 'couleur-hors-palette'],
		[PUBLIC_FILE, '<p style:COLOR="RGB(0 0 0)">x</p>', 'couleur-hors-palette'],
		[FICHIER_CSS_PUBLIC, '.p { color: WHITE; }', 'couleur-hors-palette']
	]) {
		assert.ok(regles(chemin, contenu).includes(regle), contenu);
	}
	for (const [chemin, contenu] of [
		// A function or a keyword in capitals is the same function or keyword.
		[APP_FILE, '<style>.p { font-family: VAR(--font-sans); }</style>'],
		[APP_FILE, '<style>.p { FONT-FAMILY: Inherit; }</style>'],
		[PUBLIC_FILE, '<style>.p { COLOR: VAR(--color-ink); BACKGROUND: Var(--color-paper); }</style>'],
		// A string keeps its case, and so does a Tailwind class, applied or written.
		[PUBLIC_FILE, '<style>.p { content: "WHITE"; } .q { @apply Text-White; }</style>'],
		[PUBLIC_FILE, '<p class="Text-White">x</p>']
	]) {
		assert.deepEqual(regles(chemin, contenu), [], contenu);
	}
	// A custom property keeps its case: --FONT-SANS is not the declared --font-sans.
	assert.ok(regles(APP_FILE, '<style>.p { font-family: var(--FONT-SANS); }</style>').includes('police-hors-token'));
});

// 04.8-01, next to Codex review 2 finding 2: a presentation attribute is read whatever its quoting too
// (`<text font-size=13>`, `font-family='Arial'`); text mixed with an expression is not a literal.
test('scannerFichier: an unquoted or single-quoted presentation attribute is read (Codex 2, n° 2, variant)', () => {
	for (const [chemin, contenu, regle] of [
		[PUBLIC_FILE, '<svg><text font-size=13>D</text></svg>', 'typo-hors-echelle'],
		[PUBLIC_FILE, '<svg><text font-size=14{unite}>D</text></svg>', 'typo-hors-echelle'],
		[PUBLIC_FILE, '<svg><text font-weight=700/></svg>', 'graisse-hors-contrat'],
		[APP_FILE, '<svg><text font-family=Arial>D</text></svg>', 'police-hors-token'],
		[APP_FILE, "<svg><text font-family='Arial'>D</text></svg>", 'police-hors-token'],
		[APP_FILE, '<svg><text FONT-FAMILY=Arial>D</text></svg>', 'police-hors-token']
	]) {
		assert.ok(regles(chemin, contenu).includes(regle), contenu);
	}
	for (const [chemin, contenu] of [
		[PUBLIC_FILE, '<svg><text font-size=14 font-weight=600>D</text></svg>'],
		[PUBLIC_FILE, '<svg><text font-size=16/></svg>'],
		[APP_FILE, "<svg><text font-family='var(--font-sans)' x=1>D</text></svg>"],
		[APP_FILE, '<svg><text font-family=inherit>D</text></svg>']
	]) {
		assert.deepEqual(regles(chemin, contenu), [], contenu);
	}
});

test('scannerFichier: rayon-hors-jeton and espacement-hors-jeton do not fire outside the public scope', () => {
	const contenu = '<div class="rounded-lg p-4" style="padding: 10px; border-radius: 3px">x</div>\n';
	assert.deepEqual(regles(APP_FILE, contenu), []);
	assert.deepEqual(regles('src/lib/components/admin/OverviewCards.svelte', contenu), []);
	assert.deepEqual(regles(PUBLIC_FILE, contenu).sort(), ['espacement-hors-jeton', 'rayon-hors-jeton']);
});

// 04.8-01 amendment 1 (D-02, D-27): the three new colours are contract tokens; a name outside the
// contract still fails.
test('scannerFichier: couleur-hors-palette accepts the three colours of 04.8, refuses an unknown name', () => {
	for (const nom of ['panneau', 'filet-pale', 'vigilance-bg']) {
		assert.deepEqual(regles(PUBLIC_FILE, `<style>p { background: var(--color-${nom}); }</style>`), [], nom);
	}
	assert.ok(regles(PUBLIC_FILE, '<style>p { color: var(--color-ocre); }</style>').includes('couleur-hors-palette'));
});

// 04.8-01 amendment 6: the header search and the dataset detail are public files, and the public scope
// only grows. The frozen list is the scope at the end of 04.8-01.
const PORTEE_PUBLIQUE_048 = [
	'src/routes/(marketing)/',
	'src/routes/+layout.svelte',
	'src/routes/+error.svelte',
	'src/lib/components/SiteHeader.svelte',
	'src/lib/components/SiteFooter.svelte',
	'src/lib/components/CommuneSearch.svelte',
	'src/lib/components/SearchResult.svelte',
	'src/lib/components/DatasetCard.svelte',
	'src/lib/components/RechercheEnTete.svelte',
	'src/lib/components/DatasetDetail.svelte',
	'src/lib/components/fiche/',
	'src/lib/components/adresse/',
	'src/lib/components/compare/',
	'src/lib/components/ds/'
];

function entreesRetirees(portee, figee) {
	return figee.filter((entree) => !portee.includes(entree));
}

test('scannerFichier: RechercheEnTete and DatasetDetail are under the public rules (amendment 6)', () => {
	for (const chemin of ['src/lib/components/RechercheEnTete.svelte', 'src/lib/components/DatasetDetail.svelte']) {
		assert.ok(regles(chemin, '<p class="text-red-500">x</p>\n').includes('couleur-hors-palette'), chemin);
		assert.ok(regles(chemin, '<style>.p { padding: 10px; }</style>\n').includes('espacement-hors-jeton'), chemin);
	}
});

test('check-design-gate: PORTEE_PUBLIQUE never shrinks (amendment 6)', () => {
	assert.deepEqual(entreesRetirees(PORTEE_PUBLIQUE, PORTEE_PUBLIQUE_048), []);
	// The sentinel bites: the scope without any one of its entries is reported.
	for (const entree of PORTEE_PUBLIQUE_048) {
		assert.deepEqual(
			entreesRetirees(
				PORTEE_PUBLIQUE.filter((e) => e !== entree),
				PORTEE_PUBLIQUE_048
			),
			[entree]
		);
	}
});

test('scannerFichier: infobulle-native covers <a>, <summary>, role="button" and the {title} shorthand', () => {
	assert.ok(regles(PUBLIC_FILE, '<a href="/x" title="x">y</a>').includes('infobulle-native'));
	assert.ok(regles(PUBLIC_FILE, '<summary title="x">y</summary>').includes('infobulle-native'));
	assert.ok(
		regles(PUBLIC_FILE, '<div role="button" tabindex="0" {title}>y</div>').includes('infobulle-native')
	);
	// A non-interactive element may carry a title; a script comparison does not hide later tags.
	assert.deepEqual(regles(PUBLIC_FILE, '<abbr title="Institut national">INSEE</abbr>'), []);
	assert.ok(
		regles(
			PUBLIC_FILE,
			'<script>if (i<n && ok) { go(); }</script>\n<a href="/" title="x">y</a>'
		).includes('infobulle-native')
	);
});

test('scannerFichier: perimetre-en-dur catches codes, pairs, prefixes and names, not sizes', () => {
	for (const contenu of [
		// Script code sits in a <script>, as in a component (the gate parses every .svelte file).
		"<script>const departement = '44';</script>",
		'<p>Démo 44 &amp; 85</p>',
		'<p>périmètre 44/85</p>',
		'<script>if (/^(44|85)/.test(code)) {}</script>',
		'<a href="/commune/85047">Challans</a>',
		'<p>Vendée</p>',
		'<script>goto(`/communes?departement=85`)</script>'
	]) {
		assert.ok(regles(PUBLIC_FILE, contenu).includes('perimetre-en-dur'), contenu);
	}
	for (const contenu of [
		'<svg width="44" height="44"></svg>',
		'<a class="min-h-[44px]">x</a>',
		'<p>2044</p>'
	]) {
		assert.deepEqual(regles(PUBLIC_FILE, contenu), [], contenu);
	}
});

test('check-design-gate: ALLOWLIST is empty (no silent exceptions)', () => {
	assert.deepEqual(DESIGN_ALLOWLIST, []);
});

// Frontend root of the real repository, and its ratchet reference (D-29), computed once: it reads the
// git history (base and commits of check-design-gate.mjs), out of reach of the commit under test.
const RACINE_FRONTEND = fileURLToPath(new URL('..', import.meta.url));
let referenceEnCache;
function referenceDuDepot() {
	referenceEnCache ??= designGate.detteDeReference({ racine: RACINE_FRONTEND });
	return referenceEnCache;
}

// Files written in phase 3 (and the planned ones) are born clean: the debt only lists files
// that predate the contract and waits for their redesign (T-03-03-03).
const FICHIERS_PHASE_3 = [
	'src/lib/components/SiteHeader.svelte',
	'src/lib/components/SiteFooter.svelte',
	'src/routes/+layout.svelte',
	'src/routes/+error.svelte',
	'src/routes/(marketing)/+layout.svelte',
	'src/routes/(marketing)/developpeurs/+page.svelte',
	'src/lib/components/CommuneSearch.svelte',
	'src/lib/components/SearchResult.svelte',
	'src/lib/components/DatasetCard.svelte',
	'src/lib/components/compare/'
];

// The three rules of 04.8 did not exist in phase 3: their initial debt is measured on every public
// file, phase 3 files included, and frozen (D-29, plan 04.8-01 task 3). The seven historic rules stay
// closed for phase 3 files; the three new ones are bounded there by the ratchet, with no other
// exception.
const REGLES_NOUVELLES_048 = ['graisse-hors-contrat', 'rayon-hors-jeton', 'espacement-hors-jeton'];

const estFichierPhase3 = (chemin) =>
	FICHIERS_PHASE_3.some((f) => chemin === f || (f.endsWith('/') && chemin.startsWith(f)));

function detteHistoriquePhase3(dette) {
	return Object.entries(dette)
		.filter(([chemin]) => estFichierPhase3(chemin))
		.flatMap(([chemin, comptes]) =>
			Object.keys(comptes)
				.filter((regle) => !REGLES_NOUVELLES_048.includes(regle))
				.map((regle) => `${chemin} ${regle}`)
		);
}

const dettePhase3 = (dette) => Object.fromEntries(Object.entries(dette).filter(([chemin]) => estFichierPhase3(chemin)));

test('check-design-gate: phase 3 files carry no historic debt, and their new-rule debt stays under the ratchet', () => {
	assert.deepEqual(detteHistoriquePhase3(DESIGN_DETTE), []);
	// The sentinel still bites on a historic rule, beside a frozen debt of a new one.
	assert.deepEqual(
		detteHistoriquePhase3({ 'src/lib/components/SiteHeader.svelte': { 'couleur-hors-palette': 1 } }),
		['src/lib/components/SiteHeader.svelte couleur-hors-palette']
	);
	assert.deepEqual(
		detteHistoriquePhase3({
			'src/lib/components/compare/CompareTable.svelte': { 'espacement-hors-jeton': 32, 'typo-hors-echelle': 1 }
		}),
		['src/lib/components/compare/CompareTable.svelte typo-hors-echelle']
	);
	// New rules: bounded by the git reference, never by their mere name.
	const { reference } = referenceDuDepot();
	assert.deepEqual(designGate.evaluerCliquet(reference, dettePhase3(DESIGN_DETTE)), []);
	assert.equal(
		bornePhase3(reference, 'src/lib/components/SiteHeader.svelte'),
		1,
		'one more spacing finding on SiteHeader than its reference fails'
	);
});

/**
 * Findings of one spacing finding above the reference of `chemin` (sentinel of phase 3 files). A
 * solded debt has no entry: its ceiling is zero, so the file stays bounded once it is redesigned.
 */
function bornePhase3(reference, chemin) {
	const gele = reference[chemin]?.['espacement-hors-jeton'] ?? 0;
	return designGate.evaluerCliquet(reference, { [chemin]: { 'espacement-hors-jeton': gele + 1 } }).length;
}

test('check-design-gate: a phase 3 file whose spacing debt is solded stays bounded at zero', () => {
	const sh = 'src/lib/components/SiteHeader.svelte';
	assert.equal(bornePhase3({}, sh), 1, 'no reference entry: one finding fails');
	assert.equal(bornePhase3({ [sh]: { 'espacement-hors-jeton': 3 } }, sh), 1, 'frozen at 3: a fourth fails');
});

test('check-design-gate: DETTE records no site-wide rule (hex and fonts stay closed everywhere)', () => {
	const siteWide = Object.entries(DESIGN_DETTE).filter(
		([, comptes]) => 'hex-en-dur' in comptes || 'police-hors-token' in comptes
	);
	assert.deepEqual(siteWide, []);
});

// 04.8-01 amendment 1 (D-02, D-27, UI-SPEC § 2.6 n° 1): the token sentinel of app.css compares sets,
// block by block, never counts. Every declaration block of app.css is read with the chain of its
// enclosing selectors and at-rules; comments are dropped first.
function blocsCss(css) {
	const texte = css.replace(/\/\*[\s\S]*?\*\//g, '');
	const blocs = [];
	const pile = [];
	let segment = '';
	for (const c of texte) {
		if (c === '{') {
			// Text before `{`: the parent's last declarations, then this block's selector.
			const fin = segment.lastIndexOf(';');
			if (pile.length > 0) pile.at(-1).direct += segment.slice(0, fin + 1);
			pile.push({ selecteur: segment.slice(fin + 1).trim(), direct: '' });
		} else if (c === '}') {
			const bloc = pile.pop();
			bloc.direct += segment;
			blocs.push({
				contexte: [...pile.map((b) => b.selecteur), bloc.selecteur],
				proprietes: [...bloc.direct.matchAll(/(--[\w-]+)\s*:\s*([^;]*)/g)].map((m) => [m[1], m[2].trim()])
			});
		} else {
			segment += c;
			continue;
		}
		segment = '';
	}
	return blocs;
}

// Sauge colours overridden under .public-site on screen, as they stand before 04.8 (closed list).
const COULEURS_SAUGE = ['bg', 'paper', 'soft', 'ink', 'muted', 'line', 'line-strong', 'accent', 'accent-hover', 'focus'];
// Colours of the print block of .public-site: the former seven plus the panel, white on paper.
const COULEURS_IMPRESSION = ['bg', 'paper', 'ink', 'muted', 'soft', 'line', 'accent', 'panneau'];
// Non-colour tokens of :root (UI-SPEC § 2.3, § 2.4, artefact table of 04.8-01), with their values.
const JETONS_RACINE = {
	'--s1': '4px',
	'--s2': '8px',
	'--s3': '12px',
	'--s4': '16px',
	'--s5': '24px',
	'--s6': '32px',
	'--s7': '48px',
	'--s8': '72px',
	'--s-fin': '2px',
	'--s-demi': '6px',
	'--radius-panneau': '10px',
	'--radius-bande': 'calc(var(--radius-panneau) - 1px)',
	'--radius-tuile': '8px',
	'--radius-ligne': '6px',
	'--radius-badge': '4px',
	'--mesure': '60ch',
	'--largeur-page': '1240px'
};

const memeEnsemble = (a, b) => [...new Set(a)].sort().join(' ') === [...new Set(b)].sort().join(' ');
const couleurs = (bloc) => bloc.proprietes.filter(([nom]) => nom.startsWith('--color-')).map(([nom]) => nom.slice(8));

/** Violations of the four set equalities (a) to (d), each prefixed by its letter. */
function ecartsJetonsAppCss(css) {
	const blocs = blocsCss(css);
	const contexte = (bloc) => bloc.contexte.join(' > ');
	const theme = blocs.filter((b) => contexte(b) === '@theme');
	const publicEcran = blocs.filter((b) => contexte(b) === '.public-site');
	const publicImpression = blocs.filter((b) => contexte(b) === '@media print > .public-site');
	const racine = blocs.filter((b) => b.contexte.at(-1) === ':root');
	const ecarts = [];
	// (a) @theme declares exactly the contract tokens and the transition aliases; any other block
	// may only override a contract token.
	const nomsTheme = theme.flatMap(couleurs);
	if (theme.length !== 1 || !memeEnsemble(nomsTheme, [...JETONS_CONTRAT, ...ALIAS_DE_TRANSITION])) {
		ecarts.push(`(a) @theme declare [${nomsTheme.join(', ')}]`);
	}
	const horsContrat = blocs
		.filter((b) => !theme.includes(b))
		.flatMap(couleurs)
		.filter((nom) => !JETONS_CONTRAT.includes(nom));
	if (horsContrat.length > 0) ecarts.push(`(a) couleurs hors contrat surchargees : ${horsContrat.join(', ')}`);
	// (b) .public-site overrides exactly the Sauge colours on screen, and its print block exactly its own.
	const nomsEcran = publicEcran.flatMap(couleurs);
	if (publicEcran.length !== 1 || !memeEnsemble(nomsEcran, COULEURS_SAUGE)) {
		ecarts.push(`(b) .public-site surcharge [${nomsEcran.join(', ')}]`);
	}
	const nomsImpression = publicImpression.flatMap(couleurs);
	if (publicImpression.length !== 1 || !memeEnsemble(nomsImpression, COULEURS_IMPRESSION)) {
		ecarts.push(`(b) .public-site en impression surcharge [${nomsImpression.join(', ')}]`);
	}
	// (c) :root declares exactly the non-colour tokens, with their contract values.
	const proprietesRacine = racine.flatMap((b) => b.proprietes);
	if (!memeEnsemble(proprietesRacine.map(([nom]) => nom), Object.keys(JETONS_RACINE))) {
		ecarts.push(`(c) :root declare [${proprietesRacine.map(([nom]) => nom).join(', ')}]`);
	}
	for (const [nom, valeur] of proprietesRacine) {
		if (nom in JETONS_RACINE && JETONS_RACINE[nom] !== valeur) ecarts.push(`(c) ${nom}: ${valeur}`);
	}
	// (d) None of them is declared anywhere else, .public-site included.
	const redeclares = blocs
		.filter((b) => !racine.includes(b))
		.flatMap((b) => b.proprietes.map(([nom]) => nom))
		.filter((nom) => nom in JETONS_RACINE);
	if (redeclares.length > 0) ecarts.push(`(d) redeclares hors de :root : ${redeclares.join(', ')}`);
	return ecarts;
}

const APP_CSS = () => readFileSync(new URL('../src/app.css', import.meta.url), 'utf8');

test('check-design-gate: app.css declares exactly the contract colours, the Sauge overrides and the :root tokens (a to d)', () => {
	const css = APP_CSS();
	assert.deepEqual(ecartsJetonsAppCss(css), []);
	for (const nom of ['panneau', 'filet-pale', 'vigilance-bg']) assert.ok(JETONS_CONTRAT.includes(nom), nom);
	// The values of the charter chosen by Ianis on 06/11/2026 (AA recomputed on each of them).
	const theme = Object.fromEntries(blocsCss(css).find((b) => b.contexte.join() === '@theme').proprietes);
	assert.equal(theme['--color-panneau'], '#ffffff');
	assert.equal(theme['--color-filet-pale'], '#dce1de');
	assert.equal(theme['--color-vigilance-bg'], '#f2e5b6');
	assert.equal(theme['--color-warn-bg'], '#faf4e6');
	assert.equal(theme['--radius-control'], '4px');
	assert.equal(theme['--radius-panel'], '8px');
	const faces = [...css.matchAll(/@font-face\s*\{([^}]*)\}/g)].map((m) => ({
		famille: /font-family:\s*"([^"]+)"/.exec(m[1])?.[1],
		corps: m[1]
	}));
	assert.ok(faces.length > 0, 'no @font-face found');
	const hebergees = faces.filter((f) => /url\(/.test(f.corps));
	assert.deepEqual([...new Set(hebergees.map((f) => f.famille))], ['Inter', 'Newsreader'], 'Only the two approved self-hosted typefaces');
	// Audit P-2: any other face is a fallback sized on one of the two, local() only (nothing downloaded),
	// with its four metric overrides.
	for (const repli of faces.filter((f) => !/url\(/.test(f.corps))) {
		assert.match(repli.famille ?? '', /^(Inter|Newsreader) Repli$/, `unexpected face ${repli.famille}`);
		assert.match(repli.corps, /src:\s*local\(/, `${repli.famille}: local() only`);
		for (const p of ['size-adjust', 'ascent-override', 'descent-override', 'line-gap-override']) {
			assert.match(repli.corps, new RegExp(`${p}:\\s*[\\d.]+%`), `${repli.famille}: ${p}`);
		}
	}
});

// WCAG 2 relative luminance and contrast of two hexadecimal colours.
function contrasteWcag(a, b) {
	const lum = (hex) => {
		const [r, g, b2] = [1, 3, 5].map((i) => {
			const c = Number.parseInt(hex.slice(i, i + 2), 16) / 255;
			return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
		});
		return 0.2126 * r + 0.7152 * g + 0.0722 * b2;
	};
	const [haut, bas] = [lum(a), lum(b)].sort((x, y) => y - x);
	return (haut + 0.05) / (bas + 0.05);
}

test('app.css: the text pairs of the public charter reach AA, computed on the tokens themselves', () => {
	const css = APP_CSS();
	const blocs = blocsCss(css);
	const jetons = {
		...Object.fromEntries(blocs.find((b) => b.contexte.join() === '@theme').proprietes),
		...Object.fromEntries(blocs.find((b) => b.contexte.join(' > ') === '.public-site').proprietes)
	};
	const couleur = (nom) => {
		const valeur = jetons[`--color-${nom}`];
		assert.match(valeur ?? '', /^#[0-9a-f]{6}$/i, nom);
		return valeur;
	};
	const paires = [];
	const sur = (encres, fonds) => {
		for (const encre of encres) for (const fond of fonds) paires.push([encre, fond]);
	};
	const CATEGORIES = ['prix', 'logement', 'quotidien', 'risques'];
	sur(['ink', 'muted', 'accent'], ['bg', 'paper', 'soft', 'bande', 'accent-doux', ...CATEGORIES.map((c) => `${c}-fond`)]);
	for (const c of CATEGORIES) sur([`${c}-encre`], [`${c}-fond`, 'paper']);
	sur(['paper'], ['accent', 'accent-hover']);
	sur(['hero-encre', 'hero-encre-2', 'hero-lien'], ['hero-fond', 'hero-anneau', 'hero-anneau-2']);
	sur(['hero-sur-action'], ['hero-action', 'accent-doux']);
	sur(['pied-encre', 'pied-lien'], ['pied']);
	sur(['warn'], ['vigilance-bg', 'paper']);
	sur(['danger'], ['panne-fond', 'paper']);
	sur(['neutre'], ['neutre-fond', 'paper']);
	sur(['dpe-ink'], ['dpe-b', 'dpe-c', 'dpe-d', 'dpe-e', 'dpe-f']);
	sur(['dpe-sur-a'], ['dpe-a']);
	sur(['dpe-sur-g'], ['dpe-g']);
	assert.ok(paires.length > 60, 'non-vacuity: the pairs were built');
	const sousLeSeuil = paires
		.map(([encre, fond]) => [encre, fond, contrasteWcag(couleur(encre), couleur(fond))])
		.filter(([, , rapport]) => rapport < 4.5)
		.map(([encre, fond, rapport]) => `${encre} sur ${fond} : ${rapport.toFixed(2)}`);
	assert.deepEqual(sousLeSeuil, []);
	// The check fails on a pair under AA: a white letter on the official green of class A.
	assert.ok(contrasteWcag(couleur('paper'), couleur('dpe-a')) < 4.5);
});

test('check-design-gate: the app.css sentinel catches each of its four set inequalities', () => {
	const css = APP_CSS();
	const muter = (motif, ajout) => {
		assert.ok(motif.test(css), `motif ${motif} introuvable`);
		return css.replace(motif, (m) => `${m}\n  ${ajout}`);
	};
	const lettres = (ecarts) => ecarts.map((e) => e.slice(0, 3));
	// (a) a colour added to app.css without an entry in the contract.
	assert.deepEqual(lettres(ecartsJetonsAppCss(muter(/^@theme \{/m, '--color-x: #000000;'))), ['(a)']);
	// (b) a new colour overridden under .public-site.
	assert.deepEqual(lettres(ecartsJetonsAppCss(muter(/^\.public-site \{/m, '--color-panneau: #ffffff;'))), ['(b)']);
	// (c) a spacing outside the closed list declared on :root.
	assert.deepEqual(lettres(ecartsJetonsAppCss(muter(/^:root \{/m, '--s9: 80px;'))), ['(c)']);
	// (d) a :root token redeclared under .public-site.
	assert.deepEqual(lettres(ecartsJetonsAppCss(muter(/^\.public-site \{/m, '--s3: 10px;'))), ['(d)']);
});

test('evaluerDette: fails when a file outside the debt carries a constat', () => {
	const constats = { 'src/a.svelte': { 'hex-en-dur': 1 } };
	const result = evaluerDette(constats, {});
	assert.equal(result.ok, false);
	assert.ok(result.erreurs.some((e) => e.includes('src/a.svelte')));
});

test('evaluerDette: fails when a debt file carries more constats than its recorded count', () => {
	const constats = { 'src/a.svelte': { 'hex-en-dur': 3 } };
	const dette = { 'src/a.svelte': { 'hex-en-dur': 2 } };
	const result = evaluerDette(constats, dette);
	assert.equal(result.ok, false);
});

test('evaluerDette: succeeds when a debt file carries fewer constats than its recorded count', () => {
	const constats = { 'src/a.svelte': { 'hex-en-dur': 1 } };
	const dette = { 'src/a.svelte': { 'hex-en-dur': 2 } };
	const result = evaluerDette(constats, dette);
	assert.equal(result.ok, true);
});

test('evaluerDette: tolerates a debt file absent from disk, reports it as solved (soldee)', () => {
	const constats = {};
	const dette = { 'src/gone.svelte': { 'hex-en-dur': 4 } };
	const result = evaluerDette(constats, dette);
	assert.equal(result.ok, true);
	assert.deepEqual(result.soldees, ['src/gone.svelte']);
});

test('runDesignGate: scans .svelte and .css files but never the token source app.css', () => {
	withTempFrontendRoot(
		{
			'src/app.css': '@theme { --color-accent: #355b50; }\n',
			'src/lib/extra.css': '.x { color: #123456; }\n'
		},
		(root) => {
			const result = runDesignGate({ racine: root, dette: {} });
			assert.equal(result.ok, false);
			assert.ok(result.erreurs.some((e) => e.startsWith('src/lib/extra.css')));
			assert.ok(!result.erreurs.some((e) => e.startsWith('src/app.css')));
		}
	);
});

test('runDesignGate: cheminsCibles ignores the recorded debt (strict mode)', () => {
	withTempFrontendRoot(
		{ 'src/routes/(marketing)/bad/+page.svelte': '<p class="text-bronze">x</p>\n' },
		(root) => {
			const dette = {
				'src/routes/(marketing)/bad/+page.svelte': { 'couleur-hors-palette': 1 }
			};
			const nonStrict = runDesignGate({ racine: root, dette });
			assert.equal(nonStrict.ok, true, 'covered by the recorded debt');
			const strict = runDesignGate({
				racine: root,
				dette,
				cheminsCibles: ['src/routes/(marketing)/bad/+page.svelte']
			});
			assert.equal(strict.ok, false, 'strict mode ignores the debt');
		}
	);
});

test('runDesignGate: a missing/unreadable target file fails closed, never a silent pass', () => {
	withTempFrontendRoot({}, (root) => {
		const result = runDesignGate({
			racine: root,
			dette: {},
			cheminsCibles: ['src/lib/components/SiteHeader.svelte']
		});
		assert.equal(result.ok, false);
		assert.ok(result.erreurs.some((e) => e.includes('lecture impossible')));
	});
});

// ---------------------------------------------------------------------------
// Debt ratchet (04.8-01 task 3, D-29, UI-SPEC § 2.6): each (file, rule) count of DETTE and of the
// measured debt stays under a reference read from git, out of reach of the commit under test. The
// scenarios run on throwaway git repositories in the temp directory, never on the real one.
// ---------------------------------------------------------------------------

const ESP = 'espacement-hors-jeton';
const RAY = 'rayon-hors-jeton';
const TYPO = 'typo-hors-echelle';
const REGLES_HISTORIQUES = [
	'hex-en-dur',
	'police-hors-token',
	'couleur-hors-palette',
	TYPO,
	'emoji-ui',
	'infobulle-native',
	'perimetre-en-dur'
];
const REGLES_COMPLETES = [...REGLES_HISTORIQUES, 'graisse-hors-contrat', RAY, ESP];
const SCRIPT_GARDE = 'scripts/check-design-gate.mjs';
const SH = 'src/lib/components/SiteHeader.svelte';
const SF = 'src/lib/components/SiteFooter.svelte';
const CGU = 'src/routes/(marketing)/cgu/+page.svelte';
const NOUVEAU = 'src/lib/components/fiche/Nouveau.svelte';
const PROPRE = '<p>propre</p>\n';
// Contract between the workflow and the gate: the SHA before a push.
const CLE_BASE_PUSH = 'DESIGN_GATE_PUSH_BEFORE';

const lignesStyle = (n, declaration) =>
	`<style>\n${Array.from({ length: n }, (_, i) => `\t.x${i} { ${declaration} }`).join('\n')}\n</style>\n`;
const espacements = (n) => lignesStyle(n, 'padding: 10px;');
const tailles = (n) => lignesStyle(n, 'font-size: 13px;');
const rayons = (n) => lignesStyle(n, 'border-radius: 3px;');
// Several fixtures in one component: Svelte admits a single top-level <style>.
const unSeulStyle = (...blocs) =>
	`<style>\n${blocs.map((bloc, i) => bloc.slice('<style>\n'.length, -'\n</style>\n'.length).replace(/\.x(\d+)/g, `.b${i}x$1`)).join('\n')}\n</style>\n`;

// A stand-in for check-design-gate.mjs at a commit: only its REGLES names, its DETTE block and, when
// given, its PORTEE_PUBLIQUE entries are read.
function scriptGarde(regles, dette, portee) {
	const lignesRegles = regles.map((nom) => `\t{ nom: '${nom}', portee: 'public' }`).join(',\n');
	const blocPortee =
		portee === undefined
			? ''
			: `export const PORTEE_PUBLIQUE = [\n\t// a comment line, as in the real gate\n${portee.map((e) => `\t'${e}'`).join(',\n')}\n];\n\n`;
	return `${blocPortee}export const REGLES = [\n${lignesRegles}\n];\n\n// GENERATED-DETTE-START\n${designGate.serialiserDette(dette)}\n// GENERATED-DETTE-END\n`;
}

// Environment of a scenario: no CI variable and no git variable of a calling hook leak in; git never
// climbs above the temp directory.
function envCliquet(extra = {}) {
	const env = { ...process.env };
	for (const cle of Object.keys(env)) {
		const majuscule = cle.toUpperCase();
		if (
			/^GIT_(DIR|WORK_TREE|INDEX_FILE|COMMON_DIR|OBJECT_DIRECTORY|ALTERNATE_OBJECT_DIRECTORIES|PREFIX|CEILING_DIRECTORIES)$/.test(majuscule) ||
			/^GITHUB_(BASE_REF|EVENT_NAME)$/.test(majuscule) ||
			majuscule === CLE_BASE_PUSH
		) {
			delete env[cle];
		}
	}
	return { ...env, GIT_CEILING_DIRECTORIES: tmpdir(), ...extra };
}

/** Throwaway git repository `<dossier>/frontend`, on `master`, removed after `fn`. */
function avecDepot(fn) {
	const dossier = mkdtempSync(join(tmpdir(), 'dc-cliquet-'));
	const env = envCliquet();
	const git = (...args) =>
		execFileSync('git', args, { cwd: dossier, env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
	const racine = join(dossier, 'frontend');
	const depot = {
		dossier,
		racine,
		env,
		git,
		ecrire(fichiers) {
			for (const [rel, contenu] of Object.entries(fichiers)) {
				const dest = join(racine, rel);
				if (contenu === null) {
					rmSync(dest, { force: true });
					continue;
				}
				mkdirSync(dirname(dest), { recursive: true });
				writeFileSync(dest, contenu);
			}
		},
		valider(message, fichiers = {}) {
			depot.ecrire(fichiers);
			git('add', '-A');
			git('commit', '-q', '--allow-empty', '-m', message);
			return git('rev-parse', 'HEAD');
		},
		cliquet(extra = {}) {
			return designGate.verifierCliquet({ racine, env: { ...env, ...extra } });
		}
	};
	try {
		git('init', '-q', '-b', 'master');
		for (const [cle, valeur] of [
			['user.name', 'cliquet'],
			['user.email', 'cliquet@example.invalid'],
			['commit.gpgsign', 'false'],
			['core.autocrlf', 'false'],
			['core.hooksPath', 'aucun-hook']
		]) {
			git('config', cle, valeur);
		}
		return fn(depot);
	} finally {
		rmSync(dossier, { recursive: true, force: true });
	}
}

/** Base on master with the ten rules and a typo debt of 2 on cgu, then a `dev` branch. */
function baseDixRegles(d) {
	const base = d.valider('base', {
		[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [TYPO]: 2 } }),
		[CGU]: tailles(2),
		[SH]: PROPRE
	});
	d.git('checkout', '-q', '-b', 'dev');
	return base;
}

test('evaluerCliquet: a count up by one, a new file and a rule added to a file all fail; equal or lower passes', () => {
	const reference = { [CGU]: { [TYPO]: 2 } };
	assert.deepEqual(designGate.evaluerCliquet(reference, { [CGU]: { [TYPO]: 2 } }), []);
	assert.deepEqual(designGate.evaluerCliquet(reference, { [CGU]: { [TYPO]: 1 } }), []);
	assert.deepEqual(designGate.evaluerCliquet(reference, {}), []);
	const hausse = designGate.evaluerCliquet(reference, { [CGU]: { [TYPO]: 3 } });
	assert.equal(hausse.length, 1);
	assert.match(hausse[0], /hausse/);
	const nouveau = designGate.evaluerCliquet(reference, { [NOUVEAU]: { [TYPO]: 1 } });
	assert.equal(nouveau.length, 1);
	assert.match(nouveau[0], /fichier absent de la reference/);
	const regleAjoutee = designGate.evaluerCliquet(reference, { [CGU]: { [TYPO]: 2, [ESP]: 1 } });
	assert.equal(regleAjoutee.length, 1);
	assert.match(regleAjoutee[0], /regle absente de la reference du fichier/);
});

test('cliquet: on the real repository, DETTE and the measured debt stay under the git reference (D-29)', (t) => {
	const resultat = designGate.verifierCliquet({ racine: RACINE_FRONTEND });
	t.diagnostic(`base ${resultat.base} (${resultat.origine}), gels ${JSON.stringify(resultat.gels)}`);
	assert.deepEqual(resultat.erreurs, []);
	// Non-vacuity of the reference (an amorcage measured on the base, a freeze that bounds a file) is
	// proven on the fixture repositories below, never by a positive debt required of the real tree.
	assert.deepEqual(controlerGels(RACINE_FRONTEND, resultat), []);
});

/**
 * Freeze commits of the three 04.8 rules, as found by detteDeReference on the repository `racine`:
 * each one is found (never null: the git log prefix bug of part B made them all null), and it is the
 * base when the base already knows the rule, a commit of <base>..HEAD otherwise. Empty array = consistent.
 */
function controlerGels(racine, resultat) {
	const source = execFileSync('git', ['show', `${resultat.base}:./${SCRIPT_GARDE}`], {
		cwd: racine,
		env: envCliquet(),
		encoding: 'utf8',
		stdio: ['ignore', 'pipe', 'pipe'],
		windowsHide: true
	});
	const { regles: aLaBase } = designGate.lireBlocsGarde(source, `base ${resultat.base}`);
	return REGLES_NOUVELLES_048.filter((regle) => {
		const gel = resultat.gels[regle];
		if (!gel) return true;
		if (aLaBase.includes(regle)) return gel !== resultat.base;
		// In <base>..HEAD: reachable from HEAD, not from the base. The base need not be an ancestor of the
		// freeze: the base branch of a PR may have moved on without the rules (Codex review 2, n° 1).
		const ancetre = (a, b) =>
			spawnSync('git', ['merge-base', '--is-ancestor', a, b], { cwd: racine, env: envCliquet(), windowsHide: true })
				.status === 0;
		return !ancetre(gel, 'HEAD') || ancetre(gel, resultat.base);
	}).map((regle) => `freeze commit of ${regle}`);
}

/** The last of `shas` in history order (every other one is its ancestor). */
function dernierCommit(racine, env, shas) {
	return shas.find((candidat) =>
		shas.every(
			(autre) =>
				spawnSync('git', ['merge-base', '--is-ancestor', autre, candidat], { cwd: racine, env, windowsHide: true })
					.status === 0
		)
	);
}

test('cliquet: on the real repository, a push whose base already knows the 04.8 rules passes the sentinel', () => {
	const { gels } = referenceDuDepot();
	const env = envCliquet();
	// The last freeze commit knows the three rules: taken as the base of a push, it is their freeze.
	const base = dernierCommit(RACINE_FRONTEND, env, REGLES_NOUVELLES_048.map((regle) => gels[regle]));
	assert.ok(base, `freeze commits found: ${JSON.stringify(gels)}`);
	const push = designGate.verifierCliquet({
		racine: RACINE_FRONTEND,
		env: { ...env, GITHUB_EVENT_NAME: 'push', [CLE_BASE_PUSH]: base }
	});
	assert.equal(push.origine, 'push');
	assert.deepEqual(push.erreurs, []);
	for (const regle of REGLES_NOUVELLES_048) assert.equal(push.gels[regle], push.base, regle);
	assert.deepEqual(controlerGels(RACINE_FRONTEND, push), []);
});

test('controlerGels: a base that knows the rules is their freeze; otherwise a later commit; never null', () => {
	avecDepot((d) => {
		// The base knows the ten rules: every freeze is the base.
		const base = baseDixRegles(d);
		d.valider('suite', { 'README.md': 'x\n' });
		const connue = d.cliquet();
		assert.equal(connue.base, base);
		assert.deepEqual(controlerGels(d.racine, connue), []);
		// A freeze after a base that knows the rule, or null, is refused.
		const apres = { ...connue, gels: { ...connue.gels, [ESP]: d.git('rev-parse', 'HEAD') } };
		assert.deepEqual(controlerGels(d.racine, apres), [`freeze commit of ${ESP}`]);
		const nul = { ...connue, gels: { ...connue.gels, [RAY]: null } };
		assert.deepEqual(controlerGels(d.racine, nul), [`freeze commit of ${RAY}`]);
	});
	avecDepot((d) => {
		// The base knows only the historic rules.
		const base = d.valider('base', { [SCRIPT_GARDE]: scriptGarde(REGLES_HISTORIQUES, {}), [SH]: PROPRE });
		d.git('checkout', '-q', '-b', 'dev');
		// No commit adds the 04.8 rules: their freeze is null (the git log prefix bug of part B).
		d.valider('sans gel', { 'README.md': 'x\n' });
		const sansGel = d.cliquet();
		assert.deepEqual(sansGel.gels, { ...sansGel.gels, ...Object.fromEntries(REGLES_NOUVELLES_048.map((r) => [r, null])) });
		assert.deepEqual(controlerGels(d.racine, sansGel), REGLES_NOUVELLES_048.map((r) => `freeze commit of ${r}`));
		// A commit adds them: it is their freeze, after the base.
		const gel = d.valider('gel', { [SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, {}) });
		const gele = d.cliquet();
		for (const regle of REGLES_NOUVELLES_048) assert.equal(gele.gels[regle], gel);
		assert.deepEqual(controlerGels(d.racine, gele), []);
		// A freeze at the base of a base that does not know the rule is refused.
		const aLaBase = { ...gele, gels: { ...gele.gels, [ESP]: base } };
		assert.deepEqual(controlerGels(d.racine, aLaBase), [`freeze commit of ${ESP}`]);
	});
});

test('controlerGels: rules frozen on a branch while master moves on without them, in PR, after merge, push and local (Codex 2, n° 1)', () => {
	avecDepot((d) => {
		// master: the historic rules only, a spacing debt of 2 on cgu (the bootstrap measures it).
		const base = d.valider('base', { [SCRIPT_GARDE]: scriptGarde(REGLES_HISTORIQUES, {}), [CGU]: espacements(2), [SH]: PROPRE });
		d.git('checkout', '-q', '-b', 'dev');
		const gel = d.valider('gel', { [SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [ESP]: 2 } }) });
		d.valider('suite', { 'README.md': 'dev\n' });
		// master moves on in parallel, without the rules.
		d.git('checkout', '-q', 'master');
		const masterAvance = d.valider('master avance', { 'NOTES.md': 'doc\n' });
		const controler = (r, attendu = gel) => {
			assert.deepEqual(r.erreurs, [], `${r.origine}: ratchet`);
			for (const regle of REGLES_NOUVELLES_048) assert.equal(r.gels[regle], attendu, `${r.origine}: ${regle}`);
			assert.deepEqual(controlerGels(d.racine, r), [], `${r.origine}: freeze commits`);
		};
		const pr = { GITHUB_EVENT_NAME: 'pull_request', GITHUB_BASE_REF: 'master' };
		d.git('update-ref', 'refs/remotes/origin/master', masterAvance);

		// PR, base = the tip of master, which is not an ancestor of the freeze: on the branch tip, then on
		// the merge commit that Actions checks out.
		d.git('checkout', '-q', 'dev');
		const surBranche = d.cliquet(pr);
		assert.equal(surBranche.base, masterAvance);
		controler(surBranche);
		// A freeze outside <base>..HEAD is still refused: null, reachable from the base, or not in HEAD.
		for (const faux of [null, base, masterAvance]) {
			assert.deepEqual(controlerGels(d.racine, { ...surBranche, gels: { ...surBranche.gels, [ESP]: faux } }), [
				`freeze commit of ${ESP}`
			]);
		}
		d.git('checkout', '-q', '-b', 'pr-fusion', 'master');
		d.git('merge', '-q', '--no-ff', '-m', 'fusion de test', 'dev');
		controler(d.cliquet(pr));

		// After the merge into master: push and local on master read the former tip, which ignores the rules.
		d.git('checkout', '-q', 'master');
		d.git('merge', '-q', '--no-ff', '-m', 'fusion', 'dev');
		const push = d.cliquet({ GITHUB_EVENT_NAME: 'push', [CLE_BASE_PUSH]: masterAvance });
		assert.equal(push.base, masterAvance);
		controler(push);
		const local = d.cliquet();
		assert.equal(local.origine, 'local-parent');
		assert.equal(local.base, masterAvance);
		controler(local);

		// A later branch: its base knows the rules, so it is their freeze (PR and local).
		const fusionne = d.git('rev-parse', 'HEAD');
		d.git('update-ref', 'refs/remotes/origin/master', fusionne);
		d.git('checkout', '-q', '-b', 'dev2');
		d.valider('apres fusion', { 'README.md': 'dev2\n' });
		controler(d.cliquet(pr), fusionne);
		controler(d.cliquet(), fusionne);
	});
});

/** Commit with a fixed committer date, so that `git log` orders the commits of a merge deterministically. */
function validerALaDate(d, date, message, fichiers = {}) {
	d.ecrire(fichiers);
	const env = { ...d.env, GIT_AUTHOR_DATE: date, GIT_COMMITTER_DATE: date };
	const git = (...args) =>
		execFileSync('git', args, { cwd: d.dossier, env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
	git('add', '-A');
	git('commit', '-q', '--allow-empty', '-m', message);
	return git('rev-parse', 'HEAD');
}

/** Merge `branche` into the current branch with a fixed date; the merge must be clean. */
function fusionnerALaDate(d, date, branche) {
	const env = { ...d.env, GIT_AUTHOR_DATE: date, GIT_COMMITTER_DATE: date };
	execFileSync('git', ['merge', '-q', '--no-ff', '-m', `fusion de ${branche}`, branche], {
		cwd: d.dossier,
		env,
		stdio: ['ignore', 'pipe', 'pipe']
	});
	return d.git('rev-parse', 'HEAD');
}

// A branch older than the freeze of a rule touches the gate without the rule (here: a header line,
// which merges cleanly with the freeze). Its commits are in <base>..HEAD once merged; read for that rule,
// their absent entries would count as zero and take the whole frozen debt of the rule to zero (Opus, P3-1).
const ENTETE_ANCIENNE = '// retouche de la branche ancienne\n';

test('cliquet: commits of a branch older than the freeze, merged, are not read for a rule known at the base (Opus, P3-1)', () => {
	// The rule is known at the base: the old branch is brought up to date with master, then its PR is
	// checked; after the merge into master, the push and the local check on master read it too.
	avecDepot((d) => {
		validerALaDate(d, '2026-01-01T10:00:00Z', 'base', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_HISTORIQUES, {}),
			[CGU]: espacements(2),
			[SH]: PROPRE
		});
		d.git('checkout', '-q', '-b', 'ancienne');
		validerALaDate(d, '2026-01-02T10:00:00Z', 'retouche sans la regle', {
			[SCRIPT_GARDE]: ENTETE_ANCIENNE + scriptGarde(REGLES_HISTORIQUES, {})
		});
		d.git('checkout', '-q', 'master');
		const gel = validerALaDate(d, '2026-01-03T10:00:00Z', 'gel', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [ESP]: 2 } })
		});
		d.git('update-ref', 'refs/remotes/origin/master', gel);
		d.git('checkout', '-q', 'ancienne');
		fusionnerALaDate(d, '2026-01-04T10:00:00Z', 'master');
		const pr = d.cliquet({ GITHUB_EVENT_NAME: 'pull_request', GITHUB_BASE_REF: 'master' });
		assert.equal(pr.base, gel);
		assert.equal(pr.gels[ESP], gel, 'known at the base: the base is its freeze');
		assert.deepEqual(pr.reference, { [CGU]: { [ESP]: 2 } }, 'PR: the old commit is not read for the rule');
		assert.deepEqual(pr.erreurs, []);

		d.git('checkout', '-q', 'master');
		fusionnerALaDate(d, '2026-01-05T10:00:00Z', 'ancienne');
		const push = d.cliquet({ GITHUB_EVENT_NAME: 'push', [CLE_BASE_PUSH]: gel });
		assert.deepEqual(push.reference, { [CGU]: { [ESP]: 2 } }, 'push after the merge');
		assert.deepEqual(push.erreurs, []);
		const local = d.cliquet();
		assert.equal(local.origine, 'local-parent');
		assert.deepEqual(local.erreurs, [], 'local on master after the merge');
	});
});

test('cliquet: commits of a branch older than the freeze, merged after it, are not read for a rule frozen on the branch (Opus, P3-1)', () => {
	// The rule is frozen on the branch under test, not at the base: an old branch merged after the
	// freeze has later commit dates, so `git log --reverse` lists its commit after the freeze commit.
	avecDepot((d) => {
		const base = validerALaDate(d, '2026-01-01T10:00:00Z', 'base', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_HISTORIQUES, {}),
			[CGU]: espacements(2),
			[SH]: PROPRE
		});
		d.git('checkout', '-q', '-b', 'ancienne');
		validerALaDate(d, '2026-01-03T10:00:00Z', 'retouche sans la regle', {
			[SCRIPT_GARDE]: ENTETE_ANCIENNE + scriptGarde(REGLES_HISTORIQUES, {})
		});
		d.git('checkout', '-q', 'master');
		d.git('checkout', '-q', '-b', 'dev');
		const gel = validerALaDate(d, '2026-01-02T10:00:00Z', 'gel', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [ESP]: 2 } })
		});
		fusionnerALaDate(d, '2026-01-04T10:00:00Z', 'ancienne');
		const r = d.cliquet();
		assert.equal(r.base, base);
		assert.equal(r.gels[ESP], gel);
		assert.deepEqual(r.reference, { [CGU]: { [ESP]: 2 } }, 'frozen on the branch: the old commit is not read');
		assert.deepEqual(r.erreurs, []);
	});
});

// The rules and the public scope of the base are read from git, not from the copies in this test file
// (REGLES_COMPLETES, PORTEE_PUBLIQUE_048): a commit that edits the gate and those copies together still
// fails (Opus, P3-5).
test('cliquet: a rule of the base removed from REGLES fails, whatever this file says (Opus, P3-5)', () => {
	avecDepot((d) => {
		baseDixRegles(d);
		d.valider('regle retiree', {
			[SCRIPT_GARDE]: scriptGarde(
				REGLES_COMPLETES.filter((r) => r !== ESP),
				{ [CGU]: { [TYPO]: 2 } }
			)
		});
		const r = d.cliquet();
		assert.equal(r.erreurs.length, 1, JSON.stringify(r.erreurs));
		assert.match(r.erreurs[0], /^REGLES : regle "espacement-hors-jeton" de la base [0-9a-f]{40} retiree$/);
		// The same rule renamed is a removal too.
		d.valider('regle renommee', {
			[SCRIPT_GARDE]: scriptGarde(
				REGLES_COMPLETES.map((r) => (r === RAY ? 'rayon-renomme' : r)),
				{ [CGU]: { [TYPO]: 2 } }
			)
		});
		assert.ok(d.cliquet().erreurs.some((e) => e.includes(`"${RAY}"`)));
	});
	// A rule frozen on the branch, not at the base, is not covered by this check (its removal takes its
	// reference with it, but the base never had it).
	avecDepot((d) => {
		d.valider('base', { [SCRIPT_GARDE]: scriptGarde(REGLES_HISTORIQUES, {}), [SH]: PROPRE });
		d.git('checkout', '-q', '-b', 'dev');
		d.valider('ajout puis retrait sur la branche', { [SCRIPT_GARDE]: scriptGarde(REGLES_HISTORIQUES, {}) });
		assert.deepEqual(d.cliquet().erreurs, []);
	});
});

test('cliquet: an entry of the base PORTEE_PUBLIQUE leaves the scope only when its file no longer exists (Opus, P3-5)', () => {
	const DS = 'src/lib/components/ds/';
	const BOUTON = `${DS}Bouton.svelte`;
	const portee = [SH, SF, DS];
	avecDepot((d) => {
		d.valider('base', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, {}, portee),
			[SH]: PROPRE,
			[SF]: PROPRE,
			[BOUTON]: PROPRE
		});
		d.git('checkout', '-q', '-b', 'dev');

		// A file entry removed while the file exists: refused.
		d.valider('entree retiree', { [SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, {}, [SF, DS]) });
		let r = d.cliquet();
		assert.equal(r.erreurs.length, 1, JSON.stringify(r.erreurs));
		assert.match(r.erreurs[0], /^PORTEE_PUBLIQUE : entree "src\/lib\/components\/SiteHeader\.svelte" de la base [0-9a-f]{40} retiree alors que son fichier existe$/);

		// The file removed with its entry: accepted (the removals of 04.8-01 task 1).
		d.valider('fichier retire', { [SH]: null });
		assert.deepEqual(d.cliquet().erreurs, []);

		// A directory entry removed while the directory exists: refused.
		d.valider('dossier retire de la portee', { [SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, {}, [SF]) });
		r = d.cliquet();
		assert.equal(r.erreurs.length, 1, JSON.stringify(r.erreurs));
		assert.match(r.erreurs[0], /entree "src\/lib\/components\/ds\/"/);

		// The directory gone too: accepted. An unreadable scope, when the base had one: refused.
		d.valider('dossier retire', { [BOUTON]: null });
		assert.deepEqual(d.cliquet().erreurs, []);
		d.valider('portee illisible', { [SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, {}) });
		r = d.cliquet();
		assert.equal(r.erreurs.length, 1, JSON.stringify(r.erreurs));
		assert.match(r.erreurs[0], /^PORTEE_PUBLIQUE illisible dans l'arbre de travail/);
	});
});

test('cliquet: a count raised by hand in DETTE within the same commit fails (local, merge-base)', () => {
	avecDepot((d) => {
		const base = baseDixRegles(d);
		d.valider('hausse a la main', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [TYPO]: 3 } }),
			[CGU]: tailles(3)
		});
		const r = d.cliquet();
		assert.equal(r.base, base);
		assert.equal(r.origine, 'local');
		assert.deepEqual(r.reference, { [CGU]: { [TYPO]: 2 } });
		assert.equal(r.erreurs.length, 2, 'DETTE and the measured debt both exceed the reference');
		assert.ok(r.erreurs.every((e) => e.includes(CGU) && e.includes('hausse')));
	});
});

test('cliquet: a file absent from the reference and a rule added to a debt file fail', () => {
	avecDepot((d) => {
		baseDixRegles(d);
		d.valider('fichier nouveau et regle ajoutee', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [TYPO]: 2, [ESP]: 1 }, [NOUVEAU]: { [TYPO]: 1 } }),
			[CGU]: unSeulStyle(tailles(2), espacements(1)),
			[NOUVEAU]: tailles(1)
		});
		const r = d.cliquet();
		const dette = r.erreurs.filter((e) => e.startsWith('DETTE'));
		assert.equal(dette.length, 2);
		assert.ok(dette.some((e) => e.includes(NOUVEAU) && e.includes('fichier absent de la reference')));
		assert.ok(dette.some((e) => e.includes(CGU) && e.includes(ESP) && e.includes('regle absente')));
	});
});

test('cliquet: an uncommitted raise of DETTE in the working tree fails too', () => {
	avecDepot((d) => {
		baseDixRegles(d);
		d.valider('sans rapport', { 'README.md': 'x\n' });
		d.ecrire({ [SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [TYPO]: 3 } }) });
		const r = d.cliquet();
		assert.equal(r.erreurs.length, 1);
		assert.match(r.erreurs[0], /^DETTE .*hausse/);
	});
});

test('cliquet: removals before the freeze, freeze at 5 accepted, then 0 accepted, then 1 refused', () => {
	avecDepot((d) => {
		const base = d.valider('base', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_HISTORIQUES, { [CGU]: { [TYPO]: 2 } }),
			[CGU]: tailles(2),
			[SH]: espacements(5)
		});
		d.git('checkout', '-q', '-b', 'dev');
		// Before the freeze the spacing rule is not in REGLES: its absent entry must not count as zero.
		d.valider('retraits', { [SCRIPT_GARDE]: scriptGarde(REGLES_HISTORIQUES, {}), [CGU]: PROPRE });
		const gel = d.valider('gel', {
			[SCRIPT_GARDE]: scriptGarde([...REGLES_HISTORIQUES, ESP], { [SH]: { [ESP]: 5 } })
		});
		let r = d.cliquet();
		assert.equal(r.base, base);
		assert.equal(r.gels[ESP], gel);
		assert.deepEqual(r.reference, { [SH]: { [ESP]: 5 } }, 'amorcage = 5 measured on the base, cgu solded');
		assert.deepEqual(r.erreurs, [], 'freeze at 5 accepted');

		d.valider('solde', { [SCRIPT_GARDE]: scriptGarde([...REGLES_HISTORIQUES, ESP], {}), [SH]: PROPRE });
		r = d.cliquet();
		assert.deepEqual(r.erreurs, [], '0 accepted');
		assert.deepEqual(r.reference, {});

		d.valider('remontee', {
			[SCRIPT_GARDE]: scriptGarde([...REGLES_HISTORIQUES, ESP], { [SH]: { [ESP]: 1 } }),
			[SH]: espacements(1)
		});
		r = d.cliquet();
		assert.equal(r.erreurs.length, 2, '1 refused, in DETTE and in the measure');
		assert.ok(r.erreurs.every((e) => e.includes(SH) && e.includes(ESP)));
	});
});

test('cliquet: a rule solded entirely (no entry left) never comes back', () => {
	avecDepot((d) => {
		d.valider('base', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_HISTORIQUES, {}),
			[SH]: espacements(5),
			[SF]: espacements(3)
		});
		d.git('checkout', '-q', '-b', 'dev');
		d.valider('gel', {
			[SCRIPT_GARDE]: scriptGarde([...REGLES_HISTORIQUES, ESP], { [SH]: { [ESP]: 5 }, [SF]: { [ESP]: 3 } })
		});
		assert.deepEqual(d.cliquet().erreurs, [], 'freeze at 5 and 3 accepted');
		d.valider('regle soldee', {
			[SCRIPT_GARDE]: scriptGarde([...REGLES_HISTORIQUES, ESP], {}),
			[SH]: PROPRE,
			[SF]: PROPRE
		});
		assert.deepEqual(d.cliquet().erreurs, [], 'every entry of the rule removed: accepted');
		d.valider('reintroduction', {
			[SCRIPT_GARDE]: scriptGarde([...REGLES_HISTORIQUES, ESP], { [SF]: { [ESP]: 1 } }),
			[SF]: espacements(1)
		});
		const r = d.cliquet();
		assert.equal(r.erreurs.length, 2);
		assert.ok(r.erreurs.every((e) => e.includes(SF) && e.includes(ESP)));
	});
});

test('cliquet: a rule measured at zero on the base has a null reference, any positive entry fails', () => {
	avecDepot((d) => {
		d.valider('base', { [SCRIPT_GARDE]: scriptGarde(REGLES_HISTORIQUES, {}), [SH]: PROPRE });
		d.git('checkout', '-q', '-b', 'dev');
		const gel = d.valider('gel avec dette', {
			[SCRIPT_GARDE]: scriptGarde([...REGLES_HISTORIQUES, RAY], { [SH]: { [RAY]: 2 } }),
			[SH]: rayons(2)
		});
		const r = d.cliquet();
		assert.equal(r.gels[RAY], gel);
		assert.deepEqual(r.reference, {});
		assert.equal(r.erreurs.length, 2);
		assert.ok(r.erreurs.every((e) => e.includes(SH) && e.includes(RAY) && e.includes('fichier absent')));
	});
});

test('cliquet: on master (merge-base = HEAD) the reference is HEAD~1, a hand-raised DETTE fails', () => {
	avecDepot((d) => {
		const parent = d.valider('base', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [TYPO]: 2 } }),
			[CGU]: tailles(2)
		});
		d.valider('hausse sur master', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [TYPO]: 3 } }),
			[CGU]: tailles(3)
		});
		const r = d.cliquet();
		assert.equal(r.base, parent);
		assert.equal(r.origine, 'local-parent');
		assert.equal(r.erreurs.length, 2);
	});
});

test('cliquet: on a root commit of master the reference is that commit, a debt raised over it fails', () => {
	avecDepot((d) => {
		// A repository published without history (the public export): one commit, no parent to read.
		const racine = d.valider('publication initiale', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [TYPO]: 2 } }),
			[CGU]: tailles(2)
		});
		const local = d.cliquet();
		assert.equal(local.base, racine);
		assert.equal(local.origine, 'racine');
		assert.deepEqual(local.erreurs, []);
		// First push of that commit: GitHub passes a null SHA before it.
		const push = d.cliquet({ GITHUB_EVENT_NAME: 'push', [CLE_BASE_PUSH]: '0'.repeat(40) });
		assert.equal(push.base, racine);
		assert.equal(push.origine, 'racine');
		assert.deepEqual(push.erreurs, []);
		// The committed debt is the ceiling: a count raised in the working tree fails twice (DETTE, measure).
		d.ecrire({
			[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [TYPO]: 3 } }),
			[CGU]: tailles(3)
		});
		assert.equal(d.cliquet().erreurs.length, 2);
		// A root commit elsewhere than on master names no reference.
		d.git('checkout', '-q', '--', '.');
		d.git('branch', '-q', '-m', 'ailleurs');
		assert.throws(() => d.cliquet(), /master introuvable/);
	});
});

test('cliquet: pull request -> origin/$GITHUB_BASE_REF; push -> the SHA before the push', () => {
	avecDepot((d) => {
		const base = baseDixRegles(d);
		const milieu = d.valider('baisse', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [TYPO]: 1 } }),
			[CGU]: tailles(1)
		});
		d.valider('hausse', {
			[SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, { [CGU]: { [TYPO]: 2 } }),
			[CGU]: tailles(2)
		});
		d.git('update-ref', 'refs/remotes/origin/master', base);
		const pr = d.cliquet({ GITHUB_EVENT_NAME: 'pull_request', GITHUB_BASE_REF: 'master' });
		assert.equal(pr.base, base);
		assert.equal(pr.origine, 'pr');
		assert.equal(pr.erreurs.length, 2, 'monotone: 2 -> 1 -> 2 fails even though the base said 2');
		const push = d.cliquet({ GITHUB_EVENT_NAME: 'push', [CLE_BASE_PUSH]: milieu });
		assert.equal(push.base, milieu);
		assert.equal(push.origine, 'push');
		assert.equal(push.erreurs.length, 2);
	});
});

test('cliquet: no git, no repository, no history or no base fails, never a silent pass', () => {
	// No repository at all.
	const sansDepot = mkdtempSync(join(tmpdir(), 'dc-sans-git-'));
	try {
		mkdirSync(join(sansDepot, 'scripts'), { recursive: true });
		writeFileSync(join(sansDepot, SCRIPT_GARDE), scriptGarde(REGLES_COMPLETES, {}));
		// No git executable: PATH is an empty folder. Without PATH at all, Linux falls back to /usr/bin
		// and finds git (Opus, P3-12); the message names the missing executable, not the missing repository.
		const gitIntrouvable = /^pas de depot git sous .+ \(git rev-parse --is-inside-work-tree : git introuvable\)$/;
		assert.throws(() => designGate.verifierCliquet({ racine: sansDepot, env: envCliquet() }), (err) => {
			assert.match(err.message, /^pas de depot git sous /);
			assert.doesNotMatch(err.message, gitIntrouvable, 'git is on the PATH here: only the repository is missing');
			return true;
		});
		const sansGit = mkdtempSync(join(tmpdir(), 'dc-path-vide-'));
		try {
			assert.throws(
				() =>
					designGate.verifierCliquet({
						racine: sansDepot,
						env: { PATH: sansGit, SystemRoot: process.env.SystemRoot ?? '' }
					}),
				(err) => {
					assert.match(err.message, gitIntrouvable);
					return true;
				}
			);
		} finally {
			rmSync(sansGit, { recursive: true, force: true });
		}
	} finally {
		rmSync(sansDepot, { recursive: true, force: true });
	}
	avecDepot((d) => {
		d.valider('seul commit', { [SCRIPT_GARDE]: scriptGarde(REGLES_COMPLETES, {}) });
		// Single commit on master: the root commit is its own reference (own test above), no failure here.
		d.valider('deuxieme', { 'README.md': 'x\n' });
		// Pull request whose base branch is not fetched; push without, null or foreign before SHA.
		assert.throws(() => d.cliquet({ GITHUB_EVENT_NAME: 'pull_request', GITHUB_BASE_REF: 'master' }), /origin\/master/);
		assert.throws(() => d.cliquet({ GITHUB_EVENT_NAME: 'pull_request' }), /GITHUB_BASE_REF/);
		assert.throws(() => d.cliquet({ GITHUB_EVENT_NAME: 'push' }), new RegExp(CLE_BASE_PUSH));
		assert.throws(() => d.cliquet({ [CLE_BASE_PUSH]: '0'.repeat(40) }), /nul/);
		assert.throws(() => d.cliquet({ [CLE_BASE_PUSH]: 'f'.repeat(40) }), /introuvable/);
		// Shallow clone: the history is incomplete.
		const clone = mkdtempSync(join(tmpdir(), 'dc-cliquet-clone-'));
		try {
			execFileSync('git', ['clone', '-q', '--depth', '1', pathToFileURL(d.dossier).href, clone], {
				env: d.env,
				stdio: 'ignore'
			});
			assert.throws(
				() => designGate.verifierCliquet({ racine: join(clone, 'frontend'), env: d.env }),
				/superficiel/
			);
		} finally {
			rmSync(clone, { recursive: true, force: true });
		}
	});
});

test('check-design-gate --maj-dette: exits non-zero without writing when it would write a rise; writes a fall', () => {
	avecDepot((d) => {
		// The real gate and its token source, committed as the base on master.
		mkdirSync(join(d.racine, 'scripts'), { recursive: true });
		mkdirSync(join(d.racine, 'src'), { recursive: true });
		copyFileSync(join(RACINE_FRONTEND, SCRIPT_GARDE), join(d.racine, SCRIPT_GARDE));
		copyFileSync(join(RACINE_FRONTEND, 'src/app.css'), join(d.racine, 'src/app.css'));
		// The gate parses components with svelte/compiler, resolved from the frontend's node_modules: a
		// junction gives the copy the same layout. Ignored by git, unlinked before the folder is removed.
		const modules = join(d.racine, 'node_modules');
		writeFileSync(join(d.racine, '.gitignore'), 'node_modules\n');
		symlinkSync(join(RACINE_FRONTEND, 'node_modules'), modules, 'junction');
		try {
			d.valider('base', { [NOUVEAU]: PROPRE });
			d.git('checkout', '-q', '-b', 'dev');
			d.valider('sans rapport', { 'README.md': 'x\n' });
			const majDette = () =>
				spawnSync(process.execPath, [SCRIPT_GARDE, '--maj-dette'], { cwd: d.racine, env: d.env, encoding: 'utf8' });

			d.ecrire({ [NOUVEAU]: espacements(1) });
			const avant = readFileSync(join(d.racine, SCRIPT_GARDE), 'utf8');
			const hausse = majDette();
			assert.notEqual(hausse.status, 0, `rise refused: ${hausse.stdout}${hausse.stderr}`);
			assert.match(hausse.stderr, /Nouveau\.svelte/);
			assert.equal(readFileSync(join(d.racine, SCRIPT_GARDE), 'utf8'), avant, 'nothing written');

			d.ecrire({ [NOUVEAU]: PROPRE });
			const baisse = majDette();
			assert.equal(baisse.status, 0, `fall written: ${baisse.stderr}`);
			assert.match(readFileSync(join(d.racine, SCRIPT_GARDE), 'utf8'), /export const DETTE = \{\};/);
		} finally {
			unlinkSync(modules);
		}
	});
});

// Owner slice of every public file (D-29, UI-SPEC § 11), from the `files` of plans 04.8-02 to 04.8-09.
// Files the slices created are listed with their slice, so a new public file without an owner fails.
const PROPRIETAIRES_048 = {
	'04.8-02': [
		'src/lib/components/SiteHeader.svelte',
		'src/lib/components/RechercheEnTete.svelte',
		'src/lib/components/SiteFooter.svelte',
		'src/lib/components/CommuneSearch.svelte',
		'src/routes/+layout.svelte',
		'src/routes/(marketing)/+layout.svelte',
		'src/lib/components/fiche/Notice.svelte',
		'src/lib/components/fiche/SourceButton.svelte',
		'src/lib/components/fiche/StateValue.svelte',
		'src/lib/components/fiche/ExportDialog.svelte',
		'src/lib/components/fiche/DpeTag.svelte',
		'src/lib/components/ds/icons/IconBouclier.svelte',
		'src/lib/components/ds/icons/IconChevronBas.svelte',
		'src/lib/components/ds/icons/IconFermer.svelte',
		'src/lib/components/ds/icons/IconImmeuble.svelte',
		'src/lib/components/ds/icons/IconInfo.svelte',
		'src/lib/components/ds/icons/IconMaison.svelte',
		'src/lib/components/ds/icons/IconRefuse.svelte',
		'src/lib/components/ds/icons/IconTelecharger.svelte',
		'src/lib/components/ds/icons/IconValide.svelte',
		// created by the slice
		'src/lib/components/ds/Panneau.svelte',
		'src/lib/components/ds/Tuile.svelte',
		'src/lib/components/ds/ListeLiens.svelte',
		'src/lib/components/ds/RepereEchelle.svelte',
		'src/lib/components/ds/IconeCategorie.svelte',
		'src/lib/components/ds/BadgeEtat.svelte',
		'src/lib/components/ds/FilAriane.svelte'
	],
	'04.8-03': [
		'src/routes/(marketing)/adresse/[id]/+page.svelte',
		'src/routes/(marketing)/adresse/+error.svelte',
		'src/lib/components/adresse/ComparablesForm.svelte',
		'src/lib/components/adresse/DpeDocumentList.svelte',
		'src/lib/components/adresse/Pagination.svelte',
		'src/lib/components/adresse/VenteList.svelte',
		'src/lib/components/adresse/rapport/CarteAdresse.svelte',
		'src/lib/components/adresse/rapport/ChiffresCles.svelte',
		'src/lib/components/adresse/rapport/DetailGenerique.svelte',
		'src/lib/components/adresse/rapport/Sommaire.svelte',
		'src/lib/components/fiche/SourcePanel.svelte',
		'src/lib/components/fiche/SourcesPrint.svelte',
		'src/lib/components/fiche/EnTetePrint.svelte',
		'src/lib/components/fiche/LienLimites.svelte',
		// created by the slice
		'src/lib/components/ds/EchelleDpe.svelte',
		'src/lib/components/ds/LireUneFiche.svelte',
		'src/lib/components/adresse/question/LigneCritere.svelte',
		'src/lib/components/adresse/question/BilanRisques.svelte',
		'src/lib/components/adresse/question/PartieQuestion.svelte',
		'src/lib/components/fiche/Categorie.svelte',
		'src/lib/components/adresse/question/EnBref.svelte'
	],
	'04.8-04': [
		'src/routes/(marketing)/commune/[code]/+page.svelte',
		// the definitions of the commune fiche, out of its served HTML (SEO duplicates, 2026-11-16)
		'src/routes/(marketing)/commune/[code]/definitions/+page.svelte',
		'src/lib/components/fiche/DefinitionsCommune.svelte',
		'src/routes/(marketing)/dpe/[numero]/+page.svelte',
		'src/routes/(marketing)/dpe/[numero]/+error.svelte',
		'src/lib/components/fiche/Kpi.svelte',
		'src/lib/components/fiche/KpiStrip.svelte',
		'src/lib/components/fiche/RepartitionDpe.svelte',
		// the curve of the tile « Tendance du prix médian » (yearly series, 2026-11-11), rendered by Kpi.svelte
		'src/lib/components/fiche/CourbeAnnuelle.svelte',
		// created by the slice
		'src/lib/components/ds/ListeValeurs.svelte'
	],
	'04.8-05': [
		'src/routes/(marketing)/departement/[code]/+page.svelte',
		'src/routes/(marketing)/departements/+page.svelte',
		'src/routes/(marketing)/comparer/+page.svelte',
		'src/lib/components/compare/CompareTable.svelte',
		'src/lib/components/compare/DesignationDpe.svelte',
		'src/lib/components/compare/NatureNav.svelte',
		// entry to the comparison of arrondissements (wave A of 2026-11-10)
		'src/lib/components/compare/ChoixArrondissements.svelte'
	],
	'04.8-06': [
		'src/routes/(marketing)/mentions-legales/+page.svelte',
		'src/routes/(marketing)/cgu/+page.svelte',
		'src/routes/(marketing)/confidentialite/+page.svelte',
		'src/routes/(marketing)/signaler/+page.svelte',
		'src/routes/(marketing)/couverture/+page.svelte',
		'src/routes/(marketing)/developpeurs/+page.svelte',
		'src/routes/(marketing)/ce-que-nous-ne-montrons-pas/+page.svelte',
		'src/routes/(marketing)/datasets/+page.svelte',
		'src/lib/components/DatasetCard.svelte',
		'src/lib/components/DatasetDetail.svelte',
		// Born in 04.8-06: the template of the simple pages.
		'src/lib/components/ds/PageSimple.svelte'
	],
	'04.8-07': [
		'src/routes/(marketing)/+page.svelte',
		'src/routes/(marketing)/communes/+page.svelte',
		'src/routes/(marketing)/communes/AjoutComparaison.svelte',
		'src/routes/+error.svelte'
	],
	// Shared pieces of the public pages (socle of 2026-11-09), born after 04.8.
	'socle-2026-11-09': [
		'src/lib/components/ds/MetaPartage.svelte',
		'src/lib/components/ds/PageErreur.svelte',
		// Born in the legal pages' table of contents (I5, 2026-11-10).
		'src/lib/components/ds/Sommaire.svelte'
	],
	// The interactive parcel map of the address fiche (phase 04.10), born after 04.8.
	'04.10-03': [
		'src/lib/components/adresse/carte/ParcelMapDialog.svelte',
		'src/lib/components/adresse/carte/ParcelPanel.svelte',
		// The list of the parcels of the view (04.10-08).
		'src/lib/components/adresse/carte/ParcelList.svelte'
	]
};

function fichiersSansProprietaire(fichiers, proprietaires) {
	const possedes = new Set(Object.values(proprietaires).flat());
	return fichiers.filter((chemin) => !possedes.has(chemin));
}

function proprietairesMultiples(proprietaires) {
	const vus = new Map();
	for (const [tranche, fichiers] of Object.entries(proprietaires)) {
		for (const chemin of fichiers) vus.set(chemin, [...(vus.get(chemin) ?? []), tranche]);
	}
	return [...vus].filter(([, tranches]) => tranches.length > 1).map(([chemin]) => chemin);
}

// End of 04.8 (plan 04.8-09, D-29): the three new rules hold no debt left anywhere in the public scope;
// the one written exception is the coverage list of /couverture, which may only go down.
test('DETTE: at the end of 04.8 only the written exception of /couverture remains', () => {
	const reglesNouvelles = Object.entries(DESIGN_DETTE).flatMap(([chemin, comptes]) =>
		REGLES_NOUVELLES_048.filter((regle) => regle in comptes).map((regle) => `${chemin} ${regle}`)
	);
	assert.deepEqual(reglesNouvelles, []);
	const exception = 'src/routes/(marketing)/couverture/+page.svelte';
	assert.deepEqual(Object.keys(DESIGN_DETTE).filter((chemin) => chemin !== exception), []);
	// Solding it entirely is allowed (D-29): the entry may disappear, never rise nor gain a rule.
	const couverture = DESIGN_DETTE[exception] ?? {};
	assert.deepEqual(Object.keys(couverture).filter((regle) => regle !== 'perimetre-en-dur'), []);
	assert.ok((couverture['perimetre-en-dur'] ?? 0) <= 3);
});

test('PROPRIETAIRES_048: every public .svelte or .css file has exactly one owner slice (D-29)', () => {
	const publics = designGate.collecterFichiers(RACINE_FRONTEND).filter(designGate.estDansPortee);
	assert.ok(publics.length > 0, 'non-vacuity: the public scope has files');
	assert.deepEqual(fichiersSansProprietaire(publics, PROPRIETAIRES_048), []);
	assert.deepEqual(proprietairesMultiples(PROPRIETAIRES_048), []);
	// Every owned path is a public path (a typo would own nothing).
	assert.deepEqual(Object.values(PROPRIETAIRES_048).flat().filter((c) => !designGate.estDansPortee(c)), []);
	// The sentinel bites: a public file without owner is reported.
	const sansSiteHeader = {
		...PROPRIETAIRES_048,
		'04.8-02': PROPRIETAIRES_048['04.8-02'].filter((c) => c !== SH)
	};
	assert.deepEqual(fichiersSansProprietaire(publics, sansSiteHeader), [SH]);
	assert.deepEqual(fichiersSansProprietaire([...publics, 'src/lib/components/fiche/Orphelin.svelte'], PROPRIETAIRES_048), [
		'src/lib/components/fiche/Orphelin.svelte'
	]);
});

// ---------------------------------------------------------------------------
// check-marketing-prerender.mjs (04.7-10, decision 8): the prerendered home page carries its
// canonical and a valid WebSite JSON-LD with the search action of its own form
// ---------------------------------------------------------------------------

const ACCUEIL_FORMULAIRE =
	'<form action="/communes" method="get"><input name="q"><select name="departement"></select></form>';

function webSite(racine = 'https://datacommune.fr', surcharge = {}) {
	return {
		'@context': 'https://schema.org',
		'@type': 'WebSite',
		name: 'DataCommune',
		url: `${racine}/`,
		inLanguage: 'fr-FR',
		potentialAction: {
			'@type': 'SearchAction',
			target: `${racine}/communes?q={search_term_string}`,
			'query-input': 'required name=search_term_string'
		},
		...surcharge
	};
}

/** A prerendered home page as the SSR renderer emits it, each part replaceable. */
function accueil({
	canonical = '<link rel="canonical" href="https://datacommune.fr/"/>',
	jsonLd = [`<script type="application/ld+json">${JSON.stringify(webSite())}</script>`],
	corps = `<main><h1>Rechercher</h1>${ACCUEIL_FORMULAIRE}</main>`
} = {}) {
	return `<!doctype html><html lang="fr"><head><title>DataCommune</title>${canonical}${jsonLd.join('')}</head><body>${corps}</body></html>`;
}

const ldJson = (objet) => `<script type="application/ld+json">${JSON.stringify(objet)}</script>`;

test('verifierAccueil: a home page with its search, canonical and WebSite lacks nothing', () => {
	assert.deepEqual(verifierAccueil(accueil()), []);
});

test('verifierAccueil: the preproduction origin is accepted when everything agrees on it', () => {
	const html = accueil({
		canonical: '<link rel="canonical" href="https://dev.datacommune.fr/"/>',
		jsonLd: [ldJson(webSite('https://dev.datacommune.fr'))]
	});
	assert.deepEqual(verifierAccueil(html), []);
});

test('verifierAccueil: the former rules still hold (main, one h1, GET form with q)', () => {
	assert.ok(verifierAccueil(accueil({ corps: `<h1>x</h1>${ACCUEIL_FORMULAIRE}` })).some((m) => m.includes('<main>')));
	assert.ok(verifierAccueil(accueil({ corps: `<main>${ACCUEIL_FORMULAIRE}</main>` })).some((m) => m.includes('<h1>')));
	assert.ok(verifierAccueil(accueil({ corps: '<main><h1>x</h1></main>' })).some((m) => m.includes('<form')));
	assert.ok(
		verifierAccueil(accueil({ corps: '<main><h1>x</h1><form action="/communes" method="get"><input name="p"></form></main>' })).some(
			(m) => m.includes('name="q"')
		)
	);
	// Since 09/11 the home page has no departement filter: a form with `q` alone is complete.
	assert.ok(
		!verifierAccueil(accueil({ corps: '<main><h1>x</h1><form action="/communes" method="get"><input name="q"></form></main>' })).some(
			(m) => m.includes('departement') || m.includes('<form') || m.includes('name="q"')
		)
	);
});

test('verifierAccueil: no canonical, two canonicals, a canonical off the site root or a foreign host -> missing', () => {
	assert.ok(verifierAccueil(accueil({ canonical: '' })).some((m) => m.includes('canonical')));
	const deux = '<link rel="canonical" href="https://datacommune.fr/"/><link rel="canonical" href="https://datacommune.fr/"/>';
	assert.ok(verifierAccueil(accueil({ canonical: deux })).some((m) => m.includes('canonical')));
	for (const href of ['https://datacommune.fr/communes', 'https://datacommune.fr', 'http://datacommune.fr/', 'https://example.org/', '/']) {
		const manques = verifierAccueil(accueil({ canonical: `<link rel="canonical" href="${href}"/>` }));
		assert.ok(manques.some((m) => m.includes('canonical')), href);
	}
});

test('verifierAccueil: another <link> is not a canonical', () => {
	const html = accueil({ canonical: '<link rel="icon" href="https://datacommune.fr/"/>' });
	assert.ok(verifierAccueil(html).some((m) => m.includes('canonical')));
});

test('verifierAccueil: no JSON-LD, only another type, or an unreadable one -> no valid WebSite', () => {
	assert.ok(verifierAccueil(accueil({ jsonLd: [] })).some((m) => m.includes('WebSite')));
	assert.ok(verifierAccueil(accueil({ jsonLd: [ldJson({ '@context': 'https://schema.org', '@type': 'Dataset' })] })).some((m) => m.includes('WebSite')));
	const casse = '<script type="application/ld+json">{"@type":"WebSite","name":"x</script>"}</script>';
	assert.ok(verifierAccueil(accueil({ jsonLd: [casse] })).length > 0);
	const sansJson = '<script type="application/ld+json">{"@type": WebSite}</script>';
	assert.ok(verifierAccueil(accueil({ jsonLd: [sansJson] })).some((m) => m.includes('JSON')));
});

test('verifierAccueil: two WebSite declarations are one too many', () => {
	const html = accueil({ jsonLd: [ldJson(webSite()), ldJson(webSite())] });
	assert.ok(verifierAccueil(html).some((m) => m.includes('WebSite')));
});

test('verifierAccueil: the WebSite must name the site, in fr-FR, at the canonical url', () => {
	for (const [surcharge, mot] of [
		[{ name: 'Autre' }, 'name'],
		[{ inLanguage: 'fr' }, 'inLanguage'],
		[{ url: 'https://datacommune.fr/communes' }, 'url'],
		[{ url: '/' }, 'url'],
		[{ '@context': 'http://schema.org' }, 'context']
	]) {
		const manques = verifierAccueil(accueil({ jsonLd: [ldJson(webSite('https://datacommune.fr', surcharge))] }));
		assert.ok(manques.some((m) => m.includes(mot)), JSON.stringify(surcharge));
	}
});

test('verifierAccueil: the SearchAction must target the absolute /communes?q= of the site, with its query-input', () => {
	const action = (surcharge) => ({
		'@type': 'SearchAction',
		target: 'https://datacommune.fr/communes?q={search_term_string}',
		'query-input': 'required name=search_term_string',
		...surcharge
	});
	const cas = [
		['absent', null],
		['relative target', action({ target: '/communes?q={search_term_string}' })],
		['wrong path', action({ target: 'https://datacommune.fr/recherche?q={search_term_string}' })],
		['wrong field', action({ target: 'https://datacommune.fr/communes?commune={search_term_string}' })],
		['foreign host', action({ target: 'https://example.org/communes?q={search_term_string}' })],
		['preproduction host under a production canonical', action({ target: 'https://dev.datacommune.fr/communes?q={search_term_string}' })],
		['wrong query-input', action({ 'query-input': 'required name=q' })],
		['wrong type', action({ '@type': 'ViewAction' })]
	];
	for (const [nom, potentialAction] of cas) {
		const objet = { ...webSite(), potentialAction };
		if (potentialAction === null) delete objet.potentialAction;
		const manques = verifierAccueil(accueil({ jsonLd: [ldJson(objet)] }));
		assert.ok(manques.some((m) => m.includes('SearchAction')), nom);
	}
});
