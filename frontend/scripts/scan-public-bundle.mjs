#!/usr/bin/env node
// SEC-02 (Phase 46-04) — post-build redacted secret scan of the REAL public artifact.
//
// Runs after `npm run build` on `build/` (adapter-node output) and BEFORE any release.
// D-10: the artifact is scanned for dc_live_ and secret patterns; values are never shown.
// D-11: dc_anon_ is public by design and explicitly allowed (no finding).
//
// Output contract (D-02 style): file / rule / count only. The matched string is NEVER
// printed, returned in findings, or logged. Fail-closed: a missing/unreadable directory
// is a hard failure (non-zero exit) so a broken build cannot silently ship.
//
// No new dependency. Pure node: stdlib.

import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

// Public-by-design identity — allowed, must not be treated as a secret.
export const ALLOWED_PREFIX = 'dc_anon_';

// Secret rules. Each is anchored enough to avoid false positives on minified code
// while still catching real leaked credentials. Only the RULE NAME is ever surfaced.
export const SECRET_RULES = [
	{ name: 'dc_live_key', pattern: /dc_live_[A-Za-z0-9]{8,}/g },
	{ name: 'pem_private_key', pattern: /-----BEGIN(?:[A-Z0-9 ]+)? PRIVATE KEY-----/g },
	{ name: 'pem_private_key_bare', pattern: /-----BEGIN RSA PRIVATE KEY-----/g },
	{ name: 'stripe_secret_key', pattern: /sk_(?:live|test)_[A-Za-z0-9]{16,}/g },
	{ name: 'aws_access_key_id', pattern: /AKIA[0-9A-Z]{16}/g },
	{ name: 'github_token', pattern: /gh[posru]_[A-Za-z0-9]{20,}/g },
	{ name: 'google_api_key', pattern: /AIza[0-9A-Za-z_-]{35}/g },
	{ name: 'slack_token', pattern: /xox[baprs]-[A-Za-z0-9-]{10,}/g },
	{ name: 'openai_key', pattern: /sk-[A-Za-z0-9]{32,}/g }
];

// Extensions worth scanning for embedded ASCII credentials. Files are read as
// latin1 (1:1 byte mapping, never lossy), so binary containers like .wasm are
// scanned byte-safe: an ASCII secret is preserved even next to binary bytes.
const TEXT_EXTENSIONS = new Set([
	'.js', '.mjs', '.cjs', '.ts', '.json', '.html', '.css', '.txt', '.map',
	'.svg', '.wasm', '.md', '.xml', '.webmanifest', ''
]);

/**
 * Scan a single text blob. Returns [{ rule, count }] — never the matched value.
 * @param {string} text
 * @returns {{ rule: string, count: number }[]}
 */
export function scanContent(text) {
	const findings = [];
	for (const { name, pattern } of SECRET_RULES) {
		pattern.lastIndex = 0;
		const matches = text.match(pattern);
		if (matches && matches.length > 0) {
			findings.push({ rule: name, count: matches.length });
		}
	}
	return findings;
}

function walk(dir, out = []) {
	for (const entry of readdirSync(dir)) {
		const full = join(dir, entry);
		const st = statSync(full);
		if (st.isDirectory()) {
			walk(full, out);
		} else if (st.isFile()) {
			out.push(full);
		}
	}
	return out;
}

function hasScannableExt(path) {
	const dot = path.lastIndexOf('.');
	const ext = dot === -1 ? '' : path.slice(dot).toLowerCase();
	return TEXT_EXTENSIONS.has(ext);
}

/**
 * Scan a built bundle directory. Returns [{ file, rule, count }] — no values.
 * @param {string} dir
 * @returns {{ file: string, rule: string, count: number }[]}
 */
