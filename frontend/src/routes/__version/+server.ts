import { json } from '@sveltejs/kit';
import { DEPLOYMENT_ENV, DEPLOYMENT_SHA } from '$lib/config';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = () =>
	json(
		{ environment: DEPLOYMENT_ENV, deployment_sha: DEPLOYMENT_SHA },
		{ headers: { 'cache-control': 'no-store' } }
	);
