// Projection of the fields the API serves since 04.7-17 (UI-SPEC § 7): the summary, the attachment
// sentence, the alert and links of an attachment, and the map of the head. A field absent or of the
// wrong type is null, never invented; the map is kept only on the site's own `/carte/` path.
import { describe, expect, it } from 'vitest';
import { projeterRapport, projeterTheme } from './rapport';

const point = (theme: string) => ({ theme, titre: theme, verdict: '.', granularite: { niveau: 'adresse', rayon_m: null } });
const SECTION = { vigilance: 0, pannes: 0, lus: 1, total: 1 };
const RESUME = {
	chiffres_cles: [],
	points_adresse: [point('dpe')],
	points_commune: [point('radon')],
	rassurants: [point('fibre')],
	rassurants_adresse: [point('fibre')],
	rassurants_commune: [],
	pannes: [point('argiles')],
	sources_en_panne: ['georisques'],
	non_concernes: [point('cote')],
	partiels: [],
	sans_valeur: [point('loyers')],
	lus: 4,
	lus_adresse: 3,
	lus_secteur: 1,
	total_criteres: 6,
	par_section: { batiment: SECTION, risques: { vigilance: 1, pannes: 1, lus: 1, total: 3 }, marche: SECTION, vivre: SECTION }
};
const CARTE = {
	images: { plan: '/carte/44109_zz01_00002/plan.png', parcelles: '/carte/44109_zz01_00002/parcelles.png' },
	emprise_m: 300,
	attribution: '© IGN (Plan IGN, Parcellaire Express)',
	licence: 'Licence Ouverte 2.0',
	geoportail: 'https://www.geoportail.gouv.fr/carte?c=-1.56,47.22&z=18'
};

describe('projeterRapport — champs servis (04.7-17)', () => {
	it('lit le résumé servi : identifiants de thèmes, sources en panne, comptes et sections', () => {
		const r = projeterRapport({ resume: RESUME, rattachement_phrase: 'Phrase servie 3.' });
		expect(r.resume).toEqual({
			pointsAdresse: ['dpe'],
			pointsSecteur: ['radon'],
			favorablesAdresse: ['fibre'],
			favorablesSecteur: [],
			pannes: ['argiles'],
			sourcesEnPanne: ['georisques'],
			partiels: [],
			sansObjet: ['cote'],
			sansValeur: ['loyers'],
			lus: 4,
			lusAdresse: 3,
			lusSecteur: 1,
			total: 6,
			parSection: RESUME.par_section
		});
		expect(r.rattachementPhrase).toBe('Phrase servie 3.');
	});

	it('un résumé incomplet ou mal typé n’est pas servi : null en entier, jamais complété', () => {
		const sans = (cle: string) => Object.fromEntries(Object.entries(RESUME).filter(([k]) => k !== cle));
		for (const cle of ['points_adresse', 'rassurants_commune', 'sources_en_panne', 'sans_valeur', 'lus_secteur', 'total_criteres', 'par_section']) {
			expect(projeterRapport({ resume: sans(cle) }).resume, cle).toBeNull();
		}
		expect(projeterRapport({ resume: { ...RESUME, lus: -1 } }).resume).toBeNull();
		expect(projeterRapport({ resume: { ...RESUME, lus: 2.5 } }).resume).toBeNull();
		expect(projeterRapport({ resume: { ...RESUME, par_section: { ...RESUME.par_section, vivre: { vigilance: 0 } } } }).resume).toBeNull();
		expect(projeterRapport({ resume: { ...RESUME, points_adresse: 'dpe' } }).resume).toBeNull();
		// One bad element voids the list, hence the summary: a shortened list would read « rien à signaler ».
		expect(projeterRapport({ resume: { ...RESUME, points_adresse: [null] } }).resume).toBeNull();
		expect(projeterRapport({ resume: { ...RESUME, points_commune: [{ theme: 42 }] } }).resume).toBeNull();
		expect(projeterRapport({ resume: { ...RESUME, sources_en_panne: [17] } }).resume).toBeNull();
		// The summary of a report served before 04.7-17 (points and key figures only).
		const ancien = projeterRapport({ resume: { points_adresse: [], points_commune: [], rassurants: [], chiffres_cles: [] } });
		expect(ancien.resume).toBeNull();
		expect(ancien.rattachementPhrase).toBeNull();
		expect(ancien.carte).toBeNull();
	});

	it('lit la carte servie sous /carte/, avec son crédit et son lien', () => {
		expect(projeterRapport({ carte: CARTE }).carte).toEqual({
			plan: '/carte/44109_zz01_00002/plan.png',
			parcelles: '/carte/44109_zz01_00002/parcelles.png',
			empriseM: 300,
			attribution: '© IGN (Plan IGN, Parcellaire Express)',
			licence: 'Licence Ouverte 2.0',
			geoportail: 'https://www.geoportail.gouv.fr/carte?c=-1.56,47.22&z=18'
		});
	});

	it('aucune image hors de l’origine du site : absolue, sans protocole, hors /carte/ ou remontant l’arborescence', () => {
		for (const plan of [
			'https://data.geopf.fr/wms-r/wms?LAYERS=x',
			'http://datacommune.fr/carte/k/plan.png',
			'//data.geopf.fr/carte/k/plan.png',
			'/autre/k/plan.png',
			'/carte/../admin/plan.png',
			'/carte/k/../plan.png',
			'/carte/../plan.png',
			'/carte/k/plan.png?x=1',
			'/carte/%2e%2e/plan.png',
			'/carte/.%2e/plan.png',
			'/carte/./plan.png',
			'/carte/k%2fx/plan.png',
			'/carte/k/parcelles.png',
			'/carte/k/admin.png',
			'carte/k/plan.png',
			'/carte/k/plan.svg',
			'javascript:alert(1)'
		]) {
			expect(projeterRapport({ carte: { ...CARTE, images: { ...CARTE.images, plan } } }).carte, plan).toBeNull();
		}
		expect(projeterRapport({ carte: { ...CARTE, emprise_m: 0 } }).carte).toBeNull();
		expect(projeterRapport({ carte: { ...CARTE, licence: '' } }).carte).toBeNull();
	});

	it('le lien Géoportail n’est gardé que sur son origine https ; la carte reste sans lui', () => {
		for (const geoportail of ['http://www.geoportail.gouv.fr/carte', 'https://geoportail.gouv.fr.evil.example/carte', 'javascript:alert(1)', 'pas une url']) {
			expect(projeterRapport({ carte: { ...CARTE, geoportail } }).carte?.geoportail, geoportail).toBeNull();
		}
	});
});

