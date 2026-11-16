#!/usr/bin/env node
// Remove every `.map` from the build artifact before it is scanned and released.
//
// WHY: a `.map` embeds `sourcesContent` — the original, unminified source. Shipping one
// publishes the codebase. This is not hypothetical: on 2026-08-23 the production bundle
// served 90 of them and `client.ts`, `auth.svelte.ts`, `commune-fiche.ts` and
// `SiteHeader.svelte` were readable in full, on a repository that is not open source.
//
// The cause was mechanical rather than a mistake in any one file: a build plugin turned
// Vite sourcemaps ON so the maps could be uploaded to an error tracker, and nothing removed
// them from the output afterwards. Locally the plugin was inert and no map was ever
// produced — which is exactly why the problem was invisible until a real deploy.
//
// That plugin and its error tracker were removed in plan 01-05, so today no configured
// path turns sourcemaps on. This script STAYS anyway: it is the SEC-02 guard, and its job
// is to hold whether or not someone re-enables `build.sourcemap` — deleting it would make
// the 2026-08-23 incident reproducible by a one-line config change.
//
// This is the removal; `scripts/scan-public-bundle.mjs` is the independent check that
// fails the deploy if a map ever survives. Removal and verification are deliberately kept
// in separate files: a bug here must not be able to silence the gate.
//
// Dependency-free, portable, idempotent. No map to remove is a normal, successful run.

import { existsSync, readdirSync, rmSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = fileURLToPath(new URL('.', import.meta.url));
const DEFAULT_DIR = join(scriptDir, '..', 'build');

/** Recursively collect every `.map` file under `dir`. */
export function findMaps(dir, out = []) {
	for (const entry of readdirSync(dir)) {
		const full = join(dir, entry);
		const stat = statSync(full);
		if (stat.isDirectory()) findMaps(full, out);
		else if (entry.endsWith('.map')) out.push(full);
	}
	return out;
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
	const dir = process.argv[2] ? process.argv[2] : DEFAULT_DIR;

	// A missing directory means the build did not produce an artifact. Say so and fail:
	// silently reporting "0 removed" would read as a clean run.
	if (!existsSync(dir) || !statSync(dir).isDirectory()) {
		console.error(`[strip-source-maps] FAIL artifact directory not found: ${dir}`);
		process.exit(1);
	}

	const maps = findMaps(dir);
	for (const m of maps) rmSync(m, { force: true });

	if (maps.length === 0) {
		console.log(`[strip-source-maps] OK no source map in ${relative(process.cwd(), dir) || dir}.`);
	} else {
		console.log(`[strip-source-maps] OK removed ${maps.length} source map(s) from the artifact.`);
	}
	process.exit(0);
}
