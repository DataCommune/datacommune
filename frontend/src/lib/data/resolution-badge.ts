// Honest precision badge — pure derivation (FICHE-03 / D-07).
//
// D-07: the header must expose ONLY the granularity actually reached, never a granularity
// that was not reached. There is no dedicated "resolution level" enum on the backend
// contract (RESEARCH Priority Question 6) — this function COMPOSES existing, already
// exposed fields: `meta.resolution.issue` (ResolutionOutcome) with the presence/absence of
// `adresse` (with/without a street), `code_insee` and `id_parcelle` on the resolved result.
// No new backend field, no new vocabulary of truth. Pure: no I/O, exhaustive branches,
// never invents a value, never reconstructs an unmatched street number.
//
// Deliberately does NOT read the per-dataset warnings list (its "incomplete address" code
// is emitted only for connectivite/scolaire, see bien-fiche.ts adresseIncompleteDatasets):
// that signal is dataset-scoped, not a global resolution-truth signal (RESEARCH Priority
// Question 6, point 4).

import type { ResolutionOutcome, ResultatMultiResolution } from '$lib/api/resolve';

export type ResolutionBadgeNiveau = 'adresse' | 'commune' | 'partiel' | 'none';
export type ResolutionBadgeTon = 'info' | 'neutral' | 'watch';

export interface ResolutionBadge {
	label: string;
	niveau: ResolutionBadgeNiveau;
	ton: ResolutionBadgeTon;
}

const PARCELLE_SUFFIX = ' · parcelle identifiée';

/**
 * A street is present iff `adresse` is non-null, contains a comma, and the segment before
 * the comma is non-empty — mirrors the backend's `_format_adresse` (resolve/router.py:150-152),
 * which emits `"{code_postal} {nom_commune}"` (no comma) when no street matched.
 */
function hasRue(adresse: string | null | undefined): boolean {
	if (adresse == null) return false;
	const virguleIdx = adresse.indexOf(',');
	if (virguleIdx === -1) return false;
	return adresse.slice(0, virguleIdx).trim().length > 0;
}

function parcelleSuffix(multi: ResultatMultiResolution | null): string {
	return multi?.id_parcelle != null ? PARCELLE_SUFFIX : '';
}

/**
 * Derive the honest precision badge for the Fiche Bien header.
 *
 * Branches on `issue` first for the non-exact, explicit failure/ambiguity cases; for
 * `exact`, derives the positive granularity level from `adresse`/`code_insee`/`id_parcelle`.
 * Undefined `issue` or `multi === null` (no resolution metadata) is treated as
 * non-conclusive — niveau 'none' — never as a crash.
 */
export function deriveResolutionBadge(
	multi: ResultatMultiResolution | null,
	issue: ResolutionOutcome['issue'] | undefined
): ResolutionBadge {
	if (issue === 'out_of_coverage') {
		return { label: 'Hors périmètre', niveau: 'none', ton: 'watch' };
	}
	if (issue === 'not_reported') {
		return { label: 'Aucune correspondance', niveau: 'none', ton: 'watch' };
	}
	if (issue === 'ambiguous') {
		return { label: 'Plusieurs parcelles candidates', niveau: 'partiel', ton: 'watch' };
	}
	if (issue === 'exact') {
		const suffix = parcelleSuffix(multi);
		if (hasRue(multi?.adresse)) {
			return { label: `Adresse normalisée${suffix}`, niveau: 'adresse', ton: 'info' };
		}
		if (multi?.adresse != null || multi?.code_insee != null) {
			return { label: `Commune${suffix}`, niveau: 'commune', ton: 'neutral' };
		}
	}
	// issue undefined, or 'exact' with no usable signal at all — honest "no conclusion",
	// never a fabricated granularity.
	return { label: 'Résolution non concluante', niveau: 'none', ton: 'watch' };
}
