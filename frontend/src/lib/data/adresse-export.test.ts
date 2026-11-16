// CSV export of the address fiche (04.7-03, UI-SPEC v3 § 4.1): one row per criterion, grouped by
// part in the order of the page, what the closed row shows plus its meaning, figures, attachment and
// sources; never a technical key nor an individual price.
import { describe, expect, it } from 'vitest';
import { projeterTheme, type ThemeRapport } from '$lib/api/rapport';
import { baseFichierExport, lignesExportRapport } from './adresse-export';
import dpeReel from './__fixtures__/rapport-theme-dpe-plusieurs-batiments.json';

const NB = String.fromCharCode(0xa0);
const SOURCE_DVF = { cle: 'dvf', nom: 'Demandes de valeurs foncières', producteur: 'DGFiP', url: 'https://www.data.gouv.fr', licence: 'Licence Ouverte 2.0', millesime: '2025-06', mode: 'import' };
const SOURCE_GEORISQUES = { cle: 'georisques', nom: 'Géorisques', producteur: 'BRGM', url: 'https://www.georisques.gouv.fr', licence: 'Licence Ouverte 2.0', millesime: null, mode: 'direct' };

function theme(brut: Record<string, unknown>): ThemeRapport {
	const t = projeterTheme({ granularite: { niveau: 'parcelle', rayon_m: null }, sources: [SOURCE_DVF], ...brut });
	if (!t) throw new Error('theme non projeté');
	return t;
}

const VENTES = theme({
	id: 'ventes_parcelle',
	section: 'marche',
	titre: 'Ventes de la parcelle',
	etat: 'ok',
	ton: 'neutre',
	verdict: '2 ventes enregistrées sur la parcelle de l’adresse, la dernière en mars 2024.',
	accroche: '2 ventes sur la parcelle',
	chiffres: [
		{ id: 'nombre', libelle: 'Ventes sur la parcelle', valeur: 2, unite: 'count', millesime: '2021-2025' },
		{ id: 'derniere', libelle: 'Dernière vente', valeur: '2024-03-12', unite: 'date', millesime: '2021-2025' }
	],
	rattachement: { entite: 'parcelle_du_point', cle: '85191000AB0012', confiance: 'sure' }
});
const ARGILES_EN_PANNE = theme({
	id: 'argiles',
	section: 'risques',
	titre: 'Retrait-gonflement des argiles',
	etat: 'panne',
	ton: 'info',
	granularite: { niveau: 'adresse', rayon_m: null },
	verdict: 'Géorisques n’a pas répondu ; ce n’est pas une absence de risque.',
	sources: [SOURCE_GEORISQUES],
	motif: 'amont_indisponible',
	accroche: 'Source indisponible'
});
const INCONNU = theme({
	id: 'eau_potable',
	section: 'vivre',
	titre: 'Eau potable',
	etat: 'ok',
	ton: 'neutre',
	granularite: { niveau: 'udi', rayon_m: null },
	verdict: 'Eau conforme aux limites de qualité sur la dernière année.',
	accroche: `98,5${NB}%`,
	chiffres: [{ id: 'conformite', libelle: 'Prélèvements conformes', valeur: 98.5, unite: 'pourcent', millesime: '2026' }]
});
// The headline and the alert are served since 04.7-17; the real theme was captured before.
const DPE = projeterTheme({
	...dpeReel,
	accroche: '11 DPE déclarés',
	rattachement: { ...dpeReel.rattachement, alerte: 'Ces DPE peuvent venir de plusieurs bâtiments.' }
})!;

