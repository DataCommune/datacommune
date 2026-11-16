// Categories of the address fiche (4.9, reading of 06/11; D-08, D-09, D-32, D-40). The table covers the
// whole registry of the API (read from the theme files, like registre.test.ts) and follows CLASSEMENT.md,
// read from the file: a theme placed twice, not at all, out of its rank or at the wrong level fails. The
// placement, the closed row and the arrival line are pure functions of the served report.
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { GranulariteRapport, ResumeServi, ThemeRapport } from '$lib/api/rapport';
import {
	QUESTIONS,
	annoncePartie,
	cibleDeAncre,
	placerThemes,
	plusLargesQueLAdresse,
	signauxArrivee,
	signauxPartie,
	titreZoneTuiles,
	type PartieTable
} from './questions';

const RACINE = resolve(process.cwd(), '..');
const DOSSIER_THEMES = join(RACINE, 'api/backend/app/rapport/themes');
// The table of the 43 criteria, copied verbatim from the classement of 2026-11-06. The source
// document is part of the private project tracking, absent from the public tree: where it exists,
// a test below proves the copy has not drifted.
const CLASSEMENT = join(process.cwd(), 'src/lib/data/__fixtures__/classement-43-criteres.md');
const CLASSEMENT_SOURCE = join(RACINE, '.planning/research/2026-11-06-referentiel-lecture/CLASSEMENT.md');
const DEBUT_TABLE = '## Les 43 critères';
const FIN_TABLE = '## Page : Fiche adresse';
const NBSP = String.fromCharCode(0xa0);

/** The ids of the registry as the API declares them, one `THEME = DefinitionTheme(...)` per file. */
function idsDuRegistre(): string[] {
	return readdirSync(DOSSIER_THEMES)
		.filter((f) => f.endsWith('.py') && f !== '__init__.py')
		.map((f) => {
			const source = readFileSync(join(DOSSIER_THEMES, f), 'utf8');
			const bloc = source.slice(source.indexOf('THEME = DefinitionTheme('));
			return /id="([^"]+)"/.exec(bloc)?.[1] ?? '';
		});
}

const groupes = (p: PartieTable): string[] => p.groupes.flatMap((g) => [...g.ids]);
const toutes = (p: PartieTable): string[] => [...groupes(p), ...p.autres];

/** What the table gets wrong against the registry: missing, unknown and doubly placed ids. */
function ecarts(registre: readonly string[], table: readonly PartieTable[]) {
	const places = table.flatMap(toutes);
	return {
		manquants: registre.filter((id) => !places.includes(id)),
		inconnus: places.filter((id) => !registre.includes(id)),
		doubles: places.filter((id, i) => places.indexOf(id) !== i)
	};
}

const g = (niveau: string, rayon_m: number | null = null): GranulariteRapport => ({ niveau, rayon_m, arrondissement: null });

/** A served theme reduced to what the placement reads: its id and its scale. */
function theme(id: string, granularite: GranulariteRapport = g('adresse')): ThemeRapport {
	return {
		id,
		section: 'risques',
		titre: `Titre ${id}`,
		etat: 'ok',
		ton: 'neutre',
		granularite,
		verdict: `Verdict ${id}.`,
		chiffres: [],
		details: {},
		rattachement: null,
		sources: [],
		motif: null,
		etat_libelle: null,
		accroche: null,
		pannes_partielles: []
	};
}

function resume(partiel: Partial<ResumeServi> = {}): ResumeServi {
	return {
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
		},
		...partiel
	};
}

/** The scale each theme of the table has in the registry (UI-SPEC § 4.1, « Niveau servi » column). */
const NIVEAU_DE: Record<string, GranulariteRapport> = {
	altitude: g('adresse'),
	geologie: g('commune'),
	argiles: g('adresse'),
	mouvements_terrain: g('rayon', 500),
	sismicite: g('commune'),
	radon: g('commune'),
	debroussaillement: g('parcelle'),
	seveso: g('rayon', 5000),
	icpe_commune: g('commune'),
	nucleaire: g('adresse'),
	pollution_sols: g('parcelle'),
	catnat: g('commune'),
	risques_commune: g('commune'),
	secteur_bruit: g('adresse'),
	peb: g('adresse'),
	voie_ferree: g('rayon', 500),
	grand_axe: g('rayon', 500),
	transports: g('rayon', 1000),
	ecoles: g('rayon', 10000),
	equipements: g('rayon', 1000),
	antennes_5g: g('rayon', 1000),
	qualite_air: g('epci'),
	eau_potable: g('commune'),
	delinquance: g('commune'),
	revenus: g('commune')
};

