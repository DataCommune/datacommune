import { api } from './client';
import type { DpeAttribution } from './resolve';
import {
	projectDpeAttributionForClient,
	projectEmbeddedDpeInventory,
	type DpeInventoryPage
} from './dpe-inventory';

// --- Types (mirror backend ResultatLogements — app/logements/schemas.py, Plan 48-06) ---
//
// D-17 (Phase 57) revises D-16, knowingly. The old clause read "no monetary field exists
// anywhere in this contract"; it is no longer true, and leaving it standing would be worse
// than the change itself. What was true then: the sale row was a DVF LINE, so the whole
// mutation's amount appeared beside a single local's surface — false on every multi-local
// sale, hence the blanket interdiction. What is true now: the row is a reconstructed
// MUTATION (`mutations_grain`), so its amount is the amount actually paid, and it never
// travels without `classe_mutation`, the label that says what was sold ("vente d'ensemble
// — prix global", mono-local, …). The backend makes an unlabelled amount UNCONSTRUCTIBLE
// (`VenteImmeuble._valide_prix_etiquete`), a stronger guarantee than the ban ever was.
//
// The grep-checkable guarantee therefore narrows from "no amount anywhere" to: NO AMOUNT
// OUTSIDE A LABELLED MUTATION. Since phase 01-04 there is no logement card left to protect
// (the `logements` list and `LogementItem` were removed); a price exists on `VenteImmeuble`
// only, it is a MUTATION price, it is never rendered without its class, and a mutation
// merely observed on the parcel is never "the sale of this dwelling" nor its certain price
// (D-37).
//
// D-18 (Phase 51): this module declares NO attribution type of its own. `DpeAttribution` and
// its projections live once, in `./resolve`, and are imported here — /v1/resolve and
// /v1/bien/logements serialize the SAME decision (proven backend-side by CASE-DPE-CONSIST-17),
// so a second TypeScript union would be a second business rule waiting to diverge.

/** One MVP detection signal — copro RNC / DPE appartement / DVF appartement (D-01/D-03). */
export interface SignalDetection {
	positif: boolean;
	presence: 'value' | 'absent' | 'error';
	/** Key of the resolve absence taxonomy (RESOLUTION_RAISONS) — never a new word of truth. */
	absence_reason: string | null;
	compte: number;
}

/** 3-signal detection block + verdict. `mode` drives the render state (S1..S4). */
export interface BlocDetection {
	signaux: Record<string, SignalDetection>;
	signaux_positifs: number;
	mode: 'immeuble' | 'indice' | 'bien';
}

/**
 * Honest denominator (D-03/D-07). `lots_declares` stays `null` when the RNC record
 * publishes none — never coerced to 0, the "nombre total de lots non publié" copy
 * variant depends on that distinction.
 */
export interface Denominateur {
	logements_connus: number;
	lots_declares: number | null;
	non_distinguables: number;
}

/**
 * One DVF MUTATION observed on the parcel, priced and labelled (D-11/D-17).
 *
 * Field-for-field mirror of `app/logements/schemas.py::VenteImmeuble` — same names, same
 * nullability, no field invented on this side. Every field is optional backend-side, hence
 * `| null` here: a row projected without its mutation-grain columns stays legitimate.
 *
 * `valeur_fonciere` is the price of the WHOLE mutation, never a share imputed to one lot,
 * and the backend guarantees it arrives with `classe_mutation`. `prix_m2` exists only when
 * `mono_local === true`; otherwise it is `null` and `prix_m2_raison` says why.
 * `nb_locaux_residentiel` / `nb_locaux_commercial` are the perimeter the price covers.
 *
 * No DPE↔sale correspondence is asserted (D-11), and a compatible mutation is never the
 * certain price of a dwelling (D-37). Rendering belongs to Plan 57-14, not to this module.
 */
export interface VenteImmeuble {
	date_mutation: string | null;
	type_local: string | null;
	surface_reelle_bati: number | null;
	nombre_pieces_principales: number | null;
	valeur_fonciere: number | null;
	classe_mutation: string | null;
	mono_local: boolean | null;
	hors_stats_vefa: boolean | null;
	prix_m2: number | null;
	prix_m2_raison: string | null;
	nb_locaux_residentiel: number | null;
	nb_locaux_commercial: number | null;
}

/**
 * Full response body of GET /v1/bien/logements.
 *
 * Phase 51 turned three of these fields into PROJECTIONS of `dpe_attribution`, enforced
 * backend-side by `ResultatLogements._valide_decision_dpe` — reading any of them without first
 * reading `dpe_attribution.status` is now a bug:
 *
 *   - `distribution_dpe` mirrors `dpe_attribution.building_context.distribution` and is `{}`
 *     outside the `building` state. In `multiple` each card carries its own label, and a second
 *     aggregate over the same rows would be the seed of an elected "main class" (D-08/D-12).
 *   - `diagnostics_charges` (M3) stays a count of ROWS READ (exact + building): the truncation
 *     copy names diagnostics, never cards.
 *
 * There is no `logements` card list any more. It was removed in phase 01-04 of the reboot,
 * where it had been hardcoded to `[]` backend-side since 2026-08-19 and reset to `[]` again
 * right here. See docs/REPO-STATE.md; recoverable from `3394395a` (D-05).
 *
 * `diagnostics_exclus_adresse` (T4/T5) now counts the fetched DPE dropped as carrying ANOTHER
 * BAN key. Auditability only — no approved UI surface displays it. Declared OPTIONAL: the field
 * is additive, so a response served by an older backend simply omits it.
 */
