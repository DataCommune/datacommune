#!/usr/bin/env node
// Phase 46-05 (T46-05-FG) — exhaustive E2E manifest gate.
//
// Every `tests/e2e/*.spec.ts` file on disk must be classified EXACTLY once in
// `e2e-manifest.json` as `critical` | `manual` | `legacy`. `manual`/`legacy` entries
// require a `reason` AND a `target` (D-12 traceability — a spec excluded from the
// deterministic gate must say why and who owns re-running it). A `critical` spec must
// never contain `test.only`/`describe.only`/`test.skip`/`describe.skip`/`test.fixme`/
// `waitForTimeout(` — those make CI non-deterministic or silently blind. A spec present
// on disk but absent from the manifest (or vice versa) is a hard FAIL: an unclassified
// spec is exactly the failure mode this gate exists to catch.
//
// Dependency-free, portable (Windows + POSIX). Pure functions exported for
// scripts/qa-gates.test.mjs (temp-dir/temp-manifest coverage, no real fixtures mutated).

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = fileURLToPath(new URL('.', import.meta.url));
const REPO_ROOT = join(SCRIPT_DIR, '..');
const E2E_DIR = join(REPO_ROOT, 'tests', 'e2e');
const MANIFEST_PATH = join(REPO_ROOT, 'e2e-manifest.json');

export const VALID_CLASSIFICATIONS = ['critical', 'manual', 'legacy'];

