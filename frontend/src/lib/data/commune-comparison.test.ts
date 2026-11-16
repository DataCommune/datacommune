import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import {
	composeComparaison,
	composeComparaisonAdresses,
	composeComparaisonDocuments,
	factoriserMetadonnees,
	ROLES_DATES
} from './commune-comparison';
import { projeterComparaisonAdresses } from '$lib/api/adresses';
import { projeterComparaisonDocuments } from '$lib/api/documents-dpe';
import adressesServies from './__fixtures__/adresse-comparaison.json';
import documentsServis from './__fixtures__/dpe-comparaison.json';
import { LIBELLES_ETATS, libelleEtat } from './etats-libelles';
import { mapCommuneToFiche } from './commune-fiche';
import { PERIODE_ENTAMEE } from './commune-kpi';
import type {
	ComparaisonCommunes,
	CommuneComparee,
	CommuneSynthese,
	ReponseApiComparaison
} from '$lib/api/commune';
import comparaisons from './__fixtures__/commune-comparaison.json';
import cor14 from './__fixtures__/commune-cor14-api.json';

const HOSTED = { etat: 'heberge' as const, motif: null, departements: ['44', '85'] };

function colonne(code: string, nom: string): CommuneComparee {
	return { code_insee: code, nom, departement: code.slice(0, 2), hebergement: HOSTED, synthese: null };
}

/** A served response whose gap is deliberately NOT the subtraction of the two values. */
function served(): ComparaisonCommunes {
	return {
		communes: [colonne('44109', 'Nantes'), colonne('85047', 'Challans')],
		criteres: [
			{
				cle: 'revenu_median', field_id: 'revenus.revenu_median', unite: 'euro_per_uc_year', identiques: false,
				cellules: [
					{ code_insee: '44109', etat: 'value', valeur: 21000, periode: '2021', ecart: null,
						motif_non_calcul: null, identique_a_la_reference: null },
					{ code_insee: '85047', etat: 'value', valeur: 22480, periode: '2021',
						ecart: { valeur: 999, unite: 'euro_per_uc_year' }, motif_non_calcul: null,
						identique_a_la_reference: false }
				]
			},
			{
				cle: 'taux_pauvrete', field_id: 'revenus.taux_pauvrete', unite: 'percent_point', identiques: false,
				cellules: [
					{ code_insee: '44109', etat: 'value', valeur: 15.2, periode: '2021', ecart: null,
						motif_non_calcul: null, identique_a_la_reference: null },
					{ code_insee: '85047', etat: 'value', valeur: 15.2, periode: '2019', ecart: null,
						motif_non_calcul: 'periodes_differentes', identique_a_la_reference: false }
				]
			}
		],
		codes_inconnus: ['99999']
	};
}

/**
 * Frozen harness responses of /v1/communes/comparaison, written by
 * `api/backend/tests/integration/test_commune_comparison_contract.py` (COMPARAISON_API_FIXTURE_OUTPUT)
 * on a migrated PostGIS database and guarded by `tests/test_comparison_fixture_contract.py`.
 */
const REPONSES = comparaisons as unknown as Record<string, ReponseApiComparaison>;
const TROIS = REPONSES.trois_communes_dont_hors_couverture;
const DEUX = REPONSES.deux_communes_hebergees;

/** Rows of values the API does not serve at all (structural gaps of the fiche): no path. */
const LIGNES_NON_SERVIES = new Set(['commune.risques.risques_naturels', 'commune.propriete.coproprietes']);

function existe(synthese: CommuneSynthese, path: string): boolean {
	let node: unknown = synthese;
	for (const key of path.split('.')) {
		if (node === null || typeof node !== 'object' || !(key in node)) return false;
		node = (node as Record<string, unknown>)[key];
	}
	return true;
}

function syntheses(): CommuneSynthese[] {
	return [
		...Object.values(REPONSES).flatMap(reponse =>
			reponse.donnees.communes.flatMap(commune => (commune.synthese ? [commune.synthese] : []))),
		...Object.values(cor14).map(reponse => reponse.donnees[0] as CommuneSynthese)
	];
}

/** The rows the comparator displays for a served response, in display order. */
function lignesAffichees(reponse: ReponseApiComparaison) {
	return composeComparaison(reponse.donnees, reponse.meta?.source ?? null).groupes.flatMap(groupe => groupe.lignes);
}

