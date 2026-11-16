import { defineConfig } from '@playwright/test';
import { localOrigin } from './tests/e2e/fixtures/ports';
import { ownedWebCommand, outputDir } from './playwright-resources';

// Owner acceptance replay on real data (plan 03-13, D-53): the four written tasks of 03-RECETTE.md
// against the work base API, outside the hermetic gate (never in e2e-manifest.json).
// Launched by `scripts/test-recette.ps1`, which provides the two variables below and stops every
// resource afterwards. Headless Chromium, one isolated context per test, never a personal browser.
//
// One dev server on the loopback. VITE_API_URL is its own origin: the browser reaches the API
// through the Vite relay (vite.config.ts) and server-side loads through `handleFetch`
// (hooks.server.ts), both aimed at DEV_API_PROXY_TARGET.

const APP_ORIGIN = localOrigin('E2E_APP_ORIGIN', 'http://127.0.0.1:15173');
const appPort = new URL(APP_ORIGIN).port;
const target = process.env.DEV_API_PROXY_TARGET ?? '';
const key = process.env.PUBLIC_DC_API_KEY ?? '';
if (!/^http:\/\/[\d.]+:\d+$/.test(target)) {
	throw new Error('DEV_API_PROXY_TARGET must be http://<ipv4>:<port> (the work base API)');
}
if (!/^dc_anon_[A-Za-z0-9_-]{43,128}$/.test(key)) {
	// The value is never printed.
	throw new Error('PUBLIC_DC_API_KEY must hold the dc_anon_ key of the acceptance session');
}

export default defineConfig({
	testDir: './tests/recette',
	testMatch: /\.recette\.ts$/,
	fullyParallel: false,
	workers: 1,
	retries: 0,
	forbidOnly: true,
	timeout: 180_000,
	outputDir,
	reporter: 'line',
	use: {
		baseURL: APP_ORIGIN,
		headless: true,
		// Traces and videos would carry request headers (the key): none are recorded.
		trace: 'off',
		video: 'off',
		screenshot: 'only-on-failure'
	},
	webServer: {
		command: ownedWebCommand('app', `npm run dev -- --host 127.0.0.1 --port ${appPort} --strictPort`),
		url: APP_ORIGIN,
		reuseExistingServer: false,
		timeout: 120_000,
		env: { ...process.env, VITE_API_URL: APP_ORIGIN, DEV_API_PROXY_TARGET: target, PUBLIC_DC_API_KEY: key }
	},
	projects: [{ name: 'chromium', use: { browserName: 'chromium' } }]
});
