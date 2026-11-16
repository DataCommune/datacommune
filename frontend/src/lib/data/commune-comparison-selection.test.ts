import { describe, expect, it } from 'vitest';
import {
	MAX_COMMUNES,
	MESSAGE_LIMITE,
	MESSAGE_LIMITE_ADRESSES,
	MESSAGE_LIMITE_DPE,
	avecAdresse,
	avecDesignation,
	avecDocument,
	lienNature,
	avecCommune,
	enPremier,
	lienSelection,
	lienSelectionNature,
	lireDesignations,
	lireSelection,
	lireSelectionNature,
	messageAdresseInconnue,
	messageCodeInconnu,
	messageDesignationRetiree,
	messageDpeInconnu,
	sansCommune,
	sansDesignation
} from './commune-comparison-selection';
import { MAX_COMPARAISON } from '$lib/stores/comparison-selection.svelte';

const lire = (query: string) => lireSelection(new URLSearchParams(query));

describe('comparison selection read from its link (D-49)', () => {
	it('reads 0, 1 and 2 to 4 communes in link order, with the filter', () => {
		expect(lire('')).toEqual({ statut: 'vide', codes: [], differences: false, messages: [] });
		expect(lire('communes=44109')).toEqual({ statut: 'une', codes: ['44109'], differences: false, messages: [] });
		expect(lire('communes=85084,44109,2A004,75056&diff=1')).toEqual({
			statut: 'prete', codes: ['85084', '44109', '2A004', '75056'], differences: true, messages: []
		});
	});

	it('keeps any departement: no perimeter is written in the selection (D-34)', () => {
		for (const code of ['75056', '13055', '2B033', '97411', '01001']) {
			expect(lire(`communes=44109,${code}`).codes).toEqual(['44109', code]);
		}
	});

	it('drops a malformed code with the « Lien altéré » sentence, quoting at most 16 characters', () => {
		const selection = lire('communes=44109,<script>,8508,85084');
		expect(selection.codes).toEqual(['44109', '85084']);
		expect(selection.messages).toEqual([
			'Le lien contenait un code INSEE inconnu (<SCRIPT>) ; il a été écarté.',
			'Le lien contenait un code INSEE inconnu (8508) ; il a été écarté.'
		]);
		expect(messageCodeInconnu('X'.repeat(500))).toBe(
			`Le lien contenait un code INSEE inconnu (${'X'.repeat(16)}…) ; il a été écarté.`
		);
	});

	it('drops a duplicate with a message and keeps the first occurrence', () => {
		const selection = lire('communes=44109,85084,44109');
		expect(selection.codes).toEqual(['44109', '85084']);
		expect(selection.messages).toEqual(['Le lien contenait deux fois la même commune (44109) ; le doublon a été écarté.']);
	});

	it('keeps the first four communes past the limit, with the contract sentence once', () => {
		const selection = lire('communes=44109,85084,85047,44001,75056,13055');
		expect(selection.codes).toEqual(['44109', '85084', '85047', '44001']);
		expect(selection.messages).toEqual([MESSAGE_LIMITE]);
		expect(MESSAGE_LIMITE).toBe('Seules les quatre premières communes du lien sont affichées.');
		expect(MAX_COMMUNES).toBe(MAX_COMPARAISON);
	});

	it('ignores empty entries, other parameters and a filter value other than 1', () => {
		expect(lire('communes=44109,,85084,&key=secret&diff=true')).toEqual({
			statut: 'prete', codes: ['44109', '85084'], differences: false, messages: []
		});
		expect(lire('communes=44109&communes=85084').codes).toEqual(['44109', '85084']);
		expect(lire('communes=2a004,44109').codes).toEqual(['2A004', '44109']);
	});

	it('writes the link back in column order with the filter, and reads it identically', () => {
		expect(lienSelection([], false)).toBe('/comparer');
		expect(lienSelection([], true)).toBe('/comparer?diff=1');
		expect(lienSelection(['85084', '44109'], true)).toBe('/comparer?communes=85084,44109&diff=1');
		const lu = lireSelection(new URL(lienSelection(['85084', '44109', '2B033'], true), 'https://example.test').searchParams);
		expect(lu).toEqual({ statut: 'prete', codes: ['85084', '44109', '2B033'], differences: true, messages: [] });
	});

	it('adds, removes and moves first without mutating, refusing a fifth, a duplicate or a malformed code', () => {
		const deux: readonly string[] = ['44109', '85084'];
		expect(avecCommune(deux, '85047')).toEqual(['44109', '85084', '85047']);
		expect(avecCommune(deux, ' 2b033 ')).toEqual(['44109', '85084', '2B033']);
		expect(avecCommune(deux, '44109')).toEqual(['44109', '85084']);
		expect(avecCommune(deux, 'abc')).toEqual(['44109', '85084']);
		expect(avecCommune(['44109', '85084', '85047', '44001'], '75056')).toEqual(['44109', '85084', '85047', '44001']);
		expect(sansCommune(deux, '44109')).toEqual(['85084']);
		expect(enPremier(['44109', '85084', '85047'], '85047')).toEqual(['85047', '44109', '85084']);
		expect(enPremier(deux, '99999')).toEqual(['44109', '85084']);
		expect(deux).toEqual(['44109', '85084']);
	});
});

