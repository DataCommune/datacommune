// Acronyms spelled out at their first occurrence (04.8-08, D-17, D-31; UI-SPEC v4 § 7.5).
import { describe, expect, it } from 'vitest';
import { SIGLES, developperHorsCitations, developperParCle, developperSigles, sansDeveloppement } from './sigles';

describe('SIGLES', () => {
	// The 25 entries of the mock-up (pages.mjs:30-56), in their order.
	const MAQUETTE = [
		'BAN', 'PLU', 'DPE', 'RNB', 'BDNB', 'ANIL', 'ATMO', 'DVF', 'ADEME', 'IGN', 'DGFiP', 'BRGM', 'SSMSI',
		'ARS', 'OMS', 'GPU', 'ICPE', 'LOVAC', 'MTE', 'WFS', 'GTFS', 'ODbL', 'CGU', '‰', '°f'
	];
	// The additions of the socle (2026-11-09), with the full form each one must carry, word for word.
	const SOCLE: Record<string, string> = {
		GES: 'gaz à effet de serre',
		VEFA: 'vente en l’état futur d’achèvement',
		CNIL: 'Commission nationale de l’informatique et des libertés',
		EPCI: 'établissement public de coopération intercommunale',
		TFPB: 'taxe foncière sur les propriétés bâties',
		TEOM: 'taxe d’enlèvement des ordures ménagères',
		ANFR: 'Agence nationale des fréquences',
		ANAH: 'Agence nationale de l’habitat',
		DRIHL: 'Direction régionale et interdépartementale de l’hébergement et du logement',
		DINUM: 'Direction interministérielle du numérique'
	};

	it('holds the 25 entries of the mock-up, then the additions of the socle, each once and with its full form', () => {
		const sigles = SIGLES.map((s) => s.sigle);
		expect([...sigles].sort()).toEqual([...MAQUETTE, ...Object.keys(SOCLE)].sort());
		expect(new Set(sigles).size).toBe(sigles.length);
		for (const { plein } of SIGLES) expect(plein.length).toBeGreaterThan(3);
		for (const [sigle, plein] of Object.entries(SOCLE)) expect(SIGLES.find((s) => s.sigle === sigle)?.plein, sigle).toBe(plein);
	});

	it('every entry knows its own full form as already said, in the singular and as its acronym is first written', () => {
		for (const { sigle, plein, deja } of SIGLES) {
			expect(deja.test(plein), `${sigle} : « ${plein} »`).toBe(true);
			expect(developperSigles([`${plein} (${sigle})`, `Le ${sigle}`]), sigle).toEqual([`${plein} (${sigle})`, `Le ${sigle}`]);
		}
	});

	// Left out on purpose (socle): their full form fell in a link label or broke a name (« API Carto », « code
	// INSEE »), changed a sentence a spec holds word for word (RGPD), or is an English expansion no reader gains
	// from. Adding one back is a decision, not an oversight.
	it.each(['INSEE', 'RGPD', 'API', 'JSON', 'CSV', 'Arcep', 'CSTB', 'EP'])('%s stays out of the list', (sigle) => {
		expect(SIGLES.some((s) => s.sigle === sigle)).toBe(false);
		expect(developperSigles([`Le ${sigle} du site.`])).toEqual([`Le ${sigle} du site.`]);
	});
});