export interface ResultatLogements {
	id_parcelle: string;
	detection: BlocDetection;
	denominateur: Denominateur;
	distribution_dpe: Record<string, number>;
	ventes_immeuble: VenteImmeuble[];
	diagnostics_charges: number;
	diagnostics_exclus_adresse?: number;
	tronque: boolean;
	/**
	 * REQUIRED since Plan 51-03 — the same closed decision `/v1/resolve` publishes, byte for
	 * byte. It is the only DPE truth of this payload; `distribution_dpe` is derived from it
	 * and can never contradict it.
	 */
	dpe_attribution: DpeAttribution;
	/** Independent document inventory. It never identifies a dwelling or feeds the KPI. */
	dpe_inventory?: DpeInventoryPage | null;
}

/** Typed metadata (source attribution) — the loader reads it for the sources footer. */
export interface MetaLogements {
	source?: unknown;
}

/**
 * Envelope for GET /v1/bien/logements.
 * `donnees` is a single OBJECT here (backend `ReponseApi[ResultatLogements]`), unlike
 * /v1/resolve where it is a LIST — do not write `donnees[0]`.
 */
export interface ReponseLogements {
	statut: string;
	donnees: ResultatLogements;
	meta?: MetaLogements;
}

// --- API function ---

/**
 * Fetch the multi-logement detection + logement list of one cadastral parcel.
 *
 * Dedicated endpoint (D-12): /v1/resolve is never extended nor called here. Same access
 * regime as resolve (D-14) — the anonymous public key and ApiError handling are carried by
 * `api()`, so this never raw-fetches the DataCommune API.
 *
 * `fetchImpl` mirrors `resolveBienParAdresse` (resolve.ts) and MUST be forwarded as the 3rd
 * positional argument of `api()` — SSR-safe: a universal `load()` on the Cloudflare Worker
 * that forgets SvelteKit's instrumented fetch fails or times out silently, and the fiche
 * renders with no interstitiel at all (Pitfall 4, an invisible false negative).
 *
 * Returns the FULL envelope: the loader needs `meta.source` for the sources footer, not
 * just the result. Errors are propagated as-is — the S7 "détail momentanément indisponible"
 * degradation is decided in the loader (Plan 48-09), never swallowed here.
 *
 * `adresse` is forwarded when non-blank but is now INERT (Plan 51-03): a DPE row carries no
 * postal address field, and street numbers are separated backend-side on the BAN key alone. It
 * is kept because removing it would break callers for no gain. No `limite` parameter is sent:
 * the backend owns its own bound.
 *
 * `banId` is the BAN interoperability key resolved by `/v1/resolve`
 * (`ResultatMultiResolution.ban_id`) and it is now the WHOLE attachment: equality on that key,
 * plus the RNB building carrier for the aggregate, and nothing else. An ADEME DPE position is
 * the BAN entrance point, which frequently sits on the roadway outside every parcel, so a
 * geometric read returns the neighbour's dwellings on a shared parcelle — the defect behind PR
 * #46/#47/#48. Forwarded verbatim, never rebuilt client-side. When the resolution carries no
 * key the backend no longer falls back on geometry: it answers `absent/identity_unavailable`,
 * which is the honest outcome, not a degradation to repair here.
 */
export async function getLogementsParParcelle(
	idParcelle: string,
	adresse?: string,
	banId?: string,
	fetchImpl?: typeof fetch
): Promise<ReponseLogements> {
	const qs = new URLSearchParams({ id_parcelle: idParcelle });
	if (adresse != null && adresse.trim() !== '') {
		qs.set('adresse', adresse);
	}
	if (banId != null && banId.trim() !== '') {
		qs.set('ban_id', banId);
	}
	const response = await api<ReponseLogements>(
		`/v1/bien/logements?${qs}`,
		{ cache: 'no-store' },
		fetchImpl
	);
	const result = response.donnees;
	if (result.dpe_inventory !== undefined && result.dpe_inventory !== null) {
		result.dpe_inventory = projectEmbeddedDpeInventory(result.dpe_inventory);
	}
	result.dpe_attribution = projectDpeAttributionForClient(result.dpe_attribution);
	// No V2 response may leave a per-dwelling projection reachable. The building aggregate
	// lives in dpe_attribution.
	//
	// The sibling `result.logements = []` that stood here until phase 01-04 is gone with the
	// field itself. It is NOT replaced by a `delete`: removing the key from the SSR payload
	// disconnects the inventory trigger buttons on re-render, and keyboard focus no longer
	// returns to the consulted document's own button (measured — dpe-inventory.spec.ts:208
	// goes red). A rogue payload still carrying a legacy card is neutralized where it can do
	// harm, in the view: see logements-view.test.ts "ignores candidate rows".
	result.distribution_dpe = {};
	return response;
}
