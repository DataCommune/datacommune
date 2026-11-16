import { describe, expect, it } from 'vitest';
import type { CommuneSynthese } from '$lib/api/commune';
import { etatDeColonne, type ColonnePlate } from './etats-colonnes';

/**
 * Frozen harness response (D-47, complete rule table): the `donnees[0]` of `/v1/commune` for 44991
 * and 44992, written by `tests/integration/test_commune_absences_http.py`
 * (ETATS_API_FIXTURE_OUTPUT) on a migrated PostGIS database — 5 apartment sales in 44991 (the
 * threshold), 4 in 44992, nothing else loaded. Only `couverture` is left out (every source hosted).
 * Copied as served, not hand-made: every null flat column carries its declared state.
 */
const HARNESS = {
	"44991": {
		"code_insee": "44991",
		"indicateurs_dvf": null,
		"indicateurs_dpe": null,
		"nom_commune": "Ancienne",
		"code_departement": "44",
		"code_region": null,
		"population": 100,
		"nb_menages": null,
		"nb_logements": null,
		"nb_logements_vacants_recensement": null,
		"densite": null,
		"superficie": null,
		"annee_demographie": 2022,
		"revenu_median": null,
		"taux_pauvrete": null,
		"indice_gini": null,
		"part_imposee": null,
		"annee_revenus": null,
		"zone_abc": null,
		"zone_abc_reclassement": null,
		"taux_vacance": null,
		"nb_vacants_plus_2ans": null,
		"annee_vacance": null,
		"taux_tfpb": null,
		"taux_th_rs": null,
		"taux_teom": null,
		"nom_epci": null,
		"annee_fiscalite": null,
		"loyer_m2_appartement": null,
		"loyer_m2_maison": null,
		"annee_loyers": null,
		"loyers_appartement": null,
		"loyers_maison": null,
		"prix_m2_median": 3040,
		"nb_mutations_12m": 5,
		"prix_m2_median_maison": null,
		"nb_mutations_12m_maison": 0,
		"prix_m2_median_appartement": 3040,
		"nb_mutations_12m_appartement": 5,
		"dpe_passoires_pct": null,
		"dpe_median": null,
		"annee_construction_mediane": null,
		"nb_equipements": null,
		"annee_equipements": null,
		"nb_etablissements_scolaires": null,
		"nb_monuments": null,
		"nb_proprietaires_pm": null,
		"surface_pm_ha": null,
		"fibre_pct": null,
		"etats": {
			"nb_menages": {"etat": "not_available", "motif": "La source a une ligne pour la commune à ce millésime, sans cette valeur."},
			"nb_logements": {"etat": "not_available", "motif": "La source a une ligne pour la commune à ce millésime, sans cette valeur."},
			"nb_logements_vacants_recensement": {"etat": "not_available", "motif": "La source a une ligne pour la commune à ce millésime, sans cette valeur."},
			"densite": {"etat": "not_available", "motif": "La source a une ligne pour la commune à ce millésime, sans cette valeur."},
			"superficie": {"etat": "not_available", "motif": "La source a une ligne pour la commune à ce millésime, sans cette valeur."},
			"annee_revenus": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"revenu_median": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"taux_pauvrete": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"indice_gini": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"part_imposee": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"annee_vacance": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"taux_vacance": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"nb_vacants_plus_2ans": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"annee_fiscalite": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"taux_tfpb": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"taux_th_rs": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"taux_teom": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"nom_epci": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"annee_equipements": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"nb_equipements": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"zone_abc": {"etat": "not_reported", "motif": "Aucune zone ABC chargée pour la commune."},
			"zone_abc_reclassement": {"etat": "not_reported", "motif": "Aucune zone ABC chargée pour la commune."},
			"loyer_m2_appartement": {"etat": "not_reported", "motif": "Aucune prédiction de loyer des appartements pour la commune."},
			"loyer_m2_maison": {"etat": "not_reported", "motif": "Aucune prédiction de loyer des maisons pour la commune."},
			"annee_loyers": {"etat": "not_reported", "motif": "Aucune prédiction de loyer pour la commune."},
			"prix_m2_median_maison": {"etat": "insufficient_sample", "motif": "0 mutations de maisons retenues sur 12 mois, sous le seuil de publication de 5."},
			"dpe_passoires_pct": {"etat": "not_reported", "motif": "Aucun diagnostic DPE retenu pour la commune."},
			"dpe_median": {"etat": "not_reported", "motif": "Aucun diagnostic DPE retenu pour la commune."},
			"annee_construction_mediane": {"etat": "not_reported", "motif": "Aucun diagnostic DPE retenu pour la commune."},
			"nb_etablissements_scolaires": {"etat": "not_reported", "motif": "Aucune ligne de la carte scolaire chargée pour la commune, ou identifiants RNE incomplets."},
			"nb_monuments": {"etat": "not_reported", "motif": "Aucune notice Mérimée chargée pour la commune, ou références incomplètes."},
			"nb_proprietaires_pm": {"etat": "not_reported", "motif": "Aucun droit foncier de personne morale chargé pour la commune, ou identifiants ou surfaces incomplets."},
			"surface_pm_ha": {"etat": "not_reported", "motif": "Aucun droit foncier de personne morale chargé pour la commune, ou identifiants ou surfaces incomplets."},
			"fibre_pct": {"etat": "not_reported", "motif": "Aucun immeuble chargé pour la commune, ou identifiants ou technologies manquants."}
		}
	},
	"44992": {
		"code_insee": "44992",
		"indicateurs_dvf": null,
		"indicateurs_dpe": null,
		"nom_commune": "Courante",
		"code_departement": "44",
		"code_region": null,
		"population": 200,
		"nb_menages": null,
		"nb_logements": null,
		"nb_logements_vacants_recensement": null,
		"densite": null,
		"superficie": null,
		"annee_demographie": 2025,
		"revenu_median": null,
		"taux_pauvrete": null,
		"indice_gini": null,
		"part_imposee": null,
		"annee_revenus": null,
		"zone_abc": null,
		"zone_abc_reclassement": null,
		"taux_vacance": null,
		"nb_vacants_plus_2ans": null,
		"annee_vacance": null,
		"taux_tfpb": null,
		"taux_th_rs": null,
		"taux_teom": null,
		"nom_epci": null,
		"annee_fiscalite": null,
		"loyer_m2_appartement": null,
		"loyer_m2_maison": null,
		"annee_loyers": null,
		"loyers_appartement": null,
		"loyers_maison": null,
		"prix_m2_median": null,
		"nb_mutations_12m": 4,
		"prix_m2_median_maison": null,
		"nb_mutations_12m_maison": 0,
		"prix_m2_median_appartement": null,
		"nb_mutations_12m_appartement": 4,
		"dpe_passoires_pct": null,
		"dpe_median": null,
		"annee_construction_mediane": null,
		"nb_equipements": null,
		"annee_equipements": null,
		"nb_etablissements_scolaires": null,
		"nb_monuments": null,
		"nb_proprietaires_pm": null,
		"surface_pm_ha": null,
		"fibre_pct": null,
		"etats": {
			"nb_menages": {"etat": "not_available", "motif": "La source a une ligne pour la commune à ce millésime, sans cette valeur."},
			"nb_logements": {"etat": "not_available", "motif": "La source a une ligne pour la commune à ce millésime, sans cette valeur."},
			"nb_logements_vacants_recensement": {"etat": "not_available", "motif": "La source a une ligne pour la commune à ce millésime, sans cette valeur."},
			"densite": {"etat": "not_available", "motif": "La source a une ligne pour la commune à ce millésime, sans cette valeur."},
			"superficie": {"etat": "not_available", "motif": "La source a une ligne pour la commune à ce millésime, sans cette valeur."},
			"annee_revenus": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"revenu_median": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"taux_pauvrete": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"indice_gini": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"part_imposee": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"annee_vacance": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"taux_vacance": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"nb_vacants_plus_2ans": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"annee_fiscalite": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"taux_tfpb": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"taux_th_rs": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"taux_teom": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"nom_epci": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"annee_equipements": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"nb_equipements": {"etat": "not_reported", "motif": "La source chargée n'a aucune ligne pour la commune."},
			"zone_abc": {"etat": "not_reported", "motif": "Aucune zone ABC chargée pour la commune."},
			"zone_abc_reclassement": {"etat": "not_reported", "motif": "Aucune zone ABC chargée pour la commune."},
			"loyer_m2_appartement": {"etat": "not_reported", "motif": "Aucune prédiction de loyer des appartements pour la commune."},
			"loyer_m2_maison": {"etat": "not_reported", "motif": "Aucune prédiction de loyer des maisons pour la commune."},
			"annee_loyers": {"etat": "not_reported", "motif": "Aucune prédiction de loyer pour la commune."},
			"prix_m2_median": {"etat": "insufficient_sample", "motif": "4 mutations résidentielles retenues sur 12 mois, sous le seuil de publication de 5."},
			"prix_m2_median_maison": {"etat": "insufficient_sample", "motif": "0 mutations de maisons retenues sur 12 mois, sous le seuil de publication de 5."},
			"prix_m2_median_appartement": {"etat": "insufficient_sample", "motif": "4 mutations d'appartements retenues sur 12 mois, sous le seuil de publication de 5."},
			"dpe_passoires_pct": {"etat": "not_reported", "motif": "Aucun diagnostic DPE retenu pour la commune."},
			"dpe_median": {"etat": "not_reported", "motif": "Aucun diagnostic DPE retenu pour la commune."},
			"annee_construction_mediane": {"etat": "not_reported", "motif": "Aucun diagnostic DPE retenu pour la commune."},
			"nb_etablissements_scolaires": {"etat": "not_reported", "motif": "Aucune ligne de la carte scolaire chargée pour la commune, ou identifiants RNE incomplets."},
			"nb_monuments": {"etat": "not_reported", "motif": "Aucune notice Mérimée chargée pour la commune, ou références incomplètes."},
			"nb_proprietaires_pm": {"etat": "not_reported", "motif": "Aucun droit foncier de personne morale chargé pour la commune, ou identifiants ou surfaces incomplets."},
			"surface_pm_ha": {"etat": "not_reported", "motif": "Aucun droit foncier de personne morale chargé pour la commune, ou identifiants ou surfaces incomplets."},
			"fibre_pct": {"etat": "not_reported", "motif": "Aucun immeuble chargé pour la commune, ou identifiants ou technologies manquants."}
		}
	}
} as unknown as Record<'44991' | '44992', CommuneSynthese>;

