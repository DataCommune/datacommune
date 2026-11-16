// `/commune/[code]/definitions`: the definitions and sources of a commune fiche, for a reader without
// JavaScript (A9-9), who cannot open the panels of the fiche. Same reading as the fiche (`lireFicheCommune`).
import { lireFicheCommune, type FicheCommuneData } from '$lib/data/fiche-commune';
import type { PageLoad } from './$types';

export const prerender = false;

export const load: PageLoad = ({ params, fetch, setHeaders }): Promise<FicheCommuneData> =>
	lireFicheCommune(params.code, fetch, { setHeaders });
