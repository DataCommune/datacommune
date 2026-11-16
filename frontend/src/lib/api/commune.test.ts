import { describe, expect, it, vi } from 'vitest';
import { communeKpis } from '$lib/data/commune-kpi';
import fixture from '$lib/data/__fixtures__/dvf-numeric-chain.json';
import {
	blocsPublies,
	getCommuneComparaison,
	getCommuneSynthese,
	MOTIF_BLOCS_NON_PUBLIES,
	type CommuneSynthese
} from './commune';

// Todo 2026-11-01: with the rollout gate closed the API serves both blocks as an explicit absence
// `{not_qualified, indicateurs_communaux_non_publies}`. The fiche must keep reading the flat
// columns (as for a null block), never show « Non qualifié » in place of the served prices.

const LEGACY = fixture.http_projection.legacy as CommuneSynthese;

function avecBlocs(motif: string): CommuneSynthese {
	const absence = { etat: 'not_qualified', motif } as const;
	return { ...LEGACY, indicateurs_dvf: absence, indicateurs_dpe: absence };
}

function fetchReturning(donnees: unknown) {
	return vi.fn(
		async (_input: RequestInfo | URL, _init?: RequestInit) =>
			new Response(JSON.stringify({ statut: 'ok', donnees, meta: { source: [] } }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			})
	);
}

const prixMaisons = (commune: CommuneSynthese) =>
	communeKpis(commune).find((kpi) => kpi.id === 'prix-maisons')!.lignes[0].etat;

describe('blocks the instance does not publish', () => {
	it('are served as is by getCommuneSynthese (what the open export serves)', async () => {
		const servie = avecBlocs(MOTIF_BLOCS_NON_PUBLIES);
		const reponse = await getCommuneSynthese('44001', fetchReturning([servie]));
		expect(reponse.donnees).toEqual([servie]);
	});

	it('fall back to the flat columns once read by a screen (blocsPublies)', () => {
		const commune = blocsPublies(avecBlocs(MOTIF_BLOCS_NON_PUBLIES));
		expect(commune.indicateurs_dvf).toBeNull();
		expect(commune.indicateurs_dpe).toBeNull();
		expect(prixMaisons(commune)).toEqual(prixMaisons(LEGACY));
		expect(prixMaisons(commune)).toMatchObject({ kind: 'valeur' });
	});

	it('would hide the flat price if served as is (the boundary is what keeps it)', () => {
		expect(prixMaisons(avecBlocs(MOTIF_BLOCS_NON_PUBLIES))).toMatchObject({ kind: 'absence', etat: 'not_qualified' });
	});

	it('keeps indicator_policy_unqualified as a served block absence', () => {
		const commune = blocsPublies(avecBlocs('indicator_policy_unqualified'));
		expect(commune.indicateurs_dvf).toEqual({ etat: 'not_qualified', motif: 'indicator_policy_unqualified' });
		expect(prixMaisons(commune)).toMatchObject({
			kind: 'absence',
			etat: 'not_qualified',
			motif: 'indicator_policy_unqualified'
		});
	});

	it('are read the same way in the syntheses of the comparator', async () => {
		const commune = (code: string, synthese: CommuneSynthese | null) => ({
			code_insee: code,
			nom: code,
			departement: '44',
			hebergement: { etat: 'heberge', departements: ['44'] },
			synthese
		});
		const reponse = await getCommuneComparaison(
			['44001', '44002', '44003'],
			fetchReturning({
				communes: [
					commune('44001', avecBlocs(MOTIF_BLOCS_NON_PUBLIES)),
					commune('44002', avecBlocs('indicator_policy_unqualified')),
					commune('44003', null)
				],
				criteres: [],
				codes_inconnus: []
			})
		);
		const [nonPubliee, nonQualifiee, sansSynthese] = reponse.donnees.communes;
		expect(nonPubliee.synthese).toEqual(blocsPublies(avecBlocs(MOTIF_BLOCS_NON_PUBLIES)));
		expect(nonPubliee.synthese?.indicateurs_dvf).toBeNull();
		expect(nonQualifiee.synthese?.indicateurs_dpe).toEqual({ etat: 'not_qualified', motif: 'indicator_policy_unqualified' });
		expect(sansSynthese.synthese).toBeNull();
	});
});
