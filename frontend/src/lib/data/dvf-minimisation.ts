// D-03 price-bucket masking helper for the public Fiche Bien SSR loader (47-DVF-MINIMIZATION-
// REVIEW.md §5) — extracted from routes/(marketing)/bien/[token]/+page.ts by plan 49-10 (CR-01)
// so the unit can be READ from the semantic contract and the function unit-tested. Only the
// bucket FORMATTING lives here: the masking decision itself (isolation check, 24-month window,
// call sites before serialization) stays in load(), and must never move.
import { displayUnit } from './formatters';

/**
 * Readable price bracket — never a point value, never a rounding that reconstructs the exact
 * figure. The unit suffix comes from `displayUnit(fieldId)` — the single unit authority that
 * also feeds the ℹ️ panel's « Unité : … » line — so the rendered bracket and the popover can
 * never diverge (CR-01: a hardcoded " €" once shipped on the €/m² field). Throws on an
 * unregistered field_id, like every other registry accessor (T-43-07).
 */
export function formatPriceBucket(fieldId: string, valeur: number, step: number): string {
	const nf = new Intl.NumberFormat('fr-FR');
	const low = Math.floor(valeur / step) * step;
	const unit = displayUnit(fieldId);
	return `${nf.format(low)} – ${nf.format(low + step)}${unit ? ` ${unit}` : ''}`;
}
