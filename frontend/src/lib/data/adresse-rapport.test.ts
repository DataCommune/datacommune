import { describe, expect, it } from 'vitest';
import { projeterRapport, projeterTheme, type ResumeServi, type ThemeRapport } from '$lib/api/rapport';
import {
	accrocheDeLigne,
	alerteDe,
	aplatirDetails,
	humaniser,
	libelleCode,
	aucunZeroFabrique,
	etiquetteSource,
	libelleEchelle,
	lecturePartielle,
	lireResume,
	motifDe,
	phraseMotif,
	phraseEnBref,
	raisonDeLigne,
	repliSansValeur,
	statutDe,
	valeurChiffre,
	verdictCoherent
} from './adresse-rapport';
import { formatBareNumber } from './formatters';
import dpeReel from './__fixtures__/rapport-theme-dpe-plusieurs-batiments.json';

const SOURCE_DIRECTE = { cle: 'georisques', nom: 'Géorisques', producteur: 'BRGM / MTE', url: 'https://www.georisques.gouv.fr', licence: 'Licence Ouverte 2.0', millesime: '2026-10-30', mode: 'direct' };
const SOURCE_IMPORT = { cle: 'dvf', nom: 'Demandes de valeurs foncières (DVF)', producteur: 'DGFiP / Etalab', url: 'https://www.data.gouv.fr', licence: 'Licence Ouverte 2.0', millesime: '2025-12-31', mode: 'import' };

function theme(brut: Record<string, unknown>): ThemeRapport {
	const t = projeterTheme({ section: 'risques', titre: 'Titre', verdict: 'Une phrase de verdict.', granularite: { niveau: 'adresse', rayon_m: null }, sources: [SOURCE_IMPORT], ...brut });
	if (!t) throw new Error('theme non projeté');
	return t;
}
const texteDe = (segments: { texte: string }[]): string => segments.map((s) => s.texte).join('');

describe('projection du rapport (04.7-01)', () => {
	it('garde le thème DPE réel tel que servi', () => {
		const t = projeterTheme(dpeReel)!;
		expect(t.etat).toBe('ok');
		expect(t.ton).toBe('vigilance');
		expect(t.chiffres.map((c) => c.id)).toEqual(['effectif', 'classe_dominante', 'plage']);
		expect(t.rattachement).toMatchObject({ confiance: 'incertaine', candidats: 3, motif: 'dpe_plusieurs_batiments' });
	});

	it('ramène un état inconnu à un manque sans chiffre ni ton de valeur, jamais à une valeur', () => {
		const t = theme({ id: 'x', etat: 'bizarre', ton: 'rassurant', chiffres: [{ id: 'n', libelle: 'N', valeur: 0, unite: 'count' }] });
		expect(t.etat).toBe('manque');
		expect(t.ton).toBe('info');
		expect(t.chiffres).toEqual([]);
	});

	it('lit une confiance inconnue comme la plus faible', () => {
		expect(theme({ id: 'x', etat: 'ok', ton: 'neutre', rattachement: { entite: 'parcelle_du_point', confiance: '???' } }).rattachement?.confiance).toBe('incertaine');
	});

	it('ignore un thème sans titre ou sans verdict, garde les autres et les chiffres clés', () => {
		const r = projeterRapport({ date: '2026-10-30', themes: [dpeReel, { id: 'vide' }], resume: { chiffres_cles: [{ id: 'dpe_dominant', libelle: 'DPE', theme: 'dpe', etat: 'ok', chiffres: [] }] } });
		expect(r.themes.map((t) => t.id)).toEqual(['dpe']);
		expect(r.chiffresCles[0].id).toBe('dpe_dominant');
	});
});

