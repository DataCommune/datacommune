// Page of the error report (framing of 2026-11-10). A plain form posted to this server, so it works
// without JavaScript and within `form-action 'self'`; SvelteKit refuses a post from another origin.
// The server relays it to the API over the internal network with the visitor's address: on the public
// instance the route of the API is closed to the internet (Caddyfile.public), this action is its only
// caller. Closed unless the build opens it (`$lib/signalement/reglage`).
import { error, fail } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { API_URL, DEPLOYMENT_SHA, getPublicApiHeaders } from '$lib/config';
import { DELAI_API_SSR_MS, ipVisiteur, versApiInterne } from '$lib/server/api-route-fetch';
import { BORNES, borne, contexteDuFormulaire, lireContexte, validerSaisie } from '$lib/signalement/contexte';
import { SIGNALEMENT } from '$lib/signalement/reglage';
import type { Actions, PageServerLoad } from './$types';

// The (marketing) group is prerendered; a page with a form action cannot be.
export const prerender = false;

const REFERENCE = /^[0-9A-F]{10}$/;

export const load: PageServerLoad = ({ url, request, setHeaders }) => {
	if (!SIGNALEMENT) error(404, 'Not Found');
	// The address bar may name an address fiche: never cached, never indexed.
	setHeaders({ 'cache-control': 'no-store', 'x-robots-tag': 'noindex, nofollow' });
	return {
		contexte: lireContexte(url, request.headers.get('referer'), DEPLOYMENT_SHA),
		navigateur: borne(request.headers.get('user-agent'), BORNES.userAgent)
	};
};

export const actions: Actions = {
	default: async ({ request, getClientAddress }) => {
		if (!SIGNALEMENT) error(404, 'Not Found');
		const champs = await request.formData();
		const lire = (nom: string): string => {
			const valeur = champs.get(nom);
			return typeof valeur === 'string' ? valeur : '';
		};
		const commentaire = lire('commentaire').replace(/\r\n/g, '\n').trim();
		const email = lire('email').trim();
		const contexte = contexteDuFormulaire(lire, DEPLOYMENT_SHA);
		const valeurs = { commentaire, email };

		const erreurs = validerSaisie(commentaire, email);
		if (Object.keys(erreurs).length > 0) return fail(400, { erreurs, valeurs, contexte });

		const largeur = /^\d{1,5}$/.test(lire('largeur')) ? Math.min(Number(lire('largeur')), BORNES.largeur) : null;
		// Direct fetch with an explicit init: the method and the body must survive the rewrite to the
		// internal address (a `Request` handed to `versApiInterne` would lose both).
		const versApi = versApiInterne(globalThis.fetch, {
			apiUrl: API_URL,
			internalUrl: env.API_INTERNAL_URL,
			ip: ipVisiteur(request, getClientAddress)
		});
		let reponse: Response;
		try {
			reponse = await versApi(`${API_URL.replace(/\/+$/, '')}/v1/signalements`, {
				method: 'POST',
				// The public key like every call of the site; the route itself asks for none.
				headers: { ...getPublicApiHeaders(), 'content-type': 'application/json' },
				body: JSON.stringify({
					commentaire,
					email: email === '' ? null : email,
					page: contexte.page,
					titre: contexte.titre,
					lignes: contexte.lignes,
					source: contexte.source,
					version_site: contexte.version,
					largeur,
					user_agent: borne(request.headers.get('user-agent'), BORNES.userAgent),
					// Honeypot, judged by the API alone: a robot gets the answer of a success.
					piege: borne(lire('site_web'), BORNES.piege)
				}),
				signal: AbortSignal.timeout(DELAI_API_SSR_MS)
			});
		} catch {
			return fail(503, { panne: 'envoi' as const, valeurs, contexte });
		}
		if (reponse.status === 429) return fail(429, { panne: 'debit' as const, valeurs, contexte });
		if (reponse.status !== 201) return fail(503, { panne: 'envoi' as const, valeurs, contexte });

		const corps = (await reponse.json().catch(() => null)) as { reference?: unknown } | null;
		const reference = typeof corps?.reference === 'string' && REFERENCE.test(corps.reference) ? corps.reference : null;
		return { recu: { reference, retour: contexte.page } };
	}
};
