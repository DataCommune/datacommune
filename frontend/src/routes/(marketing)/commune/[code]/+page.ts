// Universal load of `/commune/[code]`: server-rendered on the first request, re-run in the browser on
// navigation and by « Recharger la fiche ». The reading itself is `lireFicheCommune`, shared with the
// open JSON export of the fiche (04.7-14); the neighbours are read beside it, for the links of the
// page only (SEO audit 2026-11-14).
import { lireFicheCommune, type FicheCommuneData } from '$lib/data/fiche-commune';
import { lireVoisines } from '$lib/data/communes-voisines';
import type { CommunesVoisines } from '$lib/api/communes';
import type { PageLoad } from './$types';

export type { FicheCommuneData, IdentiteCommune } from '$lib/data/fiche-commune';

/** The fiche and, apart from it, its neighbours: null when their read failed, absent in a test double. */
export type FicheCommunePage = FicheCommuneData & { voisines?: CommunesVoisines['voisines'] | null };

export const prerender = false;

// `setHeaders` marks an outage for the 503 of hooks.server.ts (04.7-10); it does nothing in the browser.
export const load: PageLoad = async ({ params, fetch, setHeaders }): Promise<FicheCommunePage> => {
	const [fiche, voisines] = await Promise.all([lireFicheCommune(params.code, fetch, { setHeaders }), lireVoisines(params.code, fetch)]);
	return { ...fiche, voisines };
};