/** The 43 criteria of CLASSEMENT.md: rank, id, category of the classement, level. */
function lignesDuClassement(): { rang: number; id: string; categorie: string; niveau: string }[] {
	const texte = readFileSync(CLASSEMENT, 'utf8');
	return texte.split(/\r?\n/).flatMap((ligne) => {
		const cases = ligne.split('|').map((c) => c.trim());
		if (!/^\d+$/.test(cases[1] ?? '')) return [];
		return [{ rang: Number(cases[1]), id: cases[3].replace(/`/g, ''), categorie: cases[4], niveau: cases[11] }];
	});
}

/** The four categories of the classement, under the titles Ianis chose. */
const TITRES_DU_CLASSEMENT: Record<string, string> = {
	'Les prix': 'Prix et ventes',
	'Le logement et sa parcelle': 'Logement et DPE',
	'La vie au quotidien': 'Autour de l’adresse',
	'Ce qui peut arriver ici': 'Risques'
};

const partie = (id: string): PartieTable => QUESTIONS.find((p) => p.id === id)!;
const servis = (p: PartieTable): ThemeRapport[] => toutes(p).map((id) => theme(id));
const placee = (themes: ThemeRapport[]) => placerThemes(themes).parties[0];

describe('QUESTIONS — la table couvre tout le registre servi et suit le classement', () => {
	const registre = idsDuRegistre();
	const classement = lignesDuClassement();

	it.skipIf(!existsSync(CLASSEMENT_SOURCE))('reads a copy of the 43 criteria equal to the classement it comes from', () => {
		const source = readFileSync(CLASSEMENT_SOURCE, 'utf8');
		const table = source.slice(source.indexOf(DEBUT_TABLE), source.indexOf(FIN_TABLE));
		expect(readFileSync(CLASSEMENT, 'utf8').replace(/\r\n/g, '\n')).toBe(table.replace(/\r\n/g, '\n'));
	});

	it('reads the 43 criteria of the classement', () => {
		expect(classement).toHaveLength(43);
	});

	it('has four categories, in the order of the classement, and exactly the ids of the registry (43), none placed twice', () => {
		expect(QUESTIONS.map((p) => p.id)).toEqual(['q-prix', 'q-logement', 'q-quotidien', 'q-risques']);
		expect(QUESTIONS.map((p) => p.titre)).toEqual(['Prix et ventes', 'Logement et DPE', 'Autour de l’adresse', 'Risques']);
		expect(registre.every(Boolean)).toBe(true);
		expect(registre).toHaveLength(43);
		const places = QUESTIONS.flatMap(toutes);
		expect(places).toHaveLength(43);
		expect(new Set(places)).toEqual(new Set(registre));
		expect(ecarts(registre, QUESTIONS)).toEqual({ manquants: [], inconnus: [], doubles: [] });
	});

	it('a private copy of the table without « zone_tendue » fails; so do an unknown id and a double', () => {
		const prive: PartieTable[] = QUESTIONS.map((p) => ({ ...p, autres: p.autres.filter((id) => id !== 'zone_tendue') }));
		expect(ecarts(registre, prive).manquants).toEqual(['zone_tendue']);
		const inconnu: PartieTable[] = [{ ...QUESTIONS[0], groupes: [{ titre: null, ids: ['theme_inconnu'] }] }, ...QUESTIONS.slice(1)];
		expect(ecarts(registre, inconnu).inconnus).toEqual(['theme_inconnu']);
		const double: PartieTable[] = [...QUESTIONS, { ...QUESTIONS[0], groupes: [{ titre: null, ids: ['altitude'] }], autres: [] }];
		expect(ecarts(registre, double).doubles).toEqual(['altitude']);
	});

	it('reads the 43 criteria of the classement', () => {
		expect(classement).toHaveLength(43);
		expect(new Set(classement.map((c) => c.id))).toEqual(new Set(registre));
	});

	it('puts each criterion in the category of the classement: the noise in Autour de l’adresse, the property tax in Prix et ventes', () => {
		for (const c of classement) expect(toutes(QUESTIONS.find((p) => p.titre === TITRES_DU_CLASSEMENT[c.categorie])!), c.id).toContain(c.id);
		expect(groupes(partie('q-quotidien'))).toContain('secteur_bruit');
		expect(partie('q-quotidien').autres).toContain('peb');
		expect(groupes(partie('q-prix'))).toContain('fiscalite');
	});

	it.each(['q-prix', 'q-logement', 'q-quotidien', 'q-risques'])('%s: each group follows the rank, the groups their best rank, the two-click criteria stay behind « Voir les N autres »', (id) => {
		const p = partie(id);
		const rang = (x: string) => classement.find((c) => c.id === x)!.rang;
		for (const g of p.groupes) expect([...g.ids]).toEqual([...g.ids].sort((a, b) => rang(a) - rang(b)));
		const tetes = p.groupes.map((g) => rang(g.ids[0]));
		expect(tetes).toEqual([...tetes].sort((a, b) => a - b));
		expect(p.autres).toEqual([...p.autres].sort((a, b) => rang(a) - rang(b)));
		for (const x of p.autres) expect(classement.find((c) => c.id === x)!.niveau, x).toBe('deux clics');
		for (const x of groupes(p)) expect(classement.find((c) => c.id === x)!.niveau, x).not.toBe('deux clics');
	});

	it('names a group only where a long list needs it, never with a number', () => {
		const titres = QUESTIONS.flatMap((p) => p.groupes.flatMap((g) => (g.titre ? [g.titre] : [])));
		expect(titres).toEqual(['Services et transports', 'Bruit', 'Sécurité et air', 'Eau et sol', 'Arrêtés et risques recensés']);
		for (const titre of [...titres, ...QUESTIONS.map((p) => p.titre)]) expect(titre).not.toMatch(/^\d/);
	});

	it('every row of a category is an anchor that opens that category', () => {
		for (const p of QUESTIONS) {
			for (const id of [...p.groupes.flatMap((g) => g.ids), ...p.autres]) expect(cibleDeAncre(`#critere-${id}`), id).toBe(p.id);
		}
	});

	it('keeps the radon in Risques, after its groups', () => {
		expect(partie('q-risques').autres).toContain('radon');
		expect(groupes(partie('q-risques'))).not.toContain('radon');
	});

	it('announces no value the head already answers', () => {
		const annonces = QUESTIONS.flatMap((p) => [...p.annonce]);
		for (const id of ['prix_commune', 'loyers', 'dpe']) expect(annonces).not.toContain(id);
		for (const p of QUESTIONS) for (const id of p.annonce) expect(toutes(p)).toContain(id);
	});
});

describe('placerThemes — ce qui est servi, à sa place', () => {
	it('keeps the order of the table whatever the served order, and cuts the list at « autres »', () => {
		const dans = servis(partie('q-risques'));
		const a = placerThemes(dans);
		expect(a).toEqual(placerThemes([...dans].reverse()));
		expect(a.parties[0].premiers.map((t) => t.id)).toEqual(groupes(partie('q-risques')));
		expect(a.parties[0].groupes.map((g) => g.titre)).toEqual(['Eau et sol', 'Arrêtés et risques recensés']);
		expect(a.parties[0].autres.map((t) => t.id)).toEqual(partie('q-risques').autres);
	});

	it('keeps a theme at its place whatever its scale or its state', () => {
		const commune = theme('inondation', g('commune'));
		const panne = { ...theme('radon', g('commune')), etat: 'panne' as const };
		const p = placee([commune, panne, theme('argiles')]);
		expect(p.premiers.map((t) => t.id)).toEqual(['inondation', 'argiles']);
		expect(p.autres.map((t) => t.id)).toEqual(['radon']);
	});

	it('keeps a group only when it has a served row', () => {
		const p = placerThemes([theme('catnat', g('commune')), theme('radon', g('commune'))]).parties[0];
		expect(p.groupes.map((x) => x.titre)).toEqual(['Arrêtés et risques recensés']);
	});

	it('omits the category without a served theme', () => {
		const { parties, autres } = placerThemes([theme('inondation'), theme('zonage_abc', g('commune'))]);
		expect(parties.map((p) => p.id)).toEqual(['q-prix', 'q-risques']);
		expect(parties[0].premiers).toEqual([]);
		expect(parties[0].autres.map((t) => t.id)).toEqual(['zonage_abc']);
		expect(autres).toEqual([]);
		expect(placerThemes([])).toEqual({ parties: [], autres: [] });
	});

	it('sends a served theme absent from the table to « autres », in the served order, in no category', () => {
		const { parties, autres } = placerThemes([theme('nouveau_b'), theme('inondation'), theme('nouveau_a', g('commune'))]);
		expect(autres.map((t) => t.id)).toEqual(['nouveau_b', 'nouveau_a']);
		expect(parties.flatMap((p) => [...p.premiers, ...p.autres]).map((t) => t.id)).toEqual(['inondation']);
	});
});

describe('cibleDeAncre — une ancre ouvre sa catégorie', () => {
	it('opens the category of a criterion, wherever the row stands in it', () => {
		expect(cibleDeAncre('#critere-inondation')).toBe('q-risques');
		expect(cibleDeAncre('#critere-radon')).toBe('q-risques');
		expect(cibleDeAncre('#critere-secteur_bruit')).toBe('q-quotidien');
		expect(cibleDeAncre('#critere-fiscalite')).toBe('q-prix');
	});

	it('opens the category of a list, of the similar sales and of its own anchor', () => {
		expect(cibleDeAncre('#documents')).toBe('q-logement');
		expect(cibleDeAncre('#ventes')).toBe('q-prix');
		expect(cibleDeAncre('#ventes-adresse')).toBe('q-prix');
		expect(cibleDeAncre('#s-comparables')).toBe('q-prix');
		expect(cibleDeAncre('#q-risques')).toBe('q-risques');
	});

	it('leaves the other anchors alone', () => {
		for (const hash of ['', '#lire', '#sources', '#bref', '#critere-inconnu', '#ventes-ressemblantes']) expect(cibleDeAncre(hash), hash).toBeNull();
	});
});

describe('signauxArrivee — la ligne d’arrivée nomme ce qui touche l’adresse', () => {
	it('keeps the served points at the address, in the served order', () => {
		const points = [{ id: 'inondation' }, { id: 'secteur_bruit' }];
		expect(signauxArrivee(points)).toEqual(points);
		expect(signauxArrivee([])).toEqual([]);
	});
});

describe('annoncePartie — une ligne fermée reprend les accroches servies (R5, D-08)', () => {
	const avec = (id: string, partiel: Partial<ThemeRapport>, granularite = g('adresse')): ThemeRapport => ({ ...theme(id, granularite), ...partiel });

	it('repeats the served headline, under the title when it does not say what it counts, « commune » at that scale', () => {
		const prix = placee([
			avec('ventes_parcelle', { accroche: '3 ventes sur la parcelle', titre: 'Ventes de la parcelle' }, g('parcelle')),
			avec('fiscalite', { accroche: '53,37 %', titre: 'Taxe foncière' }, g('commune'))
		]);
		expect(annoncePartie(prix)).toEqual(['3 ventes sur la parcelle', `Taxe foncière${NBSP}: 53,37 % (commune)`]);
	});

	it('says a served zero under its title, never alone', () => {
		const zero = avec('ventes_parcelle', { etat: 'zero', accroche: 'Aucun', titre: 'Ventes de la parcelle' }, g('parcelle'));
		expect(annoncePartie(placee([zero]))).toEqual([`Ventes de la parcelle${NBSP}: Aucun`]);
	});

	it('takes no state without an answer for a result: manque, hors couverture, panne, sans objet', () => {
		for (const etat of ['manque', 'hors_couverture', 'panne', 'non_concerne'] as const) {
			const absent = avec('fiscalite', { etat, accroche: 'Non publié' }, g('commune'));
			expect(annoncePartie(placee([absent, theme('loyers', g('commune'))])), etat).toEqual(['2 critères']);
		}
	});

	it('falls back on a named count without a headline, and agrees', () => {
		expect(annoncePartie(placee([theme('fiscalite')]))).toEqual(['1 critère']);
	});

	it('invents nothing: a theme without a served headline says nothing on the closed row', () => {
		const p = placee([theme('catnat', g('commune')), avec('risques_commune', { accroche: '22 risques recensés' }, g('commune'))]);
		expect(annoncePartie(p)).toEqual(['22 risques recensés (commune)']);
	});
});

describe('signauxPartie — les comptes des listes servies, jamais additionnés d’une échelle à l’autre', () => {
	const risques = placerThemes(servis(partie('q-risques')).map((t) => ({ ...t, granularite: NIVEAU_DE[t.id] ?? t.granularite }))).parties[0];
	const textes = (r: ResumeServi | null, p = risques) => signauxPartie(p, r).map((s) => s.texte);

	it('counts the vigilances at the address, then the wider ones, in words, with the tone of each', () => {
		const r = resume({ pointsAdresse: ['inondation', 'argiles'], pointsSecteur: ['catnat', 'radon'] });
		expect(signauxPartie(risques, r)).toEqual([
			{ texte: '2 vigilances à l’adresse ou autour', ton: 'vigilance' },
			{ texte: '2 pour toute la commune', ton: 'discret' }
		]);
	});

	it('says the word when the wider vigilances are alone, and agrees', () => {
		expect(textes(resume({ pointsSecteur: ['catnat'] }))).toEqual(['1 vigilance pour toute la commune']);
		expect(textes(resume({ pointsSecteur: ['catnat', 'radon'] }))).toEqual(['2 vigilances pour toute la commune']);
	});

	it('names a wider scale as soon as one counted entry is not the commune, only then', () => {
		expect(textes(resume({ pointsSecteur: ['seveso'] }))).toEqual(['1 vigilance pour un périmètre plus large que l’adresse']);
	});

	it('names an outage, keeps it apart from the gaps, and says the out of coverage of the gaps', () => {
		const horsCouverture = { ...theme('geologie', g('commune')), etat: 'hors_couverture' as const };
		const p = placerThemes([theme('inondation'), { ...theme('radon', g('commune')), titre: 'Radon' }, { ...theme('seveso'), titre: 'Sites Seveso' }, horsCouverture, theme('altitude')]).parties[0];
		const r = resume({ pannes: ['radon', 'seveso'], sansValeur: ['geologie', 'altitude'] });
		expect(signauxPartie(p, r)).toEqual([
			{ texte: `Source indisponible${NBSP}: Sites Seveso, Radon`, ton: 'panne' },
			{ texte: '2 critères sans valeur ici, dont 1 hors couverture', ton: 'discret' }
		]);
	});

	it('counts an outage served twice once, as an outage', () => {
		expect(textes(resume({ pannes: ['seveso'], sansValeur: ['seveso', 'radon'] }))).toEqual([`Source indisponible${NBSP}: Titre seveso`, '1 critère sans valeur ici']);
	});

	it('counts only the ids held by the category, whatever the lists carry', () => {
		const ailleurs = resume({ pointsAdresse: ['inondation', 'dpe'], pointsSecteur: ['zonage_abc'], pannes: ['fibre'], sansValeur: ['loyers'] });
		expect(textes(ailleurs)).toEqual(['1 vigilance à l’adresse ou autour']);
	});

	it('says nothing at zero and computes nothing without a served summary', () => {
		expect(signauxPartie(risques, resume())).toEqual([]);
		expect(signauxPartie(risques, null)).toEqual([]);
	});
});

describe('plusLargesQueLAdresse — la coupe servie (D-32)', () => {
	it('keeps the themes beyond 1 km, the list and its extension alike', () => {
		const p = placerThemes([theme('inondation'), theme('catnat', g('commune')), theme('seveso', g('rayon', 5000)), theme('radon', g('commune')), theme('altitude')]).parties[0];
		expect(plusLargesQueLAdresse(p).map((t) => t.id)).toEqual(['catnat', 'seveso', 'radon']);
	});
});

describe('titreZoneTuiles — le titre de la zone des tuiles suit leur nature (D-40, § 4.3)', () => {
	const FIN = `${NBSP}: ne dit pas si l’adresse est concernée`;
	const larges = `Pour un périmètre plus large que l’adresse${FIN}`;

	it('all communal: « Pour toute la commune de {nom} : ne dit pas si l’adresse est concernée », no-break space before the colon', () => {
		const tuiles = [theme('geologie', g('commune')), theme('radon', g('commune'))];
		expect(titreZoneTuiles(tuiles, 'Nantes')).toBe(`Pour toute la commune de Nantes${FIN}`);
		expect(titreZoneTuiles(tuiles, 'Nantes')).toContain(`${NBSP}:`);
	});

	it('names no commune when the report serves none, and guesses none', () => {
		expect(titreZoneTuiles([theme('geologie', g('commune'))], null)).toBe(`Pour toute la commune${FIN}`);
	});

	it.each([
		['a radius of 5 km (seveso)', g('rayon', 5000)],
		['a radius of 1 001 m', g('rayon', 1001)],
		['an IRIS', g('iris')],
		['an arrondissement', g('arrondissement')],
		['an intercommunality', g('epci')],
		['a department', g('departement')],
		['an unknown level', g('niveau_futur')]
	])('one tile of %s: « Pour un périmètre plus large que l’adresse… »', (_nom, granularite) => {
		expect(titreZoneTuiles([theme('seveso', granularite)], 'Nantes')).toBe(larges);
	});

	it('a single wider tile among communal ones is enough', () => {
		const tuiles = [theme('geologie', g('commune')), theme('seveso', g('rayon', 5000)), theme('radon', g('commune'))];
		expect(titreZoneTuiles(tuiles, 'Nantes')).toBe(larges);
	});

	it('no tile, no title', () => {
		expect(titreZoneTuiles([], 'Nantes')).toBeNull();
	});
});
