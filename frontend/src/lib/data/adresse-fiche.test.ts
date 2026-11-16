// Model of the address landmarks and of the declared-documents section (04-18; UI-SPEC
// « /adresse/[id] » points 3 and 4, « Contrat de rédaction »; D-109, D-111, D-113, SEM-02). The
// page renders what 04-03 serves: these tests pin the projection, never a computation.
import { describe, expect, it } from 'vitest';
import type {
	DocumentDpeCarteAdresseBrute,
	DocumentsDpeAdresse,
	FicheAdressePublique,
	GroupeAdresseBrute,
	SelectionPlusRecent,
	ValeurLieu
} from '$lib/api/adresses';
import type { KpiLigne } from './commune-kpi';
import { MAX_COMPARAISON } from '$lib/stores/comparison-selection.svelte';
import {
	BADGE_ADRESSE_BRUTE,
	CONSIGNE_COMPARAISON_DOCUMENTS,
	EXPLICATION_ADRESSE_BRUTE,
	PHRASE_POSITIONS_CONTRADICTOIRES,
	badgeProvisoire,
	badgeRattachement,
	libelleCompteurSelection,
	criteresConstat,
	libelleBadge,
	libelleRattachement,
	ligneAdresseDeclaree,
	limiteGroupeAdresseBrute,
	phraseEnTeteDpe,
	phraseGroupeAdresseBrute,
	phraseIdentiteIndeterminee,
	qualificatifSelection,
	voletAdresseBrute,
	volet,
	voletConstat,
	voletDocuments
} from './adresse-fiche';
import { definitionPublique } from './explications';
import { libelleEtat, phraseEtat } from './etats-libelles';

const NB = ' ';

const SOURCE_DPE = {
	cle: 'dpe',
	nom: 'Diagnostics de performance énergétique (DPE)',
	producteur: 'ADEME',
	url: 'https://data.ademe.fr/datasets/dpe03existant'
};

const UNIQUE: SelectionPlusRecent = {
	etat: 'unique',
	motif: 'document_valide',
	date: '2024-03-12',
	document: { numero: '2485E0123456X', etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
	documents_ex_aequo: []
};

function fiche(): FicheAdressePublique {
	return {
		identite: {
			etat: 'etablie',
			ban_id: '85191_0420_00012',
			libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
			numero: '12',
			rep: null,
			nom_voie: 'Rue des Lilas',
			code_postal: '85000',
			commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
			departement: { code: '85', nom: 'Vendée' },
			parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1 }
		},
		couverture: { etat: 'heberge', motif: null },
		reperes: null
	};
}

function documents(partiel: Partial<DocumentsDpeAdresse> = {}): DocumentsDpeAdresse {
	return {
		etat: 'value',
		motif: null,
		source: SOURCE_DPE,
		total: 3,
		page: 1,
		pages: 1,
		par_page: 10,
		generation: '0123456789abcdef',
		distribution: { A: 0, B: 0, C: 2, D: 1, E: 0, F: 0, G: 0, unknown: 0 },
		date_import: '2026-09-30',
		documents: [],
		selection: UNIQUE,
		signal_plusieurs_logements: null,
		constat_d110: null,
		...partiel
	};
}

describe('libelleRattachement — closed list of the UI-SPEC (SEM-02)', () => {
	it.each([
		['adresse_saisie_diagnostiqueur', 'Adresse saisie par le diagnostiqueur (non vérifiée)'],
		['parcelle_du_point', "Parcelle qui contient le point de l'adresse"],
		['batiment_du_point', "Bâtiment qui contient le point de l'adresse"],
		['au_point', "Au point de l'adresse"],
		['eligibilite_adresse', 'Éligibilité publiée pour cette adresse'],
		['commune_reference', "Commune de l'adresse (référence)"]
	] as const)('%s → « %s »', (cle, texte) => {
		expect(libelleRattachement(cle)).toBe(texte);
	});

	it('a radius without its served distance, an unknown key or nothing served: no entry (never invented)', () => {
		expect(libelleRattachement('rayon_du_point')).toBeNull();
		expect(libelleRattachement('cle_inconnue')).toBeNull();
		expect(libelleRattachement(null)).toBeNull();
	});
});

