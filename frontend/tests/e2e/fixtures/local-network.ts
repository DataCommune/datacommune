import { expect, type Page } from '@playwright/test';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './ports';

const LOCAL_ORIGINS = new Set([
	E2E_APP_ORIGIN,
	E2E_APP_ORIGIN.replace('localhost', '127.0.0.1'),
	E2E_SOURCE_ORIGIN
]);

const unexpectedRequests = new WeakMap<Page, Set<string>>();

function redactedUrl(value: string): string {
	try {
		const url = new URL(value);
		return `${url.origin}${url.pathname}${url.search === '' ? '' : '?<redacted>'}`;
	} catch {
		return '<invalid-url>';
	}
}

export async function installLocalNetworkGuard(page: Page): Promise<void> {
	const violations = new Set<string>();
	unexpectedRequests.set(page, violations);
	await page.route('**/*', async (route) => {
		const requestUrl = new URL(route.request().url());
		if (LOCAL_ORIGINS.has(requestUrl.origin)) {
			await route.continue();
			return;
		}
		violations.add(redactedUrl(requestUrl.href));
		await route.abort('blockedbyclient');
	});
}

export function expectNoUnexpectedOffLocalRequest(page: Page): void {
	const violations = [...(unexpectedRequests.get(page) ?? [])];
	expect(
		violations,
		`requêtes navigateur hors environnement local: ${JSON.stringify(violations)}`
	).toEqual([]);
}
