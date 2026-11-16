// Phase 50 — the merge that closes the progressive-loading split (bien-proxies.ts).
//
// WHAT IS BEING GUARDED, in one sentence: splitting the resolution in two must change WHEN the
// Fiche Bien says something, never WHAT it says. Every assertion below compares the merged
// result against the single-call reference the page rendered before the split, or against the
// honest degradation it already rendered for an absent dataset (D-04).

import { describe, expect, it } from 'vitest';
import type { EnveloppeProxiesBien, ResultatMultiResolution } from '$lib/api/resolve';
import { fusionnerProvenance, fusionnerProxies, THEMES_DIFFERES } from './bien-proxies';
import { mapBienToFiche } from './bien-fiche';
import { provenanceParDataset } from './explications';
import { mockResolve85 } from './__fixtures__/resolve-85';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import type { KvRow, ThemeModel } from '$lib/types/fiche';

/** The fixture as the FAST leg now returns it: the two proxy datasets are simply not requested. */
function jambeRapide(): ResultatMultiResolution {
	const datasets = { ...(mockResolve85.datasets ?? {}) };
	delete datasets.risques;
	delete datasets.urbanisme;
	return { ...mockResolve85, datasets };
}

/** The streamed leg carrying exactly what the fast leg no longer asks for. */
function jambeProxies(surcharge: Partial<EnveloppeProxiesBien> = {}): EnveloppeProxiesBien {
	return {
		datasets: {
			risques: mockResolve85.datasets!.risques,
			urbanisme: mockResolve85.datasets!.urbanisme
		},
		erreurs: [],
		avertissements: [],
		dataset_states: null,
		provenance: null,
		...surcharge
	};
}

function theme(themes: ThemeModel[], key: string): ThemeModel {
	const t = themes.find((x) => x.key === key);
	if (!t) throw new Error(`theme ${key} absent`);
	return t;
}

function empreinteRows(rows: KvRow[] | undefined): string {
	return JSON.stringify(rows ?? []);
}

describe('fusionnerProxies() — la jambe streamée est arrivée', () => {
	it('reconstitue EXACTEMENT la fiche du mono-appel (aucune régression de contenu)', () => {
		const fusionne = fusionnerProxies(jambeRapide(), jambeProxies());
		// Reference = what the page produced when /v1/resolve was called once with the 6 datasets.
		const reference = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS);
		expect(mapBienToFiche(fusionne, COMMUNE_85047_CHALLANS)).toEqual(reference);
	});

	it('ne mute pas la base (la dérivation de page la relit à chaque recalcul)', () => {
		const base = jambeRapide();
		const avant = JSON.stringify(base);
		fusionnerProxies(base, jambeProxies());
		expect(JSON.stringify(base)).toBe(avant);
		expect(base.datasets).not.toHaveProperty('risques');
	});

	it('concatène les erreurs des deux appels — la LISTE, jamais un dict (Pitfall 1)', () => {
		const base = { ...jambeRapide(), erreurs: [{ dataset: 'dpe', message: 'boom' }] };
		const fusionne = fusionnerProxies(
			base,
			jambeProxies({ erreurs: [{ dataset: 'urbanisme', message: 'timeout GPU' }] })
		);
		expect(fusionne.erreurs).toEqual([
			{ dataset: 'dpe', message: 'boom' },
			{ dataset: 'urbanisme', message: 'timeout GPU' }
		]);
		// Consequence on screen: the urbanisme theme degrades as 'error' (temporary_error), NOT as
		// the 'out_of_coverage' of a dataset that was never requested — two different reasons.
		const rows = theme(mapBienToFiche(fusionne, null).themes, 'environnement').rowsBien ?? [];
		expect(rows.every((r) => r.available === false)).toBe(true);
		expect(rows[0].state).toBe('temporary_error');
	});

	it('concatène les avertissements (ADRESSE_INCOMPLETE reste lisible côté bien)', () => {
		const fusionne = fusionnerProxies(
			jambeRapide(),
			jambeProxies({
				avertissements: [{ dataset: 'risques', code: 'ADRESSE_INCOMPLETE', message: '...' }]
			})
		);
		expect(fusionne.avertissements).toHaveLength(1);
		expect(fusionne.avertissements[0].dataset).toBe('risques');
	});

	it('fusionne dataset_states des deux appels', () => {
		const etatRisques = {
			dataset: 'risques',
			presence: 'value' as const,
			absence_reason: null,
			raison: null
		};
		const base = {
			...jambeRapide(),
			dataset_states: {
				dpe: { dataset: 'dpe', presence: 'value' as const, absence_reason: null, raison: null }
			}
		};
		const fusionne = fusionnerProxies(base, jambeProxies({ dataset_states: { risques: etatRisques } }));
		expect(Object.keys(fusionne.dataset_states ?? {}).sort()).toEqual(['dpe', 'risques']);
	});

	it('préserve `null` quand les DEUX côtés sont nuls (absent ≠ vide, D-04)', () => {
		const base = { ...jambeRapide(), datasets: null, erreurs: null, dataset_states: null };
		const fusionne = fusionnerProxies(base, jambeProxies({ datasets: null, erreurs: null }));
		expect(fusionne.datasets).toBeNull();
		expect(fusionne.erreurs).toBeNull();
		expect(fusionne.dataset_states).toBeNull();
	});
});

