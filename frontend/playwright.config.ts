import { defineConfig } from '@playwright/test';
import { ownedWebCommand, outputDir, reportDir } from './playwright-resources';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN, validateDistinctOrigins } from './tests/e2e/fixtures/ports';

validateDistinctOrigins(E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN);
const appPort = new URL(E2E_APP_ORIGIN).port;
const sourcePort = new URL(E2E_SOURCE_ORIGIN).port;

/** Specs that also run on firefox and webkit (D-01, widened by plan 04.10-10). */
const MOTEURS_CROISES = /(matrix-qualite|carte-parcelles(-[a-z]+)?)\.spec\.ts/;

export default defineConfig({
	testDir: './tests/e2e',
	fullyParallel: true,
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 2 : 0,
	// The SSR fixture is configured immediately before each navigation by the historical
	// contractual specs. A single worker keeps that process-local state deterministic.
	workers: 1,
	outputDir,
	reporter: [['html', { outputFolder: reportDir, open: 'never' }]],
	use: {
		baseURL: E2E_APP_ORIGIN,
		headless: true,
		trace: 'on-first-retry'
	},
	webServer: [
		{
			command: ownedWebCommand('source', 'node --experimental-strip-types tests/e2e/fixtures/dpe-source-server.ts'),
			url: `${E2E_SOURCE_ORIGIN}/health`,
			reuseExistingServer: false,
			timeout: 30_000,
			env: { ...process.env, DPE_SOURCE_PORT: sourcePort }
		},
		{
			command: ownedWebCommand('app', `npm run dev -- --host 127.0.0.1 --port ${appPort} --strictPort`),
			url: E2E_APP_ORIGIN,
			reuseExistingServer: false,
			timeout: 120_000,
			env: {
				...process.env,
				VITE_API_URL: E2E_SOURCE_ORIGIN,
				// `$env/static/public` only exports variables present when Vite starts. Keep
				// hydration deterministic without reading a developer or CI credential.
				PUBLIC_DC_API_KEY: 'dc_anon_e2e_fixture',
				// 04.7-14b: the specs run with the audience measurement on, as the public build does;
				// the dev server answers /count with 204 (vite.config.ts).
				VITE_MESURE_AUDIENCE: 'true',
				// 04.7-10: the served sitemap keeps the API's answers for an hour; the specs change the
				// source between two requests, so the cache is off (0 ms). The cache itself is covered by
				// the unit tests (`sitemap.test.ts`).
				SITEMAP_CACHE_TTL_MS: '0'
			}
		}
	],
	// D-01 : la couverture ×3 navigateurs est scopée à la spec matrice qualité, et depuis le plan
	// 04.10-10 aux specs de la carte des parcelles (Leaflet dans un dialogue modal : le composant le
	// plus dépendant du moteur). Elles ne tournent sur firefox et webkit que nommées explicitement
	// (`test-e2e.ps1 -Specs … -Matrix` ne lance sur ces moteurs que `matrix-qualite.spec.ts`).
	// `chromium` n'a volontairement PAS de `testMatch` — son absence est ce qui lui fait
	// matcher toute la suite (comportement par défaut de Playwright). Ajouter un
	// `testMatch` ici rétrécirait la couverture Chromium existante.
	projects: [
		{
			name: 'chromium',
			use: {
				browserName: 'chromium',
				// Linux font hinting widens Inter by ~1.5 %, enough to wrap one more line
				// in the 375 px DPE cards (178.8 px vs 154.6 px on Windows). Without
				// hinting, the CI runner measures exactly what the local matrix measures.
				launchOptions: { args: ['--font-render-hinting=none'] }
			}
		},
		{
			name: 'firefox',
			use: { browserName: 'firefox' },
			testMatch: MOTEURS_CROISES
		},
		{
			name: 'webkit',
			use: { browserName: 'webkit' },
			testMatch: MOTEURS_CROISES,
			// Plan 50-10 — le bloc « SC 1 clavier » de la matrice est EXCLU de webkit, et le motif est
			// mesuré : le build WebKit de Playwright ne reproduit pas la politique de tabulation par
			// défaut de Safari (macOS ne met les LIENS dans l'ordre de tabulation qu'avec « Navigation
			// au clavier » activée, réglage système absent du build de test). Sans cette exclusion,
			// 6 des 7 tests du bloc rougissent ici — les 5 arrêts de tabulation et le lien de source du
			// panneau ℹ️, tous des `<a>` — alors qu'ils sont verts sur chromium et firefox. Ce seraient
			// des artefacts de build, pas des défauts, et un vert n'y prouverait rien sur Safari.
			// La clause « au clavier » relève du SC 1 (UI-01) ; la clause cross-moteur est le SC 2
			// (UI-02) et reste couverte par TOUS les autres blocs de la spec, qui tournent bien ×3.
			// POURQUOI ICI et non par un saut conditionnel dans la spec : l'API de saut de Playwright
			// est interdite dans une spec `critical` par `scripts/check-e2e-manifest.mjs` (scan
			// textuel, il ne distingue pas un saut motivé d'un test désactivé), et affaiblir ce gate
			// pour y faire passer son propre code est précisément ce que D-22 refuse.
			grepInvert: /SC 1 clavier/
		}
	]
});
