// Phase 49 (plan 49-07, D-19) — OFFLINE proof that the three public surfaces share ONE semantic
// layer.
//
// WHAT IS BEING GUARDED: the phase moved every threshold, every state wording and every metric
// explanation into shared leaf modules (`referentiels.ts`, `etats-libelles.ts`,
// `explications.ts`). Nothing forbids a future contributor from re-adding a local cut-off in one
// mapper "just for this one KPI" — and the surfaces would then diverge silently, which is exactly
// the defect the phase exists to remove. One fiche would keep showing a figure while the other
// judged it against a private threshold, and no rendered test would notice. (The third surface,
// the landing and its public snapshot projection, was removed in 03-05, D-38, with the cases that
// read it.)
//
// D-19 — the CI proof is OFFLINE and deterministic: no browser, no network, no live instance. The
// live reading (same commune read on /commune/[code] and inside a /bien/[token] commune block)
// is a one-off verification procedure written up in 49-07-SUMMARY.md for the phase verification,
// NOT a permanent workflow.
//
// Four families of assertions, in increasing strength:
//   1. STRUCTURAL — the modules import the shared layer, and no parallel threshold constant came
//      back (source read);
//   1bis. OUTPUT — every positioning `comparerAuReferentiel` actually produces, on the real domain
//      of every referential, carries its justification (see the §1bis header below);
//   2. EXECUTABLE — the same source object produces the same output through two different paths
//      (Fiche Bien commune block vs Fiche Commune; the rendered-vintage cases left in 04.8-01 with
//      the grid and summary components they mounted, no page rendering the Fiche Bien any more);
//   3. REFERENCE — a state label read through `formatField` IS the glossary entry, not a copy.

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { mapCommuneToFiche } from './commune-fiche';
import { mapBienToFiche } from './bien-fiche';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import { mockResolve85 } from './__fixtures__/resolve-85';
import { formatField, formatBareNumber, getFieldContract } from './formatters';
import { LIBELLES_ECARTS_HISTORIQUES as LIBELLES_ETATS } from './etats-libelles';
import { REFERENTIELS, comparerAuReferentiel } from './referentiels';
import type { ReferentielCategoriel, ReferentielNumerique } from './referentiels';
import type { DpePublicDiagnostic } from '$lib/api/resolve';
import type { DpeFicheSelection } from './dpe-attribution-view';

/**
 * Phase 51: the Fiche Bien no longer reads a DPE from the payload — it receives the diagnostic
 * the attribution policy authorised. Tests that need a rendered DPE therefore hand one over
 * explicitly, exactly as the loader does after `deriveDpeAttributionView`.
 */
const SELECTION_DPE_F: DpeFicheSelection = {
	diagnostic: {
		numero_dpe: '2385E1234567A',
		etage: null,
		batiment: null,
		porte: null,
		type_batiment: 'maison',
		surface_habitable: 78,
		etiquette_dpe: 'F',
		etiquette_ges: 'D',
		annee_construction: 1972,
		periode_construction: '1948-1974',
		type_installation_chauffage: 'chaudière fioul',
		type_energie_principale_chauffage: 'Fioul domestique',
		conso_5_usages_ep: 412,
		emission_ges_5_usages: 98,
		cout_total_5_usages: 2310,
		qualite_isolation_enveloppe: 'insuffisante',
		date_etablissement_dpe: '2022-06-01',
		date_fin_validite_dpe: '2032-05-31',
		annee_diagnostic: 2022,
		perime: false
	} satisfies DpePublicDiagnostic,
	gapState: null
};

function lireSource(fichier: string): string {
	return readFileSync(resolve(process.cwd(), 'src/lib/data/', fichier), 'utf8');
}

const SOURCES = {
	commune: lireSource('commune-fiche.ts'),
	bien: lireSource('bien-fiche.ts'),
	formatters: lireSource('formatters.ts'),
	// The DESTINATION of the in-house table removed at 49-03. Its absence from this map is half of
	// why the 49-07 lock could not see the defect (49-VERIFICATION §1.c).
	referentiels: lireSource('referentiels.ts')
} as const;

// ── 1. Structural — one shared layer, zero parallel logic ────────────────────
//
// WHAT THIS FAMILY GUARANTEES, AND WHAT IT DOES NOT (49-VERIFICATION §1.c).
// Reading a source file can only prove that two HISTORICAL IDENTIFIERS did not come back under
// their own name. It proved nothing at all about semantics: at 49-03 the table
// `zoneAbc: { good: ['A','B1'], neutral: ['B2'] }` left `commune-fiche.ts` and landed VERBATIM in
// `referentiels.ts` — which was not even in the `SOURCES` map above, so the grep was not looking
// where the table had gone. Both defects are fixed here: `referentiels.ts` is in the map, and the
// real guarantee now lives in §1bis, which asserts the OUTPUT of the comparison function rather
// than the shape of its source. This family is kept because it is still the cheapest tripwire
// against a return under the original name — it is simply not sufficient on its own.

