// Wording of the sales sections (04-05; UI-SPEC « /adresse/[id] » point 5, « Contrat de rédaction »;
// D-114, D-115). The count sentence is built from the served count and the served criteria only;
// the criteria of the URL pre-fill the form and are checked against the closed lists, never widened;
// nothing here filters, sorts or counts sales.
import { describe, expect, it } from 'vitest';

import { getVentesAdresse, type CarteVente, type CriteresAppliques, type DocumentDpeCarte, type DocumentsDpeAdresse, type VentesAdresse, type VentesRessemblantes } from '$lib/api/adresses';
import {
	BADGE_VEFA,
	DEFAUTS,
	MOTIFS_VENTES_ADRESSE,
	ORIGINES_PREREMPLISSAGE,
	PHRASE_VOLET_VEFA,
	PHRASE_RECOUVREMENT_VENTES,
	PHRASE_ZERO_COMPARABLES,
	criteresServis,
	libelleRattachementComparables,
	lienPage,
	lireCriteres,
	motifServiVentesAdresse,
	pageComparablesValide,
	phraseEffectif,
	phraseZeroComparables,
	phraseZeroVentes,
	phraseZeroVentesAdresse,
	plageVentes,
	preremplissageComparables,
	provenanceSurface,
	recouvrementPossible,
	reservesComparables,
	voletComparables,
	voletVentesAdresse
} from './adresse-ventes';
import { qualificatifSelection } from './adresse-fiche';
import { LISTE_INCOMPLETE } from './adresse-lieu';
import { QUALIFICATIONS_ATTENDUES, provenanceAttendue } from '../../../tests/recette/provenance-attendue';
import { BAN_ID_44109, VENTES_DECLAREES_44109_AVEC_VEFA, VENTES_DECLAREES_VEFA_HORS_PAGE, VENTES_DECLAREES_VEFA_SEULES } from '../../../tests/e2e/fixtures/adresse-ventes';

const NB = ' ';

const CRITERES: CriteresAppliques = {
	type: 'maison',
	surface: 90,
	tolerance: 20,
	surface_min: 72,
	surface_max: 108,
	perimetre: 'rayon',
	rayon_m: 1000,
	periode: { annees: 5, debut: 2021, fin: 2025 }
};

function servi(partiel: Partial<VentesRessemblantes> = {}): VentesRessemblantes {
	return {
		etat: 'value',
		motif: null,
		rattachement: 'rayon_du_point',
		source: { cle: 'dvf', nom: 'Demandes de valeurs foncières (DVF)', producteur: 'DGFiP / Etalab', url: 'https://www.data.gouv.fr/x' },
		criteres_appliques: CRITERES,
		effectif: 6,
		page: 1,
		pages: 1,
		par_page: 10,
		generation: 'fedcba9876543210',
		ventes: [],
		liste_incomplete: false,
		...partiel
	};
}

describe('phraseEffectif — « {n} ventes ressemblantes · {type} · {min} à {max} m² · {rayon} · {période} »', () => {
	it('recipe task 4: the served count and the served criteria', () => {
		expect(phraseEffectif(servi())).toBe(`6${NB}ventes ressemblantes · maisons · 72 à 108${NB}m² · 1${NB}km · 2021 → 2025`);
	});

	it('one sale: « 1 vente ressemblante… »; zero keeps the criteria', () => {
		expect(phraseEffectif(servi({ effectif: 1 }))).toBe(`1${NB}vente ressemblante · maisons · 72 à 108${NB}m² · 1${NB}km · 2021 → 2025`);
		expect(phraseEffectif(servi({ effectif: 0 }))).toBe(`0${NB}vente ressemblante · maisons · 72 à 108${NB}m² · 1${NB}km · 2021 → 2025`);
	});

	it('without surface: no surface range; the whole commune: « dans la commune »; 500 m', () => {
		const sansSurface = { ...CRITERES, surface: null, surface_min: null, surface_max: null };
		expect(criteresServis({ ...sansSurface, perimetre: 'commune', rayon_m: null })).toBe('maisons · dans la commune · 2021 → 2025');
		expect(criteresServis({ ...sansSurface, type: 'appartement', rayon_m: 500 })).toBe(`appartements · 500${NB}m · 2021 → 2025`);
		// FRONT IN-04: a type the page does not know is not written as « appartements ».
		expect(criteresServis({ ...sansSurface, type: 'bureau' as never, rayon_m: 500 })).toBe(`500${NB}m · 2021 → 2025`);
	});

	it('the sentence says the served criteria, never the raw input of the URL', () => {
		// The URL asked 90 m2 ± 30 %; the API applied (and served) 72 to 108: the served bounds win.
		const lus = lireCriteres(new URLSearchParams('type=maison&surface=90&tolerance=30&rayon=1000'));
		expect(lus.tolerance).toBe('30');
		expect(phraseEffectif(servi())).toContain(`72 à 108${NB}m²`);
	});

	it('no count or no criteria served: no sentence (never a computed count)', () => {
		expect(phraseEffectif(servi({ effectif: null }))).toBeNull();
		expect(phraseEffectif(servi({ criteres_appliques: null }))).toBeNull();
	});
});