describe('fusionnerProxies() — la jambe streamée a échoué', () => {
	it('rend la base À L’IDENTITÉ : rien n’est inventé, rien n’est perdu', () => {
		const base = jambeRapide();
		expect(fusionnerProxies(base, null)).toBe(base);
	});

	it('laisse les deux thèmes se dégrader honnêtement (« hors couverture », pas une valeur)', () => {
		const { themes } = mapBienToFiche(fusionnerProxies(jambeRapide(), null), null);
		for (const key of THEMES_DIFFERES) {
			const rows = theme(themes, key).rowsBien ?? [];
			expect(rows.length).toBeGreaterThan(0);
			expect(rows.every((r) => r.available === false)).toBe(true);
			expect(rows.every((r) => r.state === 'out_of_coverage')).toBe(true);
		}
	});

	it('n’altère PAS les thèmes servis par la jambe rapide (échec borné aux 2 proxys, D-04)', () => {
		const echec = mapBienToFiche(fusionnerProxies(jambeRapide(), null), COMMUNE_85047_CHALLANS);
		const complet = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS);
		expect(echec.kpi).toEqual(complet.kpi);
		for (const key of ['marche', 'energie', 'propriete']) {
			expect(empreinteRows(theme(echec.themes, key).rowsBien)).toBe(
				empreinteRows(theme(complet.themes, key).rowsBien)
			);
		}
	});
});

describe('THEMES_DIFFERES — verrouillé sur le mapper, pas sur une intention', () => {
	it('liste EXACTEMENT les thèmes dont les lignes « sur ce bien » changent à l’arrivée des proxys', () => {
		const sans = mapBienToFiche(fusionnerProxies(jambeRapide(), null), COMMUNE_85047_CHALLANS);
		const avec = mapBienToFiche(
			fusionnerProxies(jambeRapide(), jambeProxies()),
			COMMUNE_85047_CHALLANS
		);
		// A theme whose rows change when the deferred datasets land is a theme that would print
		// « Donnée non disponible » during the wait — i.e. one that MUST get the placeholder.
		const impactes = sans.themes
			.filter((t) => empreinteRows(t.rowsBien) !== empreinteRows(theme(avec.themes, t.key).rowsBien))
			.map((t) => t.key);
		expect(impactes.sort()).toEqual([...THEMES_DIFFERES].sort());
	});
});