describe('phraseEnTeteDpe — head sentence of the section (D-109, contrat de rédaction)', () => {
	it('N ≥ 2: « {N} DPE déclarés à cette adresse · le plus récent : classe {X}, {mois année} »', () => {
		expect(phraseEnTeteDpe(documents())).toBe(`3 DPE déclarés à cette adresse · le plus récent${NB}: classe C, mars 2024`);
	});

	it('N = 1: « 1 DPE déclaré à cette adresse · classe {X}, {mois année} »', () => {
		expect(phraseEnTeteDpe(documents({ total: 1 }))).toBe('1 DPE déclaré à cette adresse · classe C, mars 2024');
	});
});

// --- Task 2 — landmarks ② ③ ④ and their states (D-113, CMP-01, SEM-01, N-02, N-03) -----------

const NNB = ' ';

const EGALITE: SelectionPlusRecent = {
	etat: 'egalite',
	motif: 'document_valide',
	date: '2024-03-12',
	document: null,
	documents_ex_aequo: [
		{ numero: '2485E0000003C', etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
		{ numero: '2485E0000004D', etiquette_dpe: 'D', etiquette_ges: 'C', date_etablissement: '2024-03-12' }
	]
};
const INDETERMINEE: SelectionPlusRecent = { etat: 'indeterminee', motif: 'aucun_document_date', date: null, document: null, documents_ex_aequo: [] };
const AUCUN: SelectionPlusRecent = { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] };

function nonValide(motif: string): SelectionPlusRecent {
	return { ...UNIQUE, motif };
}

describe('phraseEnTeteDpe — every form of the contract (D-109)', () => {
	it('N = 0: the sentence with the served import date, and no claim that no DPE exists', () => {
		expect(phraseEnTeteDpe(documents({ total: 0, selection: AUCUN }))).toBe(
			"Aucun DPE déclaré à cette adresse dans la base ADEME importée le 30 septembre 2026. Cela ne prouve pas qu'aucun DPE n'a été réalisé."
		);
	});

	it.each([
		['expire', '(expiré)'],
		['ancienne_methode', '(ancienne méthode)']
	])('no valid document (%s): « … · le plus récent : classe {X}, {mois année} %s »', (motif, suffixe) => {
		expect(phraseEnTeteDpe(documents({ selection: nonValide(motif) }))).toBe(
			`3 DPE déclarés à cette adresse · le plus récent${NB}: classe C, mars 2024 ${suffixe}`
		);
		expect(phraseEnTeteDpe(documents({ total: 1, selection: nonValide(motif) }))).toBe(
			`1 DPE déclaré à cette adresse · classe C, mars 2024 ${suffixe}`
		);
	});

	it('uncertain replacement: the status wording of the UI-SPEC (« Remplacement non vérifié »), never a validity', () => {
		expect(phraseEnTeteDpe(documents({ selection: nonValide('remplacement_incertain') }))).toBe(
			`3 DPE déclarés à cette adresse · le plus récent${NB}: classe C, mars 2024 (remplacement non vérifié)`
		);
	});

	it("verified replacement: the wording of the card badge (« remplacé selon l'ADEME », row 24, API WR-01)", () => {
		expect(phraseEnTeteDpe(documents({ selection: nonValide('remplace') }))).toBe(
			`3 DPE déclarés à cette adresse · le plus récent${NB}: classe C, mars 2024 (remplacé selon l'ADEME)`
		);
	});

	it('a class not served inside the sentence: lower case and agreed (IN-02 of the D-15 review)', () => {
		const selection = { ...nonValide('expire'), document: { ...nonValide('expire').document!, etiquette_dpe: null } };

		expect(phraseEnTeteDpe(documents({ selection }))).toBe(
			`3 DPE déclarés à cette adresse · le plus récent${NB}: classe non renseignée, mars 2024 (expiré)`
		);
	});

	it('a count not served is never read as zero (IN-01 of the D-15 review)', () => {
		expect(phraseEnTeteDpe(documents({ total: null }))).toBe(
			"Les documents DPE n'ont pas pu être lus. Le reste de la page reste affiché."
		);
	});

	it('unknown validity: « (validité non déterminée) » (04-DECISIONS-TEXTES, 2026-10-05, row 3)', () => {
		expect(phraseEnTeteDpe(documents({ selection: nonValide('validite_inconnue') }))).toBe(
			`3 DPE déclarés à cette adresse · le plus récent${NB}: classe C, mars 2024 (validité non déterminée)`
		);
	});

	it('a reason served later without a wording (motif_futur) is said, never dropped nor read as valid', () => {
		expect(phraseEnTeteDpe(documents({ selection: nonValide('motif_futur') }))).toBe(
			`3 DPE déclarés à cette adresse · le plus récent${NB}: classe C, mars 2024 (valeur non affichable)`
		);
	});

	it('tie: « {k} documents partagent la date la plus récente : {date} · classes {classes servies} », no document named', () => {
		const phrase = phraseEnTeteDpe(documents({ selection: EGALITE }));

		expect(phrase).toBe(`3 DPE déclarés à cette adresse · 2 documents partagent la date la plus récente${NB}: 12 mars 2024 · classes C, D`);
		expect(phrase).not.toContain('le plus récent');
	});

	it('undetermined: « Le document le plus récent ne peut pas être déterminé. »', () => {
		expect(phraseEnTeteDpe(documents({ selection: INDETERMINEE }))).toBe(
			'3 DPE déclarés à cette adresse. Le document le plus récent ne peut pas être déterminé.'
		);
	});
});