describe('D-19 §1 — les 2 fiches importent la MÊME couche sémantique', () => {
	it('les deux mappers de fiche lisent les référentiels publiés', () => {
		expect(SOURCES.commune).toContain("from './referentiels'");
		expect(SOURCES.bien).toContain("from './referentiels'");
	});

	it('les deux mappers passent par la seule autorité de formatage/état', () => {
		expect(SOURCES.commune).toContain("from './formatters'");
		expect(SOURCES.bien).toContain("from './formatters'");
	});

	it('aucun seuil maison n’est revenu dans les mappers', () => {
		// The in-house cut-off tables removed at 49-03. Their NAMES are the tripwire: a
		// reintroduction under any of the two historical identifiers turns this red.
		const seuilsMaison = /VERDICT_THRESHOLDS|BIEN_VERDICT_THRESHOLDS/;
		expect(SOURCES.commune).not.toMatch(seuilsMaison);
		expect(SOURCES.bien).not.toMatch(seuilsMaison);
		// `referentiels.ts` MENTIONS both identifiers in its module header — it is the file that
		// documents their removal, and banning the bare name there would mean deleting that
		// explanation. What is banned in the destination file is a RE-DECLARATION, never the prose.
		const redeclaration = /(?:const|let|var|function|class)\s+(?:BIEN_)?VERDICT_THRESHOLDS\b/;
		expect(SOURCES.referentiels).not.toMatch(redeclaration);
	});

	it('les libellés d’état viennent du glossaire unique, jamais d’un littéral de rendu', () => {
		expect(SOURCES.formatters).toContain("from './etats-libelles'");
	});
});

// ── 1bis. Output — a positioning that reaches the reader carries its justification ──
//
// WHAT THIS FAMILY GUARANTEES: for EVERY entry of `REFERENTIELS`, exercised over its REAL domain
// (every published class of a categorical entry; three probes derived from the entry's own value
// and tolerance band for a numeric one), any `Comparaison` whose verdict is not `info` must carry
// an `ecartTexte` that names the publication AND states what the position rests on — the figure
// compared against for a numeric referential, the published `fondement` of the order for a graded
// categorical one. A chip that colours without saying against what is exactly the defect FICHE-04
// exists to remove (49-VERIFICATION §1.b).
//
// WHAT IT DOES NOT GUARANTEE: it says nothing about the wording of a label, nothing about the
// rendering, and nothing about referentials that do not exist yet. It is a lock on the OUTPUT of
// one function, not a proof that the copy is right.

/** Real domain of an entry — every published class, or three deterministic numeric probes. */
function domaineReel(entree: ReferentielNumerique | ReferentielCategoriel): Array<string | number> {
	if (entree.kind === 'categoriel') return Object.keys(entree.labels);
	// Below the band / on the reference / above the band — derived from the entry itself, never
	// from a literal, so a change of value or of tolerance keeps exercising the three branches.
	const marge = (entree.tolerancePct + 5) / 100;
	return [entree.valeur * (1 - marge), entree.valeur, entree.valeur * (1 + marge)];
}

describe('D-19 §1bis — aucun positionnement rendu sans sa justification publiée', () => {
	it('toute comparaison non-info cite sa source ET ce sur quoi elle se fonde', () => {
		let produites = 0;
		let positionnements = 0;

		for (const [fieldId, entree] of Object.entries(REFERENTIELS)) {
			for (const valeur of domaineReel(entree)) {
				const c = comparerAuReferentiel(fieldId, valeur);
				if (c == null) continue;
				produites += 1;
				if (c.verdict === 'info') continue;
				positionnements += 1;

				// Every failure message names the field and the value: the red output must be
				// readable without opening this file.
				const ou = `${fieldId} = ${String(valeur)}`;
				const texte = c.ecartTexte ?? '';
				expect(c.ecartTexte, `${ou} : verdict « ${c.verdict} » sans ecartTexte`).not.toBeNull();
				expect(texte.trim().length, `${ou} : ecartTexte vide`).toBeGreaterThan(0);
				expect(texte, `${ou} : la publication source n’est pas citée`).toContain(entree.source);

				if (entree.kind === 'numerique') {
					expect(
						texte,
						`${ou} : la valeur de référence chiffrée n’atteint pas le lecteur`
					).toContain(formatBareNumber(fieldId, entree.valeur));
				} else {
					expect(
						entree.gradation,
						`${ou} : verdict « ${c.verdict} » sur une classification dont la source ne publie aucun ordre (gradation: null)`
					).not.toBeNull();
					expect(
						texte,
						`${ou} : le fondement publié de la gradation n’atteint pas le lecteur`
					).toContain(entree.gradation!.fondement);
				}
			}
		}

		// Non-vacuity (T-49-11): an emptied referential or an all-`info` one must turn this red,
		// never pass in silence — a loop that never ran would otherwise be green and prove nothing.
		expect(produites, 'aucune comparaison produite sur le domaine réel').toBeGreaterThan(0);
		expect(
			positionnements,
			'aucun positionnement non-info produit — le verrou ne teste rien'
		).toBeGreaterThan(0);
	});
});

// ── 2. Executable — same source object, same output on two paths ─────────────