describe('developperSigles (D-31)', () => {
	it('spells out the first occurrence only, across the segments of a page', () => {
		expect(developperSigles(['Le DPE', 'Un autre DPE'])).toEqual([
			'Le DPE (diagnostic de performance énergétique)',
			'Un autre DPE'
		]);
		expect(developperSigles(['Le DPE et le DPE'])).toEqual(['Le DPE (diagnostic de performance énergétique) et le DPE']);
		expect(developperSigles(['Ventes DVF et DPE.'])).toEqual([
			'Ventes DVF (Demandes de valeurs foncières) et DPE (diagnostic de performance énergétique).'
		]);
	});

	it('adds nothing when a segment of the page already says the full form', () => {
		expect(developperSigles(['Le diagnostic de performance énergétique', 'Le DPE'])).toEqual([
			'Le diagnostic de performance énergétique',
			'Le DPE'
		]);
		expect(developperSigles(['Le DPE', 'Base Adresse Nationale'])).toEqual([
			'Le DPE (diagnostic de performance énergétique)',
			'Base Adresse Nationale'
		]);
		// Already spelled out in the same segment, before or after: no second parenthesis.
		expect(developperSigles(['La BAN (Base Adresse Nationale)'])).toEqual(['La BAN (Base Adresse Nationale)']);
		expect(developperSigles(['Diagnostics de performance énergétique (DPE)', 'Le DPE'])).toEqual([
			'Diagnostics de performance énergétique (DPE)',
			'Le DPE'
		]);
	});

	it('a full form said further down the page does not count: the reader meets the acronym first', () => {
		expect(developperSigles(['DPE dominant à l’adresse', 'Diagnostics de performance énergétique (DPE)'])).toEqual([
			'DPE (diagnostic de performance énergétique) dominant à l’adresse',
			'Diagnostics de performance énergétique (DPE)'
		]);
	});

	it('an acronym that closes a parenthesis takes its full form after a comma, never skipped', () => {
		expect(developperSigles(['Prix (DVF, de la DGFiP)', 'DGFiP / Etalab'])).toEqual([
			'Prix (DVF (Demandes de valeurs foncières), de la DGFiP, Direction générale des finances publiques)',
			'DGFiP / Etalab'
		]);
		expect(developperSigles(['Classe énergie (DPE)'])).toEqual(['Classe énergie (DPE, diagnostic de performance énergétique)']);
	});

	it('spells out the additions of the socle in a sentence, a hand-written full form before them counts', () => {
		// « code INSEE » names a code: its sentence stays as written (INSEE is left out of the list).
		const codeInconnu = 'Le lien contenait un code INSEE inconnu (44999) ; il a été écarté.';
		expect(developperSigles([codeInconnu])).toEqual([codeInconnu]);
		expect(developperSigles(['Géorisques, BRGM, ANFR'])).toEqual([
			'Géorisques, BRGM (Bureau de recherches géologiques et minières), ANFR (Agence nationale des fréquences)'
		]);
		// The definitions of the commune fiche already say these in full: nothing is added after them.
		const definitions = [
			'Nom de l’intercommunalité (EPCI, établissement public de coopération intercommunale) de la commune.',
			'Taux voté pour la taxe foncière sur les propriétés bâties (TFPB), en points de pourcentage.',
			'Les ventes en l’état futur d’achèvement (VEFA, achat sur plan) sont exclues.',
			'EPCI, TFPB et VEFA.'
		];
		expect(developperSigles(definitions)).toEqual(definitions);
		// A unit glued to its letters is not the acronym: « kWhEP » and a class label keep their text.
		expect(developperSigles(['250 kWhEP/m²/an', 'GESTION, AGES'])).toEqual(['250 kWhEP/m²/an', 'GESTION, AGES']);
	});

	it('only takes whole words, and leaves a postal code, an identifier and a URL intact', () => {
		for (const texte of [
			'44000',
			'44109_zz04_00004',
			'https://www.data.gouv.fr/DVF/BAN?x=DPE',
			'Voir /datasets#CGU et /dpe/DPE',
			'BANQUE, DPEX, xDPE, DPE_2, MDPE',
			'banlieue'
		]) {
			expect(developperSigles([texte])).toEqual([texte]);
		}
	});

	it('spells out an acronym after a typographic apostrophe and before punctuation', () => {
		expect(developperSigles(['Données de l’ADEME.'])).toEqual([
			'Données de l’ADEME (Agence de la transition écologique).'
		]);
		expect(developperSigles(['« DPE »'])).toEqual(['« DPE (diagnostic de performance énergétique) »']);
	});

	it('is pure: two identical calls give the same output, and the input is not changed', () => {
		const entree = Object.freeze(['Le DPE', 'La BAN', 'Le DPE']);
		const a = developperSigles(entree);
		const b = developperSigles(entree);
		expect(a).toEqual(b);
		expect(entree).toEqual(['Le DPE', 'La BAN', 'Le DPE']);
		expect(a).toHaveLength(entree.length);
	});

	it('developperParCle keys the segments of a page rendered by several components, in reading order', () => {
		const textes = developperParCle([
			['bref:dpe', 'Le DPE'],
			['absent', null],
			['titre:dpe', 'Le DPE'],
			['verdict:dpe', 'Classe D au DPE.']
		]);
		expect([...textes.entries()]).toEqual([
			['bref:dpe', 'Le DPE (diagnostic de performance énergétique)'],
			['titre:dpe', 'Le DPE'],
			['verdict:dpe', 'Classe D au DPE.']
		]);
	});

	it('developperHorsCitations keeps a quotation « … » as it is, and spells out the text around it', () => {
		const textes = developperHorsCitations([
			['mention', 'Mention imposée : « Estimations ANIL, à partir des données » pour l’ANIL.'],
			['saisie', 'Le numéro « DPE-X » n’est pas un numéro DPE.'],
			['vide', null]
		]);
		expect([...textes.entries()]).toEqual([
			['mention', 'Mention imposée : « Estimations ANIL, à partir des données » pour l’ANIL (Agence nationale pour l’information sur le logement).'],
			['saisie', 'Le numéro « DPE-X » n’est pas un numéro DPE (diagnostic de performance énergétique).']
		]);
	});

	it('the fallback spells out a single segment on its own (error page, (app))', () => {
		expect(developperSigles(['Fiche DPE introuvable.'])[0]).toBe(
			'Fiche DPE (diagnostic de performance énergétique) introuvable.'
		);
		expect(developperSigles([])).toEqual([]);
	});
});

describe('sansDeveloppement (checks)', () => {
	it('gives back the served text, and keeps a full form that is not right after its acronym', () => {
		// A full form the source writes itself after a comma (« DVF, Demandes de valeurs foncières, de… ») stays.
		const servi = ['Le DPE et la BAN.', 'Prix (DVF, Demandes de valeurs foncières, de la DGFiP)', 'Le DPE'];
		expect(developperSigles(servi).map(sansDeveloppement)).toEqual(servi);
		expect(sansDeveloppement('Eau (Agence de la transition écologique) conforme.')).toBe('Eau (Agence de la transition écologique) conforme.');
	});
});