describe('composeComparaison (D-48, D-51)', () => {
	it('factorises metadata only with one exact structured period provenance', () => {
		const reponse = structuredClone(DEUX);
		const sources = structuredClone(reponse.meta!.source);
		const revenus = sources.find((source) => source.dataset === 'revenus')!;
		revenus.periode = { statut: 'connue', label: '2021', debut: null, fin: null };
		const ligne = composeComparaison(reponse.donnees, sources).groupes
			.flatMap((groupe) => groupe.lignes)
			.find((item) => item.cle === 'revenu_median')!;

		expect(ligne.cellules.map((cellule) => cellule.naturePeriode)).toEqual(['connue', 'connue']);
		expect(factoriserMetadonnees(ligne)).toEqual({
			periode: 'Millésime 2021',
			nature: 'connue',
			source: { nom: 'Filosofi (revenus et pauvreté)', url: 'https://www.insee.fr/fr/statistiques/7756855' },
			unite: '€/UC/an'
		});
	});

	it('refuses an ambiguous exact source when one duplicate has no period proof', () => {
		const reponse = structuredClone(DEUX);
		const sources = structuredClone(reponse.meta!.source);
		const revenus = sources.find((source) => source.dataset === 'revenus')!;
		revenus.periode = { statut: 'connue', label: '2021', debut: null, fin: null };
		sources.push({ ...revenus, periode: undefined });
		const ligne = composeComparaison(reponse.donnees, sources).groupes
			.flatMap((groupe) => groupe.lignes)
			.find((item) => item.cle === 'revenu_median')!;

		expect(ligne.cellules.map((cellule) => cellule.naturePeriode)).toEqual([null, null]);
		expect(factoriserMetadonnees(ligne)).toBeNull();
	});

	it('keeps metadata in cells when period proof is missing or any strict member diverges', () => {
		const reponse = structuredClone(DEUX);
		const sources = structuredClone(reponse.meta!.source);
		const revenus = sources.find((source) => source.dataset === 'revenus')!;
		revenus.periode = { statut: 'connue', label: '2021', debut: null, fin: null };
		const originale = composeComparaison(reponse.donnees, sources).groupes
			.flatMap((groupe) => groupe.lignes)
			.find((item) => item.cle === 'revenu_median')!;
		const [premiere, seconde] = originale.cellules;

		expect(factoriserMetadonnees({ ...originale, cellules: [premiere, { ...seconde, naturePeriode: null }] })).toBeNull();
		expect(factoriserMetadonnees({ ...originale, unite: null })).toBeNull();
		expect(factoriserMetadonnees({ ...originale, cellules: [premiere, { ...seconde, naturePeriode: 'glissante' }] })).toBeNull();
		expect(factoriserMetadonnees({ ...originale, cellules: [premiere, { ...seconde, periode: 'Millésime 2020' }] })).toBeNull();
		expect(
			factoriserMetadonnees({
				...originale,
				cellules: [premiere, { ...seconde, source: seconde.source && { ...seconde.source, url: 'https://example.test/autre' } }]
			})
		).toBeNull();
		expect(
			factoriserMetadonnees({
				...originale,
				cellules: [premiere, { ...seconde, etat: { kind: 'valeur', texte: '22 480', unite: '€' } }]
			})
		).toBeNull();
	});

	// B6 (plan 04.5-10, UI-013): a row without displayable unit may be factorised for its period and its
	// source, under exactly the same guards; its unit is then not factorised.
	describe('B6 — a row without displayable unit (UI-013)', () => {
		/** The served DVF counts of two communes: no unit, one structured period, one source. */
		const sansUnite = () => {
			const ligne = lignesAffichees(structuredClone(DEUX)).find((item) => item.cle === 'nb_mutations_12m_maison')!;
			expect(ligne.unite).toBeNull();
			return ligne;
		};

		it('is factorised when period, period nature and source are strictly identical and every cell is a value', () => {
			const ligne = sansUnite();
			expect(ligne.cellules.map((cellule) => cellule.etat?.kind)).toEqual(['valeur', 'valeur']);
			expect(factoriserMetadonnees(ligne)).toEqual({
				periode: '2024-06-01 → 2025-06-01',
				nature: 'inconnue',
				source: ligne.cellules[0].source,
				unite: null
			});
		});

		it('is not factorised when one cell differs by its period, its period nature or its source', () => {
			const ligne = sansUnite();
			const [premiere, seconde] = ligne.cellules;
			const avec = (cellule: Partial<typeof seconde>) => ({ ...ligne, cellules: [premiere, { ...seconde, ...cellule }] });
			expect(factoriserMetadonnees(avec({ periode: '2023-06-01 → 2024-06-01' }))).toBeNull();
			expect(factoriserMetadonnees(avec({ naturePeriode: 'glissante' }))).toBeNull();
			expect(factoriserMetadonnees(avec({ naturePeriode: null }))).toBeNull();
			expect(factoriserMetadonnees(avec({ source: { ...seconde.source!, nom: 'Autre source' } }))).toBeNull();
			expect(factoriserMetadonnees(avec({ source: { ...seconde.source!, url: 'https://example.test/autre' } }))).toBeNull();
			expect(factoriserMetadonnees(avec({ source: null }))).toBeNull();
		});

		it('is not factorised when one cell differs by its state: an absence, an outage, nothing honest to render', () => {
			const ligne = sansUnite();
			const [premiere, seconde] = ligne.cellules;
			const avec = (cellule: Partial<typeof seconde>) => ({ ...ligne, cellules: [premiere, { ...seconde, ...cellule }] });
			expect(factoriserMetadonnees(avec({ etat: { kind: 'absence', etat: 'not_available', motif: '', effectif: null } }))).toBeNull();
			expect(factoriserMetadonnees(avec({ etat: { kind: 'absence', etat: 'temporary_error', motif: '', effectif: null } }))).toBeNull();
			expect(factoriserMetadonnees(avec({ etat: null }))).toBeNull();
			// A unit shown by one value only is a difference too.
			expect(factoriserMetadonnees(avec({ etat: { kind: 'valeur', texte: '12', unite: 'ventes' } }))).toBeNull();
		});

		it('a row with a unit keeps the former rule: its unit factorised with its period and source', () => {
			const ligne = lignesAffichees(structuredClone(DEUX)).find((item) => item.cle === 'revenu_median')!;
			expect(factoriserMetadonnees(ligne)?.unite).toBe('€/UC/an');
			expect(factoriserMetadonnees({ ...ligne, unite: null })).toBeNull();
		});

		it('on the frozen answers, a factorised row always has every cell a value with the same period, nature, source and unit', () => {
			const lignes = Object.values(REPONSES).flatMap((reponse) => lignesAffichees(structuredClone(reponse)));
			const factorisees = lignes.filter((ligne) => factoriserMetadonnees(ligne));
			expect(factorisees.some((ligne) => ligne.unite === null)).toBe(true);
			for (const ligne of factorisees) {
				const [premiere] = ligne.cellules;
				for (const cellule of ligne.cellules) {
					expect(cellule.etat?.kind, ligne.cle).toBe('valeur');
					expect(cellule.etat?.kind === 'valeur' ? cellule.etat.unite : 'absence', ligne.cle).toBe(ligne.unite);
					expect([cellule.periode, cellule.naturePeriode, cellule.source], ligne.cle).toEqual([premiere.periode, premiere.naturePeriode, premiere.source]);
				}
			}
			// The three communes with one out of coverage: no row is a value in every column, none is factorised.
			expect(lignesAffichees(structuredClone(TROIS)).filter((ligne) => factoriserMetadonnees(ligne))).toEqual([]);
		});
	});

	it('shows the gap served by the API, even when it differs from the subtraction', () => {
		const reponse = structuredClone(DEUX);
		const revenu = reponse.donnees.criteres.find(critere => critere.cle === 'revenu_median')!;
		const [a, b] = revenu.cellules;
		revenu.cellules[1] = { ...b, ecart: { valeur: 999, unite: 'euro_per_uc_year' } };
		expect((b.valeur as number) - (a.valeur as number)).not.toBe(999);
		const ligne = lignesAffichees(reponse).find(item => item.cle === 'revenu_median')!;
		expect(ligne.cellules[1].ecart).toEqual({ kind: 'ecart', texte: '+999 €/UC/an par rapport à Ancienne' });
		// Column 1 is the reference: it carries no gap line.
		expect(ligne.cellules[0].ecart).toBeNull();
	});

	it('writes a negative served gap with the minus sign U+2212, never the hyphen (V-B C-06, D-15 WR-04)', () => {
		const reponse = structuredClone(DEUX);
		const revenu = reponse.donnees.criteres.find(critere => critere.cle === 'revenu_median')!;
		revenu.cellules[1] = { ...revenu.cellules[1], ecart: { valeur: -1956, unite: 'euro_per_uc_year' } };
		const ligne = lignesAffichees(reponse).find(item => item.cle === 'revenu_median')!;
		const texte = ligne.cellules[1].ecart?.texte ?? '';
		expect(texte).toBe(`−1${' '}956 €/UC/an par rapport à Ancienne`);
		expect(texte).not.toContain('-');
	});

	it('keeps the exact inclusive DVF window on the served apartment median', () => {
		const ligne = lignesAffichees(TROIS).find(item => item.cle === 'prix_m2_median_appartement')!;
		const [publiee, sousSeuil, horsCouverture] = ligne.cellules;

		expect(publiee.etat).toEqual({ kind: 'valeur', texte: `3${' '}040`, unite: '€/m²' });
		expect(publiee.periode).toBe('2024-06-01 → 2025-06-01');
		expect(publiee.periode).not.toBe('Période de mesure non renseignée');
		expect(sousSeuil.etat).toMatchObject({ kind: 'absence', etat: 'insufficient_sample' });
		expect(sousSeuil.periode).toBeNull();
		expect(horsCouverture.etat).toMatchObject({ kind: 'absence', etat: 'out_of_coverage' });
		expect(horsCouverture.periode).toBeNull();
		expect(TROIS.donnees.communes[0].synthese).toMatchObject({ prix_m2_median_appartement: 3040, nb_mutations_12m_appartement: 5 });
		expect(TROIS.donnees.communes[1].synthese).toMatchObject({ prix_m2_median_appartement: null, nb_mutations_12m_appartement: 4 });
		expect(TROIS.donnees.communes[2].synthese).toBeNull();
	});
	it('copies the served reason and verdict without judging periods itself', () => {
		const reponse = structuredClone(DEUX);
		const pauvrete = reponse.donnees.criteres.find(critere => critere.cle === 'taux_pauvrete')!;
		// Same value and period on both sides: only the served reason says the gap is not computed.
		pauvrete.cellules[1] = { ...pauvrete.cellules[0], code_insee: '44992', ecart: null,
			motif_non_calcul: 'periodes_differentes', identique_a_la_reference: false };
		pauvrete.identiques = false;
		const ligne = lignesAffichees(reponse).find(item => item.cle === 'taux_pauvrete')!;
		expect(ligne.cellules[1].ecart).toEqual({ kind: 'motif', texte: 'Écart non calculé · périodes différentes' });
		expect(ligne.identiques).toBe(false);
	});

	it('keeps columns in the served order and reports unknown codes as served', () => {
		const model = composeComparaison(served());
		expect(model.colonnes.map(item => item.code)).toEqual(['44109', '85047']);
		expect(model.codesInconnus).toEqual(['99999']);
		expect(model.codesHorsCouverture).toEqual([]);
	});

	// 04.1-06 report, UI-031 (D-123): an overseas collectivity is out of coverage, not an altered link.
	it('keeps the out-of-coverage collectivities apart from the unknown codes, without a column', () => {
		const reponse = {
			...served(),
			codes_hors_couverture: [
				{ code_insee: '97701', hebergement: { etat: 'hors_couverture' as const, motif: 'territoire_hors_perimetre' as const, departements: ['44', '85'] } }
			]
		};
		const model = composeComparaison(reponse);
		expect(model.colonnes.map(item => item.code)).toEqual(['44109', '85047']);
		expect(model.codesInconnus).toEqual(['99999']);
		expect(model.codesHorsCouverture).toEqual(['97701']);
	});

	it('refuses a criterion whose cells do not line up with the columns', () => {
		const reponse = served();
		reponse.criteres[0].cellules.reverse();
		expect(() => composeComparaison(reponse)).toThrow(/column/i);
		const courte = served();
		courte.criteres[1].cellules.pop();
		expect(() => composeComparaison(courte)).toThrow(/column/i);
	});

	it('builds one column per served commune, the out-of-coverage one with its served state only', () => {
		const model = composeComparaison(TROIS.donnees, TROIS.meta?.source ?? null);
		expect(model.colonnes.map(item => item.code)).toEqual(['44991', '44992', '85991']);
		expect(model.codesInconnus).toEqual(['44999']);
		const [premiere, deuxieme, horsCouverture] = model.colonnes;
		expect(premiere.grille?.column.code).toBe('44991');
		expect(deuxieme.grille?.column.code).toBe('44992');
		expect(horsCouverture).toMatchObject({ horsCouverture: true, grille: null });
		const lignes = model.groupes.flatMap(groupe => groupe.lignes);
		expect(lignes.length).toBeGreaterThan(50);
		for (const ligne of lignes) {
			expect(ligne.cellules.map(cellule => cellule.code)).toEqual(['44991', '44992', '85991']);
			expect(ligne.cellules[2].etat).toMatchObject({ kind: 'absence', etat: 'out_of_coverage', motif: 'territoire_hors_perimetre' });
			expect(ligne.cellules[2].source).toBeNull();
			expect(ligne.cellules[2].ecart).toEqual({ kind: 'motif', texte: 'Écart non calculé · commune hors couverture' });
		}
	});

	it('joins each served criterion to the fiche row of the same value, verdict copied as served', () => {
		const lignes = lignesAffichees(DEUX);
		const revenu = lignes.find(ligne => ligne.cle === 'revenu_median')!;
		const servi = DEUX.donnees.criteres.find(critere => critere.cle === 'revenu_median')!;
		expect(revenu).toMatchObject({ ligne: 'revenus.revenu_median', fieldId: 'revenus.revenu_median', libelle: 'Revenu déclaré médian' });
		expect(revenu.cellules.map(cellule => cellule.etat?.kind)).toEqual(['valeur', 'valeur']);
		expect(revenu.cellules.map(cellule => cellule.brut[0].valeur)).toEqual(servi.cellules.map(cellule => JSON.stringify(cellule.valeur)));
		expect(revenu.identiques).toBe(servi.identiques);
		const quantiles = lignes.filter(ligne =>
			/^indicateurs_dvf\.residentiel_maison\.courante\.quantiles\.p(10|50|90)$/.test(ligne.cle));
		expect(quantiles).toHaveLength(3);
		expect(new Set(quantiles.map(ligne => ligne.ligne))).toEqual(new Set(['dvf-maison-quantiles-courants']));
		expect(quantiles.map(ligne => ligne.libelle.split(' · ').at(-1))).toEqual(['1er décile (P10)', 'médiane (P50)', '9e décile (P90)']);
	});

	it('lists apart the served criteria no fiche row shows, and displays each other one once', () => {
		const model = composeComparaison(DEUX.donnees, DEUX.meta?.source ?? null);
		const affichees = model.groupes.flatMap(groupe => groupe.lignes).map(ligne => ligne.cle);
		expect(new Set(affichees).size).toBe(affichees.length);
		expect([...affichees, ...model.criteresNonAffiches].sort()).toEqual(DEUX.donnees.criteres.map(critere => critere.cle).sort());
		expect(model.criteresNonAffiches).toContain('indicateurs_dpe.resultat.population.types_batiment.maison');
		expect(model.criteresNonAffiches).toContain('indicateurs_dvf.residentiel_maison.precedente.quantiles.p10');
	});

	it('gives every value row of the frozen responses a valuePath that exists in the response', () => {
		let checked = 0;
		for (const synthese of syntheses()) {
			for (const row of mapCommuneToFiche(synthese).themes.flatMap(theme => theme.rows)) {
				if (row.kind === 'context' || LIGNES_NON_SERVIES.has(row.fieldId ?? '')) {
					expect(row.valuePath).toBeUndefined();
					continue;
				}
				expect(row.valuePath, `${row.label} (${row.fieldId})`).toBeDefined();
				expect(existe(synthese, row.valuePath!), row.valuePath).toBe(true);
				checked++;
			}
		}
		expect(checked).toBeGreaterThan(100);
	});

	it('uses as valuePath the key of the served criterion of the same field', () => {
		const criteres = new Map(DEUX.donnees.criteres.map(critere => [critere.cle, critere]));
		const synthese = DEUX.donnees.communes[0].synthese!;
		let joined = 0;
		for (const row of mapCommuneToFiche(synthese).themes.flatMap(theme => theme.rows)) {
			const critere = row.valuePath ? criteres.get(row.valuePath) : undefined;
			if (!critere || !row.fieldId) continue;
			expect(row.fieldId, row.valuePath).toBe(critere.field_id);
			joined++;
		}
		expect(joined).toBeGreaterThan(30);
	});
});