describe('lecture d’une ligne', () => {
	const dpe = projeterTheme(dpeReel)!;

	// Sentinels: values no rule of the front could compute from the figures (04.7-17).
	const CHIFFRE = [{ id: 'm', libelle: 'Prix', valeur: 3385, unite: 'euro_per_m2' }];

	it('accroche : celle que l’API sert, telle quelle ; aucune sans elle, jamais recalculée', () => {
		expect(theme({ id: 'p', etat: 'ok', ton: 'neutre', chiffres: CHIFFRE, accroche: 'Accroche servie 42' }).accroche).toBe('Accroche servie 42');
		expect(theme({ id: 'p', etat: 'ok', ton: 'neutre', chiffres: CHIFFRE }).accroche).toBeNull();
		expect(theme({ id: 'a', etat: 'panne', ton: 'info' }).accroche).toBeNull();
		// Over 40 characters it is not a headline (contract): not shown, never cut.
		expect(theme({ id: 'p', etat: 'ok', ton: 'neutre', accroche: 'x'.repeat(41) }).accroche).toBeNull();
		expect(theme({ id: 'p', etat: 'ok', ton: 'neutre', accroche: 'x'.repeat(40) }).accroche).toBe('x'.repeat(40));
		// 40 characters are counted as the contract counts them, not in UTF-16 units.
		expect(theme({ id: 'p', etat: 'ok', ton: 'neutre', accroche: 'x'.repeat(39) + '🏠' }).accroche).toBe('x'.repeat(39) + '🏠');
	});

	it('alerte de rattachement : celle que l’API sert ; sans elle, aucune, jamais une recalculée', () => {
		const servie = theme({ id: 'p', etat: 'ok', ton: 'neutre', rattachement: { entite: 'x', confiance: 'probable', candidats: 2, motif: 'parcelles_multiples', alerte: 'Alerte servie 7.' } });
		expect(alerteDe(servie)).toBe('Alerte servie 7.');
		expect(alerteDe(theme({ id: 'p', etat: 'ok', ton: 'neutre', rattachement: { entite: 'x', confiance: 'probable', candidats: 2, motif: 'parcelles_multiples' } }))).toBeNull();
		expect(alerteDe(theme({ id: 'p', etat: 'ok', ton: 'neutre', rattachement: { entite: 'parcelle_du_point', confiance: 'sure', alerte: 'jamais' } }))).toBeNull();
	});

	it('motif d’un manque : le libellé servi, sinon le code mis en mots ; badge court', () => {
		const servi = theme({ id: 'a', etat: 'manque', ton: 'info', motif: 'point_hors_parcelle', etat_libelle: 'Libellé servi' });
		expect(motifDe(servi)).toBe('Libellé servi');
		expect(statutDe(servi)?.texte).toBe('Libellé servi');
		const long = theme({ id: 'a', etat: 'manque', ton: 'info', motif: 'point_hors_parcelle', etat_libelle: 'Un libellé servi bien trop long pour un badge' });
		expect(statutDe(long)?.texte).toBe('Non publié');
		expect(statutDe(theme({ id: 'a', etat: 'manque', ton: 'info', motif: 'point_hors_parcelle' }))?.texte).toBe('Non publié');
		expect(statutDe(theme({ id: 'a', etat: 'manque', ton: 'info', etat_libelle: 'Non rattaché' }))?.texte).toBe('Non rattaché');
		// The wording of a gap is served: not served, none, never the code worded again.
		expect(motifDe(theme({ id: 'a', etat: 'manque', ton: 'info', motif: 'point_hors_parcelle' }))).toBeNull();
		expect(motifDe(theme({ id: 'a', etat: 'hors_couverture', ton: 'info', motif: 'out_of_coverage' }))).toBeNull();
		expect(motifDe(theme({ id: 'a', etat: 'panne', ton: 'info', motif: 'amont_indisponible' }))).toBe('La source n’a pas répondu');
	});

	// 4.9 lot 1: the same reserve read twice in a row, on the row and again in its detail.
	it('phrase du motif : terminée par un point, absente quand la ligne la dit déjà', () => {
		const servi = theme({ id: 'a', etat: 'manque', ton: 'info', etat_libelle: 'L’état des risques porte sur une autre parcelle' });
		expect(phraseMotif(servi, null)).toBe('L’état des risques porte sur une autre parcelle.');
		expect(phraseMotif(servi, 'L’état des risques porte sur une autre parcelle')).toBeNull();
		expect(phraseMotif(servi, 'Un autre libellé')).toBe('L’état des risques porte sur une autre parcelle.');
		expect(phraseMotif(theme({ id: 'a', etat: 'panne', ton: 'info', motif: 'amont_indisponible' }), null)).toBe('La source n’a pas répondu.');
		// 4.9 lot 5: the served alert of the row may say the same reason, with its full stop.
		expect(phraseMotif(servi, null, 'L’état des risques porte sur une autre parcelle.')).toBeNull();
		// A figure that answers needs no reason.
		expect(phraseMotif(theme({ id: 'p', etat: 'ok', ton: 'neutre', chiffres: CHIFFRE, motif: 'amont_indisponible' }), null)).toBeNull();
	});

	// 4.9 lot 5, « un état, un endroit »: a state without value is said by its badge, once.
	it('accroche de ligne : le mot du badge n’est pas écrit deux fois, une accroche qui dit autre chose reste', () => {
		const sans = (etat: string, accroche: string, extra: Record<string, unknown> = {}) => accrocheDeLigne(theme({ id: 'a', etat, ton: 'info', accroche, ...extra }));
		expect(sans('panne', 'Source indisponible')).toBeNull();
		expect(sans('hors_couverture', 'Hors couverture', { etat_libelle: 'Source sans couverture' })).toBeNull();
		expect(sans('non_concerne', 'Sans objet')).toBeNull();
		expect(sans('manque', 'Non publié', { etat_libelle: 'L’état des risques porte sur une autre parcelle' })).toBeNull();
		expect(sans('manque', 'Non rattaché', { etat_libelle: 'Non rattaché' })).toBeNull();
		// The generic word under a more precise badge is the same state said twice.
		expect(sans('manque', 'Non publié', { etat_libelle: 'Non rattaché' })).toBeNull();
		// Anything else the API serves on such a row is not the front's to hide (D-08).
		expect(sans('manque', 'Trois candidats', { etat_libelle: 'Non rattaché' })).toBe('Trois candidats');
		// A value is never touched: a zero keeps its « Aucun » beside its badge.
		expect(accrocheDeLigne(theme({ id: 'z', etat: 'zero', ton: 'rassurant', accroche: 'Aucun' }))).toBe('Aucun');
		expect(accrocheDeLigne(theme({ id: 'o', etat: 'ok', ton: 'neutre', accroche: '24 m', chiffres: CHIFFRE }))).toBe('24 m');
	});

	// 4.9 A2: said on the row only where « no value » could be read as « no risk ».
	it('raison de ligne : visible pour hors couverture, au détail pour non publié et sans objet, sans perte', () => {
		const long = 'L’état des risques porte sur une autre parcelle';
		const manque = theme({ id: 'a', etat: 'manque', ton: 'info', etat_libelle: long });
		expect(raisonDeLigne(manque)).toBeNull();
		expect(phraseMotif(manque, raisonDeLigne(manque))).toBe(`${long}.`);

		const sansObjet = theme({ id: 'b', etat: 'non_concerne', ton: 'neutre', etat_libelle: 'Commune hors de la liste' });
		expect(raisonDeLigne(sansObjet)).toBeNull();
		expect(phraseMotif(sansObjet, raisonDeLigne(sansObjet))).toBe('Commune hors de la liste.');

		const hors = theme({ id: 'c', etat: 'hors_couverture', ton: 'info', etat_libelle: 'Source sans couverture' });
		expect(raisonDeLigne(hors)).toBe('Source sans couverture');
		expect(phraseMotif(hors, raisonDeLigne(hors))).toBeNull();

		// A served reason on a row that has a value is no absence: it stays.
		expect(raisonDeLigne(theme({ id: 'd', etat: 'ok', ton: 'neutre', etat_libelle: 'Lecture partielle', chiffres: CHIFFRE }))).toBe('Lecture partielle');
		// The word of the badge is not a reason.
		expect(raisonDeLigne(theme({ id: 'e', etat: 'manque', ton: 'info', etat_libelle: 'Non rattaché' }))).toBeNull();
	});

	// 4.9 A4 and its reserve: out of coverage must not pass for « no risk » behind the fold.
	it('repli « sans valeur » : le compte, et le hors couverture distingué', () => {
		const manque = theme({ id: 'a', etat: 'manque', ton: 'info' });
		const hors = theme({ id: 'b', etat: 'hors_couverture', ton: 'info' });
		expect(repliSansValeur([manque], false)).toBe('1 critère sans valeur ici');
		expect(repliSansValeur([manque, manque], false)).toBe('2 critères sans valeur ici');
		expect(repliSansValeur([manque, manque, hors], false)).toBe('3 critères sans valeur ici, dont 1 hors couverture');
		expect(repliSansValeur([manque, hors], true)).toBe('2 autres critères sans valeur ici, dont 1 hors couverture');
		expect(repliSansValeur([manque], true)).toBe('1 autre critère sans valeur ici');
		expect(repliSansValeur([hors], false)).toBe('1 critère sans valeur ici, hors couverture');
		expect(repliSansValeur([hors, hors], false)).toBe('2 critères sans valeur ici, tous hors couverture');
	});

	it('le badge suit le ton servi, jamais recalculé ; la vigilance servie est gardée', () => {
		// The API serves neutral on a link that is not sure (04.7-17): the badge reads it as is.
		const probable = theme({ id: 'b', etat: 'ok', ton: 'neutre', rattachement: { entite: 'x', confiance: 'probable' } });
		expect(statutDe(probable)).toBeNull();
		const sur = theme({ id: 'b', etat: 'ok', ton: 'rassurant', rattachement: { entite: 'x', confiance: 'sure', maillons: [{ lien: 'x', confiance: 'sure' }] } });
		expect(statutDe(sur)?.texte).toBe('Favorable');
		// A report that would break the rule is detected by the A1 invariant, never corrected here.
		const nonConforme = theme({ id: 'b', etat: 'ok', ton: 'rassurant', rattachement: { entite: 'x', confiance: 'probable' } });
		expect(statutDe(nonConforme)?.texte).toBe('Favorable');
		expect(verdictCoherent(nonConforme)).toBe(false);
		const partiel = theme({ id: 'b', etat: 'ok', ton: 'neutre', pannes_partielles: ['ppr'] });
		expect(statutDe(partiel)).toBeNull();
		expect(lecturePartielle(partiel)).toMatch(/^Lecture partielle/);
		expect(statutDe(dpe)).toEqual({ cle: 'vigilance', texte: 'Vigilance' });
	});

	it('statuts des états : mots du produit, panne = Source indisponible', () => {
		expect(statutDe(theme({ id: 'a', etat: 'non_concerne', ton: 'neutre' }))?.texte).toBe('Sans objet');
		expect(statutDe(theme({ id: 'a', etat: 'hors_couverture', ton: 'info' }))?.texte).toBe('Hors couverture');
		expect(statutDe(theme({ id: 'a', etat: 'zero', ton: 'neutre' }))?.texte).toBe('Zéro');
		expect(statutDe(theme({ id: 'a', etat: 'manque', ton: 'info', motif: 'point_hors_parcelle' }))?.cle).toBe('manque');
	});

	it('étiquette de source courte (A2)', () => {
		expect(dpe.sources.map(etiquetteSource)).toEqual(['ADEME', 'RNB']);
	});
});