describe('lireCriteres — pre-filling the GET form, closed lists (D-114, Q6)', () => {
	it('no type in the URL: none chosen, nothing relayed, the UI-SPEC defaults pre-filled', () => {
		const lus = lireCriteres(new URLSearchParams(''));
		expect(lus).toMatchObject({ type: null, surface: '', ...DEFAUTS, reconnus: true, relayes: null });
		expect(DEFAUTS).toEqual({ tolerance: '20', rayon: '500', periode: '5' });
	});

	it('recognized criteria are relayed as read, the absent ones are not sent', () => {
		const lus = lireCriteres(new URLSearchParams('type=maison&surface=90&rayon=1000'));
		expect(lus.relayes).toEqual({ type: 'maison', surface: '90', tolerance: null, rayon: '1000', periode: null });
		// An empty surface submitted by the form is an absent surface.
		expect(lireCriteres(new URLSearchParams('type=appartement&surface=&rayon=commune')).relayes).toEqual({
			type: 'appartement',
			surface: null,
			tolerance: null,
			rayon: 'commune',
			periode: null
		});
	});

	it.each(['type=bureau', 'type=maison&rayon=5000', 'type=maison&tolerance=50', 'type=maison&periode=10', 'type=maison&surface=5', 'type=maison&surface=abc', 'type=maison&surface=1001'])(
		'%s: not recognized, never relayed nor widened',
		(search) => {
			const lus = lireCriteres(new URLSearchParams(search));
			expect(lus.reconnus).toBe(false);
			expect(lus.relayes).toBeNull();
		}
	);

	it('an unrecognized value pre-fills the default, never the raw input', () => {
		const lus = lireCriteres(new URLSearchParams('type=maison&rayon=5000&surface=abc'));
		expect([lus.rayon, lus.surface, lus.type]).toEqual(['500', '', 'maison']);
	});

	it('the page of the similar sales follows the rule of the lists', () => {
		expect(pageComparablesValide(new URLSearchParams('vr_page=2&vg=fedcba9876543210'))).toBe(true);
		expect(pageComparablesValide(new URLSearchParams('vr_page=0'))).toBe(false);
		expect(pageComparablesValide(new URLSearchParams('vr_page=2&vg=zz'))).toBe(false);
	});
});