describe('D-19 §2 — Fiche Bien et Fiche Commune : le bloc commune est le MÊME', () => {
	it('préserve les lignes communes partagées et remplace le placeholder des risques par le proxy', () => {
		const bien = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS);
		const commune = mapCommuneToFiche(COMMUNE_85047_CHALLANS);

		// Non-vacuity: five themes, each carrying rows — an empty loop would be green (T-49-11).
		expect(commune.themes.length).toBe(5);
		for (const themeCommune of commune.themes) {
			expect(themeCommune.rows.length).toBeGreaterThan(0);
			const themeBien = bien.themes.find((t) => t.key === themeCommune.key);
			expect(themeBien, `thème ${themeCommune.key} absent de la Fiche Bien`).toBeDefined();
			// The Bien alone requests live commune risks. Other commune figures keep the exact
			// synthesis mapping; the unavailable synthesis placeholder is replaced, not retained.
			const liveRiskIds = ['bien.risques.inondation', 'bien.risques.sismique'];
			const expected = themeCommune.rows.filter(
				(row) => row.fieldId !== 'commune.risques.risques_naturels'
			);
			const actual = themeBien!.rowsCommune ?? [];
			const expectedMetrics = expected.filter((row) => row.kind !== 'context');
			expect(expectedMetrics.length).toBeGreaterThan(0);
			expect(actual.filter((row) => row.kind !== 'context' && !liveRiskIds.includes(row.fieldId ?? '')))
				.toStrictEqual(expectedMetrics);
			// Preserve shared contextual rows too; only the Bien adds this proxy scope reserve.
			const proxyContexts = themeCommune.key === 'risques' ? [{
				kind: 'context',
				state: 'not_reported',
				label: 'Couverture GASPAR',
				value: 'Nombre inconnu de résultats lus ; total inconnu. Première page, exhaustivité non établie.',
				muted: true
			}] : [];
			expect(actual.filter((row) => row.kind === 'context'))
				.toStrictEqual([...expected.filter((row) => row.kind === 'context'), ...proxyContexts]);
			expect(actual.filter((row) => liveRiskIds.includes(row.fieldId ?? '')).map((row) => row.fieldId))
				.toEqual(themeCommune.key === 'risques' ? liveRiskIds : []);
		}
	});

	it('le positionnement DPE de la Fiche Bien vient du référentiel publié, pas d’un seuil local', () => {
		const { kpi } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS, SELECTION_DPE_F);
		const dpe = kpi.find((k) => k.fieldId === 'bien.dpe.etiquette_dpe');
		expect(dpe, 'KPI étiquette DPE absent de la Fiche Bien').toBeDefined();
		// Non-vacuity: the fixture really carries a class, so a real lookup happens below.
		expect(dpe!.value).toBe('F');

		const referentiel = REFERENTIELS['bien.dpe.etiquette_dpe'];
		expect(referentiel.kind).toBe('categoriel');
		const attendu =
			referentiel.kind === 'categoriel'
				? referentiel.labels[String(dpe!.value)]
				: undefined;
		expect(attendu, `classe ${String(dpe!.value)} absente du référentiel publié`).toBeDefined();
		// The chip wording IS the published entry, interpolated — never a word chosen by the mapper.
		expect(dpe!.verdictLabel).toBe(attendu!.label.replace('{valeur}', String(dpe!.value)));
		expect(dpe!.verdict).toBe(attendu!.verdict);
	});
});

// ── 3. Reference — one glossary, read not copied ─────────────────────────────

describe('D-19 §3 — le libellé d’un état est LU dans le glossaire', () => {
	it("formatField('not_reported') rend exactement LIBELLES_ETATS.not_reported", () => {
		const row = formatField('revenus.revenu_median', 'Revenu médian', { state: 'not_reported' });
		expect(row.value).toBe(LIBELLES_ETATS.not_reported);
		expect(row.state).toBe('not_reported');
	});

	it('les états dégradés EN CONTRAT portent leur PROPRE état sur la ligne rendue', () => {
		// Same degraded cell, distinct reasons (49-06): the ℹ️ can only tell them apart if the row
		// carries its own state rather than a boolean "available".
		// Phase 50 / D-20: the list was `[…, 'not_applicable']`. It is now derived from the field's
		// OWN contract — `not_applicable` is listed by no registry entry at all, so probing it here
		// was out of contract. The proof is not weakened: it now covers every degraded state the
		// field may actually emit, and the assertion below states why the fourth is missing.
		const contrat = getFieldContract('revenus.revenu_median').allowed_states;
		const etats = (['not_reported', 'out_of_coverage', 'temporary_error'] as const).filter((e) =>
			contrat.includes(e)
		);
		expect(etats).toHaveLength(3);
		for (const etat of etats) {
			const row = formatField('revenus.revenu_median', 'Revenu médian', { state: etat });
			expect(row.state).toBe(etat);
			expect(row.value).toBe(LIBELLES_ETATS[etat]);
		}
		// The fourth degraded state of the D-04 set is unreachable BY CONTRACT, not by omission.
		expect(contrat).not.toContain('not_applicable');
	});
});
