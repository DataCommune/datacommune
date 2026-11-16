#!/usr/bin/env node
/**
 * Guard: prevent raw Playwright sleeps from landing in E2E specs.
 *
 * Scans tests/e2e/** recursively for the literal token `waitForTimeout(`.
 * Exits non-zero with the list of offending file:line entries when any are found.
 *
 * Dependency-free and portable (works on Windows + POSIX shells).
 *
 * Usage:
 *   node scripts/check-no-playwright-sleeps.mjs
 *   npm run test:e2e:no-sleeps
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = fileURLToPath(new URL(".", import.meta.url));
const REPO_ROOT = join(SCRIPT_DIR, "..");
const TARGET_DIR = join(REPO_ROOT, "tests", "e2e");
const NEEDLE = "waitForTimeout(";
const SPEC_EXT = /\.(spec|test)\.(ts|js|mjs|cjs)$/i;

/**
 * Walk a directory recursively and collect spec file paths.
 */
function collectSpecs(root) {
	const out = [];
	const stack = [root];
	while (stack.length > 0) {
		const dir = stack.pop();
		let entries;
		try {
			entries = readdirSync(dir, { withFileTypes: true });
		} catch (err) {
			if (err && err.code === "ENOENT") {
				return out;
			}
			throw err;
		}
		for (const entry of entries) {
			const full = join(dir, entry.name);
			if (entry.isDirectory()) {
				stack.push(full);
				continue;
			}
			if (entry.isFile() && SPEC_EXT.test(entry.name)) {
				out.push(full);
			}
		}
	}
	return out;
}

/**
 * Scan a single file for occurrences of NEEDLE; return [{line, text}, ...].
 */
function scanFile(path) {
	const raw = readFileSync(path, "utf8");
	const hits = [];
	const lines = raw.split(/\r?\n/);
	for (let i = 0; i < lines.length; i++) {
		if (lines[i].includes(NEEDLE)) {
			hits.push({ line: i + 1, text: lines[i].trim() });
		}
	}
	return hits;
}

function main() {
	// Fail early if the target directory disappears — we want the guard to be
	// loud, not silently pass.
	try {
		const st = statSync(TARGET_DIR);
		if (!st.isDirectory()) {
			console.error(`[no-sleeps] Not a directory: ${TARGET_DIR}`);
			process.exit(2);
		}
	} catch (err) {
		console.error(`[no-sleeps] Missing directory: ${TARGET_DIR}`);
		process.exit(2);
	}

	const specs = collectSpecs(TARGET_DIR);
	const offenders = [];
	for (const spec of specs) {
		const hits = scanFile(spec);
		if (hits.length > 0) {
			offenders.push({ file: spec, hits });
		}
	}

	if (offenders.length === 0) {
		console.log(
			`[no-sleeps] OK — scanned ${specs.length} spec file(s), zero waitForTimeout( calls.`
		);
		process.exit(0);
	}

	console.error("[no-sleeps] FAIL — raw Playwright sleeps detected:");
	for (const o of offenders) {
		const rel = relative(REPO_ROOT, o.file).replace(/\\/g, "/");
		for (const h of o.hits) {
			console.error(`  ${rel}:${h.line}  ${h.text}`);
		}
	}
	console.error("");
	console.error(
		"Replace each waitForTimeout() with a semantic wait (locator visibility, URL/state change, network response, expect.poll, etc.)."
	);
	process.exit(1);
}

main();