describe('links, ranges and the panel', () => {
	it('lienPage keeps every other parameter and removes a null one', () => {
		const courants = new URLSearchParams('page=2&g=0123456789abcdef&type=maison&vr_page=3&vg=aaaaaaaaaaaaaaaa');
		expect(lienPage(courants, { vr_page: '4', vg: 'fedcba9876543210' }, '#ventes-ressemblantes')).toBe(
			'?page=2&g=0123456789abcdef&type=maison&vr_page=4&vg=fedcba9876543210#ventes-ressemblantes'
		);
		expect(lienPage(courants, { vr_page: null })).toBe('?page=2&g=0123456789abcdef&type=maison&vg=aaaaaaaaaaaaaaaa');
	});

	it('ranges of a served page: « Ventes 11 à 20 sur 23 », « Ventes ressemblantes 1 à 6 sur 6 »', () => {
		const carte = { date: '2024-05-01', nature: 'Vente', composition: { maisons: 1, appartements: 0, dependances: 0, locaux_activite: 0 }, surface_batie: 90, prix: 1, prix_m2: null, prix_affiche: true, minimisation: null, distance_m: null };
		expect(plageVentes({ page: 2, par_page: 10, ventes: Array(10).fill(carte) }, 23)).toBe('Ventes 11 à 20 sur 23');
		expect(plageVentes({ page: 1, par_page: 10, ventes: Array(6).fill(carte) }, 6, 'Ventes ressemblantes')).toBe('Ventes ressemblantes 1 à 6 sur 6');
		expect(plageVentes({ page: 1, par_page: 10, ventes: [] }, 0)).toBeNull();
	});

	it('the served zero of the parcel carries its period', () => {
		expect(phraseZeroVentes('2021-2025')).toBe('Aucune vente publiée sur la parcelle dans DVF (2021-2025).');
	});

	it('relation of a similar sale: « À moins de {r} m du point de l’adresse » or the commune', () => {
		expect(libelleRattachementComparables(servi())).toBe(`À moins de 1 000${NB}m du point de l'adresse`);
		expect(libelleRattachementComparables(servi({ rattachement: 'commune_de_l_adresse' }))).toBe("Dans la commune de l'adresse");
		const volet = voletComparables(servi());
		expect(volet.map((entree) => entree.terme)).toEqual(['En clair', 'Définition', 'Objet', 'Critères', 'Rattachement', 'Source']);
	});

	// 04-DECISIONS-TEXTES rows 32 and 36: the reasons of a withheld price, one entry of the section.
	it('withheld prices: « Vente isolée récente » (row 32) and the undetermined isolation (row 36), no band named', () => {
		const retenue = (motif: string) => ({
			date: '2025-06-01', nature: 'Vente', composition: { maisons: 1, appartements: 0, dependances: 0, locaux_activite: 0 },
			surface_batie: 90, prix: null, prix_m2: null, prix_affiche: false, minimisation: { motif }, distance_m: null
		});
		const prix = (ventes: ReturnType<typeof retenue>[]) =>
			voletComparables(servi({ ventes: ventes as VentesRessemblantes['ventes'] })).filter((entree) => entree.terme === 'Prix non affiché');
		const isolee = `Vente isolée récente${NB}: le prix n'est pas publié, pour ne pas désigner un bien et son acheteur.`;
		const indeterminee = `Prix non publié${NB}: on ne peut pas établir que la vente n'est pas isolée.`;

		expect(prix([retenue('vente_isolee_recente')])).toEqual([{ terme: 'Prix non affiché', valeur: isolee }]);
		expect(prix([retenue('isolement_indetermine')])).toEqual([{ terme: 'Prix non affiché', valeur: indeterminee }]);
		expect(prix([retenue('isolement_indetermine'), retenue('vente_isolee_recente')])).toEqual([
			{ terme: 'Prix non affiché', valeur: `${isolee} ${indeterminee}` }
		]);
		expect(JSON.stringify(prix([retenue('vente_isolee_recente')]))).not.toMatch(/tranche/);
		// A reason served later without a wording: the neutral fallback, never invented.
		expect(prix([retenue('motif_futur')])).toEqual([{ terme: 'Prix non affiché', valeur: 'Valeur non affichable' }]);
	});
});

describe('ventes ressemblantes lues en partie : un rayon qui sort des départements hébergés (C16)', () => {
	it('réserve « Liste incomplète », jamais la phrase du zéro', () => {
		const partielles = servi({ liste_incomplete: true, motif: 'territoire_hors_perimetre' });
		expect(reservesComparables(partielles)).toEqual([LISTE_INCOMPLETE]);
		expect(reservesComparables(servi())).toEqual([]);
		expect(phraseZeroComparables(servi({ effectif: 0 }))).toBe(PHRASE_ZERO_COMPARABLES);
		expect(phraseZeroComparables(servi({ effectif: 0, liste_incomplete: true }))).toBeNull();
		expect(phraseZeroComparables(servi({ effectif: 3 }))).toBeNull();
	});
});