describe('import absent (04.7-19)', () => {
	it('un motif du rapport est écrit en français accentué, jamais le code servi', () => {
		expect(aplatirDetails({ lignes: { etat: 'hors_couverture', motif: 'horaires_gtfs_non_importes' } }).flatMap((g) => (g.type === 'groupe' ? g.enfants : [g]))).toContainEqual(
			expect.objectContaining({ libelle: 'Motif', valeur: 'Horaires des lignes pas encore importés' })
		);
		expect(libelleCode('moyennes_geodair_non_importees')).toBe('Moyennes annuelles Geodair pas encore importées');
		for (const code of [
			'loyers_reference_non_importes',
			'majoration_non_importee',
			'mandat_non_importe',
			'calendrier_outre_mer_non_servi',
			'carreau_impute',
			'commune_indeterminee',
			'departement_non_heberge',
			'taux_manquants_au_departement'
		]) {
			expect(libelleCode(code), code).not.toBe(humaniser(code));
		}
	});

	it('le motif d’une source importée mais pas pour ce département est dit en mots, jamais « Import absent »', () => {
		const t = theme({ id: 'construction', etat: 'panne', ton: 'info', motif: 'import_absent' });
		expect(motifDe(t)).toBe('Données pas encore importées pour ce département');
		expect(motifDe(theme({ id: 'construction', etat: 'panne', ton: 'info', motif: 'lecture_indisponible' }))).toBe('Lecture indisponible');
	});
});