describe('projeterTheme — rattachement servi (04.7-17)', () => {
	const theme = (rattachement: unknown) =>
		projeterTheme({ id: 'dpe', section: 'batiment', titre: 'DPE', verdict: '.', etat: 'ok', ton: 'rassurant', granularite: { niveau: 'batiment', rayon_m: null }, rattachement })!;

	it('lit l’alerte, les maillons, la confiance et le ton servis, sans les recalculer', () => {
		const t = theme({ entite: 'x', confiance: 'probable', alerte: 'Alerte servie.', maillons: [{ lien: 'a', confiance: 'sure' }, { lien: 'b', confiance: 'probable' }] });
		expect(t.rattachement).toMatchObject({ confiance: 'probable', alerte: 'Alerte servie.', maillons: [{ lien: 'a', confiance: 'sure' }, { lien: 'b', confiance: 'probable' }] });
		// The API computes the weakest link and the tone (04.7-17): the front reads them.
		expect(t.ton).toBe('rassurant');
	});

	it('une confiance inconnue est la plus faible ; une chaîne malformée n’est pas une chaîne, jamais réparée', () => {
		const t = theme({ entite: 'x', confiance: '???', maillons: [{ lien: 'b', confiance: '???' }] });
		expect(t.rattachement?.maillons).toEqual([{ lien: 'b', confiance: 'incertaine' }]);
		expect(t.rattachement?.confiance).toBe('incertaine');
		expect(theme({ entite: 'x', confiance: 'sure', maillons: [{ confiance: 'incertaine' }] }).rattachement?.maillons).toBeNull();
		expect(theme({ entite: 'x', confiance: 'sure', maillons: 'bad' }).rattachement?.maillons).toBeNull();
		// Absent: a report served before 04.7-17.
		expect(theme({ entite: 'x', confiance: 'sure' }).rattachement?.maillons).toEqual([]);
	});

	it('un rapport conforme est lu tel que servi', () => {
		const t = theme({ entite: 'x', confiance: 'sure', maillons: [{ lien: 'x', confiance: 'sure' }] });
		expect(t.rattachement).toMatchObject({ confiance: 'sure', alerte: null });
		expect(t.ton).toBe('rassurant');
	});
});

// 04.8-03 (D-33, UI-SPEC v4 § 3.5): the long form of the scale of a value read at a Paris, Lyon or
// Marseille arrondissement names it from the served reference, never from a guess.
describe('projeterTheme — arrondissement servi (04.8-03, D-33)', () => {
	const theme = (granularite: unknown) => ({ id: 'qualite_air', titre: 'Qualité de l’air', verdict: 'Indice servi.', etat: 'ok', ton: 'neutre', section: 'vivre', granularite });

	it('keeps the served arrondissement of a theme read at the arrondissement', () => {
		const g = projeterTheme(theme({ niveau: 'arrondissement', rayon_m: null, arrondissement: { code: '75112', nom: '12e arrondissement de Paris' } }))?.granularite;
		expect(g).toEqual({ niveau: 'arrondissement', rayon_m: null, arrondissement: { code: '75112', nom: '12e arrondissement de Paris' } });
	});

	it('another level carries none; a malformed reference is dropped, never completed', () => {
		expect(projeterTheme(theme({ niveau: 'commune', rayon_m: null }))?.granularite).toEqual({ niveau: 'commune', rayon_m: null });
		expect(projeterTheme(theme({ niveau: 'arrondissement', rayon_m: null, arrondissement: { code: 75112 } }))?.granularite).toEqual({
			niveau: 'arrondissement',
			rayon_m: null
		});
	});
});