/** Nulls that are not flat columns: an identifier and the object columns. */
const HORS_COLONNES_PLATES = new Set<keyof CommuneSynthese>([
	'code_region',
	'indicateurs_dvf',
	'indicateurs_dpe',
	'loyers_appartement',
	'loyers_maison'
]);

describe('etatDeColonne — lecture typée des colonnes plates (D-47, D-51)', () => {
	it('rend la valeur servie : médiane au seuil', () => {
		expect(etatDeColonne(HARNESS['44991'], 'prix_m2_median_appartement')).toEqual({
			kind: 'valeur',
			valeur: 3040
		});
	});

	it('un zéro servi reste une valeur, jamais une absence', () => {
		expect(etatDeColonne(HARNESS['44991'], 'nb_mutations_12m_maison')).toEqual({
			kind: 'valeur',
			valeur: 0
		});
	});

	it("rend l'absence déclarée par l'API : effectif insuffisant avec son effectif", () => {
		const etat = etatDeColonne(HARNESS['44992'], 'prix_m2_median_appartement');
		expect(etat).toEqual({
			kind: 'absence',
			etat: 'insufficient_sample',
			motif: "4 mutations d'appartements retenues sur 12 mois, sous le seuil de publication de 5."
		});
	});

	it("rend l'absence déclarée d'une source sans ligne : non renseignée, avec sa cause", () => {
		expect(etatDeColonne(HARNESS['44991'], 'taux_tfpb')).toEqual({
			kind: 'absence',
			etat: 'not_reported',
			motif: "La source chargée n'a aucune ligne pour la commune."
		});
	});

	it('signale une absence sans état déclaré, sans la maquiller en « Non renseigné » ni en 0', () => {
		// Simulated API defect: the harness declares every null column, so one entry is removed.
		const { prix_m2_median_maison: _retire, ...etats } = HARNESS['44992'].etats ?? {};
		const defaut = { ...HARNESS['44992'], etats } as CommuneSynthese;
		const etat = etatDeColonne(defaut, 'prix_m2_median_maison');
		expect(etat).toEqual({ kind: 'absence-non-declaree', colonne: 'prix_m2_median_maison' });
		expect(JSON.stringify(etat)).not.toMatch(/Non renseign|"valeur"/);
	});

	it('toute colonne nulle de la réponse du harnais a un état déclaré', () => {
		for (const commune of Object.values(HARNESS)) {
			const nulles = (Object.keys(commune) as (keyof CommuneSynthese)[]).filter(
				(colonne) => commune[colonne] === null
			);
			const plates = nulles.filter(
				(colonne): colonne is ColonnePlate => !HORS_COLONNES_PLATES.has(colonne)
			);
			expect(plates.length).toBeGreaterThan(0);
			for (const colonne of plates) {
				expect(etatDeColonne(commune, colonne).kind).toBe('absence');
			}
		}
	});

	it('lit une colonne absente de la réponse comme une absence, pas comme 0', () => {
		const { prix_m2_median_appartement: _omis, ...sansColonne } = HARNESS['44991'];
		expect(etatDeColonne(sansColonne as CommuneSynthese, 'prix_m2_median_appartement')).toEqual({
			kind: 'absence-non-declaree',
			colonne: 'prix_m2_median_appartement'
		});
	});
});
