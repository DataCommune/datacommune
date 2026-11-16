// What the departement page says (04.7-10, decision 6): four cards « value + definition + base »,
// each following the state of its own block; the list of principal communes; the lead of the page;
// and the structured data, emitted only for an indexed page that serves a figure. Nothing is
// computed from the figures: the page reads the served model.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { getExplication } from './explications';
import {
	DEPARTEMENT_22,
	DEPARTEMENT_29,
	DEPARTEMENT_44,
	DEPARTEMENT_75,
	DEPARTEMENTS_LISTE
} from './__fixtures__/departement';
import {
	annuaireParLettre,
	aDesChiffres,
	cartesDepartement,
	cartesEssentiel,
	voletCarte,
	descriptionDataset,
	descriptionMeta,
	donneesStructurees,
	donneesStructureesListe,
	etatLigneDepartement,
	filAriane,
	lienToutesLesCommunes,
	ligneSource,
	lignesCommunesPrincipales,
	nomDepartementDuFil,
	phraseImportation,
	phraseListeVide,
	phrasePortee,
	phraseTaille,
	titreChiffres,
	URL_LICENCES
} from './departement-presentation';

const NBSP = String.fromCharCode(0xa0);
/** A count the way the page writes it: grouped by the fr-FR formatter. */
const n = (x: number) => new Intl.NumberFormat('fr-FR').format(x);
const SITE = 'https://datacommune.fr';
const SOURCES = [
	{ nom: 'DVF', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 2.0', url: 'https://www.data.gouv.fr/dvf' },
	{ nom: 'DPE', producteur: 'ADEME', licence: 'Licence Ouverte 2.0', url: 'https://www.data.gouv.fr/dpe' },
	{ nom: 'Recensement', producteur: 'INSEE', licence: 'Licence Ouverte 1.0' }
];

describe('cartesDepartement', () => {
	it('has the four cards in the order of the decision: population, flats, houses, DPE F or G', () => {
		const cartes = cartesDepartement(DEPARTEMENT_44);
		expect(cartes.map((c) => c.id)).toEqual(['population', 'appartement', 'maison', 'dpe']);
		expect(cartes.map((c) => c.libelle)).toEqual([
			'Population des communes',
			'Prix médian au m² des appartements',
			'Prix médian au m² des maisons',
			'Part des DPE classés F ou G'
		]);
	});

	it('reads each definition in the explications of the contract field, never a text of its own', () => {
		const definitions = cartesDepartement(DEPARTEMENT_44).map((c) => [c.fieldId, c.definition]);
		expect(definitions).toEqual([
			['departement.population.total', getExplication('departement.population.total').definition],
			['departement.prix.appartement.mediane_m2', getExplication('departement.prix.appartement.mediane_m2').definition],
			['departement.prix.maison.mediane_m2', getExplication('departement.prix.maison.mediane_m2').definition],
			['departement.dpe.part_fg_pct', getExplication('departement.dpe.part_fg_pct').definition]
		]);
	});

	it('writes a served figure with its unit and its base', () => {
		const [population, appartement, , dpe] = cartesDepartement(DEPARTEMENT_44);
		expect(population.etat).toMatchObject({ kind: 'valeur', unite: 'hab.' });
		expect(population.etat.kind === 'valeur' && population.etat.texte.replace(/\s/g, '')).toBe('1450000');
		expect(population.base).toBe(`207${NBSP}communes · recensement 2022`);
		expect(appartement.etat).toMatchObject({ kind: 'valeur', unite: '€/m²' });
		expect(appartement.etat.kind === 'valeur' && appartement.etat.texte.replace(/\s/g, '')).toBe('3480');
		expect(appartement.base).toBe(`${n(7350)}${NBSP}ventes · du 30 juin 2024 au 30 juin 2025`);
		expect(dpe.etat).toMatchObject({ kind: 'valeur', texte: '15,7', unite: '%' });
		expect(dpe.base).toBe(`${n(157100)}${NBSP}diagnostics de classe connue · établis du 1er juillet 2021 au 28 octobre 2026`);
	});

	it('says a median under the threshold as such, with its served count, never a number', () => {
		const maison = cartesDepartement(DEPARTEMENT_44)[2];
		expect(maison.etat).toEqual({
			kind: 'absence',
			etat: 'insufficient_sample',
			motif: DEPARTEMENT_44.prix.maison.etat!.motif,
			effectif: 4
		});
		expect(maison.base).toBe(`4${NBSP}ventes · du 30 juin 2024 au 30 juin 2025`);
	});

	it('gives every block of a hosted but not imported departement its not_reported state, no base, no zero', () => {
		const cartes = cartesDepartement(DEPARTEMENT_29);
		for (const carte of cartes) {
			expect(carte.etat.kind, carte.id).toBe('absence');
			expect(carte.etat.kind === 'absence' && carte.etat.etat, carte.id).toBe('not_reported');
			expect(carte.base, carte.id).toBeNull();
		}
		expect(aDesChiffres(DEPARTEMENT_29)).toBe(false);
	});

	it('gives every block of a departement out of coverage its out_of_coverage state and its reason', () => {
		const cartes = cartesDepartement(DEPARTEMENT_22);
		expect(cartes.map((c) => c.etat.kind === 'absence' && [c.etat.etat, c.etat.motif])).toEqual(
			Array(4).fill(['out_of_coverage', 'territoire_hors_perimetre'])
		);
		expect(aDesChiffres(DEPARTEMENT_22)).toBe(false);
	});

	it('says that a share has no base when no class is known, and keeps the documents it counts', () => {
		const sansClasse = {
			...DEPARTEMENT_44,
			dpe: {
				...DEPARTEMENT_44.dpe,
				classes: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0 },
				inconnue: 12,
				total: 12,
				classes_connues: 0,
				part_fg_pct: null,
				etat_part_fg: { etat: 'zero_denominator' as const, motif: 'Aucun diagnostic avec une classe connue.' }
			}
		};
		const dpe = cartesDepartement(sansClasse)[3];
		expect(dpe.etat).toMatchObject({ kind: 'absence', etat: 'zero_denominator' });
		expect(dpe.base).toBe(`12${NBSP}diagnostics, aucun de classe connue`);
	});

	it('says the vintages differ when the sum is served without a common year, and a singular for one', () => {
		const population = { total: 2100000, communes_comptees: 1, annee: null, etat: null };
		const [carte] = cartesDepartement({ ...DEPARTEMENT_75, population });
		expect(carte.base).toBe(`1${NBSP}commune · recensements de millésimes différents`);
		const [seule] = cartesDepartement({ ...DEPARTEMENT_75, population: { ...population, annee: 2022 } });
		expect(seule.base).toBe(`1${NBSP}commune · recensement 2022`);
	});

	it('writes the first day of a month as an ordinal, « 1er », in the window, the dates of the DPE and the day of the import (Opus P2-1)', () => {
		const debutDeMois = { ...DEPARTEMENT_44, prix: { ...DEPARTEMENT_44.prix, fenetre: { debut: '2024-07-01', fin: '2025-06-30' } } };
		expect(cartesDepartement(debutDeMois)[1].base).toBe(`${n(7350)}${NBSP}ventes · du 1er juillet 2024 au 30 juin 2025`);
		expect(phraseImportation({ ...DEPARTEMENT_44, derniere_importation: '2026-11-01' })).toBe('Données importées le 1er novembre 2026.');
		expect(descriptionDataset(DEPARTEMENT_44)).toContain('établis du 1er juillet 2021');
		expect(descriptionDataset(DEPARTEMENT_44)).not.toMatch(/(^| )1 (juillet|novembre)/);
		// The other days keep their number: only the first is an ordinal.
		expect(phraseImportation({ ...DEPARTEMENT_44, derniere_importation: '2026-11-02' })).toBe('Données importées le 2 novembre 2026.');
	});

	it('serves a price window of its own: no window, no « du … au … » in the base', () => {
		const sansFenetre = { ...DEPARTEMENT_44, prix: { ...DEPARTEMENT_44.prix, fenetre: null } };
		expect(cartesDepartement(sansFenetre)[1].base).toBe(`${n(7350)}${NBSP}ventes`);
	});
});