// Forbidden inside a `critical` spec — each turns CI non-deterministic or blind.
export const FORBIDDEN_PATTERNS = [
	{ name: 'test.only', pattern: /\btest\.only\s*\(/g },
	{ name: 'describe.only', pattern: /\btest\.describe\.only\s*\(/g },
	{ name: 'test.skip', pattern: /\btest\.skip\s*\(/g },
	{ name: 'describe.skip', pattern: /\btest\.describe\.skip\s*\(/g },
	{ name: 'test.fixme', pattern: /\btest\.fixme\s*\(/g },
	{ name: 'waitForTimeout', pattern: /waitForTimeout\s*\(/g }
];

/** Recursively collect `*.spec.ts` paths under `root`, relative to `root` (posix separators). */
export function collectSpecFiles(root) {
	const out = [];
	const stack = [root];
	while (stack.length > 0) {
		const dir = stack.pop();
		let entries;
		try {
			entries = readdirSync(dir, { withFileTypes: true });
		} catch (err) {
			if (err && err.code === 'ENOENT') return out;
			throw err;
		}
		for (const entry of entries) {
			const full = join(dir, entry.name);
			if (entry.isDirectory()) {
				stack.push(full);
				continue;
			}
			if (entry.isFile() && entry.name.endsWith('.spec.ts')) {
				out.push(relative(root, full).replace(/\\/g, '/'));
			}
		}
	}
	return out.sort();
}

/**
 * Cross-check the manifest against the real spec inventory. Returns `{ ok, errors }`;
 * `errors` is a flat list of human-readable problems (never throws on a malformed
 * manifest — a missing/invalid manifest is itself an error entry, fail-closed).
 * @param {unknown} manifest parsed e2e-manifest.json
 * @param {string[]} specsOnDisk paths relative to tests/e2e, as returned by collectSpecFiles
 */
export function validateManifestCoverage(manifest, specsOnDisk) {
	const errors = [];
	const specs =
		manifest && typeof manifest === 'object' && manifest.specs && typeof manifest.specs === 'object'
			? manifest.specs
			: null;

	if (!specs) {
		errors.push('manifest is missing a "specs" object — fail-closed.');
		return { ok: false, errors };
	}

	const manifestKeys = new Set(Object.keys(specs));
	const diskKeys = new Set(specsOnDisk);

	for (const spec of specsOnDisk) {
		if (!manifestKeys.has(spec)) {
			errors.push(`unclassified spec on disk: ${spec}`);
		}
	}
	for (const key of manifestKeys) {
		if (!diskKeys.has(key)) {
			errors.push(`stale manifest entry (no such spec on disk): ${key}`);
		}
	}

	for (const [spec, entry] of Object.entries(specs)) {
		if (!entry || typeof entry !== 'object') {
			errors.push(`${spec}: entry must be an object`);
			continue;
		}
		const { classification, reason, target } = entry;
		if (!VALID_CLASSIFICATIONS.includes(classification)) {
			errors.push(
				`${spec}: classification "${classification}" must be one of ${VALID_CLASSIFICATIONS.join('/')}`
			);
			continue;
		}
		if (classification === 'manual' || classification === 'legacy') {
			if (typeof reason !== 'string' || reason.trim() === '') {
				errors.push(`${spec}: classification "${classification}" requires a non-empty "reason"`);
			}
			if (typeof target !== 'string' || target.trim() === '') {
				errors.push(`${spec}: classification "${classification}" requires a non-empty "target"`);
			}
		}
	}

	return { ok: errors.length === 0, errors };
}

/** Scan spec source text for forbidden tokens. Returns `[{ name, line }]`, never the value. */
export function scanForbiddenTokens(content) {
	const hits = [];
	const lines = content.split(/\r?\n/);
	for (const { name, pattern } of FORBIDDEN_PATTERNS) {
		pattern.lastIndex = 0;
		for (let i = 0; i < lines.length; i++) {
			const p = new RegExp(pattern.source, pattern.flags);
			if (p.test(lines[i])) hits.push({ name, line: i + 1 });
		}
	}
	return hits;
}

/**
 * Full gate: coverage + per-critical-spec forbidden-token scan. `readSpec(relPath)` is
 * injected so tests can supply synthetic content without touching real files.
 * @param {unknown} manifest
 * @param {string[]} specsOnDisk
 * @param {(relPath: string) => string} readSpec
 */
export function runManifestGate(manifest, specsOnDisk, readSpec) {
	const errors = [];
	const coverage = validateManifestCoverage(manifest, specsOnDisk);
	errors.push(...coverage.errors);

	if (coverage.ok) {
		const specs = manifest.specs;
		for (const spec of specsOnDisk) {
			const entry = specs[spec];
			if (!entry || entry.classification !== 'critical') continue;
			const content = readSpec(spec);
			const hits = scanForbiddenTokens(content);
			for (const h of hits) {
				errors.push(`${spec}:${h.line}: forbidden "${h.name}" in a critical spec`);
			}
		}
	}

	return { ok: errors.length === 0, errors };
}

function isMain() {
	if (!process.argv[1]) return false;
	try {
		return (
			import.meta.url === new URL(`file://${process.argv[1]}`).href ||
			import.meta.url.endsWith(process.argv[1].replace(/\\/g, '/'))
		);
	} catch {
		return false;
	}
}

if (isMain()) {
	let manifest;
	try {
		manifest = JSON.parse(readFileSync(MANIFEST_PATH, 'utf8'));
	} catch (err) {
		console.error(`[check-e2e-manifest] FAIL cannot read/parse ${MANIFEST_PATH}: ${err.message}`);
		process.exit(1);
	}

	let specsOnDisk;
	try {
		const st = statSync(E2E_DIR);
		if (!st.isDirectory()) throw new Error('not a directory');
		specsOnDisk = collectSpecFiles(E2E_DIR);
	} catch {
		console.error(`[check-e2e-manifest] FAIL missing directory: ${E2E_DIR}`);
		process.exit(1);
	}

	const result = runManifestGate(manifest, specsOnDisk, (rel) =>
		readFileSync(join(E2E_DIR, rel), 'utf8')
	);

	if (!result.ok) {
		console.error(`[check-e2e-manifest] FAIL ${result.errors.length} problem(s):`);
		for (const e of result.errors) console.error(`  - ${e}`);
		process.exit(1);
	}

	const counts = { critical: 0, manual: 0, legacy: 0 };
	for (const spec of specsOnDisk) counts[manifest.specs[spec].classification]++;
	console.log(
		`[check-e2e-manifest] OK ${specsOnDisk.length} spec(s) classified — critical=${counts.critical} manual=${counts.manual} legacy=${counts.legacy}.`
	);
	process.exit(0);
}