// --- 04-07: the nature of the link (D-102), addresses kept apart from communes -------------------

const nature = (query: string) => lireSelectionNature(new URLSearchParams(query));
const A = '85191_0420_00012';
const B = '85191_0601_00001';

describe('comparison selection by nature (04-07, D-102)', () => {
	it('reads the addresses of `?adresses=`, in link order, with the filter', () => {
		expect(nature(`adresses=${A},${B}&diff=1`)).toEqual({
			nature: 'adresse',
			statut: 'prete',
			codes: [A, B],
			differences: true,
			messages: []
		});
		expect(nature(`adresses=${A}`)).toMatchObject({ nature: 'adresse', statut: 'une', codes: [A] });
	});

	it('keeps the communes nature unchanged, and shows only the communes of a mixed link', () => {
		expect(nature('communes=44109,85084')).toEqual({ nature: 'commune', ...lire('communes=44109,85084') });
		expect(nature('')).toMatchObject({ nature: 'commune', statut: 'vide' });
		const melange = nature(`communes=44109,85084&adresses=${A},${B}`);
		expect(melange).toMatchObject({ nature: 'commune', codes: ['44109', '85084'] });
		expect(melange.messages).toEqual([
			"Le lien mélangeait plusieurs natures d'objets ; seules les communes sont affichées."
		]);
	});

	it('drops a malformed key with the « Lien altéré » sentence, a duplicate silently, keeps four', () => {
		const lu = nature(`adresses=${A},46.67;-1.43,${A},${B},85191_0602_00001,85191_0603_00001,85191_0604_00001`);
		expect(lu.codes).toEqual([A, B, '85191_0602_00001', '85191_0603_00001']);
		expect(lu.messages).toEqual([messageAdresseInconnue('46.67;-1.43'), MESSAGE_LIMITE_ADRESSES]);
		// A forged key is quoted on 16 characters at most (same rule as the communes).
		expect(messageAdresseInconnue('85191_0420_00012_bis')).toBe(
			"Le lien contenait un identifiant d'adresse inconnu (85191_0420_00012…) ; il a été écarté."
		);
		expect(MESSAGE_LIMITE_ADRESSES).toBe('Seules les quatre premières adresses du lien sont affichées.');
	});

	it('writes the link of each nature back, and reads it identically', () => {
		expect(lienSelectionNature('commune', ['85084', '44109'], true)).toBe(lienSelection(['85084', '44109'], true));
		expect(lienSelectionNature('adresse', [B, A], true)).toBe(`/comparer?adresses=${B},${A}&diff=1`);
		expect(lienSelectionNature('adresse', [], false)).toBe('/comparer');
		const lu = nature(new URL(lienSelectionNature('adresse', [B, A], false), 'https://example.test').search);
		expect(lu).toEqual({ nature: 'adresse', statut: 'prete', codes: [B, A], differences: false, messages: [] });
	});

	it('adds an address without mutating, refusing a fifth, a duplicate or a malformed key', () => {
		const deux: readonly string[] = [A, B];
		expect(avecAdresse(deux, ' 85191_0602_00001 ')).toEqual([A, B, '85191_0602_00001']);
		expect(avecAdresse(deux, A)).toEqual([A, B]);
		expect(avecAdresse(deux, '85191-0420-00012')).toEqual([A, B]);
		expect(avecAdresse([A, B, '85191_0602_00001', '85191_0603_00001'], '85191_0604_00001')).toHaveLength(MAX_COMMUNES);
		expect(deux).toEqual([A, B]);
	});
});

// --- 04-10: DPE documents, the third nature (D-102, CMP-02) --------------------------------------