// Arithmetic on a served value, a value equality test (the API serves `identiques`) or a comparison
// of periods, in the modules that present the comparison (D-48, D-51). A textual net only: the proof
// that nothing is recomputed stays the behavioural test above (the shown gap is the served one).
const CALCUL_INTERDIT: readonly RegExp[] = [
	/\.(?:valeur|ecart)\b\s*[-+*/%]/,
	/[-+*/%]\s*[\w.?]*\.(?:valeur|ecart)\b/,
	/\.valeur\b\s*[!=]==?/,
	/\.periode\b\s*[!=<>]=?/,
	/[!=<>]=?\s*[\w.?]*\.periode\b/,
	/\bMath\./,
	/\bNumber\(/,
	/\bparseFloat\(/
];

/**
 * Modules that present the served comparison, relative to `frontend/`: the composition, the table
 * of the comparator and the `/comparer` page and load (03-10), under the same guard.
 */
const MODULES_SANS_CALCUL: readonly string[] = [
	'src/lib/data/commune-comparison.ts',
	'src/lib/components/compare/CompareTable.svelte',
	'src/routes/(marketing)/comparer/+page.svelte',
	'src/routes/(marketing)/comparer/+page.ts'
];

function calculs(source: string): string[] {
	return source.split('\n').filter(line => CALCUL_INTERDIT.some(pattern => pattern.test(line)));
}

describe('the comparison presentation computes nothing (D-51)', () => {
	it.each(MODULES_SANS_CALCUL)('%s: no arithmetic or equality on a cell value, no period test', (file) => {
		const source = readFileSync(resolve(process.cwd(), file), 'utf8');
		expect(source.length).toBeGreaterThan(0);
		expect(calculs(source)).toEqual([]);
	});

	it('the listed modules include the composition itself', () => {
		const source = readFileSync(resolve(process.cwd(), MODULES_SANS_CALCUL[0]), 'utf8');
		expect(source).toContain('export function composeComparaison');
	});

	it('the guard catches a recomputed gap, a judged equality or a judged period', () => {
		expect(calculs('const ecart = b.servie.valeur - a.servie.valeur;')).toHaveLength(1);
		expect(calculs('const same = cellule.valeur === reference.valeur;')).toHaveLength(1);
		expect(calculs('if (a.valeur != b.valeur) return;')).toHaveLength(1);
		expect(calculs('if (cellule.periode !== reference.periode) return null;')).toHaveLength(1);
		expect(calculs('const x = Math.abs(v);')).toHaveLength(1);
		expect(calculs('return { servie, fiche };')).toEqual([]);
	});
});

// UI-032 in the comparator (GPT review of lot 1, 2026-10-24): a commune outside the qualified build
// serves its DVF and DPE blocks absent; the other commune keeps its indicator rows, whatever the order.
describe('composeComparaison — a commune outside the qualified build, in either column', () => {
	const ABSENT = { etat: 'not_qualified', motif: 'commune_hors_build_qualifie' } as const;
	const BLOCS = ['indicateurs_dvf', 'indicateurs_dpe'] as const;

	function horsBuild(position: number): ReponseApiComparaison {
		const reponse = structuredClone(DEUX);
		const commune = reponse.donnees.communes[position];
		for (const bloc of BLOCS) (commune.synthese as unknown as Record<string, unknown>)[bloc] = ABSENT;
		for (const critere of reponse.donnees.criteres) {
			if (!BLOCS.some(bloc => critere.cle.startsWith(`${bloc}.`))) continue;
			critere.cellules[position] = { ...critere.cellules[position], etat: 'not_qualified', valeur: null, ecart: null };
			critere.identiques = false;
		}
		return reponse;
	}

	const indicateurs = (reponse: ReponseApiComparaison) =>
		lignesAffichees(reponse).filter(ligne => BLOCS.some(bloc => ligne.cle.startsWith(`${bloc}.`)));

	it('shows the same indicator rows, under the same row and label, with the commune outside the build first or second', () => {
		const lignes = (reponse: ReponseApiComparaison) =>
			indicateurs(reponse).map(({ cle, ligne, libelle }) => ({ cle, ligne, libelle }));
		const attendues = lignes(DEUX);
		expect(attendues.length).toBeGreaterThan(0);
		expect(new Set(attendues.map(item => item.cle)).size).toBe(attendues.length);
		expect(lignes(horsBuild(1))).toEqual(attendues);
		expect(lignes(horsBuild(0))).toEqual(attendues);
	});

	it('the commune outside the build reads « Non qualifié » in each of these rows, the other its served state', () => {
		const servies = new Map(indicateurs(DEUX).map(ligne => [ligne.cle, ligne]));
		for (const position of [0, 1]) {
			for (const ligne of indicateurs(horsBuild(position))) {
				expect(ligne.cellules[position].etat).toMatchObject({ kind: 'absence', etat: 'not_qualified' });
				expect(ligne.cellules[1 - position].etat).toEqual(servies.get(ligne.cle)!.cellules[1 - position].etat);
			}
		}
	});
});

describe('composeComparaison — a blank is never a value (CR-02)', () => {
	const MODES = 'indicateurs_dpe.resultat.modes';

	function avecCellule(cle: string, patch: Record<string, unknown>) {
		const reponse = structuredClone(DEUX);
		const critere = reponse.donnees.criteres.find(item => item.cle === cle)!;
		critere.cellules[1] = { ...critere.cellules[1], ...patch } as typeof critere.cellules[1];
		critere.identiques = false;
		const ligne = lignesAffichees(reponse).find(item => item.cle === cle);
		if (!ligne) throw new Error(`ligne ${cle} non affichée`);
		return ligne;
	}

	it('a served value the formatter refuses is never an outage: no state, named on the console (03-17 C5)', () => {
		const erreur = vi.spyOn(console, 'error').mockImplementation(() => {});
		try {
			const ligne = avecCellule('revenu_median', {
				etat: 'value', valeur: 'pas un nombre', ecart: null, motif_non_calcul: 'non_numerique',
				identique_a_la_reference: false
			});
			expect(ligne.cellules[1].etat).toBeNull();
			expect(JSON.stringify(ligne.cellules[1])).not.toContain('temporary_error');
			expect(erreur.mock.calls.some(([message]) => String(message).includes('valeur refusée par le formateur'))).toBe(true);
		} finally {
			erreur.mockRestore();
		}
	});

	it('an empty list served as a value is refused: « Valeur non affichable », named on the console', () => {
		const erreur = vi.spyOn(console, 'error').mockImplementation(() => {});
		try {
			const ligne = avecCellule(MODES, {
				etat: 'value', valeur: [], ecart: null, motif_non_calcul: 'non_numerique',
				identique_a_la_reference: false
			});
			expect(ligne.cellules[1].etat).toBeNull();
			expect(ligne.cellules[1].brut).toEqual([]);
			expect(erreur.mock.calls.some(([message]) => String(message).includes('liste servie vide'))).toBe(true);
		} finally {
			erreur.mockRestore();
		}
	});

	it('the served zero_denominator absence reads « Sans base de calcul », like the fiche', () => {
		const periode = DEUX.donnees.criteres.find(item => item.cle === MODES)!.cellules[1].periode;
		const ligne = avecCellule(MODES, {
			etat: 'zero_denominator', valeur: null, periode, ecart: null,
			motif_non_calcul: 'donnee_absente', identique_a_la_reference: false
		});
		const etat = ligne.cellules[1].etat;
		expect(etat).toMatchObject({ kind: 'absence', etat: 'zero_denominator' });
		if (etat?.kind !== 'absence') throw new Error('absence attendue');
		expect(libelleEtat(etat.etat, etat.motif)).toBe(LIBELLES_ETATS.zero_denominator);
		expect(ligne.cellules[1].ecart).toEqual({ kind: 'motif', texte: 'Écart non calculé · donnée absente' });
	});

	it('an empty text served as a value is refused, never rendered blank', () => {
		const erreur = vi.spyOn(console, 'error').mockImplementation(() => {});
		try {
			const ligne = avecCellule('indicateurs_dpe.resultat.mediane_ordinale', { etat: 'value', valeur: '' });
			expect(ligne.cellules[1].etat).toBeNull();
			expect(erreur.mock.calls.some(([message]) => String(message).includes('texte servi vide'))).toBe(true);
		} finally {
			erreur.mockRestore();
		}
	});
});

// --- 04-07: addresses compared, the groups served by the API rendered as served (CMP-01) ---------

describe('composeComparaisonAdresses — the served groups, as served (04-07)', () => {
	type Brut = Parameters<typeof projeterComparaisonAdresses>[0];
	const reponse = (cle: 'deux_adresses' | 'trois_dont_hors_couverture') =>
		projeterComparaisonAdresses(structuredClone((adressesServies as Record<string, { donnees: Brut }>)[cle].donnees));

	it('keeps the four served groups in their order, the commune of the address last and flagged', () => {
		const modele = composeComparaisonAdresses(reponse('deux_adresses'));
		expect(modele.nature).toBe('adresse');
		expect(modele.groupes.map((groupe) => [groupe.cle, groupe.titre, groupe.reference])).toEqual([
			['documents', "Documents à l'adresse", false],
			['ventes', 'Ventes', false],
			['lieu', 'Lieu', false],
			['commune_reference', "Commune de l'adresse (référence)", true]
		]);
		expect(modele.colonnes.map((colonne) => [colonne.code, colonne.nom, colonne.identite, colonne.lien])).toEqual([
			['85191_0420_00012', '12 Rue des Lilas', '85000 La Roche-sur-Yon', '/adresse/85191_0420_00012'],
			['85191_0601_00001', '1 Rue du Lieu', '85000 La Roche-sur-Yon', '/adresse/85191_0601_00001']
		]);
	});

	it('recopies the served gap, reason and verdict; a categorical value says only Identique / Différent', () => {
		const servie = reponse('deux_adresses');
		const contenance = servie.groupes[2].lignes.find((ligne) => ligne.cle === 'contenance')!;
		// A served gap that is NOT the subtraction of the values: recopied as served.
		contenance.cellules[1].ecart = { valeur: 999, unite: 'm2' };
		const lignes = composeComparaisonAdresses(servie).groupes.flatMap((groupe) => groupe.lignes);
		const ligne = (cle: string) => lignes.find((l) => l.cle === cle)!;
		expect(ligne('contenance').cellules[1].ecart).toEqual({
			kind: 'ecart',
			texte: '+999 m² par rapport à 12 Rue des Lilas'
		});
		expect(ligne('argiles').cellules[1].ecart).toEqual({ kind: 'verdict', texte: 'Différent' });
		expect(ligne('fibre').cellules[1].ecart).toEqual({ kind: 'verdict', texte: 'Identique' });
		expect(ligne('arret_proche').cellules[1].ecart).toEqual({ kind: 'ecart', texte: '+200 m par rapport à 12 Rue des Lilas' });
		expect(ligne('ventes_parcelle').cellules[1].ecart).toEqual({ kind: 'motif', texte: 'Écart non calculé · donnée absente' });
		expect(ligne('ventes_parcelle').cellules[1].etat).toMatchObject({ kind: 'absence', etat: 'not_available', motif: 'point_hors_parcelle' });
		expect(lignes.every((l) => l.cellules[0].ecart === null)).toBe(true);
	});

	it('a list of zones read in part says why no verdict is served (D-15 API WR-04)', () => {
		const servie = reponse('deux_adresses');
		const zone = servie.groupes[2].lignes.find((ligne) => ligne.cle === 'zone_plu')!;
		zone.cellules[1].verdict = null;
		zone.cellules[1].motif_non_calcul = 'liste_incomplete';
		const ligne = composeComparaisonAdresses(servie)
			.groupes.flatMap((groupe) => groupe.lignes)
			.find((l) => l.cle === 'zone_plu')!;
		expect(ligne.cellules[1].ecart).toEqual({ kind: 'motif', texte: 'Écart non calculé · liste incomplète' });
	});

	it('words each value as the address page does: no technical key, no recomputation', () => {
		const lignes = composeComparaisonAdresses(reponse('deux_adresses')).groupes.flatMap((groupe) => groupe.lignes);
		const valeur = (cle: string, colonne = 0) => {
			const etat = lignes.find((l) => l.cle === cle)!.cellules[colonne].etat;
			return etat?.kind === 'valeur' ? `${etat.texte}${etat.unite ? ` ${etat.unite}` : ''}` : etat;
		};
		expect(lignes.map((l) => l.libelle)).toEqual([
			'DPE déclarés',
			'Le plus récent',
			'Ventes sur la parcelle',
			'Dernière vente',
			'Ventes déclarées à cette adresse',
			'Hauteur du bâtiment',
			'Contenance de la parcelle',
			'Argiles au point',
			'Zone du PLU au point',
			'Éligibilité à la fibre',
			'Arrêt le plus proche',
			'Prix médian dans la commune · Maisons',
			'Prix médian dans la commune · Appartements'
		]);
		expect(valeur('dpe_plus_recent')).toBe('Classe D · mars 2024');
		expect(valeur('dpe_plus_recent', 1)).toBe('Classe D · mars 2024 · expiré');
		expect(valeur('derniere_vente')).toBe('juin 2024');
		expect(valeur('fibre')).toBe('Oui');
		expect(valeur('zone_plu')).toBe('UB · Zone urbaine');
		expect(valeur('arret_proche')).toBe("Arrêt 100 · à 100 m (à vol d'oiseau, pas un temps de trajet)");
		expect(String(valeur('contenance')).replace(/\s/g, ' ')).toBe('1 412 m²');
	});

	it('« Le plus récent » carries its public definition in its panel (04-DECISIONS-TEXTES row 53)', () => {
		const lignes = composeComparaisonAdresses(reponse('deux_adresses')).groupes.flatMap((groupe) => groupe.lignes);
		const volet = lignes.find((l) => l.cle === 'dpe_plus_recent')!.cellules[0].volet;
		expect(volet.find((entree) => entree.terme === 'Définition')).toEqual({
			terme: 'Définition',
			valeur:
				"Diagnostic de performance énergétique (DPE) dont la date d'établissement est la plus récente parmi ceux déclarés à cette adresse. Il n'est rattaché à aucun logement."
		});
	});

	it('names a tie without any document, keeps an address out of coverage and reports the dropped key', () => {
		const modele = composeComparaisonAdresses(reponse('trois_dont_hors_couverture'));
		expect(modele.codesInconnus).toEqual(['85191_9999_00001']);
		const [, , hors] = modele.colonnes;
		expect(hors).toMatchObject({ horsCouverture: true, nom: 'Adresse à Rennes (35)', identite: 'Rennes' });
		const plusRecent = modele.groupes[0].lignes.find((l) => l.cle === 'dpe_plus_recent')!;
		expect(plusRecent.cellules[1].etat).toEqual({ kind: 'valeur', texte: '2 documents à la même date · mars 2024', unite: null });
		for (const ligne of modele.groupes.flatMap((groupe) => groupe.lignes)) {
			expect(ligne.cellules[2].etat).toMatchObject({ kind: 'absence', etat: 'out_of_coverage' });
			expect(ligne.cellules[2].ecart).toEqual({ kind: 'motif', texte: 'Écart non calculé · adresse hors couverture' });
			expect(ligne.identiques).toBe(false);
		}
	});

	it('refuses a row whose cells do not line up with the columns', () => {
		const servie = reponse('deux_adresses');
		servie.groupes[1].lignes[0].cellules.reverse();
		expect(() => composeComparaisonAdresses(servie)).toThrow(/out of its columns/);
	});
});

describe('the declared sales in the address comparator (C40) and two equal values (C41)', () => {
	type BrutAdresses = Parameters<typeof projeterComparaisonAdresses>[0];
	type BrutDocuments = Parameters<typeof projeterComparaisonDocuments>[0];
	const servieAdresses = (cle: string) =>
		projeterComparaisonAdresses(structuredClone((adressesServies as Record<string, { donnees: BrutAdresses }>)[cle].donnees));
	const ligneDe = <L extends { cle: string }>(modele: { groupes: { lignes: L[] }[] }, cle: string): L =>
		modele.groupes.flatMap((groupe) => groupe.lignes).find((ligne) => ligne.cle === cle)!;

	it('C40: « Ventes déclarées à cette adresse » follows « Dernière vente », with its period and source', () => {
		const modele = composeComparaisonAdresses(servieAdresses('trois_dont_hors_couverture'));
		const ventes = modele.groupes.find((groupe) => groupe.cle === 'ventes')!;
		expect(ventes.lignes.map((ligne) => ligne.libelle)).toEqual([
			'Ventes sur la parcelle',
			'Dernière vente',
			'Ventes déclarées à cette adresse'
		]);
		const [deux, suffixe, hors] = ligneDe(modele, 'ventes_declarees').cellules;
		expect(deux.etat).toMatchObject({ kind: 'valeur', texte: '2' });
		expect(deux.periode).toContain('2021-2025');
		expect(deux.source?.nom).toBeTruthy();
		// The suffix is said as the address page says it, never a zero nor a bare « not available ».
		expect(suffixe.etat).toMatchObject({
			kind: 'absence',
			etat: 'not_available',
			motif: 'suffixe_non_distingue',
			libelle: 'Suffixe non distingué',
			// C44: the sentence of the page, never the glossary's « La source ne publie pas… ».
			aide: expect.stringMatching(/^Les données DVF importées ne gardent pas le suffixe/)
		});
		expect(suffixe.ecart).toEqual({ kind: 'motif', texte: 'Écart non calculé · donnée absente' });
		expect(hors.etat).toMatchObject({ kind: 'absence', etat: 'out_of_coverage' });
	});

	it('C3 (04.5-08): the reasons of the number and suffix rule keep the label and the sentence of the page in C40', () => {
		const attendus = {
			suffixe_sans_correspondance: ['Suffixe sans correspondance', /^Le suffixe de cette adresse ne correspond pas de façon sûre/],
			suffixe_non_importe: ['Suffixe non disponible', /^Les données DVF servies ont été importées sans le suffixe/]
		} as const;
		for (const [motif, [libelle, aide]] of Object.entries(attendus)) {
			const servie = servieAdresses('trois_dont_hors_couverture');
			const ligne = ligneDe(servie, 'ventes_declarees');
			Object.assign(ligne.cellules[1], { etat: 'not_available', motif, valeur: null, periode: null });
			const [, cellule] = ligneDe(composeComparaisonAdresses(servie), 'ventes_declarees').cellules;
			expect(cellule.etat).toMatchObject({ kind: 'absence', etat: 'not_available', motif, libelle, aide: expect.stringMatching(aide) });
		}
	});

	// Second Codex review of 04.5-08 (D45-01, D45-08): the reason without wording replaces the label
	// only; the served absence stays typed, with its reason and its raw data (the Source button).
	it('Codex review M1 (04.5-08): a served reason of C40 without wording keeps its typed absence, its reason and its raw data, under « Valeur non affichable », marked provisional, without a sentence, named on the console', () => {
		const erreurs = vi.spyOn(console, 'error').mockImplementation(() => {});
		try {
			const servie = servieAdresses('trois_dont_hors_couverture');
			const servi = ligneDe(servie, 'ventes_declarees').cellules[1];
			Object.assign(servi, { etat: 'not_available', motif: 'suffixe_futur', valeur: null, periode: null });
			const [, cellule] = ligneDe(composeComparaisonAdresses(servie), 'ventes_declarees').cellules;
			expect(cellule.etat).toEqual({
				kind: 'absence',
				etat: 'not_available',
				motif: 'suffixe_futur',
				effectif: null,
				libelle: 'Valeur non affichable',
				provisoire: true
			});
			expect(cellule.brut).toEqual([
				{ fieldId: servi.field_id, valeur: 'null', etat: { kind: 'absence', etat: 'not_available', motif: 'suffixe_futur' } }
			]);
			const etatEtMotif = cellule.volet.find((entree) => entree.terme === 'État et motif');
			expect(etatEtMotif && 'absences' in etatEtMotif ? etatEtMotif.absences.map((absence) => absence.etat) : []).toEqual([cellule.etat]);
			expect(erreurs).toHaveBeenCalledWith(expect.stringContaining('motif sans libellé (suffixe_futur)'));
		} finally {
			erreurs.mockRestore();
		}
	});

	it('C44: an address without any sale attached says so, never the figure 0, a gap nor « Identique »', () => {
		const [une, aucune] = ligneDe(composeComparaisonAdresses(servieAdresses('deux_designations')), 'ventes_declarees').cellules;
		expect(une.etat).toMatchObject({ kind: 'valeur', texte: '1' });
		expect(aucune.etat).toMatchObject({
			kind: 'absence',
			etat: 'not_available',
			motif: 'aucune_vente_rattachee',
			libelle: 'Aucune vente rattachée',
			aide: "Aucune vente de DVF n'est rattachée à cette adresse\u202f; ce n'est pas la preuve qu'aucune vente n'y a eu lieu."
		});
		expect(aucune.ecart).toEqual({ kind: 'motif', texte: 'Écart non calculé · donnée absente' });
	});

	it('C41: two equal served values say « Identique » in the three comparators, never « 0 … par rapport à »', () => {
		// Addresses: the frozen response serves two equal counts (3 and 3).
		const adresses = composeComparaisonAdresses(servieAdresses('deux_adresses'));
		expect(ligneDe(adresses, 'ventes_declarees').cellules[1].ecart).toEqual({ kind: 'verdict', texte: 'Identique' });

		// Communes: a served gap of 0 between two values the API serves as the same.
		const reponse = structuredClone(DEUX);
		const revenu = reponse.donnees.criteres.find((critere) => critere.cle === 'revenu_median')!;
		revenu.cellules[1] = { ...revenu.cellules[1], ecart: { valeur: 0, unite: 'euro_per_uc_year' }, identique_a_la_reference: true };
		expect(lignesAffichees(reponse).find((item) => item.cle === 'revenu_median')!.cellules[1].ecart).toEqual({
			kind: 'verdict',
			texte: 'Identique'
		});
		// A served 0 between two values the API does not serve as the same (a rounded gap) stays a gap.
		revenu.cellules[1] = { ...revenu.cellules[1], identique_a_la_reference: false };
		expect(lignesAffichees(reponse).find((item) => item.cle === 'revenu_median')!.cellules[1].ecart).toEqual({
			kind: 'ecart',
			texte: '0 €/UC/an par rapport à Ancienne'
		});

		// Documents: the same rule on a figure of the same method.
		const documents = projeterComparaisonDocuments(
			structuredClone((documentsServis as Record<string, { donnees: BrutDocuments }>).meme_methode_trois.donnees)
		);
		const cout = documents.groupes.flatMap((groupe) => groupe.lignes).find((ligne) => ligne.cle === 'cout_annuel')!;
		cout.cellules[1] = { ...cout.cellules[1], ecart: { valeur: 0, unite: 'euro_per_year' }, identique_a_la_reference: true };
		expect(ligneDe(composeComparaisonDocuments(documents), 'cout_annuel').cellules[1].ecart).toEqual({
			kind: 'verdict',
			texte: 'Identique'
		});
	});
});

// --- 04-10: DPE documents (CMP-02, D-116): the served groups, worded as the document page ----------

describe('composeComparaisonDocuments — the served groups, as served (04-10)', () => {
	type Brut = Parameters<typeof projeterComparaisonDocuments>[0];
	const documents = (cle: 'ancien_et_actuel' | 'meme_methode_trois') =>
		composeComparaisonDocuments(
			projeterComparaisonDocuments(structuredClone((documentsServis as Record<string, { donnees: Brut }>)[cle].donnees))
		);
	const cellule = (modele: ReturnType<typeof documents>, cle: string, position: number) =>
		modele.groupes.flatMap((groupe) => groupe.lignes).find((ligne) => ligne.cle === cle)!.cellules[position];

	it('one column per document, « DPE n° {numéro} » linked to its page, its declared address and badges', () => {
		const modele = documents('ancien_et_actuel');
		expect(modele.nature).toBe('dpe');
		expect(modele.colonnes.map((c) => [c.nom, c.nomBande, c.lien, c.identite, c.badges])).toEqual([
			['DPE n° 2185E0000001A', '2185E0000001A', '/dpe/2185E0000001A', 'Adresse déclarée : « 12 rue des Lilas »', ['Ancienne méthode']],
			['DPE n° 2385E0123456A', '2385E0123456A', '/dpe/2385E0123456A', 'Adresse déclarée non affichée', []]
		]);
		expect(modele.groupes.map((groupe) => [groupe.cle, groupe.titre])).toEqual([
			['etiquettes', 'Étiquettes'],
			['logement', 'Logement décrit'],
			['cout_validite', 'Coût et validité'],
			['adresse', 'Adresse']
		]);
	});

	it('task 6: classes and figures of two methods say why no gap is computed, in warn', () => {
		const modele = documents('ancien_et_actuel');
		for (const cle of ['classe_energie', 'consommation_m2', 'classe_climat', 'emissions_m2', 'consommation', 'emissions']) {
			expect(cellule(modele, cle, 1).ecart).toEqual({ kind: 'motif', texte: 'Écart non calculé · méthodes de calcul différentes' });
		}
		expect(cellule(modele, 'type_batiment', 1).ecart).toEqual({ kind: 'verdict', texte: 'Identique' });
		expect(cellule(modele, 'cout_annuel', 1).ecart).toEqual({ kind: 'ecart', texte: '+200 €/an par rapport à DPE n° 2185E0000001A' });
		expect(cellule(modele, 'methode', 0).etat).toEqual({ kind: 'valeur', texte: 'Ancienne méthode', unite: null });
	});

	it('the yearly totals keep their true unit, never divided by the surface', () => {
		const modele = documents('meme_methode_trois');
		expect(cellule(modele, 'consommation', 0).etat).toEqual({ kind: 'valeur', texte: '17 830', unite: 'kWhEP/an' });
		expect(cellule(modele, 'consommation', 1).ecart?.texte).toBe('−5 830 kWhEP/an par rapport à DPE n° 2385E0123456A');
		expect(cellule(modele, 'classe_energie', 1).ecart).toEqual({ kind: 'verdict', texte: 'Différent' });
		// The third document has no establishment date: no verdict on its classes.
		expect(cellule(modele, 'classe_energie', 2).ecart?.texte).toBe('Écart non calculé · méthode non déterminée');
		expect(modele.codesInconnus).toEqual(['2385E9999999X']);
	});

	// R5 (plan 04.5-13): the per-m² measures published by the ADEME at first level, the yearly totals
	// after them under their own fields; nothing is divided by the surface, every gap is the served one.
	it('R5: the label rows read the per-m² measures first, the yearly totals after them, each under its field', () => {
		const modele = documents('meme_methode_trois');
		expect(modele.groupes[0].lignes.map((ligne) => [ligne.cle, ligne.fieldId, ligne.libelle, ligne.unite])).toEqual([
			['classe_energie', 'dpe.document.etiquette_dpe', 'Classe énergie', null],
			['consommation_m2', 'dpe.document.conso_ep_m2', 'Consommation par m²', 'kWhEP/m²/an'],
			['classe_climat', 'dpe.document.etiquette_ges', 'Classe climat', null],
			['emissions_m2', 'dpe.document.emissions_ges_m2', 'Émissions par m²', 'kg CO₂ éq/m²/an'],
			['consommation', 'dpe.document.conso_ep', "Consommation annuelle d'énergie primaire", 'kWhEP/an'],
			['emissions', 'dpe.document.emissions_ges', 'Émissions annuelles de gaz à effet de serre', 'kg CO₂ éq/an']
		]);
		expect(cellule(modele, 'consommation_m2', 0).etat).toEqual({ kind: 'valeur', texte: '231', unite: 'kWhEP/m²/an' });
		expect(cellule(modele, 'consommation_m2', 1).etat).toEqual({ kind: 'valeur', texte: '158', unite: 'kWhEP/m²/an' });
		expect(cellule(modele, 'consommation_m2', 1).ecart?.texte).toBe('−73 kWhEP/m²/an par rapport à DPE n° 2385E0123456A');
		expect(cellule(modele, 'emissions_m2', 1).etat).toEqual({ kind: 'valeur', texte: '12', unite: 'kg CO₂ éq/m²/an' });
		expect(cellule(modele, 'emissions_m2', 1).ecart?.texte).toBe('−35 kg CO₂ éq/m²/an par rapport à DPE n° 2385E0123456A');
		expect(cellule(modele, 'consommation_m2', 1).source).toEqual(cellule(modele, 'consommation', 1).source);
		expect(cellule(modele, 'consommation_m2', 1).source).not.toBeNull();
	});

	it('R5: a published zero per m² is a value; a missing one is « non renseigné », never total ÷ surface', () => {
		const modele = documents('meme_methode_trois');
		// Third document: total 17 830 kWhEP/an and 79 m² served, no energy per m², a climate zero.
		expect(cellule(modele, 'consommation', 2).etat).toEqual({ kind: 'valeur', texte: '17 830', unite: 'kWhEP/an' });
		expect(cellule(modele, 'surface', 2).etat?.kind).toBe('valeur');
		const manque = cellule(modele, 'consommation_m2', 2);
		expect(manque.etat).toEqual({ kind: 'absence', etat: 'not_reported', motif: '', effectif: null });
		expect(manque.brut).toEqual([{ fieldId: 'dpe.document.conso_ep_m2', valeur: 'null', etat: { kind: 'absence', etat: 'not_reported', motif: '' } }]);
		expect(JSON.stringify(manque)).not.toMatch(/22[56]/);
		expect(cellule(modele, 'emissions_m2', 2).etat).toEqual({ kind: 'valeur', texte: '0', unite: 'kg CO₂ éq/m²/an' });
	});

	it('R5: the gap of a per-m² row is the served one, never the difference of its cells', () => {
		type Brut = Parameters<typeof projeterComparaisonDocuments>[0];
		const brut = structuredClone((documentsServis as Record<string, { donnees: Brut }>).meme_methode_trois.donnees) as {
			groupes: { lignes: { cle: string; cellules: { ecart: { valeur: number; unite: string } | null }[] }[] }[];
		};
		brut.groupes[0].lignes.find((ligne) => ligne.cle === 'consommation_m2')!.cellules[1].ecart = { valeur: -999, unite: 'kwh_ep_per_m2_per_year' };
		const modele = composeComparaisonDocuments(projeterComparaisonDocuments(brut as never));
		expect(cellule(modele, 'consommation_m2', 1).ecart?.texte).toBe('−999 kWhEP/m²/an par rapport à DPE n° 2385E0123456A');
	});

	it('a masked declared address is worded as on the document page, never as a secret, its text never read', () => {
		const modele = documents('ancien_et_actuel');
		const masquee = cellule(modele, 'adresse_declaree', 1);
		expect(masquee.etat).toEqual({
			kind: 'valeur',
			texte: 'non affichée : le document y joint un complément (étage, porte, bâtiment ou nom).',
			unite: null
		});
		expect(masquee.ecart?.texte).toBe('Écart non calculé · adresse déclarée non affichée');
		expect(masquee.brut[0].etat).toEqual({ kind: 'absence', etat: 'suppressed', motif: 'complement_present' });
		expect(masquee.volet.at(-1)).toEqual({
			terme: 'Adresse déclarée non affichée',
			valeur: "Le texte saisi par le diagnostiqueur est conservé par DataCommune et n'est pas publié."
		});
		expect(JSON.stringify(modele)).not.toContain('Secret statistique');
	});

	it('a text the API would leak with a masked address never reaches the model', () => {
		const brut = structuredClone((documentsServis as Record<string, { donnees: Brut }>).ancien_et_actuel.donnees) as {
			colonnes: { adresse_declaree: { texte: string | null } }[];
			groupes: { lignes: { cle: string; cellules: { valeur: unknown }[] }[] }[];
		};
		brut.colonnes[1].adresse_declaree.texte = 'SENTINELLE-0410';
		brut.groupes.flatMap((g) => g.lignes).find((l) => l.cle === 'adresse_declaree')!.cellules[1].valeur = 'SENTINELLE-0410';
		const modele = composeComparaisonDocuments(projeterComparaisonDocuments(brut as never));
		expect(JSON.stringify(modele)).not.toContain('SENTINELLE');
	});

	it('refuses a row whose cells do not line up with the columns', () => {
		const brut = structuredClone((documentsServis as Record<string, { donnees: Brut }>).ancien_et_actuel.donnees) as {
			groupes: { lignes: { cellules: { numero: string }[] }[] }[];
		};
		brut.groupes[0].lignes[0].cellules[1].numero = '2485E0000003C';
		expect(() => composeComparaisonDocuments(projeterComparaisonDocuments(brut as never))).toThrow(/out of its columns/);
	});
});

// --- B6 (plan 04.5-10, UI-014): dates written with their role, never a raw ISO day or quarter ---------
//
// A served period of an address cell is written formatted. Its role is said only when a public projection
// already names it for this field_id (`ROLES_DATES`: the day of the DPE import of ①, C14); any other one is
// written under the neutral « Période : ». A value served without period reads « Période non renseignée »,
// its state untouched. The served period stays on the cell (`periode`) and in the Source panel, exactly.

describe('B6 — dates qualifiées du comparateur d’adresses et de documents (UI-014)', () => {
	type BrutAdresses = Parameters<typeof projeterComparaisonAdresses>[0];
	type BrutDocuments = Parameters<typeof projeterComparaisonDocuments>[0];
	const ADRESSES = adressesServies as Record<string, { donnees: BrutAdresses }>;
	const DOCUMENTS = documentsServis as Record<string, { donnees: BrutDocuments }>;
	const lignesAdresses = (cle: string) =>
		composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone(ADRESSES[cle].donnees))).groupes.flatMap((groupe) => groupe.lignes);
	const lignesDocuments = (cle: string) =>
		composeComparaisonDocuments(projeterComparaisonDocuments(structuredClone(DOCUMENTS[cle].donnees))).groupes.flatMap((groupe) => groupe.lignes);
	const ligne = (cle: string) => lignesAdresses('deux_adresses').find((item) => item.cle === cle)!;
	const BRUT = /\d{4}-\d{2}-\d{2}|\d{4}[-_]T\d/;
	type CelluleAdresse = ReturnType<typeof lignesAdresses>[number]['cellules'][number];
	type CelluleDocument = ReturnType<typeof lignesDocuments>[number]['cellules'][number];

	/**
	 * The one anti-ISO assertion of the comparator cells, shared by the ordinary checks and their
	 * mutation (Codex review of plan 10, MINOR): it reads the period as the table displays it —
	 * `periodeAffichee` when the model gives one, else the served period (CompareTable `periodeDe`) —
	 * and the text of a value.
	 */
	function expectAucunePeriodeBrute(cellules: { etiquette: string; cellule: CelluleAdresse | CelluleDocument }[]) {
		for (const { etiquette, cellule } of cellules) {
			const affichee = cellule.periodeAffichee !== undefined ? cellule.periodeAffichee : cellule.periode;
			expect(affichee ?? '', etiquette).not.toMatch(BRUT);
			if (cellule.etat?.kind === 'valeur') expect(cellule.etat.texte, etiquette).not.toMatch(BRUT);
		}
	}
	const cellulesAdresses = () =>
		Object.keys(ADRESSES).flatMap((cle) => lignesAdresses(cle).flatMap((item) => item.cellules.map((cellule) => ({ etiquette: `${cle} ${item.cle}`, cellule }))));
	const cellulesDocuments = () =>
		Object.keys(DOCUMENTS).flatMap((cle) => lignesDocuments(cle).flatMap((item) => item.cellules.map((cellule) => ({ etiquette: `${cle} ${item.cle}`, cellule }))));

	it('the DPE count says the day of its corpus import, the role named by the address page (C14)', () => {
		const cellules = ligne('dpe_declares').cellules;
		expect(cellules.map((cellule) => cellule.periodeAffichee)).toEqual(['Corpus importé le 30 septembre 2026', 'Corpus importé le 30 septembre 2026']);
		// The served period stays on the cell and in its Source panel, in its exact form.
		expect(cellules.map((cellule) => cellule.periode)).toEqual(['2026-09-30', '2026-09-30']);
		for (const cellule of cellules) expect(cellule.volet).toContainEqual({ terme: 'Période', valeur: '2026-09-30' });
	});

	it('a period whose role no public projection names is formatted under the neutral « Période : »', () => {
		expect(ligne('dpe_plus_recent').cellules[0].periodeAffichee).toBe('Période : 30 septembre 2026');
		expect(ligne('argiles').cellules[0].periodeAffichee).toBe('Période : 5 octobre 2026');
		expect(ligne('fibre').cellules.map((cellule) => cellule.periodeAffichee)).toEqual(['Période : 2e trimestre 2026', 'Période : 2e trimestre 2026']);
		expect(ligne('fibre').cellules[0].periode).toBe('2026-T2');
		expect(ligne('ventes_parcelle').cellules[0].periodeAffichee).toBe('Période : 2021-2025');
	});

	it('the published height served without period reads « Période non renseignée », still a value, no cause invented', () => {
		const hauteur = ligne('hauteur');
		for (const cellule of hauteur.cellules) {
			expect(cellule.etat).toMatchObject({ kind: 'valeur' });
			expect(cellule.periode).toBeNull();
			expect(cellule.periodeAffichee).toBe('Période non renseignée');
		}
	});

	it('an absence keeps its served state and reason, without any period line', () => {
		const absente = ligne('ventes_parcelle').cellules[1];
		expect(absente.etat).toMatchObject({ kind: 'absence', etat: 'not_available', motif: 'point_hors_parcelle' });
		expect(absente.periodeAffichee ?? null).toBeNull();
	});

	it('no displayed period of any address answer is a raw ISO day or quarter; the served periods are unchanged', () => {
		expectAucunePeriodeBrute(cellulesAdresses());
	});

	// Codex review of plan 10 (MINOR): the same assertion, on the cells deprived of `periodeAffichee`,
	// must fail: the table then falls back to the served period, raw. A guard reading
	// `periodeAffichee ?? ''` would read an empty period and pass.
	it('the shared assertion fails once the displayed period is removed from the address cells', () => {
		const sansAffichage = cellulesAdresses().map(({ etiquette, cellule }) => ({ etiquette, cellule: { ...cellule, periodeAffichee: undefined } }));
		expect(() => expectAucunePeriodeBrute(sansAffichage)).toThrow();
	});

	it('no value or period of a document answer is a raw ISO day or quarter', () => {
		expectAucunePeriodeBrute(cellulesDocuments());
	});

	it('the closed table of date roles names only roles a public projection already gives', () => {
		expect(ROLES_DATES).toEqual({ 'adresse.dpe.nombre_declares': 'Corpus importé le' });
	});
});

