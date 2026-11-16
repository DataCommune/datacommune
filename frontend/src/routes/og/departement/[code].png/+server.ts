// Link preview of a departement page (SEO audit of 2026-11-14): `/og/departement/{code}.png`, 1200 x 630,
// read with the page's own reading (`lireDepartement`). What is drawn is `carteDepartement`; the
// outcomes and caches are those of `servirOg`.
import { isHttpError } from '@sveltejs/kit';
import { lireDepartement } from '$lib/data/departement';
import { servirOg, type LectureOg } from '$lib/server/og/service';
import { carteDepartement } from '$lib/seo/og-carte';
import type { RequestHandler } from './$types';

export const prerender = false;

export const GET: RequestHandler = ({ params, fetch, request }) =>
	servirOg(
		`departement:${params.code.toUpperCase()}`,
		async (): Promise<LectureOg> => {
			try {
				const data = await lireDepartement(params.code, fetch);
				if (data.etat === 'panne') return { kind: 'panne' };
				const carte = carteDepartement(data);
				return carte ? { kind: 'carte', carte } : { kind: 'sans-chiffre' };
			} catch (erreur) {
				if (isHttpError(erreur) && erreur.status === 404) return { kind: 'inconnu', message: erreur.body.message };
				throw erreur;
			}
		},
		request.headers.get('if-none-match')
	);
