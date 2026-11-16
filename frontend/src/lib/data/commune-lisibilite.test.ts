// Phase 4.9, lot 3 (fiche commune): « L'essentiel » shows what is known, every absence stays in its
// theme with its state, reason and explanation, one field has one label, and the explanations written
// on the tile go to the panel, on paper and without JavaScript too.
import { describe, expect, it } from 'vitest';
import type { CommuneSynthese, IndicateursDvf, ReponseApiComparaison, SourceAttribution } from '$lib/api/commune';
import comparaisons from './__fixtures__/commune-comparaison.json';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from './__fixtures__/commune-85191-20261012';
import { COMMUNE_INDICATORS_44 } from './__fixtures__/commune-comparison-indicators';
import { aideAuNiveau1, definitionsCommune, themesCommune, themesPourExport, type EntreeDefinition, type LigneTheme } from './commune-grid';
import { composeComparaison } from './commune-comparison';
import { AUCUNE_VENTE, communeKpis, PERIODE_ENTAMEE, reperesConnus, reservesSynthese, TENDANCE_NON_PUBLIEE, type KpiCommune } from './commune-kpi';

const SANS_BLOC: CommuneSynthese = {
	...COMMUNE_85047_CHALLANS,
	indicateurs_dvf: null,
	indicateurs_dpe: null,
	fenetre_mediane_dvf: '2024-12-31 → 2025-12-31',
	prix_m2_median_maison: 2480,
	nb_mutations_12m_maison: 96,
	prix_m2_median_appartement: 3120.5,
	nb_mutations_12m_appartement: 41
};
const COMMUNE_85191 = COMMUNE_85191_OBSERVED as unknown as CommuneSynthese;
const SOURCES_85191 = COMMUNE_85191_SOURCES as unknown as SourceAttribution[];
const NON_QUALIFIE = { etat: 'not_qualified', motif: 'commune_hors_build_qualifie' } as never;

const lignes = (commune: CommuneSynthese, sources: SourceAttribution[] | null = null): LigneTheme[] =>
	themesCommune(commune, sources).flatMap((theme) => theme.lignes);
const immobilier = (commune: CommuneSynthese) => themesCommune(commune).find((theme) => theme.cle === 'immobilier')!.lignes;
const entree = (ligne: LigneTheme, terme: string) => ligne.volet.find((item) => item.terme === terme);
const blocDvf = () => structuredClone(COMMUNE_INDICATORS_44.indicateurs_dvf) as IndicateursDvf;

describe('tendance sans bloc daté : une ligne dans Immobilier (A1)', () => {
	it('keeps the label, the badge, the reason and the explanation of the former tile', () => {
		const ligne = immobilier(SANS_BLOC).find((item) => item.libelle === 'Tendance du prix médian')!;
		expect(ligne.etat).toEqual({ kind: 'absence', etat: 'not_available', motif: TENDANCE_NON_PUBLIEE, effectif: null, aide: TENDANCE_NON_PUBLIEE });
		expect(ligne.fieldId).toBe('dvf.indicateurs.maison.variation_pct');
		expect(entree(ligne, 'Définition')).toBeTruthy();
		expect(entree(ligne, 'État et motif')).toEqual({ terme: 'État et motif', absences: [{ libelle: null, etat: ligne.etat }] });
		expect(ligne.brut.map((item) => item.fieldId)).toEqual(['dvf.indicateurs.maison.variation_pct', 'dvf.indicateurs.appartement.variation_pct']);
	});

	it('sits right after the sales it compares', () => {
		const ids = immobilier(SANS_BLOC).map((item) => item.fieldId);
		expect(ids.indexOf('dvf.indicateurs.maison.variation_pct')).toBe(ids.indexOf('dvf.nb_mutations_12m_appartement') + 1);
	});

	it('is not added to the CSV, which keeps its reference rows', () => {
		const rows = themesPourExport(themesCommune(SANS_BLOC)).flatMap((theme) => theme.rows);
		expect(rows.some((row) => row.label === 'Tendance du prix médian')).toBe(false);
	});

	it('says out of coverage when the prices are, and two rows when houses and flats differ', () => {
		const horsCouverture = (colonnes: string[]) =>
			({
				...SANS_BLOC,
				...Object.fromEntries(colonnes.map((colonne) => [colonne, null])),
				etats: { ...SANS_BLOC.etats, ...Object.fromEntries(colonnes.map((colonne) => [colonne, { etat: 'out_of_coverage', motif: 'dataset_non_heberge' }])) }
			}) as CommuneSynthese;
		const memes = immobilier(horsCouverture(['prix_m2_median_maison', 'prix_m2_median_appartement']));
		expect(memes.find((item) => item.libelle === 'Tendance du prix médian')?.etat).toMatchObject({ kind: 'absence', etat: 'out_of_coverage' });
		const differents = immobilier(horsCouverture(['prix_m2_median_maison']));
		expect(differents.find((item) => item.libelle === 'Tendance du prix médian des maisons')?.etat).toMatchObject({ etat: 'out_of_coverage' });
		expect(differents.find((item) => item.libelle === 'Tendance du prix médian des appartements')?.etat).toMatchObject({ etat: 'not_available' });
	});

	it('adds nothing when the dated block is served: its rows say the trend', () => {
		const libelles = immobilier(COMMUNE_INDICATORS_44).map((item) => item.libelle);
		expect(libelles).not.toContain('Tendance du prix médian');
		expect(libelles).toContain('Tendance du prix médian des maisons');
	});
});