// C37: the sales declared at the address — wording of rows 69 and 72, corrected by C44 (row 73,
// provisional): no sale attached is not a zero of the source, the imported DVF keeps no suffix.
describe('ventes déclarées à cette adresse (C37, C44)', () => {
	it('no sale attached carries its source and period, never « aucune vente déclarée »', () => {
		expect(phraseZeroVentesAdresse('2021-2025')).toBe('Aucune vente rattachée à cette adresse dans DVF (2021-2025).');
		expect(phraseZeroVentesAdresse(null)).toBe('Aucune vente rattachée à cette adresse dans DVF.');
	});

	it('the served reasons have a label and a sentence; the imported data keep no suffix (C44)', () => {
		expect(motifServiVentesAdresse('suffixe_non_distingue')).toEqual({
			libelle: 'Suffixe non distingué',
			phrase: "Les données DVF importées ne gardent pas le suffixe (bis, ter)\u202f; les ventes déclarées à ce numéro sont montrées sur l'adresse sans suffixe.",
			provisoire: false
		});
		expect(motifServiVentesAdresse('aucune_vente_rattachee')).toEqual({
			libelle: 'Aucune vente rattachée',
			phrase: "Aucune vente de DVF n'est rattachée à cette adresse\u202f; ce n'est pas la preuve qu'aucune vente n'y a eu lieu.",
			provisoire: false
		});
		expect(motifServiVentesAdresse('sans_numero')?.libelle).toBe('Numéro non comparable');
		expect(motifServiVentesAdresse('adresse_indeterminee')?.libelle).toBe('Adresse non déterminée');
		// A key of the object prototype is not a reason: the provisional label.
		expect(motifServiVentesAdresse('constructor')).toEqual({ libelle: 'Valeur non affichable', phrase: null, provisoire: true });
		expect(motifServiVentesAdresse(null)).toBeNull();
	});

	// C3 (plan 04.5-08): the reasons of the number and suffix rule, worded as the report 4.1-14 proposes
	// them (04.1-REPORTS-UI.md, « Plan 14 », copied here by hand); only the space before « ; » is the
	// narrow no-break space of the site's typography.
	const TEXTES_REPORT_4_1_14 = {
		suffixe_sans_correspondance: {
			libelle: 'Suffixe sans correspondance',
			phrase: "Le suffixe de cette adresse ne correspond pas de façon sûre à un suffixe déclaré dans DVF ; les ventes déclarées à ce numéro n'y sont pas montrées."
		},
		suffixe_non_importe: {
			libelle: 'Suffixe non disponible',
			phrase: "Les données DVF servies ont été importées sans le suffixe de l'adresse ; les ventes déclarées n'y sont pas encore distinguées par suffixe."
		}
	} as const;
	const espacesOrdinaires = (texte: string) => texte.replace(/[  ]/g, ' ');
	const declarees = (partiel: Partial<VentesAdresse> = {}): VentesAdresse => ({
		etat: 'value',
		motif: null,
		source: { cle: 'dvf', nom: 'Demandes de valeurs foncières (DVF)', producteur: 'DGFiP', url: 'https://www.data.gouv.fr' },
		periode: '2021-2025',
		total: 0,
		nombre_vefa: 0,
		page: 1,
		pages: 1,
		par_page: 10,
		generation: null,
		ventes: [],
		...partiel
	});

	it('C3 : les deux motifs du suffixe ont le libellé et la phrase du report 4.1-14, mot pour mot', () => {
		for (const [motif, attendu] of Object.entries(TEXTES_REPORT_4_1_14)) {
			const servi = motifServiVentesAdresse(motif);
			expect(servi?.provisoire, motif).toBe(false);
			expect(servi!.libelle).toBe(attendu.libelle);
			expect(espacesOrdinaires(servi!.phrase!)).toBe(attendu.phrase);
			expect(servi!.phrase).toContain(' ;');
		}
	});

	it('C3 : « suffixe_non_distingue » reste reconnu pour une réponse ancienne, un motif inconnu reste provisoire', () => {
		expect(motifServiVentesAdresse('suffixe_non_distingue')?.libelle).toBe('Suffixe non distingué');
		// Codex review M1: a served reason without wording is said as not displayable, marked
		// provisional, without a sentence: never another reason's wording nor the glossary's bare
		// « Non publiée » (its cause is not known).
		expect(motifServiVentesAdresse('suffixe_futur')).toEqual({ libelle: 'Valeur non affichable', phrase: null, provisoire: true });
		expect(motifServiVentesAdresse('suffixe_sans_correspondance')).toEqual({ ...MOTIFS_VENTES_ADRESSE.suffixe_sans_correspondance, provisoire: false });
		expect(motifServiVentesAdresse(null)).toBeNull();
	});

	it('C3 : la phrase « peut apparaître sur la page du « 2 » » n’est plus servie, ni dans un motif ni dans la définition', () => {
		const voletDefinition = voletVentesAdresse(declarees({}))
			.map((entree) => ('valeur' in entree ? String(entree.valeur) : ''))
			.join(' ');
		const textes = [...Object.values(MOTIFS_VENTES_ADRESSE).map((motif) => motif.phrase), voletDefinition].map(espacesOrdinaires);
		for (const texte of textes) {
			expect(texte).not.toContain('peut apparaître sur la page du « 2 »');
			expect(texte).not.toContain('Les données DVF importées ne gardent pas le suffixe (bis, ter) : une vente');
		}
	});

	it('the panel: definition by the declared address, not the parcel; its relation, period and source', () => {
		const volet = voletVentesAdresse({
			etat: 'value',
			motif: null,
			source: { cle: 'dvf', nom: 'Demandes de valeurs foncières (DVF)', producteur: 'DGFiP', url: 'https://www.data.gouv.fr' },
			periode: '2021-2025',
			total: 0,
			nombre_vefa: 0,
			page: 1,
			pages: 1,
			par_page: 10,
			generation: null,
			ventes: []
		});
		const entree = (terme: string) => {
			const trouvee = volet.find((e) => e.terme === terme);
			return trouvee && 'valeur' in trouvee ? trouvee.valeur : undefined;
		};
		expect(entree('Définition')).toContain("l'adresse saisie dans la déclaration");
		expect(entree('Définition')).toContain('pas par la parcelle');
		// C44: why no sale attached is not a zero; C3 (04.5-08): the sale is attached by number and suffix.
		expect(entree('Définition')).toContain('Aucune vente rattachée ne veut pas dire aucune vente');
		expect(entree('Définition')).toContain("seule l'adresse de sa première ligne est gardée");
		expect(entree('Définition')).toContain('ne retrouve aucune adresse de la Base Adresse Nationale');
		expect(entree('Définition')).toContain(
			'La vente est rattachée par le numéro et le suffixe déclarés : une vente déclarée au «\u202f2\u202f» sans suffixe ne figure que sur la page du «\u202f2\u202f», une vente déclarée au «\u202f2 B\u202f» sur celle du «\u202f2 bis\u202f». ' +
				"Quand un suffixe ne correspond à aucune adresse, ou à plusieurs, la vente n'est montrée sur aucune."
		);
		expect(entree('Définition')).not.toContain('Les données DVF importées ne gardent pas le suffixe');
		expect(entree('Définition')).not.toContain('peut apparaître sur la page du');
		expect(entree('Définition')).not.toMatch(/DVF ne distingue pas/);
		expect(entree('Rattachement')).toBe('Adresse déclarée dans la vente (DVF)');
		expect(entree('Période')).toBe('2021-2025');
		expect(entree('Source')).toBe('Demandes de valeurs foncières (DVF)');
	});
});