describe('fusionnerProvenance()', () => {
	const prov = (dataset: string, label: string) =>
		({
			nom: `Source ${dataset}`,
			licence: 'Licence ouverte',
			url: 'https://example.org',
			producteur: 'X',
			dataset,
			periode: { statut: 'connue', label, debut: null, fin: null },
			derniere_importation_reussie: null,
			couverture: { type: 'nationale', departements: null },
			cadence: {
				statut: 'non_publiee_ou_irreguliere',
				label: null,
				evidence_url: null,
				intervalle_jours: null
			},
			etat: 'actuel'
		}) as const;

	it('indexe les deux appels dans UNE carte par dataset (ℹ️ + tableau imprimé)', () => {
		const base = [prov('dpe', '2024')];
		const fusionne = fusionnerProvenance(base, jambeProxies({ provenance: [prov('risques', '2025')] }));
		const parDataset = provenanceParDataset(fusionne);
		expect(Object.keys(parDataset).sort()).toEqual(['dpe', 'risques']);
		expect(parDataset.risques.periode.label).toBe('2025');
	});

	it('donne la priorité à la 2e jambe sur un dataset publié par les deux (lecture la plus fraîche)', () => {
		const fusionne = fusionnerProvenance(
			[prov('risques', 'ancien')],
			jambeProxies({ provenance: [prov('risques', 'frais')] })
		);
		expect(provenanceParDataset(fusionne).risques.periode.label).toBe('frais');
	});

	it('reste null quand aucun des deux appels ne publie de provenance', () => {
		expect(fusionnerProvenance(null, null)).toBeNull();
		expect(fusionnerProvenance(null, jambeProxies({ provenance: null }))).toBeNull();
	});

	it('rend la provenance de la jambe rapide seule quand les proxys ont échoué', () => {
		const base = [prov('dpe', '2024')];
		expect(fusionnerProvenance(base, null)).toEqual(base);
	});

	it('filtre la provenance hors allowlist — la 2e jambe ne répond que de ses 2 datasets', () => {
		const base = [prov('dpe', '2024')];
		const fusionne = fusionnerProvenance(
			base,
			jambeProxies({ provenance: [prov('risques', '2025'), prov('dpe', 'intrus')] })
		);
		const parDataset = provenanceParDataset(fusionne);
		expect(parDataset.dpe.periode.label).toBe('2024'); // never overridden by the slow leg
		expect(parDataset.risques.periode.label).toBe('2025');
	});
});

describe('fusionnerProxies() — allowlist : la jambe streamée ne contribue QUE risques/urbanisme', () => {
	// PRIVACY GUARD (D-03/B1). The loader minimised `dvf` (price bucket on an isolated recent
	// sale) and anchored `dpe` (only the chosen logement's diagnostic) BEFORE serialisation. An
	// envelope carrying those keys on the slow leg — blanket e2e mock, misbehaving upstream —
	// must NEVER overwrite them: the raw exact price would reach the DOM.
	it('un dvf/dpe embarqué par la 2e jambe est IGNORÉ (le minimisé/ancré de la jambe rapide survit)', () => {
		const base = jambeRapide();
		const dvfMinimise = base.datasets!.dvf;
		const dpeAncre = base.datasets!.dpe;
		const fusionne = fusionnerProxies(
			base,
			jambeProxies({
				datasets: {
					risques: mockResolve85.datasets!.risques,
					urbanisme: mockResolve85.datasets!.urbanisme,
					dvf: [{ valeur_fonciere: 199999 }], // raw value a blanket mock would carry
					dpe: [{ numero_dpe: 'INTRUS' }]
				}
			})
		);
		expect(fusionne.datasets!.dvf).toBe(dvfMinimise);
		expect(fusionne.datasets!.dpe).toBe(dpeAncre);
		expect(fusionne.datasets!.risques).toBe(mockResolve85.datasets!.risques);
	});

	it('erreurs, avertissements et dataset_states hors allowlist sont filtrés', () => {
		const fusionne = fusionnerProxies(
			jambeRapide(),
			jambeProxies({
				erreurs: [
					{ dataset: 'urbanisme', message: 'timeout GPU' },
					{ dataset: 'dvf', message: 'intrus' }
				],
				avertissements: [{ dataset: 'dpe', code: 'X', message: 'intrus' }],
				dataset_states: {
					risques: { dataset: 'risques', presence: 'value', absence_reason: null, raison: null },
					dvf: { dataset: 'dvf', presence: 'error', absence_reason: null, raison: 'intrus' }
				}
			})
		);
		// The fixture's own base erreurs (errial) survive untouched; only the slow leg is filtered.
		expect(fusionne.erreurs).toEqual([
			...(jambeRapide().erreurs ?? []),
			{ dataset: 'urbanisme', message: 'timeout GPU' }
		]);
		expect(fusionne.avertissements).toEqual([]);
		expect(Object.keys(fusionne.dataset_states ?? {})).toEqual(['risques']);
	});

	it('une 2e jambe qui ne porte QUE des intrus équivaut à une jambe vide', () => {
		const base = { ...jambeRapide(), erreurs: null };
		const fusionne = fusionnerProxies(
			base,
			jambeProxies({ datasets: { dvf: [{ valeur_fonciere: 1 }] }, erreurs: null })
		);
		expect(fusionne.datasets).toEqual(base.datasets);
		expect(fusionne.erreurs).toBeNull(); // absent ≠ vide preserved through the filter
	});
});