describe('reperesConnus : « L’essentiel » ne montre que ce qui est connu', () => {
	it('drops a tile without a value', () => {
		const ids = reperesConnus(communeKpis(SANS_BLOC)).map((kpi) => kpi.id);
		expect(ids).not.toContain('tendance');
		expect(ids.slice(0, 2)).toEqual(['prix-maisons', 'prix-appartements']);
	});

	it('keeps only the known lines of a tile, its panel without the state of the dropped line', () => {
		const tendance = reperesConnus(communeKpis(COMMUNE_INDICATORS_44)).find((kpi) => kpi.id === 'tendance')!;
		expect(tendance.lignes.map((ligne) => ligne.libelle)).toEqual(['Maisons']);
		expect(tendance.volet.some((item) => item.terme === 'État et motif')).toBe(false);
	});

	it('leaves no state word anywhere in a tile', () => {
		for (const commune of [SANS_BLOC, COMMUNE_85191, COMMUNE_INDICATORS_44]) {
			for (const kpi of reperesConnus(communeKpis(commune))) {
				expect(kpi.lignes.every((ligne) => ligne.etat.kind === 'valeur')).toBe(true);
				expect(JSON.stringify(kpi.volet)).not.toContain('"absences"');
			}
		}
	});

	it('is empty when nothing is known', () => {
		const vide = communeKpis(SANS_BLOC).map(
			(kpi): KpiCommune => ({ ...kpi, lignes: kpi.lignes.map((ligne) => ({ ...ligne, etat: { kind: 'absence', etat: 'not_reported', motif: 'x', effectif: null } })) })
		);
		expect(reperesConnus(vide)).toEqual([]);
	});

	it('loses no absence: each dropped line has its state and reason on a theme row', () => {
		const block = blocDvf();
		block.residentiel_maison.courante = { ...block.residentiel_maison.courante, mediane: { etat: 'insufficient_sample', motif: 'Moins de 5 ventes.' } };
		const cas: [CommuneSynthese, SourceAttribution[] | null][] = [
			[SANS_BLOC, null],
			[COMMUNE_85191, SOURCES_85191],
			[{ ...COMMUNE_INDICATORS_44, indicateurs_dvf: block }, null],
			[{ ...SANS_BLOC, indicateurs_dvf: NON_QUALIFIE }, null]
		];
		for (const [commune, sources] of cas) {
			const rangees = lignes(commune, sources);
			for (const ligne of communeKpis(commune, sources).flatMap((kpi) => kpi.lignes)) {
				if (ligne.etat.kind !== 'absence') continue;
				const { etat, motif } = ligne.etat;
				const trouvee = rangees.some((item) => item.etat.kind === 'absence' && item.etat.etat === etat && item.etat.motif === motif);
				expect(trouvee, `${ligne.id} : ${etat}`).toBe(true);
			}
		}
	});

	it('a caveat of a dropped tile points at its theme, not at a missing anchor', () => {
		const commune = { ...COMMUNE_INDICATORS_44, indicateurs_dvf: NON_QUALIFIE };
		const presentes = new Set(reperesConnus(communeKpis(commune)).map((kpi) => `#kpi-${kpi.id}`));
		for (const reserve of reservesSynthese(commune)) expect([...presentes, '#immobilier', '#energie-bati']).toContain(reserve.ancre);
	});
});