describe('qualificatifSelection — each qualification served by the API for the most recent document (D-111)', () => {
	it('keeps each served qualification, and the neutral label for a reason served later', () => {
		expect(qualificatifSelection('document_valide')).toBeNull();
		expect(qualificatifSelection('expire')).toBe('expiré');
		expect(qualificatifSelection('ancienne_methode')).toBe('ancienne méthode');
		expect(qualificatifSelection('validite_inconnue')).toBe('validité non déterminée');
		expect(qualificatifSelection('remplace')).toBe("remplacé selon l'ADEME");
		expect(qualificatifSelection('remplacement_incertain')).toBe('remplacement non vérifié');
		expect(qualificatifSelection('motif_futur')).toBe('valeur non affichable');
	});
});

describe('libelleBadge — served badges of a card (UI-SPEC « Politique d’affichage DPE »)', () => {
	it.each([
		['plus_recent', 'Le plus récent'],
		['meme_date_plus_recente', 'Même date la plus récente'],
		['ancienne_methode', 'Ancienne méthode'],
		['expire', 'Expiré'],
		['remplacement_incertain', 'Remplacement non vérifié'],
		// Owner's wording of 2026-10-04/05 (04-DECISIONS-TEXTES): no successor number on a card.
		['remplace', "Remplacé selon l'ADEME"],
		['dates_incoherentes', 'Dates incohérentes'],
		['etablissement_futur', "Date d'établissement future"]
	])('%s → « %s »', (badge, libelle) => {
		expect(libelleBadge(badge)).toBe(libelle);
		expect(badgeProvisoire(badge)).toBe(false);
	});

	it('a badge served later without a wording: the neutral label of the glossary, never dropped', () => {
		expect(libelleBadge('cle_future')).toBe('Valeur non affichable');
		expect(badgeProvisoire('cle_future')).toBe(true);
	});
});

describe('phraseEtat — not reported, by object (UI-SPEC « Glossaire — deltas »)', () => {
	it('commune by default (existing callers unchanged), address and document on request', () => {
		expect(phraseEtat('not_reported', '', null)).toBe('La source ne fournit pas cette valeur pour cette commune.');
		expect(phraseEtat('not_reported', '', null, 'adresse')).toBe('La source ne fournit pas cette valeur pour cette adresse.');
		expect(phraseEtat('not_reported', '', null, 'document')).toBe('La source ne fournit pas cette valeur dans ce document.');
	});
});

describe('ligneAdresseDeclaree — line 4 of a card (P-ADR-01, UI-SPEC « Contrat de rédaction »)', () => {
	const NNB = ' ';

	it('published: the served text as typed, between French quotes, never corrected', () => {
		expect(ligneAdresseDeclaree({ etat: 'publiee', texte: '12 BIS Rue des Lilas', motif: null })).toBe(
			`Adresse déclarée${NB}: «${NNB}12 BIS Rue des Lilas${NNB}»`
		);
	});

	it('masked: the short line « Adresse déclarée non affichée » whatever the reason (reason in the panel, 2026-10-04)', () => {
		for (const motif of ['complement_present', 'non_verifiable_sans_complement', 'motif_futur']) {
			expect(ligneAdresseDeclaree({ etat: 'masquee', texte: null, motif })).toBe('Adresse déclarée non affichée');
		}
	});

	it('nothing for a concordant document, an absent text, or a published state without text', () => {
		expect(ligneAdresseDeclaree(null)).toBeNull();
		expect(ligneAdresseDeclaree({ etat: 'absente', texte: null, motif: null })).toBeNull();
		expect(ligneAdresseDeclaree({ etat: 'publiee', texte: null, motif: null })).toBeNull();
	});

	it('a masked state never shows a text, even if one were sent', () => {
		const ligne = ligneAdresseDeclaree({ etat: 'masquee', texte: 'SENTINELLE', motif: 'complement_present' });
		expect(ligne).not.toContain('SENTINELLE');
		expect(ligne).not.toContain('Secret statistique');
	});
});

