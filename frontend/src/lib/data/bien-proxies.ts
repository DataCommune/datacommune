// Phase 50 — progressive loading of the public Fiche Bien: merge the STREAMED leg of the
// resolution (`risques`, `urbanisme`, the two live proxies split out of `DATASETS_BIEN`) back
// into the result of the fast leg. Pure module — zero Svelte imports, zero network access.
//
// WHY A MODULE AND NOT THREE LINES IN THE PAGE: the merge decides what `bien-fiche.ts` will
// read for two whole themes, and the honesty invariant D-04 (« degrade per-dataset, never
// invent ») is enforced by the SHAPE of the merged envelope, not by the component that renders
// it. `datasetState()` reads three independent signals — the presence of the KEY in `datasets`,
// the value under it, and the presence of the dataset in the `erreurs` LIST (Pitfall 1) — so a
// merge that flattened `null` into `{}` would silently turn an « absent » dataset into an
// « empty » one, and the fiche would state a different reason for the same absence. Every
// branch below exists to preserve that distinction, and it is testable exactly because it does
// not live inside a component.

import type {
	DatasetProvenance,
	EnveloppeProxiesBien,
	ResultatMultiResolution
} from '$lib/api/resolve';

/**
 * Fiche theme keys whose « Sur ce bien » rows are built from the DEFERRED datasets, read off
 * `bien-fiche.ts::buildBienThemes`:
 *   - 'risques'       ← `mapRisquesRows(multi)`   reads `datasets.risques`
 *   - 'environnement' ← `mapUrbanismeRows(multi)` reads `datasets.urbanisme`
 *
 * Used by the page to substitute a loading placeholder for these two themes while the second
 * call is in flight: rendering them normally would print « Donnée non disponible » — the exact
 * copy of a *settled* absence (D-04) — on a dataset that is still being fetched. A transient
 * lie is still a lie, and it is the one this list prevents.
 *
 * `bien-proxies.test.ts` locks this list against the mapper itself: the themes whose rows
 * actually change when the proxy datasets arrive MUST be exactly these two.
 */
export const THEMES_DIFFERES: readonly string[] = ['risques', 'environnement'];

/**
 * DATASET keys (not theme keys) the slow leg is AUTHORIZED to contribute — the mirror of
 * `DATASETS_BIEN_PROXIES` (resolve.ts). The merge below filters every envelope field through
 * this allowlist, and that filter is a PRIVACY GUARD, not bookkeeping: the fast leg's `dvf` was
 * minimised (D-03, price bucket) and its `dpe` anchored (B1) by the loader BEFORE serialisation.
 * An envelope that carried those keys — a misbehaving upstream, a blanket e2e mock, a future
 * backend change — would silently overwrite the minimised values with raw ones on merge. The
 * allowlist makes that structurally impossible instead of relying on the backend only returning
 * what was requested.
 */
const DATASETS_DIFFERES: readonly string[] = ['risques', 'urbanisme'];

/** Keep only the allowlisted dataset keys of a record; `null` when nothing survives. */
function restreindreRecord<T>(
	record: Record<string, T> | null | undefined
): Record<string, T> | null {
	if (record == null) return null;
	const filtre = Object.fromEntries(
		Object.entries(record).filter(([cle]) => DATASETS_DIFFERES.includes(cle))
	);
	return Object.keys(filtre).length > 0 ? (filtre as Record<string, T>) : null;
}

/** Keep only the entries naming an allowlisted dataset. */
function restreindreListe<T extends { dataset: string }>(liste: T[] | null | undefined): T[] {
	return (liste ?? []).filter((entree) => DATASETS_DIFFERES.includes(entree.dataset));
}

/** Merge two optional records, preserving `null` when BOTH sides are absent (see header). */
function fusionnerRecord<T>(
	base: Record<string, T> | null | undefined,
	ajout: Record<string, T> | null | undefined
): Record<string, T> | null {
	if (base == null && ajout == null) return null;
	return { ...(base ?? {}), ...(ajout ?? {}) };
}

/**
 * Fold the streamed proxy envelope into the fast leg's resolution.
 *
 * `proxies === null` (call failed, rejected, or empty envelope) returns `base` UNCHANGED and
 * by identity: the two proxy datasets then stay absent from `datasets`, `datasetState()` reads
 * 'absent', and the themes degrade with the honest « Donnée non disponible » they already
 * rendered before this phase. Failure costs the reader nothing but the two proxy blocks.
 *
 * Never mutates `base` (unlike the route's own D-03/B1 minimisations, which deliberately mutate
 * before serialisation): here the source object is the page's own reactive `data`, re-read on
 * every derivation, and a mutation would make the merge non-idempotent.
 */
export function fusionnerProxies(
	base: ResultatMultiResolution,
	proxies: EnveloppeProxiesBien | null
): ResultatMultiResolution {
	if (proxies == null) return base;
	// Allowlist first (see DATASETS_DIFFERES): the slow leg contributes risques/urbanisme and
	// NOTHING else — never a second copy of dvf/dpe that would undo the loader's D-03/B1 work.
	const ajoutDatasets = restreindreRecord(proxies.datasets);
	const ajoutErreurs = restreindreListe(proxies.erreurs);
	const erreurs =
		base.erreurs == null && ajoutErreurs.length === 0
			? (base.erreurs ?? null)
			: [...(base.erreurs ?? []), ...ajoutErreurs];
	return {
		...base,
		datasets: fusionnerRecord(base.datasets, ajoutDatasets),
		erreurs,
		avertissements: [...base.avertissements, ...restreindreListe(proxies.avertissements)],
		dataset_states: fusionnerRecord(base.dataset_states, restreindreRecord(proxies.dataset_states))
	};
}

/**
 * Concatenate the two `meta.source` lists so the ℹ️ popovers AND the printed sources table read
 * ONE provenance map (`provenanceParDataset`, which keys by `provenance.dataset`). The proxy
 * entries come LAST, so on the theoretical overlap of a dataset published by both calls the
 * freshest reading — the one that actually produced the rendered rows — wins.
 *
 * Returns `null` when neither call published a provenance: an empty list would be indistinguishable
 * from « the backend answered with sources », and `provenanceParDataset` treats both the same, but
 * keeping `null` preserves the loader's own contract (`provenance: res.meta?.source ?? null`).
 */
export function fusionnerProvenance(
	base: DatasetProvenance[] | null | undefined,
	proxies: EnveloppeProxiesBien | null
): DatasetProvenance[] | null {
	// Same allowlist as the datasets merge: the slow leg only vouches for its own two datasets.
	const ajout = proxies?.provenance == null ? null : restreindreListe(proxies.provenance);
	if (base == null && ajout == null) return null;
	return [...(base ?? []), ...(ajout ?? [])];
}
