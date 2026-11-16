#!/usr/bin/env node
// Phase 46-05 (T46-05-FG) — strict build wrapper.
//
// `npm run build:app` (the real Vite/SvelteKit build, formerly `build`) is spawned as a
// child process. Its stdout/stderr are streamed through UNCHANGED (nothing is swallowed —
// CI logs keep the real build output) while ALSO being buffered for a post-hoc scan. The
// wrapper fails on:
//   - a non-zero exit code from the child (unchanged failure semantics), or
//   - any allowlisted-empty warning marker matched in the combined output (Svelte/Vite/
//     Rollup/SSR compiler warnings that `vite build` prints but does not itself fail on).
//
// `ALLOWLIST` is intentionally empty. It exists as a named export so a future, EXPLICIT,
// reviewed exception can be added with a comment — never populated silently by this file.
//
// Dependency-free, portable (Windows + POSIX): spawns via the shell so `npm` resolves the
// same way `npm run build:app` would from a terminal on either OS.

import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// Each pattern targets a REAL, known Vite/Rollup/Svelte/SvelteKit warning marker — not a
// generic word that could appear in legitimate build banner text (avoids false positives
// on lines like "vite v7.x building for production...").
export const WARNING_RULES = [
	// Rollup/Vite generic warning marker: lines start with "(!) ...".
	{ name: 'rollup-generic-warning', pattern: /^\(!\)\s/m },
	// vite-plugin-svelte emits its own prefixed warnings.
	{ name: 'vite-plugin-svelte-warning', pattern: /\[vite-plugin-svelte\][^\n]*(?:warn|Warning)/i },
	// Svelte compiler a11y warnings (a11y-* rule codes) surfaced during the build.
	{ name: 'svelte-a11y-warning', pattern: /\ba11y[-_][a-z][a-z-]*\b/i },
	// Svelte compiler "Unused CSS selector" warning.
	{ name: 'svelte-unused-css-selector', pattern: /Unused CSS selector/i },
	// SvelteKit-prefixed warnings (routing/SSR conflicts, deprecated config, etc.).
	{ name: 'sveltekit-warning', pattern: /\[SvelteKit\][^\n]*(?:warn|Warning)/i },
	// Generic "WARN"/"WARNING" line marker some Vite plugins use at line start.
	{ name: 'generic-warn-marker', pattern: /^(?:WARN|WARNING)[:\s]/im }
];

// Reserved for future, explicitly reviewed exceptions (rule names). Empty by design.
export const ALLOWLIST = [];

/**
 * Scan combined build output for forbidden warning markers, honoring an allowlist of
 * rule names to skip. Returns the list of matched rule names (never the raw output).
 * @param {string} output
 * @param {string[]} allowlist
 * @returns {string[]}
 */
export function scanBuildOutput(output, allowlist = ALLOWLIST) {
	const findings = [];
	for (const rule of WARNING_RULES) {
		if (allowlist.includes(rule.name)) continue;
		if (rule.pattern.test(output)) findings.push(rule.name);
	}
	return findings;
}

/**
 * Run a command, streaming its stdout/stderr through unchanged while buffering a copy for
 * `scanBuildOutput`. Exposed as a pure-ish function (injectable command) so tests can spawn
 * a throwaway Node script instead of the real `npm run build:app`.
 * @param {{ cmd?: string, args?: string[], allowlist?: string[], cwd?: string }} opts
 * @returns {Promise<{ code: number, findings: string[] }>}
 */
export function runBuildStrict({ cmd = 'npm', args = ['run', 'build:app'], allowlist = ALLOWLIST, cwd } = {}) {
	return new Promise((resolve, reject) => {
		let combined = '';
		const child = spawn(cmd, args, { shell: true, cwd });

		child.stdout.on('data', (chunk) => {
			process.stdout.write(chunk);
			combined += chunk.toString();
		});
		child.stderr.on('data', (chunk) => {
			process.stderr.write(chunk);
			combined += chunk.toString();
		});
		child.on('error', reject);
		child.on('close', (code) => {
			resolve({ code: code ?? 1, findings: scanBuildOutput(combined, allowlist) });
		});
	});
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
	const { code, findings } = await runBuildStrict();

	if (code !== 0) {
		console.error(`[run-build-strict] FAIL build:app exited with code ${code}.`);
		process.exit(code);
	}

	if (findings.length > 0) {
		console.error(
			`[run-build-strict] FAIL ${findings.length} forbidden warning marker(s) detected: ${findings.join(', ')}`
		);
		console.error('[run-build-strict] allowlist is empty by design — fix the warning, do not allowlist it silently.');
		process.exit(1);
	}

	console.log('[run-build-strict] OK build:app exited 0, zero forbidden warning markers.');
	process.exit(0);
}