// --- Owner's decisions of 2026-10-04/05 (04-DECISIONS-TEXTES.md) -----------------------------------

function carteDoc(partiel: Partial<DocumentsDpeAdresse['documents'][number]> = {}): DocumentsDpeAdresse['documents'][number] {
	return {
		numero: '2485E0000003C',
		etiquette_dpe: 'C',
		etiquette_ges: 'B',
		type_batiment: 'maison',
		surface_habitable: 92,
		date_etablissement: '2024-03-12',
		date_fin_validite: '2034-03-11',
		badges: [],
		adresse_declaree: null,
		...partiel
	};
}

describe('voletDocuments — reasons and date anomalies read in the panel of the section', () => {
	const entree = (volet: ReturnType<typeof voletDocuments>, terme: string) => {
		const trouvee = volet.find((e) => e.terme === terme);
		return trouvee && 'valeur' in trouvee ? trouvee.valeur : null;
	};

	it('masked addresses: the served reasons, in the table order, then the conservation sentence, once', () => {
		const volet = voletDocuments(
			documents({
				documents: [
					carteDoc({ adresse_declaree: { etat: 'masquee', texte: null, motif: 'non_verifiable_sans_complement' } }),
					carteDoc({ adresse_declaree: { etat: 'masquee', texte: null, motif: 'complement_present' } }),
					carteDoc({ adresse_declaree: { etat: 'masquee', texte: null, motif: 'complement_present' } })
				]
			})
		);

		expect(volet.filter((e) => e.terme === 'Adresse déclarée non affichée')).toHaveLength(1);
		expect(entree(volet, 'Adresse déclarée non affichée')).toBe(
			'Le document y joint un complément (étage, porte, bâtiment ou nom). Elle ne se vérifie pas comme une adresse sans complément. ' +
				"Le texte saisi par le diagnostiqueur est conservé par DataCommune et n'est pas publié."
		);
	});

	it('a masked address with a reason served later keeps the conservation sentence only', () => {
		const volet = voletDocuments(documents({ documents: [carteDoc({ adresse_declaree: { etat: 'masquee', texte: null, motif: 'motif_futur' } })] }));

		expect(entree(volet, 'Adresse déclarée non affichée')).toBe("Le texte saisi par le diagnostiqueur est conservé par DataCommune et n'est pas publié.");
	});

	it('« Dates incohérentes » and « Date d’établissement future » carry their decided sentence', () => {
		const volet = voletDocuments(documents({ documents: [carteDoc({ badges: ['dates_incoherentes', 'etablissement_futur', 'remplace'] })] }));

		expect(entree(volet, 'Dates incohérentes')).toBe(
			`Les dates déclarées dans ce document ne sont pas cohérentes entre elles${NNB}; sa validité ne peut pas être établie.`
		);
		expect(entree(volet, "Date d'établissement future")).toBe(
			`La date d'établissement déclarée est postérieure à aujourd'hui${NNB}; sa validité ne peut pas être établie.`
		);
		// « Remplacé selon l'ADEME » has no panel sentence of its own.
		expect(entree(volet, "Remplacé selon l'ADEME")).toBeNull();
	});
});

describe('reasons of an undetermined parcel (②) or position (③) — own label and sentence, never « Non publiée »', () => {
	it('② parcelle_indeterminee: « Parcelle non déterminée »', () => {
		expect(phraseEtat('not_available', 'parcelle_indeterminee', 1, 'adresse')).toBe("Le point de l'adresse ne désigne pas une seule parcelle.");
		expect(libelleEtat('not_available', 'parcelle_indeterminee')).toBe('Parcelle non déterminée');
	});

	it('③ position_indeterminee: « Position non déterminée »', () => {
		expect(phraseEtat('not_available', 'position_indeterminee', 1, 'adresse')).toBe(
			`La position de l'adresse n'est pas établie${NNB}; l'exposition au point n'est pas calculée.`
		);
		expect(libelleEtat('not_available', 'position_indeterminee')).toBe('Position non déterminée');
		expect(libelleEtat('not_available', 'motif_quelconque')).toBe('Non publiée');
	});
});