describe('une explication de tuile retirée reste sur sa ligne', () => {
	const livraison: IndicateursDvf['ancrage_temporel'] = {
		etat: 'delivery_received_and_stock_bound', version_qualification: 'dvf-delivery-qualification.v3', lot_id: 'lot', periode_debut_inclus: '2021-01-01', periode_fin_exclue: '2020-01-01',
		departements: ['85'], acquisition_complete: true, revisions_possibles: true, exhaustivite_historique: 'unknown', generation_fournisseur: null, date_observation: '2026-08-01',
		preuve_qualification_sha256: 'a', preuve_observation_sha256: 'b', preuve_reconstruction_sha256: 'c', snapshot_sha256: 'd', contenu_projete_sha256: 'e', cog_sha256: 'f',
		methode_projection: 'sha256-sorted-row-digests-json.v1', generation_stock: null
	};

	it('a withheld trend keeps « Période non close » and its sentence', () => {
		const block = { ...blocDvf(), ancrage_temporel: livraison };
		const ligne = lignes({ ...COMMUNE_INDICATORS_44, indicateurs_dvf: block }).find((item) => item.id === 'dvf-maison-variation')!;
		expect(ligne.etat).toMatchObject({ kind: 'absence', etat: 'not_qualified', motif: PERIODE_ENTAMEE, libelle: 'Période non close', aide: PERIODE_ENTAMEE });
		expect(entree(ligne, 'Méthode')).toBeTruthy();
		expect(entree(ligne, 'Limites')).toBeTruthy();
	});

	it('a window without a sale keeps its sentence', () => {
		const block = blocDvf();
		block.residentiel_maison.courante = { ...block.residentiel_maison.courante, effectif: 0, mediane: { etat: 'insufficient_sample', motif: 'Aucune vente.' } };
		const ligne = lignes({ ...COMMUNE_INDICATORS_44, indicateurs_dvf: block }).find((item) => item.id === 'dvf-maison-mediane-courante')!;
		expect(ligne.etat).toMatchObject({ kind: 'absence', aide: AUCUNE_VENTE });
	});

	it('a flat median keeps the served count in its sentence', () => {
		const commune = {
			...SANS_BLOC,
			prix_m2_median_appartement: null,
			nb_mutations_12m_appartement: 3,
			etats: { ...SANS_BLOC.etats, prix_m2_median_appartement: { etat: 'insufficient_sample', motif: 'Moins de 5 ventes.' } }
		} as CommuneSynthese;
		const ligne = lignes(commune).find((item) => item.fieldId === 'dvf.prix_m2_median_appartement')!;
		expect(ligne.etat).toMatchObject({ kind: 'absence', effectif: 3 });
	});

	it('the qualification of a dropped trend stays on the row', () => {
		const block = blocDvf();
		block.residentiel_maison = { ...block.residentiel_maison, variation_pct: { etat: 'insufficient_sample', motif: 'x' }, qualification_tendance: 'Tendance à lire avec prudence' };
		const ligne = lignes({ ...COMMUNE_INDICATORS_44, indicateurs_dvf: block }).find((item) => item.id === 'dvf-maison-variation')!;
		expect(entree(ligne, 'Qualification')).toEqual({ terme: 'Qualification', valeur: 'Tendance à lire avec prudence' });
	});

	it('a DPE absence keeps the document caveat', () => {
		const ligne = lignes({ ...COMMUNE_INDICATORS_44, indicateurs_dpe: NON_QUALIFIE }).find((item) => item.id === 'dpe-indicateurs-absence')!;
		expect(JSON.stringify(entree(ligne, 'Limites'))).toContain('pas un logement');
	});
});

describe('un champ, un libellé', () => {
	it('a tile line and the theme row of its field share one label', () => {
		for (const commune of [SANS_BLOC, COMMUNE_85191, COMMUNE_INDICATORS_44]) {
			const rangees = lignes(commune);
			for (const kpi of reperesConnus(communeKpis(commune))) {
				for (const ligne of kpi.lignes) {
					const rangee = rangees.find((item) => item.fieldId === ligne.fieldId);
					expect(rangee, ligne.fieldId).toBeTruthy();
					expect(ligne.libelleChamp ?? ligne.libelle ?? kpi.libelle).toBe(rangee!.libelle);
				}
			}
		}
	});
});