const ANCIEN = '2185E0000001A';
const ACTUEL = '2385E0123456A';

describe('comparison selection of DPE documents (04-10)', () => {
	it('reads the numbers of `?dpe=`, in link order, with the filter', () => {
		expect(nature(`dpe=${ANCIEN},${ACTUEL}&diff=1`)).toEqual({
			nature: 'dpe',
			statut: 'prete',
			codes: [ANCIEN, ACTUEL],
			differences: true,
			messages: []
		});
		// An empty parameter names the nature: its empty comparator, never the communes.
		expect(nature('dpe=')).toMatchObject({ nature: 'dpe', statut: 'vide', codes: [] });
	});

	it('a link carrying two natures shows the first one only, communes → adresses → dpe', () => {
		const communes = nature(`communes=44109,85084&dpe=${ANCIEN},${ACTUEL}`);
		expect(communes).toMatchObject({ nature: 'commune', codes: ['44109', '85084'] });
		expect(communes.messages).toEqual(["Le lien mélangeait plusieurs natures d'objets ; seules les communes sont affichées."]);
		const adresses = nature(`dpe=${ANCIEN}&adresses=${A},${B}`);
		expect(adresses).toMatchObject({ nature: 'adresse', codes: [A, B] });
		expect(adresses.messages).toEqual(["Le lien mélangeait plusieurs natures d'objets ; seules les adresses sont affichées."]);
	});

	it('drops a malformed number with the « Lien altéré » sentence, a duplicate silently, keeps four', () => {
		const lu = nature(`dpe=${ANCIEN},2385e0123456a,${ANCIEN},${ACTUEL},2485E0000003C,2485E0000004C,2485E0000005K`);
		expect(lu.codes).toEqual([ANCIEN, ACTUEL, '2485E0000003C', '2485E0000004C']);
		expect(lu.messages).toEqual([messageDpeInconnu('2385e0123456a'), MESSAGE_LIMITE_DPE]);
		expect(messageDpeInconnu('2385E9999999X')).toBe('Le lien contenait un numéro DPE inconnu (2385E9999999X) ; il a été écarté.');
		expect(MESSAGE_LIMITE_DPE).toBe('Seuls les quatre premiers documents DPE du lien sont affichés.');
	});

	it('writes the link of the documents back, and the switch link of an empty nature', () => {
		expect(lienSelectionNature('dpe', [ACTUEL, ANCIEN], true)).toBe(`/comparer?dpe=${ACTUEL},${ANCIEN}&diff=1`);
		const lu = nature(new URL(lienSelectionNature('dpe', [ACTUEL, ANCIEN], false), 'https://example.test').search);
		expect(lu).toEqual({ nature: 'dpe', statut: 'prete', codes: [ACTUEL, ANCIEN], differences: false, messages: [] });
		expect(lienNature('dpe', [])).toBe('/comparer?dpe=');
		expect(lienNature('adresse', [])).toBe('/comparer?adresses=');
		expect(lienNature('commune', [])).toBe('/comparer');
		expect(lienNature('dpe', [ACTUEL])).toBe(`/comparer?dpe=${ACTUEL}`);
	});

	it('adds a number without mutating, refusing a fifth, a duplicate or a malformed one', () => {
		const deux: readonly string[] = [ANCIEN, ACTUEL];
		expect(avecDocument(deux, ' 2485E0000003C ')).toEqual([ANCIEN, ACTUEL, '2485E0000003C']);
		expect(avecDocument(deux, ANCIEN)).toEqual(deux);
		expect(avecDocument(deux, '2485e0000003c')).toEqual(deux);
		expect(avecDocument([ANCIEN, ACTUEL, '2485E0000003C', '2485E0000004C'], '2485E0000005K')).toHaveLength(MAX_COMMUNES);
		expect(deux).toEqual([ANCIEN, ACTUEL]);
	});
});

// --- 04-11 (D-102): a DPE designated on a compared address, in the link only ---------------------