describe('phraseIdentiteIndeterminee — undetermined identity (rows 7, 22, 23)', () => {
	const identite = (parcelle: FicheAdressePublique['identite']['parcelle'], etat: 'etablie' | 'indeterminee' = 'indeterminee') => ({
		...fiche().identite,
		etat,
		libelle: null,
		parcelle
	});

	it('contradictory positions: the decided sentence', () => {
		expect(phraseIdentiteIndeterminee(identite({ etat: 'indeterminee', id_parcelle: null, motif: 'positions_contradictoires' }))).toBe(
			`Les sources ne s'accordent pas sur la position de cette adresse${NNB}; les repères qui en dépendent ne sont pas calculés.`
		);
		expect(PHRASE_POSITIONS_CONTRADICTOIRES).toBe(
			`Les sources ne s'accordent pas sur la position de cette adresse${NNB}; les repères qui en dépendent ne sont pas calculés.`
		);
	});

	it('a missing position: the decided sentence of row 22', () => {
		expect(phraseIdentiteIndeterminee(identite({ etat: 'indeterminee', id_parcelle: null, motif: 'position_absente' }))).toBe(
			`La position de cette adresse n'est pas publiée par la BAN${NNB}; les repères qui en dépendent ne sont pas calculés.`
		);
	});

	it('BAN rows disagreeing around one position (parcel resolved): the decided sentence of row 23', () => {
		expect(phraseIdentiteIndeterminee(identite({ etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1 }))).toBe(
			`Les lignes de la BAN ne s'accordent pas sur cette adresse${NNB}; les repères qui en dépendent ne sont pas calculés.`
		);
	});

	it('an established identity, or a parcel reason without wording: no sentence', () => {
		expect(
			phraseIdentiteIndeterminee(identite({ etat: 'indeterminee', id_parcelle: null, motif: 'positions_contradictoires' }, 'etablie'))
		).toBeNull();
		expect(phraseIdentiteIndeterminee(identite({ etat: 'indeterminee', id_parcelle: null, motif: 'motif_futur' }))).toBeNull();
	});
});

describe('constat D-110 — criterion without column name (row 8)', () => {
	const CONSTAT = {
		criteres: ['maison', 'annexes_exclues_usage_inconnu_compte'],
		sources: [SOURCE_DPE, { cle: 'batiments', nom: 'BD TOPO — Bâtiments', producteur: 'IGN', url: 'https://geoservices.ign.fr/bdtopo' }]
	};

	it('« annexes déclarées exclues, bâtiment d’usage inconnu compté »; BD TOPO stays in « Sources » only', () => {
		expect(criteresConstat(CONSTAT)).toEqual([
			{ texte: 'maison', provisoire: false },
			{ texte: "annexes déclarées exclues, bâtiment d'usage inconnu compté", provisoire: false }
		]);
		const volet = voletConstat(CONSTAT);
		expect(volet.map((e) => e.terme)).toEqual(['Méthode', 'Sources', 'Limite']);
		const texte = JSON.stringify(volet.filter((e) => e.terme !== 'Sources'));
		expect(texte).not.toMatch(/BD TOPO|RNB|cad_parcelles/);
		expect(volet.find((e) => e.terme === 'Sources')).toMatchObject({ valeur: expect.stringContaining('BD TOPO') });
	});
});

describe('definition of ④ (row 68, C35): the last 12 months of the imported DVF file, period shown under the value', () => {
	it.each(['adresse.reference_commune.prix_median_maison', 'adresse.reference_commune.prix_median_appartement'])('%s', (fieldId) => {
		const texte = definitionPublique(fieldId) ?? '';
		expect(texte).toContain('sur les 12 derniers mois du fichier DVF importé, période indiquée sous la valeur');
		expect(texte).not.toContain('sur la période du fichier DVF importé');
		expect(texte).not.toContain('douze derniers mois');
	});
});

