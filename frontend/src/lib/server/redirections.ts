// Permanent redirects of the public site (SEO audit, 2026-11-14), applied by `hooks.server.ts` before
// any route is resolved. Two families:
//
//   1. Routes removed in phase 3 (03-02-SUMMARY): they may still be indexed or linked from outside.
//      `/docs`, `/playground`, `/reference` and `/guides` were the developer pages that `/developpeurs`
//      replaced (it links the API reference served at {API_URL}/docs); `/roadmap` announced the
//      extension of the coverage, which `/couverture` (« Couverture et limites ») now states without
//      a calendar. Sub-paths (`/reference/{group}`, `/guides/{slug}`) go to the same page: the
//      replacement has no per-group or per-guide anchor.
//   2. A lower-case Corsican code on `/departement/{code}` or `/commune/{code}`: the loads accept it
//      (`toUpperCase`) and answered a 200 whose canonical is the upper-case form, a duplicate. Only a
//      code whose upper-case form is a valid code is redirected; anything else reaches the route and
//      its own 404.
//
// The target is always built from this file, never from the request beyond the validated code, and the
// query string is dropped: the removed pages had no parameter the replacements understand.

import { CODE_DEPARTEMENT } from '$lib/data/departement';
import { CODE_INSEE } from '$lib/data/fiche-commune';

const RETIREES: ReadonlyArray<readonly [RegExp, string]> = [
	[/^\/(?:docs|playground|reference|guides)(?:\/.*)?$/, '/developpeurs'],
	[/^\/roadmap(?:\/.*)?$/, '/couverture']
];

const PAGE_A_CODE = /^\/(departement|commune)\/([^/]+)$/;
// The shapes the loads themselves validate: a code they would refuse is never redirected.
const CODE_VALIDE: Readonly<Record<string, RegExp>> = { departement: CODE_DEPARTEMENT, commune: CODE_INSEE };

/** Target of a permanent redirect for this path, or null when the path is served as it is. */
export function redirectionPermanente(pathname: string): string | null {
	for (const [motif, cible] of RETIREES) if (motif.test(pathname)) return cible;
	const m = PAGE_A_CODE.exec(pathname);
	if (m) {
		const [, page, code] = m;
		const canonique = code.toUpperCase();
		if (canonique !== code && CODE_VALIDE[page].test(canonique)) return `/${page}/${canonique}`;
	}
	return null;
}
