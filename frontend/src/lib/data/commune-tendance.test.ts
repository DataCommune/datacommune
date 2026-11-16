import { describe, expect, it } from 'vitest';
import { communeAvecTendance, TENDANCE_EVOLUTION, TENDANCE_INSUFFISANTE, TENDANCE_POINTS } from './__fixtures__/commune-tendance';
import { categoriesCommune, groupeDuChamp } from './commune-categories';
import { THEME_DU_CHAMP, themesCommune, themesPourExport } from './commune-grid';
import { LIBELLE_TENDANCE, TENDANCE_NON_PUBLIEE } from './commune-indicators';
import { communeKpis, reperesConnus } from './commune-kpi';
import {
	CHAMP_EVOLUTION,
	DEFINITION_TENDANCE_ANNUELLE,
	detailParAnnee,
	LIBELLE_EVOLUTION_NON_CHIFFREE,
	periodeTendance,
	pointsTendance
} from './commune-tendance';
import { listFieldIds } from './formatters';

// The yearly price series of the commune fiche (decision of Ianis of 2026-11-11): the tile ③ and its
// theme rows read the served block `tendance_prix`; nothing is computed in the browser.

const net = (texte: string) => texte.replace(/[  ]/g, ' ');
const tuile = (tendance: Parameters<typeof communeAvecTendance>[0]) =>
	reperesConnus(communeKpis(communeAvecTendance(tendance))).find((kpi) => kpi.id === 'tendance');
const lignesDeTheme = (tendance: Parameters<typeof communeAvecTendance>[0]) =>
	themesCommune(communeAvecTendance(tendance)).flatMap((theme) => theme.lignes).filter((ligne) => ligne.id.startsWith('dvf-tendance'));

