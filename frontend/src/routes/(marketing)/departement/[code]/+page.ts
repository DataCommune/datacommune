// Universal load of `/departement/[code]` (04.7-10, decision 6): server-rendered on the first request,
// re-run in the browser on navigation and by « Recharger les chiffres ». The reading is
// `lireDepartement`. `prerender = false` is explicit: the marketing layout prerenders by default,
// and the figures follow the database served, not the build.
import { lireDepartement } from '$lib/data/departement';
import type { PageLoad } from './$types';

export type { DepartementPageData } from '$lib/data/departement';

export const prerender = false;

// `setHeaders` marks an outage for the 503 of hooks.server.ts; it does nothing in the browser.
export const load: PageLoad = ({ params, fetch, setHeaders }) => lireDepartement(params.code, fetch, { setHeaders });