export function scanBundle(dir) {
	if (!existsSync(dir) || !statSync(dir).isDirectory()) {
		throw new Error(`bundle directory not found: ${dir}`);
	}
	const findings = [];
	for (const file of walk(dir)) {
		if (!hasScannableExt(file)) continue;
		let text;
		try {
			// latin1: 1:1 byte mapping, lossless — an ASCII secret survives even
			// inside a binary container (.wasm), unlike utf8 which replaces
			// invalid bytes with U+FFFD and could split a match.
			text = readFileSync(file, 'latin1');
		} catch {
			// Fail-closed: an unreadable file in the artifact cannot be verified,
			// so block the deploy instead of silently skipping it.
			throw new Error(`unreadable bundle file: ${relative(dir, file).replace(/\\/g, '/')}`);
		}
		for (const f of scanContent(text)) {
			findings.push({ file: relative(dir, file).replace(/\\/g, '/'), rule: f.rule, count: f.count });
		}
	}
	return findings;
}

/**
 * List the source maps present in the artifact.
 *
 * A `.map` carries `sourcesContent`: the ORIGINAL source, unminified. Deploying one
 * publishes the codebase. This is not a hypothetical — on 2026-08-23 the production
 * bundle served 90 of them, and `client.ts`, `auth.svelte.ts` and `commune-fiche.ts`
 * were readable in full. The cause was mechanical: a build plugin turned on Vite
 * sourcemaps so an error tracker could ingest them, and nothing removed them from the
 * output afterwards.
 *
 * That plugin was removed in plan 01-05, so no configured path turns sourcemaps on
 * today. This check is the backstop and stays: `scripts/strip-source-maps.mjs` does the
 * removal, this one fails the deploy if a map ever survives — instead of trusting a
 * config to stay correct.
 *
 * @param {string} dir
 * @returns {string[]} artifact-relative paths, sorted
 */
export function findSourceMaps(dir) {
	if (!existsSync(dir) || !statSync(dir).isDirectory()) {
		throw new Error(`bundle directory not found: ${dir}`);
	}
	return walk(dir)
		.filter((f) => f.endsWith('.map'))
		.map((f) => relative(dir, f).replace(/\\/g, '/'))
		.sort();
}

/**
 * Format findings as redacted lines: file<TAB>rule<TAB>count. Never the value.
 * @param {{ file: string, rule: string, count: number }[]} findings
 * @returns {string}
 */
export function formatFindings(findings) {
	return findings.map((f) => `${f.file}\t${f.rule}\t${f.count}`).join('\n');
}

function isMain() {
	if (!process.argv[1]) return false;
	try {
		return import.meta.url === new URL(`file://${process.argv[1]}`).href ||
			import.meta.url.endsWith(process.argv[1].replace(/\\/g, '/'));
	} catch {
		return false;
	}
}

if (isMain()) {
	const dir = process.argv[2] || 'build';
	let findings;
	try {
		findings = scanBundle(dir);
	} catch (err) {
		// Fail-closed: cannot verify the artifact => block deploy.
		console.error(`[scan-public-bundle] FAIL could not scan "${dir}": ${err.message}`);
		process.exit(1);
	}

	if (findings.length > 0) {
		console.error(`[scan-public-bundle] FAIL ${findings.length} secret pattern match(es) in ${dir}:`);
		console.error('file\trule\tcount');
		console.error(formatFindings(findings));
		console.error('[scan-public-bundle] values are redacted by design (D-11).');
		process.exit(1);
	}

	// Source maps publish the original sources — checked separately from the secret
	// rules because it is a different failure: nothing is "leaked into" a file here,
	// the file itself must not exist in a public artifact.
	let maps;
	try {
		maps = findSourceMaps(dir);
	} catch (err) {
		console.error(`[scan-public-bundle] FAIL could not list source maps in "${dir}": ${err.message}`);
		process.exit(1);
	}

	if (maps.length > 0) {
		console.error(`[scan-public-bundle] FAIL ${maps.length} source map(s) in ${dir} — they carry the original sources:`);
		for (const m of maps.slice(0, 10)) console.error(`  ${m}`);
		if (maps.length > 10) console.error(`  … and ${maps.length - 10} more`);
		console.error('[scan-public-bundle] fix: sourceMapsUploadOptions.filesToDeleteAfterUpload in vite.config.ts.');
		process.exit(1);
	}

	console.log(`[scan-public-bundle] OK no secret pattern and no source map in ${dir} (dc_anon_ allowed).`);
	process.exit(0);
}