describe('échelle visible', () => {
	it('chaque niveau a son libellé écrit, y compris un niveau inconnu', () => {
		for (const niveau of ['adresse', 'batiment', 'parcelle', 'iris', 'commune', 'departement', 'udi', 'station', 'zone_inconnue']) {
			const g = { niveau, rayon_m: null };
			expect(libelleEchelle(g, 'Nantes').length).toBeGreaterThan(3);
		}
		expect(libelleEchelle({ niveau: 'rayon', rayon_m: 500 }, null)).toMatch(/rayon de 500\s?m/);
	});

});

describe('détail générique', () => {
	it('rend le détail du DPE réel : répartition, groupes, aucun nul écrit', () => {
		const noeuds = aplatirDetails(projeterTheme(dpeReel)!.details);
		expect(noeuds[0]).toMatchObject({ type: 'repartition_dpe' });
		const texte = JSON.stringify(noeuds);
		expect(texte).toContain('Aucun document valide');
		expect(texte).not.toContain('null');
	});

	it('un thème inconnu du front s’affiche à partir de ses seuls champs', () => {
		const eau = theme({
			id: 'eau_potable',
			section: 'vivre',
			titre: 'Eau du robinet',
			etat: 'ok',
			ton: 'rassurant',
			granularite: { niveau: 'udi', rayon_m: null },
			chiffres: [{ id: 'durete', libelle: 'Dureté', valeur: 12, unite: 'degre_f' }],
			details: { prelevements: 214, non_conformes: 0, prix_m3: { valeur: 4.1, annee: 2024 }, controles: [{ date: '2026-09-02', parametre: 'nitrates', conforme: true }] }
		});
		expect(statutDe(eau)?.texte).toBe('Favorable');
		const noeuds = aplatirDetails(eau.details);
		expect(noeuds.map((n) => n.type)).toEqual(['valeur', 'valeur', 'groupe', 'table']);
		expect(libelleEchelle(eau.granularite, 'Nantes')).toBe('unité de distribution d’eau');
		expect(libelleEchelle({ niveau: 'zone_inconnue', rayon_m: null }, null)).toBe(`échelle${String.fromCharCode(0xa0)}: zone inconnue`);
	});

	it('prix_affiche : aucun prix masqué, ni dans un tableau ni dans un objet', () => {
		const noeuds = aplatirDetails({
			ventes: [
				{ date: '2025-05-23', surface_batie: 95, prix: 200000, prix_m2: 2105, prix_affiche: false },
				{ date: '2024-01-12', surface_batie: 80, prix: 150000, prix_m2: 1875, prix_affiche: true }
			],
			derniere: { date: '2025-05-23', prix: 200000, prix_m2: 2105, prix_affiche: false }
		});
		const texte = JSON.stringify(noeuds);
		expect(texte).not.toMatch(/200\s?000|2\s?105/);
		expect(texte).toMatch(/150\s?000/);
		expect(texte).toContain('Prix non affiché');
		expect(noeuds[0]).toMatchObject({ type: 'table', note: `1 prix n’est pas affiché${String.fromCharCode(0xa0)}: vente isolée récente.` });
	});
});

