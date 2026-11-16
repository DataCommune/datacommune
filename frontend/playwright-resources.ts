import { dirname, join, resolve } from 'node:path';

const manifest = process.env.DC_TEST_RUN_MANIFEST;
const runId = process.env.DC_TEST_RUN_ID;
const scope = process.env.DC_TEST_SCOPE_ID;
if (manifest && (!/^[a-f0-9]{32}$/.test(runId ?? '') || !/^[a-f0-9]{32}$/.test(scope ?? ''))) {
	throw new Error('Managed Playwright requires an explicit run and invocation scope');
}
const artifacts = manifest ? join(dirname(manifest), 'playwright', scope!, process.env.DC_TEST_STAGE ?? 'tests') : '.';
export const outputDir = manifest ? join(artifacts, 'test-results') : 'test-results';
export const reportDir = manifest ? join(artifacts, 'report') : 'playwright-report';

export function ownedWebCommand(kind: 'app' | 'source', fallback: string): string {
	if (!manifest) return fallback; // Linux CI uses Playwright's own server lifecycle.
	const launcher = resolve('..', 'scripts', 'start-test-web.ps1');
	if ([launcher, manifest].some((value) => /["\r\n]/.test(value))) throw new Error('Invalid launcher path');
	return `pwsh -NoProfile -File "${launcher}" -RunManifest "${manifest}" -Kind ${kind}`;
}