describe('deux rattachements des ventes : recouvrement et liste déclarée repliée (UI-005, plan 04.5-06)', () => {
	const carteVente: CarteVente = {
		date: '2025-06-09',
		nature: 'Vente',
		composition: { maisons: 0, appartements: 0, dependances: 0, locaux_activite: 1 },
		surface_batie: 47,
		prix: 141000,
		prix_m2: null,
		prix_affiche: true,
		minimisation: null,
		distance_m: null,
		hors_stats_vefa: false
	};
	const declarees = (partiel: Partial<VentesAdresse> = {}): VentesAdresse => ({
		etat: 'value',
		motif: null,
		source: { cle: 'dvf', nom: 'Demandes de valeurs foncières (DVF)', producteur: 'DGFiP', url: 'https://www.data.gouv.fr' },
		periode: '2021-2025',
		total: 3,
		nombre_vefa: 0,
		page: 1,
		pages: 1,
		par_page: 10,
		generation: 'fedcba9876543210',
		ventes: [carteVente, carteVente, carteVente],
		...partiel
	});

	it('the overlap sentence: one sale may be in both lists, each list keeps its own relation', () => {
		expect(PHRASE_RECOUVREMENT_VENTES).toBe(
			`Une même vente peut figurer dans les deux listes${NB}: l'une rattache les ventes à la parcelle du point de l'adresse, l'autre à l'adresse déclarée dans la vente.`
		);
	});

	it('the sentence only when both relations serve sales (served totals, nothing compared)', () => {
		const parcelle = { etat: 'value' as const, total: 3 };
		expect(recouvrementPossible(parcelle, declarees())).toBe(true);
		expect(recouvrementPossible({ etat: 'value', total: 0 }, declarees())).toBe(false);
		expect(recouvrementPossible({ etat: 'not_available', total: null }, declarees())).toBe(false);
		expect(recouvrementPossible(parcelle, declarees({ total: 0, ventes: [] }))).toBe(false);
		expect(recouvrementPossible(parcelle, declarees({ etat: 'not_available', motif: 'suffixe_non_distingue', total: null }))).toBe(false);
		expect(recouvrementPossible(null, declarees())).toBe(false);
		expect(recouvrementPossible(parcelle, null)).toBe(false);
	});

});

