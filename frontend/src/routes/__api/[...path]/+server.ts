import { IS_PREPRODUCTION } from '$lib/config';
import { proxyPreproductionRequest } from '$lib/server/preproduction-api';
import type { RequestHandler } from './$types';

const handle: RequestHandler = ({ request, params, platform }) =>
	proxyPreproductionRequest({
		request,
		path: params.path,
		env: platform?.env ?? {},
		preproduction: IS_PREPRODUCTION
	});

export const GET = handle;
export const HEAD = handle;
export const OPTIONS = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