// B3 (UI-008, plan 04.5-05): the instruction before the boxes of the list, worded by the review, and
// the count of the boxes the visitor checked. New labels, to be read again as a block by Ianis (R10).
describe('consigne de comparaison des documents et compteur de sélection (UI-008)', () => {
	it('the instruction is the wording of the review and names the ceiling of the comparator', () => {
		expect(CONSIGNE_COMPARAISON_DOCUMENTS).toBe('Sélectionnez 2 à 4 documents DPE pour les comparer');
		expect(CONSIGNE_COMPARAISON_DOCUMENTS).toContain(`à ${MAX_COMPARAISON} documents`);
	});

	it('the counter agrees with its number (French: 0 and 1 singular)', () => {
		expect([0, 1, 2, 4].map(libelleCompteurSelection)).toEqual(['0 sélectionné', '1 sélectionné', '2 sélectionnés', '4 sélectionnés']);
	});
});

// --- 04.1-13 (D-127), plan 04.5-05 C2: the group of the documents attached by their exact raw ------
// address. The three texts proposed by 04.1-REPORTS-UI.md (« Plan 13 », « Textes proposés »), copied
// here word for word, are applied as written and read again as a block by Ianis (D-119 point 5, R10).
describe('groupe rapproché par l’adresse brute — textes 4.1-13 repris tels quels, volet du groupe (C2)', () => {
	const BADGE_4_1_13 = "Rattaché par l'adresse saisie";
	const VOLET_4_1_13 =
		"L'identifiant d'adresse fourni par l'ADEME pour ce document ne correspond à aucune adresse de la Base Adresse Nationale importée. " +
		'Son adresse saisie par le diagnostiqueur (numéro, suffixe, voie et commune) désigne exactement cette adresse, et une seule. ' +
		'Ce rapprochement est fait par DataCommune, sans approximation ; il ne désigne aucun logement précis.';
	const LIBELLE_4_1_13 = 'Adresse saisie, rapprochée exactement de la BAN';

	const carteBrute = (partiel: Partial<DocumentDpeCarteAdresseBrute> = {}): DocumentDpeCarteAdresseBrute => ({
		...carteDoc({ numero: '2544E9000008S' }),
		rattachement: 'adresse_brute_exacte',
		...partiel
	});
	const groupe = (partiel: Partial<GroupeAdresseBrute> = {}): GroupeAdresseBrute => ({ etat: 'value', total: 3, documents: [carteBrute(), carteBrute(), carteBrute()], ...partiel });
	const valeur = (volet: ReturnType<typeof voletDocuments>, terme: string) => {
		const trouvee = volet.find((e) => e.terme === terme);
		return trouvee && 'valeur' in trouvee ? trouvee.valeur : null;
	};

	it('the badge, the panel text and the attachment label are the texts of 4.1-13, word for word', () => {
		expect(BADGE_ADRESSE_BRUTE).toBe(BADGE_4_1_13);
		// Same words; the site typography puts a narrow no-break space before « ; » (03-17 T-1).
		expect(EXPLICATION_ADRESSE_BRUTE).toBe(VOLET_4_1_13.replace(' ;', ' ;'));
		expect(libelleRattachement('adresse_brute_exacte')).toBe(LIBELLE_4_1_13);
	});

	it('the badge of a card is the one of its served attachment; an unknown one is neutral and provisional, never the ADEME wording', () => {
		expect(badgeRattachement('adresse_brute_exacte')).toEqual({ texte: BADGE_4_1_13, provisoire: false });
		expect(badgeRattachement('cle_future')).toEqual({ texte: libelleBadge('cle_future'), provisoire: true });
		expect(badgeRattachement(null).texte).not.toBe(libelleRattachement('adresse_saisie_diagnostiqueur'));
	});

	it('the panel of the group: definition of the contract, object, attachment, the badge explained, source and last import', () => {
		const volet = voletAdresseBrute(documents({ adresseBrute: groupe() }));
		expect(volet.map((e) => e.terme)).toEqual(['En clair', 'Définition', 'Objet', 'Rattachement', BADGE_4_1_13, 'Source', 'Dernière importation réussie']);
		expect(valeur(volet, 'Définition')).toBe(definitionPublique('adresse.dpe.documents_adresse_brute'));
		expect(valeur(volet, 'Rattachement')).toBe(LIBELLE_4_1_13);
		expect(valeur(volet, BADGE_4_1_13)).toBe(EXPLICATION_ADRESSE_BRUTE);
	});

	it('the badge is explained only when a card carries it (empty or not computed: no badge, no entry)', () => {
		for (const sans of [groupe({ total: 0, documents: [] }), groupe({ etat: 'not_available', total: null, documents: [] })]) {
			const volet = voletAdresseBrute(documents({ adresseBrute: sans }));
			expect(volet.map((e) => e.terme)).not.toContain(BADGE_4_1_13);
			expect(valeur(volet, 'Rattachement')).toBe(LIBELLE_4_1_13);
		}
	});

	it('the masked addresses and the badges of the cards of the group are explained in its own panel', () => {
		const volet = voletAdresseBrute(
			documents({ adresseBrute: groupe({ documents: [carteBrute({ badges: ['expire'], adresse_declaree: { etat: 'masquee', texte: null, motif: 'complement_present' } })] }) })
		);
		expect(valeur(volet, 'Expiré')).not.toBeNull();
		expect(valeur(volet, 'Adresse déclarée non affichée')).toContain('complément');
	});

	it('the panel of the ADEME list never names the group (C2: strictly unchanged)', () => {
		const sans = voletDocuments(documents({ documents: [carteDoc()] }));
		const avec = voletDocuments(documents({ documents: [carteDoc()], adresseBrute: groupe({ documents: [carteBrute({ badges: ['expire'] })] }) }));
		expect(avec).toEqual(sans);
	});

	it('head sentence of the group: its served total, apart from the count of the ADEME key; zero said; not computed never a zero', () => {
		expect(phraseGroupeAdresseBrute(groupe({ total: 1, documents: [carteBrute()] }))).toBe(
			"1 DPE rapproché de cette adresse par son adresse saisie. Il n'est pas compté parmi les DPE déclarés à cette adresse."
		);
		expect(phraseGroupeAdresseBrute(groupe({ total: 12 }))).toBe(
			'12 DPE rapprochés de cette adresse par leur adresse saisie. Ils ne sont pas comptés parmi les DPE déclarés à cette adresse.'
		);
		expect(phraseGroupeAdresseBrute(groupe({ total: 0, documents: [] }))).toBe('Aucun DPE rapproché de cette adresse par son adresse saisie.');
		const nonCalcule = phraseGroupeAdresseBrute(groupe({ etat: 'not_available', total: null, documents: [] }));
		expect(nonCalcule).toBe("Le rapprochement par l'adresse saisie n'est pas calculé pour cette importation des données DPE.");
		expect(nonCalcule).not.toMatch(/\d/);
		// A `value` without a readable total is not a zero either.
		expect(phraseGroupeAdresseBrute(groupe({ total: null }))).not.toMatch(/\d|Aucun/);
	});

	it('the limit (R6, « dix sur N »): said only when the served total exceeds the cards served', () => {
		const dix = Array.from({ length: 10 }, () => carteBrute());
		expect(limiteGroupeAdresseBrute(groupe({ total: 12, documents: dix }))).toBe('Les 10 plus récents sur 12 sont affichés.');
		expect(limiteGroupeAdresseBrute(groupe({ total: 12, documents: [carteBrute()] }))).toBe('Le plus récent sur 12 est affiché.');
		expect(limiteGroupeAdresseBrute(groupe({ total: 10, documents: dix }))).toBeNull();
		expect(limiteGroupeAdresseBrute(groupe({ total: 3 }))).toBeNull();
		expect(limiteGroupeAdresseBrute(groupe({ etat: 'not_available', total: null, documents: [] }))).toBeNull();
	});
});