describe('invariants', () => {
	it('A1 : le statut ne contredit jamais l’état, les chiffres ni le rattachement ; un rapport non conforme est détecté', () => {
		const conformes = [
			projeterTheme(dpeReel)!,
			theme({ id: 'a', etat: 'panne', ton: 'info' }),
			theme({ id: 'b', etat: 'ok', ton: 'neutre', rattachement: { entite: 'x', confiance: 'incertaine' } }),
			theme({ id: 'c', etat: 'ok', ton: 'neutre', pannes_partielles: ['x'] }),
			theme({ id: 'd', etat: 'zero', ton: 'vigilance' })
		];
		for (const t of conformes) expect(verdictCoherent(t), t.id).toBe(true);
		const nonConformes = [
			theme({ id: 'b', etat: 'ok', ton: 'rassurant', rattachement: { entite: 'x', confiance: 'incertaine' } }),
			// A served `sure` over a weaker link breaks the weakest-link rule.
			theme({ id: 'e', etat: 'ok', ton: 'rassurant', rattachement: { entite: 'x', confiance: 'sure', maillons: [{ lien: 'x', confiance: 'incertaine' }] } }),
			// A malformed chain cannot vouch for a sure link either.
			theme({ id: 'f', etat: 'ok', ton: 'rassurant', rattachement: { entite: 'x', confiance: 'sure', maillons: [{ confiance: 'incertaine' }] } }),
			theme({ id: 'g', etat: 'ok', ton: 'rassurant', rattachement: { entite: 'x', confiance: 'sure', maillons: 'bad' } }),
			theme({ id: 'c', etat: 'ok', ton: 'rassurant', pannes_partielles: ['x'] })
		];
		for (const t of nonConformes) expect(verdictCoherent(t), t.id).toBe(false);
	});

	it('A5 : un nul ou un champ absent ne devient jamais 0 ; un 0 servi reste 0', () => {
		expect(aucunZeroFabrique(theme({ id: 'a', etat: 'ok', ton: 'neutre', details: { nb: null, autre: 0 } }))).toBe(true);
		const noeuds = aplatirDetails({ nb: null, autre: 0 });
		expect(noeuds).toEqual([{ type: 'valeur', libelle: 'Autre', valeur: '0' }]);
		expect(aucunZeroFabrique(projeterTheme(dpeReel)!)).toBe(true);
	});

	it('un loyer s’écrit comme sur la fiche commune : 14,35 sur la commune, 14,4 servi sur l’adresse', () => {
		const adresse = valeurChiffre({ id: 'maison', libelle: 'Loyer maison', valeur: 14.4, unite: 'euro_per_m2_mois', millesime: null });
		expect(adresse.split(String.fromCharCode(0xa0))[0]).toBe(formatBareNumber('loyers.loyer_m2_maison', 14.35));
	});

	it.each([
		['pour_mille', 6.3, /^6,3\s‰$/],
		['degre_francais', 12.3, /^12,3\s°f$/],
		['indice_sur_6', 3, /^3\ssur\s6$/]
	])('aucun code d’unité brut : %s', (unite, valeur, attendu) => {
		expect(valeurChiffre({ id: 'a', libelle: 'a', valeur, unite, millesime: null })).toMatch(attendu);
	});

	it('valeur d’un chiffre par unité', () => {
		expect(valeurChiffre({ id: 'a', libelle: 'a', valeur: 14.5, unite: 'euro_per_m2_mois', millesime: null })).toMatch(/^14,5\s€\/m²\/mois$/);
		expect(valeurChiffre({ id: 'a', libelle: 'a', valeur: 15, unite: 'euro_per_m2_mois', millesime: null })).toMatch(/^15\s€\/m²\/mois$/);
		expect(valeurChiffre({ id: 'a', libelle: 'a', valeur: '2026-08-26', unite: 'date', millesime: null })).toBe('26 août 2026');
		expect(valeurChiffre({ id: 'a', libelle: 'a', valeur: 1400, unite: 'metre', millesime: null })).toMatch(/1,4\skm/);
	});
});

const VIDE: ResumeServi = {
	pointsAdresse: [],
	pointsSecteur: [],
	favorablesAdresse: [],
	favorablesSecteur: [],
	pannes: [],
	sourcesEnPanne: [],
	partiels: [],
	sansObjet: [],
	sansValeur: [],
	lus: 0,
	lusAdresse: 0,
	lusSecteur: 0,
	total: 0,
	parSection: {
		batiment: { vigilance: 0, pannes: 0, lus: 0, total: 0 },
		risques: { vigilance: 0, pannes: 0, lus: 0, total: 0 },
		marche: { vigilance: 0, pannes: 0, lus: 0, total: 0 },
		vivre: { vigilance: 0, pannes: 0, lus: 0, total: 0 }
	}
};
const servi = (r: Partial<ResumeServi>): ResumeServi => ({ ...VIDE, ...r });