describe('composeComparaison — no variation on a begun period (04.7-15 A4)', () => {
	// Both communes serve a 3.2 % house variation whose current window ends on 2025-01-01;
	// `fin` is the end of the received DVF delivery.
	function servie(fin: string) {
		const reponse = structuredClone(DEUX);
		for (const commune of reponse.donnees!.communes) {
			const dvf = commune.synthese!.indicateurs_dvf!;
			(dvf.ancrage_temporel as { periode_fin_exclue: string }).periode_fin_exclue = fin;
			dvf.residentiel_maison!.variation_pct = { etat: 'value', valeur: 3.2 };
		}
		const critere = reponse.donnees!.criteres.find((item) => item.cle === 'indicateurs_dvf.residentiel_maison.variation_pct')!;
		critere.cellules = critere.cellules.map((cellule, position) => ({
			...cellule, etat: 'value', valeur: 3.2, motif_non_calcul: null,
			...(position === 0 ? {} : { ecart: { valeur: 0, unite: 'point' }, identique_a_la_reference: true })
		})) as typeof critere.cellules;
		return composeComparaison(reponse.donnees!, reponse.meta!.source).groupes
			.flatMap((groupe) => groupe.lignes)
			.find((ligne) => ligne.cle === critere.cle)!;
	}

	it('withholds the served variation in every cell, its raw value and its gap, like the fiche', () => {
		const ligne = servie('2024-07-01');
		for (const cellule of ligne.cellules) {
			expect(cellule.etat).toMatchObject({ kind: 'absence', etat: 'not_qualified', motif: PERIODE_ENTAMEE });
			expect(cellule.brut[0].valeur).toBe('null');
		}
		expect(ligne.cellules[1].ecart).toEqual({ kind: 'motif', texte: 'Écart non calculé · période non close' });
	});

	it('shows the served variation and its verdict once the window is closed in the delivery', () => {
		const ligne = servie('2025-01-01');
		expect(ligne.cellules[0].etat).toMatchObject({ kind: 'valeur', texte: '3,2' });
		expect(ligne.cellules[1].ecart).toEqual({ kind: 'verdict', texte: 'Identique' });
	});
});

