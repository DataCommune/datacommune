// Universal load of `/departements` (04.7-10, decision 7): the departements grouped by region, from
// `GET /v1/departements`. `prerender = false` is explicit: the list and the state of each departement
// (prices hosted and imported) follow the database served, not the build.
import { lireDepartements } from '$lib/data/departement';
import type { PageLoad } from './$types';

export type { DepartementsPageData } from '$lib/data/departement';

export const prerender = false;

// `setHeaders` marks an outage for the 503 of hooks.server.ts; it does nothing in the browser.
export const load: PageLoad = ({ fetch, setHeaders }) => lireDepartements(fetch, { setHeaders });