describe('résumé servi par l’API (04.7-17)', () => {
	const themes = [
		theme({ id: 'argiles', etat: 'ok', ton: 'vigilance' }),
		theme({ id: 'sols', etat: 'ok', ton: 'vigilance', granularite: { niveau: 'rayon', rayon_m: 500 } }),
		theme({ id: 'industrie', etat: 'ok', ton: 'vigilance', granularite: { niveau: 'rayon', rayon_m: 5000 } }),
		theme({ id: 'radon', etat: 'ok', ton: 'vigilance', granularite: { niveau: 'commune', rayon_m: null } }),
		theme({ id: 'fibre', section: 'vivre', etat: 'ok', ton: 'rassurant' }),
		theme({ id: 'eau', section: 'vivre', etat: 'ok', ton: 'rassurant', granularite: { niveau: 'commune', rayon_m: null } }),
		theme({ id: 'inondation', etat: 'panne', ton: 'info', sources: [SOURCE_DIRECTE] }),
		theme({ id: 'cote', etat: 'non_concerne', ton: 'neutre' })
	];
	const manquant = theme({ id: 'loyers', section: 'marche', etat: 'manque', ton: 'info', motif: 'loyer_non_publie' });
	const tous = [...themes, manquant];
	// What the API serves for these themes; `sols` (500 m) on the address side, `industrie` (5 km) on
	// the area side, as its 1 km rule puts them. The front reads the split, it never makes it.
	const SERVI = servi({
		pointsAdresse: ['argiles', 'sols'],
		pointsSecteur: ['industrie', 'radon'],
		favorablesAdresse: ['fibre'],
		favorablesSecteur: ['eau'],
		pannes: ['inondation'],
		sourcesEnPanne: ['georisques'],
		sansObjet: ['cote'],
		sansValeur: ['loyers'],
		lus: 7,
		lusAdresse: 4,
		lusSecteur: 3,
		total: 9,
		parSection: {
			...VIDE.parSection,
			risques: { vigilance: 4, pannes: 1, lus: 5, total: 6 },
			marche: { vigilance: 0, pannes: 0, lus: 0, total: 1 },
			vivre: { vigilance: 0, pannes: 0, lus: 2, total: 2 }
		}
	});
	const r = lireResume(SERVI, tous)!;

	it('lit les points et favorables servis, rapportés aux lignes rendues ; pannes à part avec leur source mise en mots', () => {
		expect(r.pointsAdresse.map((t) => t.id)).toEqual(['argiles', 'sols']);
		expect(r.pointsSecteur.map((t) => t.id)).toEqual(['industrie', 'radon']);
		expect(r.favorablesAdresse.map((t) => t.id)).toEqual(['fibre']);
		expect(r.favorablesSecteur.map((t) => t.id)).toEqual(['eau']);
		expect(r.pannes.map((t) => t.id)).toEqual(['inondation']);
		expect(r.sourcesEnPanne).toEqual(['Géorisques']);
		expect([r.lus, r.lusAdresse, r.lusSecteur, r.total]).toEqual([7, 4, 3, 9]);
	});

	it('règle d’échelle : la répartition de l’API, jamais refaite (un rayon de 1 200 m servi côté adresse y reste)', () => {
		const large = theme({ id: 'bruit', etat: 'ok', ton: 'vigilance', granularite: { niveau: 'rayon', rayon_m: 1200 } });
		const proche = theme({ id: 'sismicite', etat: 'ok', ton: 'vigilance', granularite: { niveau: 'rayon', rayon_m: 1000 } });
		const lu = lireResume(servi({ pointsAdresse: ['bruit'], pointsSecteur: ['sismicite'] }), [large, proche])!;
		expect(lu.pointsAdresse.map((t) => t.id)).toEqual(['bruit']);
		expect(lu.pointsSecteur.map((t) => t.id)).toEqual(['sismicite']);
	});

	it('un point servi sans ligne rendue est écarté ; une source inconnue garde sa clé ; un résumé non servi reste non servi', () => {
		const lu = lireResume(servi({ pointsAdresse: ['fantome', 'argiles'], sourcesEnPanne: ['source_nouvelle'] }), tous)!;
		expect(lu.pointsAdresse.map((t) => t.id)).toEqual(['argiles']);
		expect(lu.sourcesEnPanne).toEqual(['source_nouvelle']);
		expect(lireResume(null, tous)).toBeNull();
	});

	it('la phrase de tête dit la panne, jamais un « rien » seul', () => {
		const NNBSP = String.fromCharCode(0x202f);
		expect(texteDe(phraseEnBref(r))).toBe('2 points de vigilance à l’adresse ou autour, 2 pour un périmètre plus large que l’adresse, et 1 critère n’a pas pu être lu.');
		const lueEtPanne = lireResume(servi({ pannes: ['inondation'], favorablesAdresse: ['fibre'], lus: 1 }), tous)!;
		expect(texteDe(phraseEnBref(lueEtPanne))).toBe('Rien à signaler parmi les critères lus, et 1 critère n’a pas pu être lu.');
		expect(texteDe(phraseEnBref(lireResume(servi({ lus: 1 }), tous)!))).toBe('Rien à signaler, ni à l’adresse ou autour, ni pour toute la commune.');
		// Nothing read at all: never « rien à signaler »; the outage is said once.
		expect(texteDe(phraseEnBref(lireResume(servi({ pannes: ['inondation'] }), tous)!))).toBe(`Aucun critère n’a pu être lu à cette adresse${NNBSP}; 1 critère sans réponse de sa source.`);
		// Two criteria of one source: two criteria, never « 2 sources ».
		const deux = [...tous, { ...themes[6], id: 'autre' }];
		expect(texteDe(phraseEnBref(lireResume(servi({ pannes: ['inondation', 'autre'] }), deux)!))).toBe(`Aucun critère n’a pu être lu à cette adresse${NNBSP}; 2 critères sans réponse de leur source.`);
		expect(texteDe(phraseEnBref(lireResume(servi({ sansValeur: ['loyers'] }), tous)!))).toBe('Aucun critère n’a pu être lu à cette adresse.');
		// A missing value beside a read one: the reading is said partial, never complete.
		expect(texteDe(phraseEnBref(lireResume(servi({ sansValeur: ['loyers'], lus: 1 }), tous)!))).toBe('Rien à signaler parmi les critères lus.');
	});

	// The six sentences of UI-SPEC v4 § 7.2. Only the words changed since 04.7-17 (« à l’adresse ou autour »,
	// « Rien à l’adresse ni autour »): the branches and the served counts are the same.
	describe('les six phrases d’« En bref » (§ 7.2)', () => {
		const NNBSP = String.fromCharCode(0x202f);
		const ids = (prefixe: string, n: number): string[] => Array.from({ length: n }, (_, i) => `${prefixe}${i + 1}`);
		/** A summary of `a` points at the address, `c` for the area, `p` outages, `v` missing values; `lus` criteria read. */
		const resumeDe = (a: number, c: number, p: number, v: number, lus: number, niveau = 'commune') => {
			const servis = { pointsAdresse: ids('adr', a), pointsSecteur: ids('sec', c), pannes: ids('pan', p), sansValeur: ids('val', v) };
			const lignes = Object.values(servis).flat().map((id) => theme({ id, granularite: { niveau: id.startsWith('sec') ? niveau : 'adresse', rayon_m: null } }));
			return lireResume(servi({ ...servis, lus }), lignes)!;
		};
		const forts = (segments: { texte: string; fort?: boolean }[]) => segments.filter((s) => s.fort).map((s) => s.texte);

		it.each(['epci', 'departement', 'iris', 'arrondissement'])('A4: %s widens the sentence while keeping its counts and outage clause', (niveau) => {
			const segments = phraseEnBref(resumeDe(2, 3, 7, 0, 5, niveau));
			expect(texteDe(segments)).toBe('2 points de vigilance à l’adresse ou autour, 3 pour un périmètre plus large que l’adresse, et 7 critères n’ont pas pu être lus.');
			expect(forts(segments)).toEqual(['2 points de vigilance', '3', '7 critères']);
			expect(texteDe(phraseEnBref(resumeDe(0, 1, 0, 0, 4, niveau)))).toBe(`Rien à l’adresse ni autour${NNBSP}; 1 point de vigilance pour un périmètre plus large que l’adresse.`);
		});

		it('1. points at the address and for the area, and outages: each count in strong', () => {
			const segments = phraseEnBref(resumeDe(2, 3, 7, 0, 5));
			expect(texteDe(segments)).toBe('2 points de vigilance à l’adresse ou autour, 3 pour toute la commune, et 7 critères n’ont pas pu être lus.');
			expect(forts(segments)).toEqual(['2 points de vigilance', '3', '7 critères']);
		});

		it('2. points at the address only, nothing missing: none for the area is said', () => {
			const segments = phraseEnBref(resumeDe(1, 0, 0, 0, 4));
			expect(texteDe(segments)).toBe('1 point de vigilance à l’adresse ou autour, aucun pour toute la commune.');
			expect(forts(segments)).toEqual(['1 point de vigilance']);
			// Something missing beside it: « aucun » would claim a complete reading, it is not said.
			expect(texteDe(phraseEnBref(resumeDe(1, 0, 0, 1, 4)))).toBe('1 point de vigilance à l’adresse ou autour.');
		});

		it('3. nothing at the address, points for the area: « Rien à l’adresse ni autour », the outage said after', () => {
			expect(texteDe(phraseEnBref(resumeDe(0, 1, 0, 0, 4)))).toBe(`Rien à l’adresse ni autour${NNBSP}; 1 point de vigilance pour toute la commune.`);
			// A reading that is not complete says so (« parmi les critères lus »), and an outage is said in the same sentence.
			expect(texteDe(phraseEnBref(resumeDe(0, 2, 1, 0, 4)))).toBe(
				`Rien à l’adresse ni autour parmi les critères lus${NNBSP}; 2 points de vigilance pour toute la commune, et 1 critère n’a pas pu être lu.`
			);
		});

		it('4. nothing anywhere, nothing missing: « Rien à signaler », both scales named', () => {
			expect(texteDe(phraseEnBref(resumeDe(0, 0, 0, 0, 6)))).toBe('Rien à signaler, ni à l’adresse ou autour, ni pour toute la commune.');
		});

		it('5. nothing anywhere but something unread: never a bare « rien »', () => {
			const segments = phraseEnBref(resumeDe(0, 0, 2, 0, 3));
			expect(texteDe(segments)).toBe('Rien à signaler parmi les critères lus, et 2 critères n’ont pas pu être lus.');
			expect(forts(segments)).toEqual(['2 critères']);
			expect(texteDe(phraseEnBref(resumeDe(0, 0, 0, 1, 3)))).toBe('Rien à signaler parmi les critères lus.');
		});

		it('6. no criterion read: the outages are said once, in the same sentence', () => {
			const segments = phraseEnBref(resumeDe(0, 0, 12, 0, 0));
			expect(texteDe(segments)).toBe(`Aucun critère n’a pu être lu à cette adresse${NNBSP}; 12 critères sans réponse de leur source.`);
			expect(forts(segments)).toEqual(['12 critères']);
		});

		it('no sentence keeps the words of 04.7-17', () => {
			for (const [a, c, p, v, lus] of [[2, 3, 7, 0, 5], [1, 0, 0, 0, 4], [0, 1, 0, 0, 4], [0, 0, 0, 0, 6], [0, 0, 2, 0, 3], [0, 0, 12, 0, 0]]) {
				expect(texteDe(phraseEnBref(resumeDe(a, c, p, v, lus)))).not.toMatch(/propres? à cette adresse|ni à cette adresse/);
			}
		});
	});

});

