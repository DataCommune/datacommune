// Texts of the interactive parcel map (04.10-03). `GABARITS` is the only source of the visible strings
// at run time; its character-for-character equality with the copy tables of the UI contract is held by
// the private check of the phase, outside `frontend/`. Here: the shape of the table, what the filling
// functions render, the words the map never says and the French spacing.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
	GABARITS,
	SOURCE_FOND,
	VOLET_CARTE,
	afficherPlus,
	annonceSelection,
	compteParcelles,
	compteSansVentes,
	descriptionCarte,
	groupeTropGrand,
	horsDeLaVue,
	horsDeLaZone,
	legendeVentes,
	ligneGroupe,
	ligneLimites,
	ligneSourceVentes,
	nomCarte,
	nombreVentes,
	prixDeLaVente,
	referenceLigne,
	referenceCadastrale,
	reserveZero,
	surfaceCadastrale,
	surfaceIncomplete,
	surfaceSansValeur,
	titreParcelle,
	tropLoin,
	venduAvec
} from './parcel-map';

const NB = String.fromCharCode(0xa0);
const NNB = String.fromCharCode(0x202f);
const PARCELLE = '00000000ZA0001';

describe('table des gabarits', () => {
	it('one entry per number of the UI contract, variants under a `-v<rank>` key', () => {
		const cles = Object.keys(GABARITS);
		expect(cles.length).toBeGreaterThan(0);
		for (const cle of cles) expect(cle).toMatch(/^T-\d{2}(-v[1-9])?$/);
		// A variant never stands without its number.
		for (const cle of cles.filter((c) => c.includes('-v'))) expect(cles).toContain(cle.slice(0, 4));
	});

	it('carries the texts of the tracer path, of the whole map (04.10-06), of the sales of the panel (04.10-07), of the list (04.10-08) and of the map not loaded (04.10-09)', () => {
		expect(Object.keys(GABARITS).sort()).toEqual(
			[
				'T-01', 'T-02', 'T-03', 'T-03-v1', 'T-04', 'T-05', 'T-06', 'T-07', 'T-08', 'T-09', 'T-10', 'T-11', 'T-12', 'T-13',
				'T-13-v1', 'T-14', 'T-15', 'T-16', 'T-17', 'T-17-v1', 'T-18', 'T-20', 'T-21', 'T-21-v1', 'T-22',
				'T-23', 'T-23-v1', 'T-24', 'T-25', 'T-25-v1', 'T-26', 'T-26-v1', 'T-27', 'T-28', 'T-29', 'T-29-v1', 'T-29-v2', 'T-29-v3',
				'T-30', 'T-31', 'T-32', 'T-33', 'T-34', 'T-35', 'T-35-v1', 'T-36', 'T-36-v1', 'T-37', 'T-37-v1', 'T-38', 'T-39', 'T-41', 'T-43',
				'T-44', 'T-44-v1', 'T-45', 'T-46', 'T-46-v1', 'T-47', 'T-47-v1', 'T-47-v2', 'T-48', 'T-48-v1'
			].sort()
		);
	});

	it('the panel of sources and limits has its seven entries, in the order of the UI contract', () => {
		expect(VOLET_CARTE.map((e) => e.terme)).toEqual([
			'Parcelles',
			'Surface',
			'Ventes',
			'Vendues ensemble',
			'Prix',
			'Ce que la carte ne dit pas',
			'Fond'
		]);
		// R1 (Ianis, 2026-11-15): « Plan IGN » links to the source page of the IGN, as the Licence Ouverte asks.
		expect(VOLET_CARTE.at(-1)).toEqual({ terme: 'Fond', valeur: 'Plan IGN', url: SOURCE_FOND });
		expect(SOURCE_FOND).toBe('https://cartes.gouv.fr/rechercher-une-donnee/dataset/IGNF_PLAN-IGN');
		// « propriétaire » only in the two sentences that say what the map does not say.
		const avecProprietaire = VOLET_CARTE.filter((e) => /propriétaire|possède/.test(e.valeur)).map((e) => e.terme);
		expect(avecProprietaire).toEqual(['Vendues ensemble', 'Ce que la carte ne dit pas']);
	});

	it('never says what the map cannot know (forbidden words of the UI contract)', () => {
		const interdits = /propriétaire|unité foncière|terrain de|jamais vendue|estimation|€/i;
		for (const [cle, texte] of Object.entries(GABARITS)) expect(texte, cle).not.toMatch(interdits);
	});

	it('the panel of the parcel never says what the map cannot know either', () => {
		const panneau = readFileSync('src/lib/components/adresse/carte/ParcelPanel.svelte', 'utf8');
		expect(panneau).toContain('<VenteList');
		expect(panneau).not.toMatch(/propriétaire|unité foncière|terrain de|jamais vendue|estimation/i);
	});

	it('uses the typographic apostrophe and the ellipsis character, never their keyboard forms', () => {
		for (const [cle, texte] of Object.entries(GABARITS)) {
			expect(texte, cle).not.toContain("'");
			expect(texte, cle).not.toContain('...');
		}
		expect(GABARITS['T-02']).toContain('’');
		expect(GABARITS['T-04']).toContain('…');
	});
});