// R-09 in the comparator: an arrondissement is served the values its commune alone publishes
// (`synthese.heritages`: ABC zone, voted tax rates, intercommunality). The fiche marks each of them
// « Commune »; the comparator showed « Abis » in the column of the 11e without saying whose value it is.
describe('composeComparaison — a value of the commune on an arrondissement keeps its mark (R-09)', () => {
	function servie() {
		const reponse = structuredClone(DEUX);
		const [arrondissement, commune] = reponse.donnees!.communes;
		arrondissement.code_insee = '75111';
		arrondissement.nom = 'Paris 11e Arrondissement';
		Object.assign(arrondissement.synthese!, {
			code_insee: '75111', zone_abc: 'Abis', type_zone: 'arrondissement', commune_parent: '75056',
			heritages: { zone_abc: { echelle: 'commune', code_insee: '75056', nom: 'Paris' } }
		});
		Object.assign(commune.synthese!, { zone_abc: 'B1' });
		for (const item of [arrondissement, commune]) {
			const etats = item.synthese!.etats as Record<string, unknown> | undefined;
			if (etats) delete etats.zone_abc;
		}
		for (const critere of reponse.donnees!.criteres) {
			critere.cellules[0].code_insee = '75111';
			if (critere.cle !== 'zone_abc') continue;
			critere.identiques = false;
			critere.cellules = [
				{ ...critere.cellules[0], etat: 'value', valeur: 'Abis' },
				{ ...critere.cellules[1], etat: 'value', valeur: 'B1', motif_non_calcul: 'non_numerique', identique_a_la_reference: false }
			] as typeof critere.cellules;
		}
		return composeComparaison(reponse.donnees!, reponse.meta!.source).groupes
			.flatMap((groupe) => groupe.lignes)
			.find((ligne) => ligne.cle === 'zone_abc')!;
	}

	it('the inherited cell carries the scale mark of the fiche and names its commune', () => {
		const [heritee] = servie().cellules;
		expect(heritee.etat).toMatchObject({ kind: 'valeur', texte: 'Abis' });
		expect(heritee.echelle).toEqual({ pictogramme: 'commune', mot: 'Commune', enClair: 'Commune de Paris' });
		expect(heritee.volet.find((entree) => entree.terme === 'Objet')).toMatchObject({ valeur: 'Commune de Paris' });
	});

	it('a value the commune publishes for itself carries no mark', () => {
		const [, propre] = servie().cellules;
		expect(propre.etat).toMatchObject({ kind: 'valeur', texte: 'B1' });
		expect(propre.echelle).toBeUndefined();
	});
});