describe('tuile « Tendance du prix médian » — série annuelle', () => {
	it('writes the served change, the most sold type first, with its computed period', () => {
		const kpi = tuile(TENDANCE_EVOLUTION)!;
		expect(kpi.libelle).toBe(LIBELLE_TENDANCE);
		expect(kpi.definition).toBe(DEFINITION_TENDANCE_ANNUELLE);
		expect(kpi.lignes.map((ligne) => ligne.libelle)).toEqual(['Appartements', 'Maisons']);
		const [appartements, maisons] = kpi.lignes;
		expect(appartements.etat).toEqual({ kind: 'valeur', texte: '+12', unite: '%' });
		expect(appartements.periodeVisible).toBe('2021 → 2025');
		expect(appartements.serie?.map((point) => point.prix)).toEqual([3500, 3700, 3900, 3850, 3920]);
		expect(appartements.brut).toMatchObject({ fieldId: CHAMP_EVOLUTION, valeur: '12', etat: { kind: 'valeur' } });
		// Houses: points without a change, said as such, never a percentage computed here.
		expect(maisons.etat).toMatchObject({ kind: 'absence', etat: 'insufficient_sample', libelle: LIBELLE_EVOLUTION_NON_CHIFFREE });
		expect(maisons.serie?.map((point) => point.prix)).toEqual([4100, 4300, null, 4250, 4400]);
	});

	it('keeps the sign of a fall and never adds one to zero', () => {
		const baisse = structuredClone(TENDANCE_EVOLUTION);
		baisse.types[0].evolution_pct = -3.7;
		expect(tuile(baisse)!.lignes[0].etat).toMatchObject({ texte: '−3,7', unite: '%' });
		baisse.types[0].evolution_pct = 0;
		expect(tuile(baisse)!.lignes[0].etat).toMatchObject({ texte: '0' });
	});

	it('the period is the served one, never written in the code', () => {
		const glissee = { ...structuredClone(TENDANCE_EVOLUTION), annee_debut: 2022, annee_fin: 2026 };
		expect(periodeTendance(glissee)).toBe('2022 → 2026');
		expect(tuile(glissee)!.lignes[0].periodeVisible).toBe('2022 → 2026');
		expect(periodeTendance({ ...glissee, annee_debut: 2026 })).toBe('2026');
	});

	it('shows points without a percentage under the threshold, and drops a second type without points', () => {
		const kpi = tuile(TENDANCE_POINTS)!;
		expect(kpi.lignes.map((ligne) => ligne.libelle)).toEqual(['Maisons']);
		const [maisons] = kpi.lignes;
		expect(maisons.etat).toMatchObject({ kind: 'absence', etat: 'insufficient_sample', libelle: 'Évolution non chiffrée' });
		expect(net((maisons.etat as { aide: string }).aide)).toBe('Trop peu de ventes pour chiffrer une évolution : il en faut au moins 30 en 2021 et en 2025.');
		expect(maisons.serie).toHaveLength(5);
		expect(maisons.brut.valeur).toBe('null');
	});

	it('has no tile when no type has two medians: the theme rows say « Effectif insuffisant »', () => {
		// 4.9: « L'essentiel » shows what is known; a tile without a value nor points is dropped.
		expect(tuile(TENDANCE_INSUFFISANTE)).toBeUndefined();
		const lignes = lignesDeTheme(TENDANCE_INSUFFISANTE);
		expect(lignes.map((ligne) => ligne.id)).toEqual(['dvf-tendance-maison', 'dvf-tendance-appartement']);
		for (const ligne of lignes) {
			expect(ligne.etat).toMatchObject({ kind: 'absence', etat: 'insufficient_sample' });
			expect((ligne.etat as { libelle?: string }).libelle).toBeUndefined();
			expect(net((ligne.etat as { motif: string }).motif)).toBe('Moins de deux années comptent au moins 5 ventes entre 2021 et 2025.');
		}
		// The served detail stays readable behind the row.
		const detail = lignes[0].volet.find((entree) => entree.terme === 'Maisons, année par année');
		expect(net((detail as { valeur: string }).valeur)).toContain('2022 : 1 800 €/m², 6 ventes');
	});

	it('puts the medians and counts of every year behind the information button', () => {
		const volet = new Map(tuile(TENDANCE_EVOLUTION)!.volet.flatMap((entree) => ('valeur' in entree ? [[entree.terme, net(entree.valeur)] as const] : [])));
		expect(volet.get('Appartements, année par année')).toBe(
			'2021 : 3 500 €/m², 3 996 ventes · 2022 : 3 700 €/m², 3 800 ventes · 2023 : 3 900 €/m², 3 100 ventes · 2024 : 3 850 €/m², 2 900 ventes · 2025 : 3 920 €/m², 3 200 ventes'
		);
		expect(volet.get('Maisons, année par année')).toContain('2023 : 4 ventes, prix médian non publié');
		expect(volet.get('Période')).toBe('2021 → 2025, années civiles entières');
		expect(volet.get('Méthode')).toContain('au moins 30 ventes');
		expect(volet.get('Limites')).toContain('Le prix médian suit ce qui s’est vendu');
		// The detail comes before the caveat, which closes the panel.
		const termes = tuile(TENDANCE_EVOLUTION)!.volet.map((entree) => entree.terme);
		expect(termes.indexOf('Maisons, année par année')).toBeLessThan(termes.indexOf('Limites'));
	});

	it('a year without a sale is said as such, a single sale is singular', () => {
		expect(net(detailParAnnee(TENDANCE_POINTS.types[1]))).toBe('2021 : 1 vente, prix médian non publié · 2022 : aucune vente · 2023 : 2 ventes, prix médian non publié · 2024 : aucune vente · 2025 : 1 vente, prix médian non publié');
		expect(pointsTendance(TENDANCE_POINTS.types[1])).toEqual([]);
	});

	it('says neither the change of a dwelling nor a forecast', () => {
		const textes = JSON.stringify([tuile(TENDANCE_EVOLUTION), tuile(TENDANCE_POINTS), tuile(TENDANCE_INSUFFISANTE)]);
		expect(textes).not.toMatch(/votre bien|a pris|prendra|prévoit|va (?:monter|baisser)|devrait/i);
	});

	it('a series served absent is read as no series: no tile, the former row, no error', () => {
		const absente = { ...communeAvecTendance(null), tendance_prix: { etat: 'not_qualified' as const, motif: 'tendance_prix_non_publiee' } };
		expect(reperesConnus(communeKpis(absente)).find((kpi) => kpi.id === 'tendance')).toBeUndefined();
		const lignes = themesCommune(absente).flatMap((theme) => theme.lignes).filter((ligne) => ligne.id.startsWith('dvf-tendance'));
		expect(lignes.map((ligne) => ligne.id)).toEqual(['dvf-tendance']);
		expect(lignes[0].etat).toMatchObject({ kind: 'absence', etat: 'not_available', motif: TENDANCE_NON_PUBLIEE });
	});

	it('without the served block, the tile keeps its former reading', () => {
		expect(tuile(null)).toBeUndefined();
		const ligne = lignesDeTheme(null);
		expect(ligne.map((l) => l.id)).toEqual(['dvf-tendance']);
		expect(ligne[0].etat).toMatchObject({ kind: 'absence', etat: 'not_available', motif: TENDANCE_NON_PUBLIEE });
	});
});