describe('revue Codex 1 (04.7-03)', () => {
	const texte = (t: ThemeRapport) => JSON.stringify(aplatirDetails(t.details));

	it('#1 un prix masqué le reste dans tous les descendants de l’objet qui le masque', () => {
		const t = theme({ id: 'inconnu', etat: 'ok', ton: 'neutre', details: { vente: { prix_affiche: false, chiffres: { prix: 987654, prix_m2: 12345 } } } });
		expect(texte(t)).not.toMatch(/987|12[\s\u00a0\u202f]?345/);
		expect(texte(t)).toContain('Prix non affiché');
		const table = theme({ id: 'inconnu', etat: 'ok', ton: 'neutre', details: { ventes: [{ date: '2025-01-02', detail: { prix: 222333 }, prix_affiche: false }] } });
		expect(texte(table)).not.toMatch(/222/);
	});

	it('#2 « Favorable » exige un lien établi : sûr, ou une échelle sans entité à rattacher', () => {
		// The API never serves it (04.7-17); the invariant would see it.
		const batimentSansLien = theme({ id: 'inconnu', etat: 'ok', ton: 'rassurant', granularite: { niveau: 'batiment', rayon_m: null }, rattachement: null });
		expect(verdictCoherent(batimentSansLien)).toBe(false);
		const communeSansLien = theme({ id: 'inconnu', etat: 'ok', ton: 'rassurant', granularite: { niveau: 'commune', rayon_m: null }, rattachement: null });
		expect(statutDe(communeSansLien)?.texte).toBe('Favorable');
		const sur = theme({ id: 'inconnu', etat: 'ok', ton: 'rassurant', granularite: { niveau: 'batiment', rayon_m: null }, rattachement: { entite: 'batiment', confiance: 'sure' } });
		expect(statutDe(sur)?.texte).toBe('Favorable');
	});

	it('#4 une classe non servie reste non servie, les DPE sans classe sont gardés', () => {
		const t = theme({ id: 'inconnu', etat: 'ok', ton: 'neutre', details: { repartition: { A: 1, B: null, C: 0, unknown: 2 } } });
		const [noeud] = aplatirDetails(t.details);
		expect(noeud).toMatchObject({ type: 'repartition_dpe', inconnus: 2 });
		if (noeud.type !== 'repartition_dpe') throw new Error('répartition attendue');
		expect(noeud.classes.slice(0, 4)).toEqual([
			{ classe: 'A', n: 1 },
			{ classe: 'B', n: null },
			{ classe: 'C', n: 0 },
			{ classe: 'D', n: null }
		]);
		expect(aucunZeroFabrique(t)).toBe(true);
		// The served zero is the only zero rendered: a 0 written for B or D would fail here.
		const fabrique = { ...t, details: { repartition: { A: 1, C: 0 } } };
		expect(aucunZeroFabrique(fabrique)).toBe(true);
	});

	it('#5 une valeur imbriquée est rendue en texte, jamais perdue', () => {
		const t = theme({ id: 'eau', etat: 'ok', ton: 'neutre', details: { controles: [{ parametre: 'nitrates', mesure: { valeur: 61, unite: 'mg/L', limite: 50 } }], a: { b: { c: { d: 'profond' } } } } });
		const rendu = texte(t);
		expect(rendu).toContain('nitrates');
		expect(rendu).toContain('61');
		expect(rendu).toContain('mg/L');
		expect(rendu).toContain('50');
		expect(rendu).toContain('profond');
	});
});

// Lot 4 of 4.9: the two fiches say the same thing; `revenu_median` is the declared income (Q221), not a standard of living.
describe('labels shared with the commune fiche', () => {
	it.each([
		['taux_teom', 'Taxe d’ordures ménagères'],
		['indice_gini', 'Inégalités de revenus (indice de Gini)'],
		['revenu_median', 'Revenu déclaré médian']
	])('%s', (cle, libelle) => {
		expect(JSON.stringify(aplatirDetails({ [cle]: 1 }))).toContain(libelle);
	});
});