describe('lignesExportRapport — une ligne par critère, par partie dans l’ordre de la page', () => {
	const groupes = lignesExportRapport([VENTES, INCONNU, ARGILES_EN_PANNE, DPE], 'La Roche-sur-Yon');

	it('parts in the order of the page, empty parts left out, one row per served criterion', () => {
		expect(groupes.map((g) => [g.key, g.name, g.rows.map((r) => r.label)])).toEqual([
			['batiment', 'Le bâti et l’énergie', ['DPE']],
			['risques', 'Le terrain et les risques', ['Retrait-gonflement des argiles']],
			['marche', 'Le prix', ['Ventes de la parcelle']],
			['vivre', 'Le quartier', ['Eau potable']]
		]);
	});

	it('a served row: headline, status, scale, then the meaning, the other figures, the attachment and the sources', () => {
		const [ligne] = groupes.find((g) => g.key === 'marche')!.rows;
		expect(ligne.value).toBe('2 ventes sur la parcelle');
		expect(ligne.state).toBe('Valeur lue');
		expect(ligne.presentation?.grain).toBe('parcelle');
		expect(ligne.presentation?.reserves).toEqual([
			'2 ventes enregistrées sur la parcelle de l’adresse, la dernière en mars 2024.',
			`Dernière vente${NB}: 12 mars 2024 (2021-2025)`,
			'Rattachement sûr',
			`Source${NB}: Demandes de valeurs foncières, DGFiP, juin 2025`
		]);
	});

	it('the real DPE row keeps its vigilance and says why its attachment is not sure (A1)', () => {
		const [ligne] = groupes.find((g) => g.key === 'batiment')!.rows;
		expect(ligne.value).toBe('11 DPE déclarés');
		expect(ligne.state).toBe('Vigilance');
		expect(ligne.presentation?.reserves).toContain('Ces DPE peuvent venir de plusieurs bâtiments.');
		expect(ligne.presentation?.reserves).toContain('Rattachement incertain');
	});

	it('an outage is exported as « Source indisponible », never as a zero nor a value', () => {
		const [ligne] = groupes.find((g) => g.key === 'risques')!.rows;
		expect(ligne.value).toBe('Source indisponible');
		expect(ligne.state).toBe('Source indisponible');
		expect(JSON.stringify(ligne)).not.toMatch(/(^|[^0-9])0([^0-9]|$)/);
	});

	it('a criterion the front does not know is exported from its own fields', () => {
		const [ligne] = groupes.find((g) => g.key === 'vivre')!.rows;
		expect(ligne.label).toBe('Eau potable');
		expect(ligne.value).toBe(`98,5${NB}%`);
		expect(ligne.presentation?.grain).toBe('unité de distribution d’eau');
	});

	it('the export writes the served headline and alert, never ones computed again (04.7-17)', () => {
		const servi = theme({ ...dpeReel, accroche: 'Accroche servie 9', rattachement: { ...dpeReel.rattachement, alerte: 'Alerte servie 9.' } });
		const [ligne] = lignesExportRapport([servi], null)[0].rows;
		expect(ligne.value).toBe('Accroche servie 9');
		expect(ligne.presentation?.reserves).toContain('Alerte servie 9.');
		// No served headline: the row says its verdict, it never builds one from the figures.
		const [sans] = lignesExportRapport([{ ...servi, accroche: null }], null)[0].rows;
		expect(sans.value).toBe(servi.verdict);
	});

	it('never a technical key: the parcel and building identifiers of the attachments stay out', () => {
		const texte = JSON.stringify(groupes);
		expect(texte).not.toContain('85191000AB0012');
		expect(texte).not.toMatch(/rnb_|ban_id|field_id/i);
	});

	it('no criterion, no group', () => {
		expect(lignesExportRapport([], null)).toEqual([]);
	});

	// 04.7-13 parity with the published fiche (946d5a71, `identite()` of the old export): the file says
	// which address it describes, without the parcel identifier (never a technical key, UI-SPEC v3 § 4.1).
	it('04.7-13: opens with the identity of the address, without a technical key', () => {
		const identite = { adresse: '12 Rue des Lilas 85000 La Roche-sur-Yon', commune: 'La Roche-sur-Yon', departement: 'Vendée (85)' };
		const avec = lignesExportRapport([VENTES], 'La Roche-sur-Yon', identite);
		expect(avec[0].key).toBe('identite');
		expect(avec[0].name).toBe('Identité');
		expect(avec[0].rows.map((r) => [r.label, r.value])).toEqual([
			['Adresse', '12 Rue des Lilas 85000 La Roche-sur-Yon'],
			['Commune', 'La Roche-sur-Yon'],
			['Département', 'Vendée (85)']
		]);
		expect(avec.slice(1)).toEqual(lignesExportRapport([VENTES], 'La Roche-sur-Yon'));
		expect(JSON.stringify(avec[0])).not.toMatch(/85191000AB0012|_\d{4}_\d{5}/);
	});
});

describe('nom du fichier', () => {
	it('« adresse-{clé BAN} », the base of « adresse-{clé BAN}-export.csv »', () => {
		expect(baseFichierExport('85191_0420_00012')).toBe('adresse-85191_0420_00012');
	});
});