// Opus P3-6: « l’ensemble des communes » is true of the sales and of the diagnostics, not of the
// population when some communes have none loaded: the sentence then says how many are counted.
describe('explication d’une tuile (4.9, A9-6, A9-7)', () => {
	const SOURCES_SERVIES = [
		{ nom: 'Demandes de valeurs foncières (DVF)', producteur: 'DGFiP / Etalab', url: 'https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres/' },
		{ nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' },
		{ nom: 'Recensement de la population', producteur: 'INSEE' }
	];
	const norm = (texte: string | null) => (texte ?? '').replace(/\s/g, ' ');
	const [population, appartement, maison, dpe] = cartesDepartement(DEPARTEMENT_44);

	// D3 (inventory of 2026-11-10, validated by Ianis): the tile reads « value + count », as on the commune fiche;
	// the months, the census year and « pas une part de logements » are in the panel (Base, Définition) and on paper.
	it('keeps the count alone on the tile: the period and the reserve of documents are in the panel', () => {
		expect(norm(population.portee)).toBe('207 communes');
		expect(norm(appartement.portee)).toBe('7 350 ventes');
		expect(norm(dpe.portee)).toBe('157 100 diagnostics de classe connue');
		expect(norm(appartement.base)).toContain('du 30 juin 2024 au 30 juin 2025');
		expect(norm(population.base)).toContain('recensement 2022');
		expect(dpe.definition).toContain('pas une part de logements');
		for (const carte of cartesDepartement(DEPARTEMENT_44)) {
			expect((carte.portee ?? '').split(/\s+/).length).toBeLessThanOrEqual(25);
		}
	});

	it('keeps the reserve on the tile when the population does not count every commune', () => {
		const partielle = { ...DEPARTEMENT_44, population: { ...DEPARTEMENT_44.population, communes_comptees: 200 } };
		expect(norm(cartesDepartement(partielle)[0].portee)).toBe('200 communes sur 207');
	});

	it('moves the definition, the exact base and the perimeter to the panel, and matches the source by card', () => {
		const volet = voletCarte(appartement, DEPARTEMENT_44, SOURCES_SERVIES);
		const termes = Object.fromEntries(volet.entrees.flatMap((e) => ('valeur' in e ? [[e.terme, e.valeur]] : [])));
		expect(termes['Définition']).toBe(appartement.definition);
		expect(termes['Base']).toBe(appartement.base);
		expect(termes['Base']).toContain('du 30 juin 2024 au 30 juin 2025');
		expect(termes['Périmètre']).toBe(phrasePortee(DEPARTEMENT_44));
		expect(termes['Source']).toBe('Demandes de valeurs foncières (DVF)');
		expect(volet.source?.url).toMatch(/^https:/);
		expect(volet.brut).toEqual([{ fieldId: appartement.fieldId, valeur: '3480', etat: { kind: 'valeur' } }]);
		expect(voletCarte(population, DEPARTEMENT_44, SOURCES_SERVIES).source?.nom).toBe('Recensement de la population');
		expect(voletCarte(dpe, DEPARTEMENT_44, SOURCES_SERVIES).source?.nom).toBe('Diagnostics de performance énergétique (DPE)');
	});

	it('explains an absence too: its raw state stays in the panel, never a number', () => {
		const volet = voletCarte(maison, DEPARTEMENT_44, SOURCES_SERVIES);
		expect(volet.brut).toEqual([
			{ fieldId: maison.fieldId, valeur: 'null', etat: { kind: 'absence', etat: 'insufficient_sample', motif: expect.any(String) } }
		]);
	});

	it('names no source it does not know', () => {
		expect(voletCarte(dpe, DEPARTEMENT_44, []).source).toBeNull();
	});
});

describe('phrasePortee', () => {
	const partielle = { ...DEPARTEMENT_44, population: { ...DEPARTEMENT_44.population, communes_comptees: 200 } };

	it('says the figures bear on all the communes when the population counts all of them, or says nothing of it', () => {
		const complete = `Ces chiffres portent sur l’ensemble des communes du département${NBSP}: chaque vente et chaque diagnostic y est compté une fois.`;
		expect(phrasePortee(DEPARTEMENT_44)).toBe(complete);
		expect(phrasePortee(DEPARTEMENT_75)).toBe(complete);
		expect(phrasePortee(DEPARTEMENT_29)).toBe(complete);
		// Counting more communes than the referential holds is not « fewer »: the sentence stays as it is.
		expect(phrasePortee({ ...DEPARTEMENT_44, population: { ...DEPARTEMENT_44.population, communes_comptees: 208 } })).toBe(complete);
	});

	it('says how many communes the population adds up, out of how many, when some are missing', () => {
		const phrase = phrasePortee(partielle);
		expect(phrase).toContain(`200${NBSP}communes sur 207`);
		expect(phrase).not.toContain('ensemble des communes');
		// The prices and the DPE keep their own scope, said as such.
		expect(phrase).toMatch(/ventes retenues et des diagnostics du département/);
		expect(phrase).toMatch(/chaque vente et chaque diagnostic y est compté une fois/);
	});

	it('has a definition of the population that says which communes it adds up, and when the total is partial', () => {
		const definition = getExplication('departement.population.total').definition;
		expect(definition).toContain('communes dont la population est chargée');
		expect(definition).toContain('communes comptées');
		expect(definition).toContain('le total est partiel');
		expect(definition).not.toMatch(/de ses communes publiées/);
	});

	it('is the lead of the description of the Dataset, so that the structured data says the same', () => {
		expect(descriptionDataset(partielle)!.startsWith(phrasePortee(partielle))).toBe(true);
		expect(descriptionDataset(DEPARTEMENT_44)!.startsWith(phrasePortee(DEPARTEMENT_44))).toBe(true);
	});
});

describe('aDesChiffres', () => {
	it('is true as soon as one block serves a figure', () => {
		expect(aDesChiffres(DEPARTEMENT_44)).toBe(true);
		const seulementLaPopulation = {
			...DEPARTEMENT_22,
			population: { total: 5000, communes_comptees: 3, annee: 2022, etat: null }
		};
		expect(aDesChiffres(seulementLaPopulation)).toBe(true);
	});
});

describe('titre et textes', () => {
	it('titles the figures with the name of the departement, whatever its article', () => {
		expect(titreChiffres('Loire-Atlantique')).toBe(`Chiffres clés${NBSP}: Loire-Atlantique`);
		expect(titreChiffres("Côtes-d'Armor")).toBe(`Chiffres clés${NBSP}: Côtes-d'Armor`);
	});

	it('writes a meta description from the served figures only, and leaves out those it has not', () => {
		const description = descriptionMeta(DEPARTEMENT_44);
		expect(description).toContain('Loire-Atlantique');
		expect(description).toMatch(/1.450.000\shab\./);
		expect(description).toMatch(/3.480\s€\/m²/);
		expect(description).toMatch(/15,7\s%/);
		expect(description).not.toMatch(/maisons/);
		expect(descriptionMeta(DEPARTEMENT_22)).toBe(`Chiffres clés du département Côtes-d'Armor${NBSP}(22) et ses communes les plus peuplées, avec leurs sources.`);
	});

	it('keeps the meta description within the snippet when the four figures are served (166 characters on 2026-11-14)', () => {
		const complet = {
			...DEPARTEMENT_44,
			population: { ...DEPARTEMENT_44.population, total: 1473156 },
			prix: {
				...DEPARTEMENT_44.prix,
				maison: { mediane_m2: 2865, effectif: 9000, etat: null },
				appartement: { mediane_m2: 3303, effectif: 7350, etat: null }
			},
			dpe: { ...DEPARTEMENT_44.dpe, part_fg_pct: 6 }
		} as typeof DEPARTEMENT_44;
		const description = descriptionMeta(complet);
		expect(description.length).toBeLessThanOrEqual(155);
		expect(description).toMatch(/maisons à 2.865\s€\/m²/);
		expect(description).toMatch(/6\s% des DPE classés F ou G\./);
		expect(description.endsWith('sources.')).toBe(true);
	});

	it('describes the Dataset with the visible texts of the cards served, between 50 and 5 000 characters', () => {
		const description = descriptionDataset(DEPARTEMENT_44)!;
		expect(description.length).toBeGreaterThanOrEqual(50);
		expect(description.length).toBeLessThanOrEqual(5000);
		for (const carte of cartesDepartement(DEPARTEMENT_44).filter((c) => c.etat.kind === 'valeur')) {
			expect(description).toContain(carte.libelle);
			expect(description).toContain(carte.base!);
		}
		// A card without a figure is not described: its libellé does not appear.
		expect(description).not.toContain('Prix médian au m² des maisons');
	});

	it('has no description when no figure is served (no Dataset then)', () => {
		expect(descriptionDataset(DEPARTEMENT_29)).toBeNull();
		expect(descriptionDataset(DEPARTEMENT_22)).toBeNull();
	});

	it('says the day of the last import, that none was made for a hosted departement, and nothing out of coverage', () => {
		expect(phraseImportation(DEPARTEMENT_44)).toBe('Données importées le 30 octobre 2026.');
		expect(phraseImportation(DEPARTEMENT_29)).toBe('Aucune donnée n’a encore été importée pour ce département.');
		// An unreadable date is not a claim about the import: nothing is said.
		expect(phraseImportation({ ...DEPARTEMENT_44, derniere_importation: 'pas une date' })).toBeNull();
		// Out of coverage, the cards say why; a sentence about an import would be false.
		expect(phraseImportation(DEPARTEMENT_22)).toBeNull();
	});
});

describe('fil d’Ariane', () => {
	it('goes Accueil > Départements > the departement, the last step being the page itself', () => {
		expect(filAriane('44', 'Loire-Atlantique')).toEqual([
			{ nom: 'Accueil', href: '/' },
			{ nom: 'Départements', href: '/departements' },
			{ nom: 'Loire-Atlantique', href: '/departement/44' }
		]);
	});
});

describe('nomDepartementDuFil', () => {
	it('tells the departement from the commune that bears its name, and leaves every other name alone', () => {
		expect(nomDepartementDuFil('Paris', ['Paris'])).toBe('Paris (département)');
		expect(nomDepartementDuFil('Paris', [' paris '])).toBe('Paris (département)');
		expect(nomDepartementDuFil('Loire-Atlantique', ['Nantes'])).toBe('Loire-Atlantique');
		expect(nomDepartementDuFil('Vendée', ['Challans'])).toBe('Vendée');
		expect(nomDepartementDuFil('Paris', [])).toBe('Paris');
	});
});

describe('lienToutesLesCommunes', () => {
	it('targets the directory filtered on the departement', () => {
		expect(lienToutesLesCommunes(DEPARTEMENT_44)).toBe('/communes?departement=44');
	});

	it('asks for the whole country when nothing of the departement is hosted, so that the directory lists its communes', () => {
		expect(lienToutesLesCommunes(DEPARTEMENT_22)).toBe('/communes?departement=22&couverture=toutes');
		expect(lienToutesLesCommunes(DEPARTEMENT_29)).toBe('/communes?departement=29');
	});
});

describe('lignesCommunesPrincipales', () => {
	it('lists the communes with their link and their population as the directory writes it', () => {
		const lignes = lignesCommunesPrincipales(DEPARTEMENT_44);
		expect(lignes.map((l) => [l.code, l.href, l.nom])).toEqual([
			['44109', '/commune/44109', 'Nantes'],
			['44184', '/commune/44184', 'Saint-Nazaire'],
			['44162', '/commune/44162', 'Rezé']
		]);
		// T8 (2026-11-10): the unit and the year with its source apart, so that a page sets them on two lines.
		expect(lignes[0].population).toEqual({
			nombre: expect.stringMatching(/^320.732$/),
			suite: 'habitants · 2022 · INSEE',
			unite: 'habitants',
			millesime: '2022 · INSEE'
		});
		// A population of the referential has no year: never invented.
		expect(lignes[2].population).toEqual({ nombre: expect.stringMatching(/^43.500$/), suite: 'habitants', unite: 'habitants', millesime: null });
	});

	it('names the arrondissements of Paris by their rank and their city, as their fiche is titled', () => {
		const lignes = lignesCommunesPrincipales(DEPARTEMENT_75);
		expect(lignes.map((l) => l.nom)).toEqual([
			'15e arrondissement de Paris',
			'20e arrondissement de Paris',
			'12e arrondissement de Paris'
		]);
		expect(lignes[0].href).toBe('/commune/75115');
	});

	it('says « Non renseignée » for a zone without population rather than a zero', () => {
		const sans = { ...DEPARTEMENT_44, communes_principales: [{ ...DEPARTEMENT_44.communes_principales[0], population: null, population_annee: null, population_source: null }] };
		expect(lignesCommunesPrincipales(sans)[0].population).toBeNull();
	});
});

describe('donneesStructurees', () => {
	const donnees = (synthese = DEPARTEMENT_44, sources = SOURCES) => donneesStructurees({ synthese, sources, siteUrl: SITE });

	it('emits a BreadcrumbList and a Dataset for an indexed page that serves a figure', () => {
		expect(donnees().map((o) => o['@type'])).toEqual(['BreadcrumbList', 'Dataset']);
	});

	it('emits nothing for a page that is not indexable, even with figures (noindex, no structured data)', () => {
		expect(donnees({ ...DEPARTEMENT_44, indexable: false })).toEqual([]);
		expect(donnees(DEPARTEMENT_29)).toEqual([]);
	});

	it('emits nothing when no figure is served, even for an indexable departement', () => {
		const vide = { ...DEPARTEMENT_22, indexable: true };
		expect(donnees(vide)).toEqual([]);
	});

	it('names the Dataset as the visible title of the figures, with the departement as the area covered', () => {
		const dataset = donnees().find((o) => o['@type'] === 'Dataset') as Record<string, any>;
		expect(dataset.name).toBe(titreChiffres('Loire-Atlantique'));
		expect(dataset.description).toBe(descriptionDataset(DEPARTEMENT_44));
		expect(dataset.url).toBe(`${SITE}/departement/44`);
		expect(dataset.inLanguage).toBe('fr');
		expect(dataset.isAccessibleForFree).toBe(true);
		expect(dataset.creator).toMatchObject({ '@type': 'Organization', name: 'DataCommune' });
		expect(dataset.spatialCoverage).toEqual({
			'@type': 'AdministrativeArea',
			name: 'Loire-Atlantique',
			identifier: '44',
			url: `${SITE}/departement/44`
		});
	});

	it('gives the time coverage of the DVF window alone, and names it in the variable it qualifies', () => {
		const dataset = donnees().find((o) => o['@type'] === 'Dataset') as Record<string, any>;
		expect(dataset.temporalCoverage).toBe('2024-06-30/2025-06-30');
		const mesures = dataset.variableMeasured as { name: string; description: string }[];
		const appartements = mesures.find((m) => m.name === 'Prix médian au m² des appartements')!;
		expect(appartements.description).toContain('du 30 juin 2024 au 30 juin 2025');
		// The population and the DPE are not dated by that window.
		expect(mesures.find((m) => m.name === 'Population des communes')!.description).not.toContain('30 juin 2024');
		expect(mesures.find((m) => m.name === 'Part des DPE classés F ou G')!.description).not.toContain('30 juin 2024');
	});

	it('measures only what is served: a median under the threshold is not a variable', () => {
		const dataset = donnees().find((o) => o['@type'] === 'Dataset') as Record<string, any>;
		expect(dataset.variableMeasured.map((m: { name: string }) => m.name)).toEqual([
			'Population des communes',
			'Prix médian au m² des appartements',
			'Part des DPE classés F ou G'
		]);
		for (const mesure of dataset.variableMeasured) {
			expect(mesure['@type']).toBe('PropertyValue');
			expect(typeof mesure.value).toBe('number');
		}
	});

	it('has no time coverage when the window is not served', () => {
		const sans = { ...DEPARTEMENT_44, prix: { ...DEPARTEMENT_44.prix, fenetre: null } };
		expect((donnees(sans).find((o) => o['@type'] === 'Dataset') as Record<string, any>).temporalCoverage).toBeUndefined();
	});

	// Opus P3-5: schema.org wants a URL for `license` and a CreativeWork for `isBasedOn` (a Dataset there
	// would ask for a description of its own). The label of the licence is shown on the page; the URL is
	// the one of the source catalogue of the API, which the answer of the departement does not carry.
	const LO2 = 'https://www.data.gouv.fr/pages/legal/licences/etalab-2.0';
	const LO1 = 'https://www.data.gouv.fr/pages/legal/licences/etalab-1.0';

	it('states the licences as addresses and the sources as works, with their producer', () => {
		const dataset = donnees().find((o) => o['@type'] === 'Dataset') as Record<string, any>;
		expect(dataset.license).toEqual([LO2, LO1]);
		expect(dataset.isBasedOn).toEqual([
			{ '@type': 'CreativeWork', name: 'DVF', url: 'https://www.data.gouv.fr/dvf', license: LO2, creator: { '@type': 'Organization', name: 'DGFiP / Etalab' } },
			{ '@type': 'CreativeWork', name: 'DPE', url: 'https://www.data.gouv.fr/dpe', license: LO2, creator: { '@type': 'Organization', name: 'ADEME' } },
			{ '@type': 'CreativeWork', name: 'Recensement', license: LO1, creator: { '@type': 'Organization', name: 'INSEE' } }
		]);
		for (const adresse of [dataset.license].flat()) expect(adresse).toMatch(/^https:\/\//);
	});

	it('writes a single licence as one address, and no address for a licence it does not know', () => {
		const une = donnees(DEPARTEMENT_44, [SOURCES[0], SOURCES[1]]).find((o) => o['@type'] === 'Dataset') as Record<string, any>;
		expect(une.license).toBe(LO2);
		const inconnue = [{ nom: 'Source', licence: 'Licence maison', producteur: 'X', url: 'https://exemple.fr/s' }];
		const dataset = donnees(DEPARTEMENT_44, inconnue).find((o) => o['@type'] === 'Dataset') as Record<string, any>;
		expect(dataset.license).toBeUndefined();
		expect(dataset.isBasedOn).toEqual([{ '@type': 'CreativeWork', name: 'Source', url: 'https://exemple.fr/s', creator: { '@type': 'Organization', name: 'X' } }]);
	});

	it('states the address of a source only when it is a web address, as the page links it', () => {
		const sources = [{ nom: 'S', licence: 'Licence Ouverte 2.0', producteur: 'P', url: 'javascript:alert(1)' }];
		const dataset = donnees(DEPARTEMENT_44, sources).find((o) => o['@type'] === 'Dataset') as Record<string, any>;
		expect(dataset.isBasedOn[0].url).toBeUndefined();
	});

	it('takes the addresses of the licences from the source catalogue of the API', () => {
		const catalogue = readFileSync(resolve(__dirname, '../../../../api/backend/app/common/source_catalog.py'), 'utf8');
		const constante = (nom: string) => new RegExp(nom + ' = "([^"]+)"').exec(catalogue)?.[1];
		expect(URL_LICENCES['Licence Ouverte 2.0']).toBe(constante('_LO2_URL'));
		expect(URL_LICENCES['Licence Ouverte 1.0']).toBe(constante('_LO1_URL'));
		expect(URL_LICENCES['ODbL 1.0']).toBe(constante('_ODBL_URL'));
	});

	it('writes no source claim when none was served', () => {
		const dataset = donnees(DEPARTEMENT_44, []).find((o) => o['@type'] === 'Dataset') as Record<string, any>;
		expect(dataset.license).toBeUndefined();
		expect(dataset.isBasedOn).toBeUndefined();
	});

	it('repeats the visible breadcrumb with absolute addresses', () => {
		const fil = donnees().find((o) => o['@type'] === 'BreadcrumbList') as Record<string, any>;
		expect(fil.itemListElement).toEqual([
			{ '@type': 'ListItem', position: 1, name: 'Accueil', item: `${SITE}/` },
			{ '@type': 'ListItem', position: 2, name: 'Départements', item: `${SITE}/departements` },
			{ '@type': 'ListItem', position: 3, name: 'Loire-Atlantique', item: `${SITE}/departement/44` }
		]);
	});
});

describe('phraseTaille', () => {
	it('counts the communes, and the arrondissements apart where there are some, never added', () => {
		expect(phraseTaille(DEPARTEMENT_44.communes)).toBe(`207${NBSP}communes`);
		expect(phraseTaille(DEPARTEMENT_75.communes)).toBe(`1${NBSP}commune et 20${NBSP}arrondissements`);
		expect(phraseTaille({ nombre: 1, arrondissements: 1 })).toBe(`1${NBSP}commune et 1${NBSP}arrondissement`);
	});
});

describe('phraseListeVide', () => {
	it('gives the reason of the coverage when nothing of the departement is hosted, else says the referential lists none', () => {
		expect(phraseListeVide(DEPARTEMENT_22)).toBe('Ce site ne couvre pas ce département.');
		expect(phraseListeVide(DEPARTEMENT_44)).toBe('Le référentiel ne liste aucune commune pour ce département.');
		const motifInconnu = { ...DEPARTEMENT_22, couverture: { dvf: { etat: 'hors_couverture' as const, motif: null, departements: [] } } };
		expect(phraseListeVide(motifInconnu)).toBe('Le référentiel ne liste aucune commune pour ce département.');
	});
});

describe('ligneSource', () => {
	it('keeps the name, the producer and the licence, and links only an https address', () => {
		expect(ligneSource(SOURCES[0])).toEqual({
			nom: 'DVF',
			detail: '· DGFiP / Etalab · Licence Ouverte 2.0',
			href: 'https://www.data.gouv.fr/dvf'
		});
		expect(ligneSource({ nom: 'X', url: 'javascript:alert(1)' })).toEqual({ nom: 'X', detail: '', href: null });
		expect(ligneSource({ nom: 'X', url: 'http://exemple.fr/' })?.href).toBeNull();
		expect(ligneSource({ producteur: 'INSEE' })).toBeNull();
	});
});

describe('etatLigneDepartement', () => {
	const par = (code: string) => DEPARTEMENTS_LISTE.find((d) => d.code === code)!;

	it('says nothing of an indexable departement, why a prices-less one has none otherwise', () => {
		expect(etatLigneDepartement(par('44'))).toBeNull();
		// D13 (2026-11-10): the common label of the state, the one the page of the departement shows on its tiles.
		expect(etatLigneDepartement(par('29'))).toBe('Non renseigné');
		expect(etatLigneDepartement(par('22'))).toBe('Hors couverture · département non hébergé');
	});

	it('words an out-of-coverage reason it does not know with the generic label, never the key', () => {
		const inconnu = { ...par('22'), prix: { etat: 'hors_couverture' as const, motif: null, departements: [] } };
		expect(etatLigneDepartement(inconnu)).toBe('Hors couverture');
	});
});

describe('donneesStructureesListe', () => {
	it('is the BreadcrumbList Accueil > Départements, with absolute addresses', () => {
		expect(donneesStructureesListe(SITE)).toEqual([
			{
				'@context': 'https://schema.org',
				'@type': 'BreadcrumbList',
				itemListElement: [
					{ '@type': 'ListItem', position: 1, name: 'Accueil', item: `${SITE}/` },
					{ '@type': 'ListItem', position: 2, name: 'Départements', item: `${SITE}/departements` }
				]
			}
		]);
	});
});

// D2 (inventory of 2026-11-10, validated by Ianis): « L'essentiel » reads the prices first, as the commune
// fiche (① houses, ② flats), then the DPE, the population last. The structured data keep the order of
// `cartesDepartement`, locked word for word by `portage-departement.spec.ts`.
describe('ordre de « L’essentiel » (D2)', () => {
	it('shows houses, flats, the DPE share, then the population', () => {
		expect(cartesEssentiel(DEPARTEMENT_44).map((carte) => carte.id)).toEqual(['maison', 'appartement', 'dpe', 'population']);
	});

	it('moves no card: the same four cards, the same states', () => {
		const parId = (cartes: ReturnType<typeof cartesDepartement>) => Object.fromEntries(cartes.map((c) => [c.id, c]));
		for (const synthese of [DEPARTEMENT_44, DEPARTEMENT_29, DEPARTEMENT_22]) {
			expect(parId(cartesEssentiel(synthese))).toEqual(parId(cartesDepartement(synthese)));
		}
	});
});

describe('annuaireParLettre', () => {
	const fiche = (code_insee: string, nom: string, type_zone: 'commune' | 'arrondissement' = 'commune') => ({ code_insee, nom, type_zone });

	it('sorts every page by name and groups it under its first letter, accents and ligature read plainly', () => {
		const groupes = annuaireParLettre([
			fiche('44109', 'Nantes'),
			fiche('85113', "L'Île-d'Yeu"),
			fiche('44003', 'Ancenis-Saint-Géréon'),
			fiche('85114', 'Île-d’Elle'),
			fiche('57527', 'Œting'),
			fiche('44010', 'Orvault'),
			fiche('44005', 'Écoublay')
		]);
		expect(groupes.map((g) => [g.lettre, g.fiches.map((f) => f.nom)])).toEqual([
			['A', ['Ancenis-Saint-Géréon']],
			['E', ['Écoublay']],
			['I', ['Île-d’Elle']],
			['L', ["L'Île-d'Yeu"]],
			['N', ['Nantes']],
			['O', ['Œting', 'Orvault']]
		]);
		expect(groupes[0].fiches[0]).toEqual({ code: '44003', href: '/commune/44003', nom: 'Ancenis-Saint-Géréon' });
	});

	it('puts the arrondissements after their commune, in their order, with their readable title', () => {
		const groupes = annuaireParLettre([
			fiche('13210', 'Marseille 10e Arrondissement', 'arrondissement'),
			fiche('13055', 'Marseille'),
			fiche('13202', 'Marseille 2e Arrondissement', 'arrondissement'),
			fiche('13201', 'Marseille 1er Arrondissement', 'arrondissement')
		]);
		expect(groupes.map((g) => g.lettre)).toEqual(['M']);
		expect(groupes[0].fiches.map((f) => f.nom)).toEqual([
			'Marseille',
			'1er arrondissement de Marseille',
			'2e arrondissement de Marseille',
			'10e arrondissement de Marseille'
		]);
	});

	it('is empty for no page', () => {
		expect(annuaireParLettre([])).toEqual([]);
	});
});
