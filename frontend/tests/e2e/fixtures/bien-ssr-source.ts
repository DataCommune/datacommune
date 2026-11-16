import { expect, type APIRequestContext, type APIResponse } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './ports';

// Exact fail-closed contract of the local HTTP source for server-side loads. The `/bien` helper
// (`configureBienSsrSource`, Resolve + logements) left with its last caller in 04-08 (D-101: `/bien`
// is a 308); `configureSsrSource` serves every other SSR surface.
const SOURCE_URL = E2E_SOURCE_ORIGIN;

export interface SourceExpectation {
	method: 'GET';
	pathname: string;
	query: Record<string, string>;
	minCalls?: number;
	maxCalls?: number;
	/** A response may carry headers (04.7-10: the `Retry-After` of a 503), which the source sends as given. */
	responses?: Array<{ status: number; body: unknown; headers?: Record<string, string> } | null>;
}

export interface SourceAudit {
	requests: Array<{
		method: string;
		pathname: string;
		search: string;
		query: Record<string, string>;
	}>;
	violations: unknown[];
	unmetExpectations: unknown[];
}

export interface SsrSourceProbe {
	assert(): Promise<SourceAudit>;
}

async function expectSuccessfulResponse(response: APIResponse) {
	if (!response.ok()) {
		expect(response.ok(), await response.text()).toBe(true);
	}
}

/** Configure an exact fail-closed contract for any SSR endpoint served by the local source. */
export async function configureSsrSource(
	request: APIRequestContext,
	expectations: SourceExpectation[]
): Promise<SsrSourceProbe> {
	const configured = await request.post(`${SOURCE_URL}/__configure`, {
		data: { reset: true, expectations }
	});
	await expectSuccessfulResponse(configured);

	return {
		async assert() {
			const response = await request.get(`${SOURCE_URL}/__requests`);
			await expectSuccessfulResponse(response);
			const audit = (await response.json()) as SourceAudit;
			expect(
				audit.violations,
				`violations du contrat SSR: ${JSON.stringify(audit.violations)}`
			).toEqual([]);
			expect(
				audit.unmetExpectations,
				`appels SSR requis manquants: ${JSON.stringify(audit.unmetExpectations)}`
			).toEqual([]);
			return audit;
		}
	};
}