describe('designations of the link (04-11)', () => {
	const ANCIEN_D = '2185E0000001A';
	const ACTUEL_D = '2385E0123456A';
	const designes = (query: string, adresses: string[] = [A, B]) => lireDesignations(new URLSearchParams(query), adresses);

	it('keeps one pair per compared address, in column order', () => {
		expect(designes(`designes=${B}:${ACTUEL_D},${A}:${ANCIEN_D}`)).toEqual({
			designations: [
				{ ban_id: A, numero: ANCIEN_D },
				{ ban_id: B, numero: ACTUEL_D }
			],
			ecartees: []
		});
		// An exact duplicate is the same designation.
		expect(designes(`designes=${A}:${ANCIEN_D},${A}:${ANCIEN_D}`).designations).toEqual([{ ban_id: A, numero: ANCIEN_D }]);
	});

	it('drops the pair of an address not compared, sets aside a malformed number or a second pair', () => {
		expect(designes(`designes=85191_0999_00001:${ANCIEN_D},nimportequoi,:${ANCIEN_D}`)).toEqual({ designations: [], ecartees: [] });
		expect(designes(`designes=${A}:2185e0000001a,${B}:${ACTUEL_D},${B}:${ANCIEN_D}`)).toEqual({
			designations: [{ ban_id: B, numero: ACTUEL_D }],
			ecartees: [
				{ ban_id: A, numero: '2185e0000001a' },
				{ ban_id: B, numero: ANCIEN_D }
			]
		});
	});

	it('travels with the addresses of the link, never with another nature', () => {
		const lu = nature(`adresses=${A},${B}&designes=${A}:${ANCIEN_D}&diff=1`);
		expect(lu).toMatchObject({ nature: 'adresse', codes: [A, B], designations: [{ ban_id: A, numero: ANCIEN_D }] });
		expect(nature(`dpe=${ANCIEN_D},${ACTUEL_D}&designes=${A}:${ANCIEN_D}`)).not.toHaveProperty('designations');
		const designations = [{ ban_id: A, numero: ANCIEN_D }];
		expect(lienSelectionNature('adresse', [B, A], true, designations)).toBe(`/comparer?adresses=${B},${A}&designes=${A}:${ANCIEN_D}&diff=1`);
		// An address removed takes its designation away; another nature never carries one.
		expect(lienSelectionNature('adresse', [B], false, designations)).toBe(`/comparer?adresses=${B}`);
		expect(lienSelectionNature('dpe', [ANCIEN_D], false, designations)).toBe(`/comparer?dpe=${ANCIEN_D}`);
		// Written back, read identically.
		const relu = nature(new URL(lienSelectionNature('adresse', [A, B], false, designations), 'https://example.test').search);
		expect(relu.designations).toEqual(designations);
	});

	it('designates and removes without mutating, one designation per address', () => {
		const une: readonly { ban_id: string; numero: string }[] = [{ ban_id: A, numero: ANCIEN_D }];
		expect(avecDesignation(une, A, ACTUEL_D)).toEqual([{ ban_id: A, numero: ACTUEL_D }]);
		expect(avecDesignation(une, B, ACTUEL_D)).toEqual([
			{ ban_id: A, numero: ANCIEN_D },
			{ ban_id: B, numero: ACTUEL_D }
		]);
		expect(avecDesignation(une, B, 'pas-un-numero')).toEqual(une);
		expect(sansDesignation(une, A)).toEqual([]);
		expect(une).toEqual([{ ban_id: A, numero: ANCIEN_D }]);
	});

	it('words a designation set aside with the sentence of the contract', () => {
		expect(messageDesignationRetiree('12 Rue des Lilas')).toBe(
			"Le DPE désigné pour 12 Rue des Lilas n'est pas déclaré à cette adresse ; la désignation a été retirée."
		);
	});
});

describe('a notice of the link is said once (review R-02)', () => {
	it('says the same altered commune code once, however many times the link repeats it', () => {
		expect(lire('communes=ABC,ABC').messages).toEqual([messageCodeInconnu('ABC')]);
	});

	it('says the same duplicated commune once', () => {
		const lu = lire('communes=44109,44109,44109');
		expect(lu.codes).toEqual(['44109']);
		expect(lu.messages).toEqual(["Le lien contenait deux fois la même commune (44109) ; le doublon a été écarté."]);
	});

	it('keeps two different altered codes as two notices', () => {
		expect(lire('communes=ABC,XYZ').messages).toEqual([messageCodeInconnu('ABC'), messageCodeInconnu('XYZ')]);
	});

	it('says the same altered address key once', () => {
		expect(lireSelectionNature(new URLSearchParams('adresses=x,x')).messages).toEqual([messageAdresseInconnue('x')]);
	});

	it('says the same altered DPE number once', () => {
		expect(lireSelectionNature(new URLSearchParams('dpe=x,x')).messages).toEqual([messageDpeInconnu('x')]);
	});
});