describe('volet — source panel of one served value (SEM-02, UI-SPEC « Volet ⓘ — deltas »)', () => {
	const FIELD_ID = 'adresse.reference_commune.prix_median_maison';
	const valeur = (partiel: Partial<ValeurLieu> = {}): ValeurLieu => ({
		field_id: FIELD_ID,
		etat: 'value',
		valeur: 2500,
		unite: 'euro_per_m2',
		periode: null,
		motif: null,
		source: { cle: 'dvf', nom: 'Demandes de valeurs foncières (DVF)', url: 'https://www.data.gouv.fr/datasets/dvf' } as ValeurLieu['source'],
		rattachement: 'commune_reference',
		reference: null,
		...partiel
	});
	const REFERENCE: NonNullable<ValeurLieu['reference']> = {
		valeur: 2400,
		unite: 'euro_per_m2',
		territoire: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
		source: { cle: 'dvf', nom: 'DVF', url: null } as NonNullable<ValeurLieu['reference']>['source'],
		millesime: '2025',
		url: 'https://www.data.gouv.fr/datasets/dvf'
	};
	const entree = (entrees: ReturnType<typeof volet>, terme: string) => entrees.find((e) => e.terme === terme);

	it('lists « Définition », « Objet », « Rattachement », « Période », « Unité », « Source » in this order', () => {
		const entrees = volet({ fieldId: FIELD_ID, objet: 'Commune', valeur: valeur(), lignes: [], periode: '2021-2025' });

		expect(entrees.map((e) => e.terme)).toEqual(['En clair', 'Définition', 'Objet', 'Rattachement', 'Période', 'Unité', 'Source']);
		expect(entrees[1]).toEqual({ terme: 'Définition', valeur: definitionPublique(FIELD_ID)! });
		expect(entrees[2]).toEqual({ terme: 'Objet', valeur: 'Commune' });
	});

	it('reads « Rattachement » from the served key, and omits it for a key outside the closed list', () => {
		const lu = volet({ fieldId: FIELD_ID, objet: 'Commune', valeur: valeur({ rattachement: 'au_point' }), lignes: [] });
		const inconnu = volet({ fieldId: FIELD_ID, objet: 'Commune', valeur: valeur({ rattachement: 'cle_future' as ValeurLieu['rattachement'] }), lignes: [] });

		expect(entree(lu, 'Rattachement')).toEqual({ terme: 'Rattachement', valeur: "Au point de l'adresse" });
		expect(entree(inconnu, 'Rattachement')).toBeUndefined();
	});

	it('gives the served period, and none when it is not served', () => {
		const avec = volet({ fieldId: FIELD_ID, objet: 'Commune', valeur: valeur(), lignes: [], periode: '2021-2025' });
		const sans = volet({ fieldId: FIELD_ID, objet: 'Commune', valeur: valeur(), lignes: [] });

		expect(entree(avec, 'Période')).toEqual({ terme: 'Période', valeur: '2021-2025' });
		expect(entree(sans, 'Période')).toBeUndefined();
	});

	it('ends with « Référence »: its text and its https link for a served reference, nothing for a null one', () => {
		const avec = volet({ fieldId: FIELD_ID, objet: 'Commune', valeur: valeur({ reference: REFERENCE }), lignes: [] });
		const sans = volet({ fieldId: FIELD_ID, objet: 'Commune', valeur: valeur(), lignes: [] });
		const sansChiffre = volet({ fieldId: FIELD_ID, objet: 'Commune', valeur: valeur({ reference: { ...REFERENCE, valeur: null } }), lignes: [] });

		expect(avec[avec.length - 1]).toEqual({
			terme: 'Référence',
			valeur: `2${NNB}400${NB}€/m² · La Roche-sur-Yon · DVF, 2025`,
			url: 'https://www.data.gouv.fr/datasets/dvf'
		});
		expect(entree(sans, 'Référence')).toBeUndefined();
		expect(entree(sansChiffre, 'Référence')).toBeUndefined();
	});

	it('says « État et motif » for an absent line, before « Référence », and not for a displayed one', () => {
		const absence = { kind: 'absence', etat: 'not_available', motif: 'parcelle_indeterminee', libelle: null, aide: null, effectif: null } as unknown as Extract<KpiLigne['etat'], { kind: 'absence' }>;
		const ligne = (etat: KpiLigne['etat']): KpiLigne => ({ id: 'x', fieldId: FIELD_ID, libelle: 'Maisons', etat, effectif: null, fenetre: null, brut: { fieldId: FIELD_ID, valeur: 'null', etat: { kind: 'valeur' } } });
		const absente = volet({ fieldId: FIELD_ID, objet: 'Commune', valeur: valeur({ reference: REFERENCE }), lignes: [ligne(absence)] });
		const affichee = volet({ fieldId: FIELD_ID, objet: 'Commune', valeur: valeur(), lignes: [ligne({ kind: 'valeur', texte: '2 500', unite: null })] });

		expect(absente.map((e) => e.terme).slice(-2)).toEqual(['État et motif', 'Référence']);
		expect(entree(absente, 'État et motif')).toEqual({ terme: 'État et motif', absences: [{ libelle: 'Maisons', etat: absence }] });
		expect(entree(affichee, 'État et motif')).toBeUndefined();
	});
});