describe('lignes de thème et catégories — série annuelle', () => {
	it('every served field of the series has a theme and lands in « Prix au m² »', () => {
		const champs = listFieldIds().filter((id) => id.startsWith('dvf.tendance.'));
		expect(champs).toHaveLength(5);
		for (const champ of champs) {
			expect(THEME_DU_CHAMP[champ], champ).toBe('immobilier');
			expect(groupeDuChamp(champ), champ).toBe('prix_commune');
		}
	});

	it('one row per type replaces « Non publiée », after the sales, in the group of the prices', () => {
		const themes = themesCommune(communeAvecTendance(TENDANCE_EVOLUTION));
		const immobilier = themes.find((theme) => theme.cle === 'immobilier')!;
		const ids = immobilier.lignes.map((ligne) => ligne.id);
		expect(ids.filter((id) => id.startsWith('dvf-tendance'))).toEqual(['dvf-tendance-appartement', 'dvf-tendance-maison']);
		expect(ids.indexOf('dvf-tendance-appartement')).toBe(immobilier.lignes.findIndex((ligne) => ligne.fieldId === 'dvf.nb_mutations_12m_appartement') + 1);
		const [appartements, maisons] = lignesDeTheme(TENDANCE_EVOLUTION);
		expect(appartements).toMatchObject({ libelle: 'Tendance du prix médian des appartements', periode: '2021 → 2025, années civiles entières', etat: { kind: 'valeur', texte: '+12', unite: '%' } });
		expect(maisons.etat).toMatchObject({ kind: 'absence', libelle: 'Évolution non chiffrée' });
		expect(JSON.stringify(themes)).not.toContain(TENDANCE_NON_PUBLIEE);
		const prix = categoriesCommune(themes).flatMap((categorie) => categorie.groupes).find((groupe) => groupe.id === 'prix_commune')!;
		expect(prix.lignes.map((ligne) => ligne.id)).toEqual(expect.arrayContaining(['dvf-tendance-appartement', 'dvf-tendance-maison']));
	});

	it('the rows are exported with their state, their period and the detail of the years in their panel', () => {
		const themes = themesCommune(communeAvecTendance(TENDANCE_POINTS));
		const lignes = themesPourExport(themes).find((groupe) => groupe.key === 'immobilier')!.rows;
		const maison = lignes.find((ligne) => ligne.id === 'dvf-tendance-maison')!;
		expect(maison).toMatchObject({ label: 'Tendance du prix médian des maisons', state: 'insufficient_sample' });
		// The CSV keeps the glossary label of the served state; the reason says why no change is written.
		expect(maison.value).toBe('Effectif insuffisant');
		expect(maison.presentation?.reserves?.[0]).toBe('2021 → 2025, années civiles entières');
		expect(maison.presentation?.reserves?.map(net)).toContain('Motif : Trop peu de ventes pour chiffrer une évolution : il en faut au moins 30 en 2021 et en 2025.');
		const appartement = lignes.find((ligne) => ligne.id === 'dvf-tendance-appartement')!;
		expect(appartement.state).toBe('insufficient_sample');
		const volet = lignesDeTheme(TENDANCE_POINTS)[0].volet.map((entree) => entree.terme);
		expect(volet).toEqual(expect.arrayContaining(['Définition', 'Période', 'Méthode', 'Maisons, année par année', 'Limites']));
	});
});