// Lot 4: the words of the page, the CSV of reference keeps its own.
describe('termes en clair (lot 4)', () => {
	const COMMUNE = {
		...SANS_BLOC,
		taux_tfpb: 38.5,
		taux_th_rs: 20.1,
		taux_teom: 9.2,
		nom_epci: 'Challans Gois Communauté',
		indice_gini: 0.25,
		revenu_median: 21340
	} as CommuneSynthese;
	const rangee = (fieldId: string) => lignes(COMMUNE).find((item) => item.fieldId === fieldId)!;

	it.each([
		['fiscalite.taux_tfpb', 'Taxe foncière sur le bâti', 'Taux TFPB'],
		['fiscalite.taux_th_rs', 'Taxe d’habitation (résidences secondaires)', 'Taux TH résidences secondaires'],
		['fiscalite.taux_teom', 'Taxe d’ordures ménagères', 'Taux TEOM'],
		['fiscalite.nom_epci', 'Intercommunalité', 'EPCI'],
		['revenus.indice_gini', 'Inégalités de revenus (indice de Gini)', 'Gini (revenu déclaré)']
	])('%s reads « %s », the CSV keeps « %s »', (fieldId, page, csv) => {
		const ligne = rangee(fieldId);
		expect(ligne.libelle).toBe(page);
		const exportees = themesPourExport(themesCommune(COMMUNE)).flatMap((theme) => theme.rows);
		expect(exportees.find((row) => row.id === ligne.id)?.label).toBe(csv);
	});

	it('explains €/UC/an in the panel of the income, the unit unchanged', () => {
		const ligne = rangee('revenus.revenu_median');
		expect(ligne.etat).toMatchObject({ kind: 'valeur', unite: '€/UC/an' });
		expect(JSON.stringify(entree(ligne, 'Définition'))).toContain('unité de consommation');
		expect(entree(ligne, 'Unité')).toEqual({ terme: 'Unité', valeur: '€/UC/an' });
	});

	it('leaves no fiscal acronym in a label of the page', () => {
		const libelles = lignes(COMMUNE).map((ligne) => ligne.libelle).join(' | ');
		expect(libelles).not.toMatch(/TFPB|TEOM|\bTH\b|EPCI|\bTaux T/);
	});

	it('the comparator reads the labels of the fiche', () => {
		const { trois_communes_dont_hors_couverture: reponse } = comparaisons as never as { trois_communes_dont_hors_couverture: ReponseApiComparaison };
		const comparees = composeComparaison(reponse.donnees, reponse.meta!.source).groupes.flatMap((groupe) => groupe.lignes);
		for (const [cle, fieldId] of [['taux_tfpb', 'fiscalite.taux_tfpb'], ['taux_teom', 'fiscalite.taux_teom'], ['indice_gini', 'revenus.indice_gini']]) {
			expect(comparees.find((ligne) => ligne.cle === cle)?.libelle, cle).toBe(rangee(fieldId).libelle);
		}
	});
});

describe('phrase d’aide sous un badge (A2)', () => {
	it('stays on the row for the two states read as « no risk », goes to the panel for the two others', () => {
		const avec = (etat: string) => aideAuNiveau1({ kind: 'absence', etat: etat as never, motif: 'x', effectif: null });
		expect(avec('out_of_coverage')).toBe(true);
		expect(avec('temporary_error')).toBe(true);
		expect(avec('not_available')).toBe(false);
		expect(avec('not_applicable')).toBe(false);
		expect(avec('insufficient_sample')).toBe(true);
	});
});

describe('definitionsCommune : le papier et la page sans JavaScript', () => {
	const entrees = (commune: CommuneSynthese, sources: SourceAttribution[] | null = null): EntreeDefinition[] =>
		definitionsCommune(reperesConnus(communeKpis(commune, sources)), themesCommune(commune, sources));

	it('says the definition and the source of each tile', () => {
		const kpi = reperesConnus(communeKpis(COMMUNE_85191, SOURCES_85191))[0];
		const item = entrees(COMMUNE_85191, SOURCES_85191).find((candidate) => candidate.id === `kpi:${kpi.id}`)!;
		expect(item.libelle).toBe(kpi.libelle);
		expect(item.textes).toEqual([kpi.definition, ...(kpi.source ? [`Source : ${kpi.source.nom}`] : [])]);
	});

	it('says the definition and the source of each row, and the sentence moved to the panel', () => {
		const tous = entrees(SANS_BLOC);
		expect(tous.find((item) => item.libelle === 'Tendance du prix médian')!.textes).toContain(TENDANCE_NON_PUBLIEE);
		for (const ligne of lignes(SANS_BLOC)) {
			const definition = entree(ligne, 'Définition');
			if (!definition || !('valeur' in definition)) continue;
			expect(tous.find((item) => item.id === `ligne:${ligne.id}`)?.textes).toContain(definition.valeur);
		}
	});
});