describe('remplissage', () => {
	it('names a parcel by its section and its number, as its reference writes them', () => {
		expect(titreParcelle(PARCELLE)).toBe('Parcelle ZA 0001');
		expect(titreParcelle('2A004000AB0123')).toBe('Parcelle AB 0123');
		expect(annonceSelection(PARCELLE)).toBe('Parcelle ZA 0001 sélectionnée.');
	});

	it('writes the cadastral reference in its four parts', () => {
		expect(referenceCadastrale(PARCELLE)).toBe('00000 000 ZA 0001');
		expect(referenceCadastrale('97101000AB0042')).toBe('97101 000 AB 0042');
	});

	it('keeps the unit on the line of its number (U+00A0) and groups the thousands (U+202F)', () => {
		expect(surfaceCadastrale(450)).toBe(`450${NB}m²`);
		expect(surfaceCadastrale(1960)).toBe(`1${NNB}960${NB}m²`);
		expect(surfaceCadastrale(0)).toBe(`0${NB}m²`);
	});

	it('names the map by the address, and describes how to move it', () => {
		expect(nomCarte('3 Rue des Parcelles, 85000 La Roche-sur-Yon')).toBe(
			'Carte des parcelles autour de l’adresse 3 Rue des Parcelles, 85000 La Roche-sur-Yon'
		);
		expect(descriptionCarte()).toMatch(/^Les flèches déplacent la carte/);
	});

	it('says the served period in the legend, never a written one', () => {
		expect(legendeVentes('2021-2025')).toBe('Au moins une vente (2021-2025)');
		expect(legendeVentes('2019-2023')).toBe('Au moins une vente (2019-2023)');
	});

	it('writes the limits line from the served values, an absent one by the label of the glossary', () => {
		const servi = { periode: '2021-2025', millesime: '2026-07', attribution: '© IGN (Plan IGN)', licence: 'Licence Ouverte 2.0' };
		expect(ligneLimites(servi)).toBe(
			`Ventes connues${NB}: 2021-2025. Cadastre${NB}: 2026-07. Fond${NB}: © IGN (Plan IGN), Licence Ouverte 2.0.`
		);
		const sans = ligneLimites({ ...servi, periode: null, millesime: null });
		expect(sans).toBe(`Ventes connues${NB}: Non renseigné. Cadastre${NB}: Non renseigné. Fond${NB}: © IGN (Plan IGN), Licence Ouverte 2.0.`);
		expect(sans).not.toMatch(/null|undefined/);
	});

	it('fills the period and the vintage of the panel of sources from the served values', () => {
		const entrees = VOLET_CARTE.map((e) => e.valeur);
		expect(entrees.join(' ')).toContain('{millesime}');
		expect(entrees.join(' ')).toContain('{periode}');
	});

	it('says the first served year of the sales under a zero, never a written one (T-27)', () => {
		expect(reserveZero(2021)).toBe('Avant 2021, les ventes ne sont pas connues ici.');
		expect(reserveZero(2019)).toBe('Avant 2019, les ventes ne sont pas connues ici.');
	});

	it('the source line of the sales is the one of the fiche, which still writes it so', () => {
		// The fiche writes this line in its page; the panel takes it as is (UI contract, existing texts).
		const fiche = readFileSync('src/routes/(marketing)/adresse/[id]/+page.svelte', 'utf8');
		expect(fiche).toContain(
			"<span>DVF (DGFiP){#if listeVentes.periode}{' · '}{listeVentes.periode}{/if} · ventes de la parcelle, rattachées à aucun logement</span>"
		);
		expect(ligneSourceVentes('2021-2025')).toBe('DVF (DGFiP) · 2021-2025 · ventes de la parcelle, rattachées à aucun logement');
		expect(ligneSourceVentes(null)).toBe('DVF (DGFiP) · ventes de la parcelle, rattachées à aucun logement');
	});

	it('a sale of several parcels: its price is that of the whole sale (T-20)', () => {
		expect(prixDeLaVente(2)).toBe('Prix de toute la vente (2 parcelles).');
		expect(prixDeLaVente(60)).toBe('Prix de toute la vente (60 parcelles).');
	});

	it('the other parcels of the sale, singular and plural (T-21)', () => {
		expect(venduAvec(1)).toBe('Vendue avec 1 autre parcelle');
		expect(venduAvec(2)).toBe('Vendue avec 2 autres parcelles');
		expect(venduAvec(59)).toBe('Vendue avec 59 autres parcelles');
	});

	it('the line of a shown group: total, date of the card and served surface, or its short form (T-23)', () => {
		expect(ligneGroupe(2, 'juin 2024', 830)).toBe(`2 parcelles vendues ensemble · juin 2024 · 830${NB}m² au total`);
		expect(ligneGroupe(60, 'février 2021', 48210)).toBe(`60 parcelles vendues ensemble · février 2021 · 48${NNB}210${NB}m² au total`);
		expect(ligneGroupe(3, 'septembre 2022', null)).toBe('3 parcelles vendues ensemble · septembre 2022');
	});

	it('an incomplete total surface, singular and plural (T-25)', () => {
		expect(surfaceIncomplete(1)).toBe(`Surface totale non calculée${NB}: 1 de ces parcelles ne figure plus au cadastre.`);
		expect(surfaceIncomplete(4)).toBe(`Surface totale non calculée${NB}: 4 de ces parcelles ne figurent plus au cadastre.`);
	});

	it('parcels of the group out of the view, singular and plural (T-26)', () => {
		expect(horsDeLaVue(1)).toBe('1 de ces parcelles est hors de la vue.');
		expect(horsDeLaVue(12)).toBe('12 de ces parcelles sont hors de la vue.');
	});

	it('the toggle and the reserve of a group are fixed texts', () => {
		expect(GABARITS['T-22']).toBe('Montrer ces parcelles');
		expect(GABARITS['T-24']).toBe('Ne dit pas qui les possède aujourd’hui.');
	});

	it('the list of the parcels shown: its title, its box and its badges are fixed texts (T-28, T-30 to T-32)', () => {
		expect(GABARITS['T-28']).toBe('Parcelles affichées');
		expect(GABARITS['T-30']).toBe('Seulement les parcelles avec une vente');
		expect(GABARITS['T-31']).toBe('Vendue ensemble');
		expect(GABARITS['T-32']).toBe('Adresse');
	});

	it('the count of the list, plural, singular and with no sale, from the served period (T-29)', () => {
		expect(compteParcelles(12, 3, '2021-2025')).toBe('12 parcelles sur la carte, dont 3 avec une vente (2021-2025).');
		expect(compteParcelles(12, 1, '2019-2023')).toBe('12 parcelles sur la carte, dont 1 avec une vente (2019-2023).');
		expect(compteParcelles(1, 1, '2021-2025')).toBe('1 parcelle sur la carte, dont 1 avec une vente (2021-2025).');
		expect(compteParcelles(12, 0, '2021-2025')).toBe('12 parcelles sur la carte, aucune avec une vente (2021-2025).');
		expect(compteParcelles(1, 0, '2021-2025')).toBe('1 parcelle sur la carte, aucune avec une vente (2021-2025).');
		expect(compteParcelles(1240, 312, '2021-2025')).toBe(`1${NNB}240 parcelles sur la carte, dont 312 avec une vente (2021-2025).`);
		// The singular is the sentence the contract writes for it (S8, Ianis, 2026-11-15).
		expect(compteParcelles(1, 1, '2021-2025')).toBe(GABARITS['T-29-v1'].replace('{periode}', '2021-2025'));
	});

	it('the count without the map says « autour de l’adresse », with the same agreements (T-29, variant)', () => {
		expect(compteParcelles(12, 3, '2021-2025', true)).toBe('12 parcelles autour de l’adresse, dont 3 avec une vente (2021-2025).');
		expect(compteParcelles(1, 1, '2021-2025', true)).toBe('1 parcelle autour de l’adresse, dont 1 avec une vente (2021-2025).');
		expect(compteParcelles(12, 0, '2021-2025', true)).toBe('12 parcelles autour de l’adresse, aucune avec une vente (2021-2025).');
		expect(compteParcelles(1, 0, '2021-2025', true)).toBe('1 parcelle autour de l’adresse, aucune avec une vente (2021-2025).');
	});

	it('the button of the next parcels: 20, or what is left for the last batch (T-36)', () => {
		expect(afficherPlus(20)).toBe('Afficher 20 parcelles de plus');
		expect(afficherPlus(7)).toBe('Afficher 7 parcelles de plus');
		expect(afficherPlus(2)).toBe('Afficher 2 parcelles de plus');
		// No variant of the contract for one parcel: the agreement of its noun only (case for the review).
		expect(afficherPlus(1)).toBe('Afficher 1 parcelle de plus');
	});

	it('no parcel chosen, with the map or without it (T-35)', () => {
		expect(GABARITS['T-35']).toBe('Choisissez une parcelle sur la carte ou dans la liste.');
		expect(GABARITS['T-35-v1']).toBe('Choisissez une parcelle dans la liste.');
	});

	it('no parcel in the view, with the map or without it (T-37)', () => {
		expect(GABARITS['T-37']).toBe('Aucune parcelle cadastrale dans cette vue.');
		expect(GABARITS['T-37-v1']).toBe('Aucune parcelle cadastrale autour de l’adresse.');
	});

	it('a line of the list: « section number », and « n ventes » with « 1 vente » in the singular', () => {
		expect(referenceLigne(PARCELLE)).toBe('ZA 0001');
		expect(referenceLigne('2A004000AB0123')).toBe('AB 0123');
		expect(nombreVentes(1)).toBe(`1${NB}vente`);
		expect(nombreVentes(4)).toBe(`4${NB}ventes`);
		expect(nombreVentes(1500)).toBe(`1${NNB}500${NB}ventes`);
	});

	it('leaves no placeholder in a rendered text', () => {
		const limites = ligneLimites({ periode: '2021-2025', millesime: null, attribution: 'a', licence: 'b' });
		for (const texte of [titreParcelle(PARCELLE), annonceSelection(PARCELLE), surfaceCadastrale(12), referenceCadastrale(PARCELLE), nomCarte('x'), legendeVentes('2021-2025'), limites, reserveZero(2021),
			prixDeLaVente(2), venduAvec(1), venduAvec(3), ligneGroupe(2, 'mai 2023', 10), ligneGroupe(2, 'mai 2023', null), surfaceIncomplete(1),
			surfaceIncomplete(2), horsDeLaVue(1), horsDeLaVue(2), compteParcelles(1, 0, 'p'), compteParcelles(2, 1, 'p'), compteParcelles(2, 0, 'p', true),
			compteParcelles(1, 1, 'p', true), afficherPlus(1), afficherPlus(20), afficherPlus(3), referenceLigne(PARCELLE), nombreVentes(1), nombreVentes(2)]) {
			expect(texte).not.toMatch(/[{}]/);
		}
	});

	it('the texts Ianis chose on 2026-11-15 for the cases left without one (R3, R4, S2, S5, S6, S8)', () => {
		// R3: no total surface, no parcel missing from the cadastre: the served count without a surface.
		expect(surfaceSansValeur(3)).toBe(`Surface totale non calculée${NB}: 3 de ces parcelles n’ont pas de surface au cadastre.`);
		expect(surfaceSansValeur(1)).toBe(`Surface totale non calculée${NB}: 1 de ces parcelles n’a pas de surface au cadastre.`);
		// R4: a group too large to be shown, with its served total.
		expect(groupeTropGrand(1240)).toBe(`Groupe trop grand pour être montré sur la carte (1${NNB}240 parcelles).`);
		// S2: parcels of the group outside the bounds of the map.
		expect(tropLoin(4)).toBe('4 de ces parcelles sont trop loin de l’adresse pour être montrées.');
		expect(tropLoin(1)).toBe('1 de ces parcelles est trop loin de l’adresse pour être montrée.');
		// S5: the sales of the view could not be counted, with the map or without it.
		expect(compteSansVentes(12)).toBe('12 parcelles sur la carte. Les ventes n’ont pas pu être comptées.');
		expect(compteSansVentes(1)).toBe('1 parcelle sur la carte. Les ventes n’ont pas pu être comptées.');
		expect(compteSansVentes(12, true)).toBe('12 parcelles autour de l’adresse. Les ventes n’ont pas pu être comptées.');
		expect(compteSansVentes(1, true)).toBe('1 parcelle autour de l’adresse. Les ventes n’ont pas pu être comptées.');
		// S6: without the map, what the listed zone leaves out.
		expect(horsDeLaZone(5)).toBe('5 de ces parcelles sont hors de la zone listée.');
		expect(horsDeLaZone(1)).toBe('1 de ces parcelles est hors de la zone listée.');
		// S8: the singular of T-29 is a whole sentence of the contract.
		expect(GABARITS['T-29-v1']).toBe('1 parcelle sur la carte, dont 1 avec une vente ({periode}).');
		expect(compteParcelles(1, 1, '2021-2025')).toBe(GABARITS['T-29-v1'].replace('{periode}', '2021-2025'));
		for (const texte of [surfaceSansValeur(1), surfaceSansValeur(2), groupeTropGrand(3), tropLoin(1), tropLoin(2), compteSansVentes(1), compteSansVentes(2, true), horsDeLaZone(1), horsDeLaZone(2)]) {
			expect(texte).not.toMatch(/[{}]/);
		}
	});

	it('the list of the parcels never says what the map cannot know either', () => {
		const liste = readFileSync('src/lib/components/adresse/carte/ParcelList.svelte', 'utf8');
		expect(liste).not.toMatch(/propriétaire|unité foncière|terrain de|jamais vendue|estimation/i);
	});
});