describe('ventes ressemblantes préremplies d’après les documents de l’adresse (C42)', () => {
	const carte = (numero: string, partiel: Partial<DocumentDpeCarte> = {}): DocumentDpeCarte => ({
		numero,
		etiquette_dpe: 'D',
		etiquette_ges: 'C',
		type_batiment: 'maison',
		surface_habitable: 92.4,
		date_etablissement: '2022-06-01',
		date_fin_validite: '2032-05-31',
		badges: [],
		adresse_declaree: null,
		...partiel
	});
	const unique = (numero: string) => ({
		etat: 'unique' as const,
		motif: 'document_valide',
		date: '2024-03-12',
		document: { numero, etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
		documents_ex_aequo: []
	});
	const liste = (documents: DocumentDpeCarte[], partiel: Partial<DocumentsDpeAdresse> = {}): DocumentsDpeAdresse => ({
		etat: 'value',
		motif: null,
		source: null,
		total: documents.length,
		page: 1,
		pages: 1,
		par_page: 10,
		generation: null,
		distribution: null,
		date_import: null,
		documents,
		selection: unique(documents[0]?.numero ?? 'X'),
		signal_plusieurs_logements: null,
		constat_d110: null,
		...partiel
	});

	it('every document a house, a single most recent: type and surface, said where they come from', () => {
		const prerempli = preremplissageComparables(
			liste([carte('2485E0000003C', { surface_habitable: 92.4 }), carte('2285E0000002D', { surface_habitable: 70 })])
		);
		expect(prerempli).toEqual({
			type: 'maison',
			surface: '92',
			phrase: null,
			numero: '2485E0000003C',
			qualification: null
		});
	});

	it('the surface is the one of the most recent document, wherever it stands in the served order', () => {
		const documents = [carte('2285E0000002D', { surface_habitable: 70 }), carte('2485E0000003C', { surface_habitable: 55.5 })];
		expect(preremplissageComparables(liste(documents, { selection: unique('2485E0000003C') }))?.surface).toBe('56');
	});

	it('types that differ or are not declared: no type; the surface of the most recent alone', () => {
		const melanges = liste([carte('2485E0000003C'), carte('2285E0000002D', { type_batiment: 'appartement' })]);
		expect(preremplissageComparables(melanges)).toEqual({
			type: null,
			surface: '92',
			phrase: null,
			numero: '2485E0000003C',
			qualification: null
		});
		const immeuble = liste([carte('2485E0000003C', { type_batiment: 'immeuble' })]);
		expect(preremplissageComparables(immeuble)?.type).toBeNull();
		const inconnu = liste([carte('2485E0000003C'), carte('2285E0000002D', { type_batiment: null })]);
		expect(preremplissageComparables(inconnu)?.type).toBeNull();
	});

	it('a tie of the most recent date (C. ①) or an undetermined selection: no surface, the type alone', () => {
		const egalite = liste([carte('2485E0000003C'), carte('2285E0000002D')], {
			selection: { etat: 'egalite', motif: 'document_valide', date: '2024-03-12', document: null, documents_ex_aequo: [] }
		});
		expect(preremplissageComparables(egalite)).toEqual({ type: 'maison', surface: null, phrase: "Type d'après les DPE de cette adresse.", numero: null, qualification: null });
		const indeterminee = liste([carte('2485E0000003C')], {
			selection: { etat: 'indeterminee', motif: 'aucun_document_date', date: null, document: null, documents_ex_aequo: [] }
		});
		expect(preremplissageComparables(indeterminee)?.surface).toBeNull();
	});

	it('a list not wholly on this page: no type (all documents unknown); the most recent off this page: no surface', () => {
		const partielle = liste([carte('2485E0000003C')], { total: 14, pages: 2 });
		expect(preremplissageComparables(partielle)).toEqual({
			type: null,
			surface: '92',
			phrase: null,
			numero: '2485E0000003C',
			qualification: null
		});
		const ailleurs = liste([carte('2285E0000002D', { type_batiment: 'appartement' })], { total: 14, pages: 2, page: 2, selection: unique('2485E0000003C') });
		expect(preremplissageComparables(ailleurs)).toBeNull();
	});

	it('nothing to prefill: no document, a surface outside the closed bounds of the form, out of coverage', () => {
		expect(preremplissageComparables(liste([], { selection: null }))).toBeNull();
		expect(preremplissageComparables(liste([carte('2485E0000003C', { type_batiment: 'appartement', surface_habitable: 4 })]))).toEqual({
			type: 'appartement',
			surface: null,
			phrase: "Type d'après les DPE de cette adresse.",
			numero: null,
			qualification: null
		});
		expect(preremplissageComparables(liste([carte('2485E0000003C')], { etat: 'out_of_coverage', total: null }))).toBeNull();
		expect(preremplissageComparables(null)).toBeNull();
	});

	// UI-007 (plan 04.5-06): the prefilled surface names the served document it comes from. The number
	// is `selection.document.numero` as served, and only when a surface is actually prefilled.
	it('UI-007: the number of the served selection and its qualification, when its surface is prefilled', () => {
		const documents = [carte('2285E0000002D', { surface_habitable: 70 }), carte('2485E0000003C', { surface_habitable: 55.5 })];
		const servie = liste(documents, { selection: unique('2485E0000003C') });
		const prerempli = preremplissageComparables(servie);
		expect(prerempli?.surface).toBe('56');
		expect(prerempli?.numero).toBe(servie.selection?.document?.numero);
		expect(prerempli?.qualification).toBeNull();
		const expire = liste(documents, { selection: { ...unique('2485E0000003C'), motif: 'expire' } });
		expect(preremplissageComparables(expire)).toMatchObject({ surface: '56', numero: '2485E0000003C', qualification: 'expiré' });
		const ancienne = liste(documents, { selection: { ...unique('2485E0000003C'), motif: 'ancienne_methode' } });
		expect(preremplissageComparables(ancienne)?.qualification).toBe('ancienne méthode');
	});

	it('UI-007: no number when no surface is prefilled (absent, out of bounds, off this page) or the selection is not unique', () => {
		const sansSurface = liste([carte('2485E0000003C', { surface_habitable: null })]);
		expect(preremplissageComparables(sansSurface)).toEqual({ type: 'maison', surface: null, phrase: "Type d'après les DPE de cette adresse.", numero: null, qualification: null });
		const petite = liste([carte('2485E0000003C', { surface_habitable: 4 })]);
		expect(preremplissageComparables(petite)).toMatchObject({ type: 'maison', surface: null, numero: null, qualification: null });
		const grande = liste([carte('2485E0000003C', { surface_habitable: 1500 })]);
		expect(preremplissageComparables(grande)).toMatchObject({ type: 'maison', surface: null, numero: null, qualification: null });
		// The selected document is on another page: its number is served, its surface is not read.
		const ailleurs = liste([carte('2285E0000002D')], { selection: unique('2485E0000003C') });
		expect(preremplissageComparables(ailleurs)).toMatchObject({ surface: null, numero: null, qualification: null });
		const egalite = liste([carte('2485E0000003C')], {
			selection: { etat: 'egalite', motif: 'document_valide', date: '2024-03-12', document: { numero: '2485E0000003C', etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' }, documents_ex_aequo: [] }
		});
		expect(preremplissageComparables(egalite)).toMatchObject({ surface: null, numero: null });
	});

	it('UI-007: the wording of the provenance, its link to the document page, its qualification', () => {
		expect(provenanceSurface('2485E0000003C', null)).toEqual({
			lien: '/dpe/2485E0000003C',
			texte: `Surface issue du DPE n°${NB}2485E0000003C`,
			suite: ', le plus récent de cette adresse.'
		});
		expect(provenanceSurface('2485E0000003C', 'expiré').suite).toBe(', le plus récent de cette adresse (expiré).');
		expect(provenanceSurface('A/B C', null).lien).toBe('/dpe/A%2FB%20C');
	});
});

// C4 (plan 04.5-07, report 4.1-15): the mark of a VEFA card, word for word the report (read in a block
// by Ianis, R10), on the template of « Prix non affiché ».
describe('VEFA : la marque de la carte (04.5-07, C4)', () => {
	it('« VEFA, hors statistiques », le texte du report 4.1-15', () => {
		expect(BADGE_VEFA).toBe('VEFA, hors statistiques');
	});
});

// C4 (plan 04.5-07, report 4.1-15; D45-05): under `vefa=incluses` the served total counts the listed
// mutations and `nombre_vefa` the VEFA among them. The line of the page says both, as served; nothing
// is subtracted, nothing is counted from the cards. Every expected text
// is written here by hand (texts of the report, read in a block by Ianis, R10).
describe('VEFA listées, jamais comptées : ligne de page, essentiel, volet (04.5-07, C4)', () => {
	const lire = async (corps: unknown): Promise<VentesAdresse> => {
		const fetchImpl = (async () => new Response(JSON.stringify(corps), { status: 200, headers: { 'Content-Type': 'application/json' } })) as typeof fetch;
		const issue = await getVentesAdresse(fetchImpl, BAN_ID_44109, '1', null);
		if (issue.kind !== 'ok') throw new Error('served list expected');
		return issue.ventes;
	};
	const corps = (reponse: { body: unknown }, partiel: Record<string, unknown> = {}) => {
		const copie = structuredClone(reponse.body) as { donnees: Record<string, unknown> };
		Object.assign(copie.donnees, partiel);
		return copie;
	};

	it('la ligne de page : « Ventes {a} à {b} sur {total}, dont {nombre_vefa} VEFA hors statistiques »', async () => {
		const liste = await lire(VENTES_DECLAREES_44109_AVEC_VEFA.body);
		expect(plageVentes(liste, liste.total, 'Ventes', liste.nombre_vefa)).toBe('Ventes 1 à 5 sur 5, dont 2 VEFA hors statistiques');
		// No VEFA served, or the count not served: the range of plan 06.
		expect(plageVentes(liste, liste.total, 'Ventes', 0)).toBe('Ventes 1 à 5 sur 5');
		expect(plageVentes(liste, liste.total, 'Ventes', null)).toBe('Ventes 1 à 5 sur 5');
		expect(plageVentes(liste, liste.total)).toBe('Ventes 1 à 5 sur 5');
	});

	it('le zéro de page seulement pour un total servi à 0 ; une liste aux seules VEFA n’est jamais « Aucune vente »', async () => {
		const seules = await lire(VENTES_DECLAREES_VEFA_SEULES.body);
		expect(plageVentes(seules, seules.total, 'Ventes', seules.nombre_vefa)).toBe('Ventes 1 à 2 sur 2, dont 2 VEFA hors statistiques');
		const zero = await lire(corps(VENTES_DECLAREES_VEFA_SEULES, { total: 0, nombre_vefa: 0, ventes: [] }));
		expect(zero.total).toBe(0);
	});

	// Counter-review (MINOR): a page whose served VEFA count (4) differs from its VEFA cards (1). Only
	// the served counts give the expected texts: a count of the cards (« dont 1 VEFA ») or a total minus
	// the VEFA (« 19 ventes ») fails these equalities.
	it('une page dont le nombre_vefa servi diffère de ses cartes VEFA : le nombre servi, jamais un compte des cartes ni une différence', async () => {
		const liste = await lire(VENTES_DECLAREES_VEFA_HORS_PAGE.body);
		expect(liste.ventes.filter((carte) => carte.hors_stats_vefa)).toHaveLength(1);
		expect(plageVentes(liste, liste.total, 'Ventes', liste.nombre_vefa)).toBe('Ventes 1 à 10 sur 23, dont 4 VEFA hors statistiques');
		expect(plageVentes(liste, liste.total, 'Ventes', liste.nombre_vefa)).not.toMatch(/dont 1 VEFA|(^|[^0-9])(19|22)([^0-9]|$)/);
	});

	it('le volet de la liste : l’entrée « VEFA » du report quand des VEFA sont servies, rien sinon', async () => {
		expect(PHRASE_VOLET_VEFA).toBe(
			`Vente en l'état futur d'achèvement${NB}: un logement vendu avant d'être construit. Elle est montrée dans la liste mais n'entre dans aucune statistique, ni dans le nombre de ventes de cette adresse.`
		);
		const avec = voletVentesAdresse(await lire(VENTES_DECLAREES_44109_AVEC_VEFA.body));
		expect(avec.filter((entree) => entree.terme === 'VEFA')).toEqual([{ terme: 'VEFA', valeur: PHRASE_VOLET_VEFA }]);
		for (const nombreVefa of [0, null]) {
			const sans = voletVentesAdresse(await lire(corps(VENTES_DECLAREES_44109_AVEC_VEFA, { nombre_vefa: nombreVefa })));
			expect(sans.map((entree) => entree.terme), String(nombreVefa)).not.toContain('VEFA');
		}
	});

	it('la définition du nombre de ventes de l’adresse dit que les VEFA sont listées et marquées, pas comptées', async () => {
		const definition = voletVentesAdresse(await lire(VENTES_DECLAREES_44109_AVEC_VEFA.body)).find((entree) => entree.terme === 'Définition');
		expect(definition && 'valeur' in definition ? definition.valeur : '').toContain(
			"Les ventes en l'état futur d'achèvement (VEFA) déclarées à l'adresse sont listées et marquées, mais ne sont pas comptées."
		);
	});
});

// Review of the corrections of 04.5-06 (MINOR, carried to 04.5-07): the real recipe compares the whole
// provenance line to a text written apart from the application, one per served reason
// (tests/recette/provenance-attendue.ts). The real recipe is out of the gate: this proves, on the unit
// level, that the application writes exactly those texts and that a replaced qualification fails.
describe('recette réelle — texte attendu de la provenance, par motif servi (revue 04.5-06, MINOR)', () => {
	const NUMERO = '2644E8000029X';
	const rendu = (motif: string, typePrerempli: boolean) => {
		const ligne = provenanceSurface(NUMERO, qualificatifSelection(motif));
		return `${typePrerempli ? `${ORIGINES_PREREMPLISSAGE.type} ` : ''}${ligne.texte}${ligne.suite}`.replace(/[  ]/g, ' ');
	};

	it.each(Object.keys(QUALIFICATIONS_ATTENDUES))('%s: the application writes exactly the expected text', (motif) => {
		expect(rendu(motif, false)).toBe(provenanceAttendue(NUMERO, motif, false));
		expect(rendu(motif, true)).toBe(provenanceAttendue(NUMERO, motif, true));
	});

	it('a replaced qualification fails the comparison, which the former predicate of the recipe accepted', () => {
		const debut = `Surface issue du DPE n° ${NUMERO}, le plus récent de cette adresse`;
		const attendu = provenanceAttendue(NUMERO, 'expire', false);
		expect(attendu).toBe(`${debut} (expiré).`);
		// The predicate of the recipe at aa5b1f9e (adresses-reel.recette.ts, l. 401).
		const ancienPredicat = (lu: string) => lu.startsWith(`${debut} (`) && /\([^()]+\)\.$/.test(lu);
		for (const remplacee of ['valide', 'ancienne méthode', "remplacé selon l'ADEME"]) {
			const lu = `${debut} (${remplacee}).`;
			expect(ancienPredicat(lu), remplacee).toBe(true);
			expect(() => expect(lu).toBe(attendu)).toThrow();
		}
	});

	it('a served reason without an expected text fails, named', () => {
		expect(() => provenanceAttendue(NUMERO, 'motif_futur', false)).toThrow('motif_futur');
	});
});
