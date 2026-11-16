// Link preview of a commune fiche (SEO audit of 2026-11-14): `/og/commune/{code}.png`, 1200 x 630,
// read with the page's own reading (`lireFicheCommune`), so the image never says another figure than
// the page. What is drawn is `carteCommune`; the outcomes and caches are those of `servirOg`.
import { isHttpError } from '@sveltejs/kit';
import { lireFicheCommune } from '$lib/data/fiche-commune';
import { servirOg, type LectureOg } from '$lib/server/og/service';
import { carteCommune } from '$lib/seo/og-carte';
import type { RequestHandler } from './$types';

export const prerender = false;

export const GET: RequestHandler = ({ params, fetch, request }) =>
	servirOg(
		`commune:${params.code.toUpperCase()}`,
		async (): Promise<LectureOg> => {
			try {
				const fiche = await lireFicheCommune(params.code, fetch);
				if (fiche.etat === 'panne') return { kind: 'panne' };
				const carte = fiche.etat === 'fiche' ? carteCommune(fiche) : null;
				return carte ? { kind: 'carte', carte } : { kind: 'sans-chiffre' };
			} catch (erreur) {
				if (isHttpError(erreur) && erreur.status === 404) return { kind: 'inconnu', message: erreur.body.message };
				throw erreur;
			}
		},
		request.headers.get('if-none-match')
	);
