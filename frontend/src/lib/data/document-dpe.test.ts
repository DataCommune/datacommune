import { describe, it, expect } from 'vitest';
import type { DocumentDpePublic } from '$lib/api/documents-dpe';
import type { ValeurLieu } from '$lib/api/adresses';
import {
	LIGNE_COURTE_COUT,
	LIGNE_COURTE_M2,
	NATURE_RAPPROCHEMENT,
	PHRASE_DIVERGENCE,
	PHRASE_ENERGIE_PRIMAIRE,
	badgesIdentite,
	caracteristiquesDocument,
	filAriane,
	jeuxSources,
	lienAdresse,
	ligneIdentite,
	ligneStatut,
	methodeProvenance,
	modeleAdresse,
	phraseInconnu,
	phraseMalForme,
	reperesDocument,
	titreDocument
} from './document-dpe';
import { DEPARTEMENTS } from './departements';
import { LIBELLE_NON_AFFICHABLE } from './etats-libelles';
import { definitionPublique } from './explications';
import { enClairChamp } from './explications-simples';

// The document page projects the served axes as served (D-111, R02-05/6): no date is computed,
// no class or range is invented, a field a document leaves empty is « Non renseigné » about the
// document (SEM-01, SEM-02).

const NB = ' ';
const NNB = ' ';
const NUMERO = '2385E0123456A';
const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };

function valeur(partiel: Partial<ValeurLieu>): ValeurLieu {
	return { field_id: 'x', etat: 'value', valeur: null, unite: null, periode: null, motif: null, source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur', reference: null, ...partiel };
}

function document(partiel: Partial<DocumentDpePublic> = {}): DocumentDpePublic {
	return {
		numero: NUMERO,
		source_identity: { reference: NUMERO, sources: [{ dataset: 'dpe03existant', url: 'https://data.ademe.fr/datasets/dpe03existant' }] },
		disponibilite: 'disponible',
		methode: 'actuelle',
		dates: { etablissement: '2024-03-01', fin_validite: '2034-02-28' },
		reperes: {
			etiquette_energie: {
				classe: valeur({ field_id: 'dpe.document.etiquette_dpe', valeur: 'D', unite: 'none' }),
				mesure: valeur({ field_id: 'dpe.document.conso_ep', valeur: 17830.4, unite: 'kwh_ep_per_year' }),
				// Published by the ADEME, never 17 830,4 / 79 (about 226): plan 04.5-12, D45-05.
				mesure_m2: valeur({ field_id: 'dpe.document.conso_ep_m2', valeur: 231, unite: 'kwh_ep_per_m2_per_year' })
			},
			etiquette_climat: {
				classe: valeur({ field_id: 'dpe.document.etiquette_ges', valeur: 'C', unite: 'none' }),
				mesure: valeur({ field_id: 'dpe.document.emissions_ges', valeur: 3433.1, unite: 'kg_co2_eq_per_year' }),
				mesure_m2: valeur({ field_id: 'dpe.document.emissions_ges_m2', valeur: 41, unite: 'kg_co2_eq_per_m2_per_year' })
			},
			logement_decrit: {
				type_batiment: valeur({ field_id: 'dpe.document.type_batiment', valeur: 'maison', unite: 'none' }),
				surface: valeur({ field_id: 'dpe.document.surface_habitable', valeur: 79, unite: 'm2' })
			},
			cout_annuel: valeur({ field_id: 'dpe.document.cout_annuel', valeur: 1834.2, unite: 'euro_per_year' })
		},
		statut: { code: 'en_vigueur', validite: 'not_expired_by_date', date: null, remplace_par: null, remplace: null },
		caracteristiques: [
			valeur({ field_id: 'dpe.document.type_batiment', valeur: 'maison' }),
			valeur({ field_id: 'dpe.document.surface_habitable', valeur: 79 }),
			valeur({ field_id: 'dpe.document.periode_construction', valeur: '1948-1974' }),
			valeur({ field_id: 'dpe.document.isolation', etat: 'not_reported', valeur: null })
		],
		adresse_declaree: { etat: 'publiee', texte: '14 rue des Lilas', motif: null },
		adresse_ban: {
			etat: 'retenue',
			ban_id: '85191_0420_00012',
			libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
			numero: '12',
			rep: null,
			nom_voie: 'Rue des Lilas',
			commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
			motif: null
		},
		adresse_divergente: true,
		provenance: { source: 'ADEME', date_import: '2026-09-30' },
		...partiel
	};
}

describe('titre et fil d’Ariane (UI-SPEC point 1)', () => {
	it('« DPE n° {numero} » : le numéro source tel que servi', () => {
		expect(titreDocument(NUMERO)).toBe(`DPE n°${NB}${NUMERO}`);
	});

	// T6, decided by Ianis on 2026-11-10: « Accueil » is the root of every breadcrumb (it said « Recherche »).
	it('adresse BAN retenue : « Accueil / {Commune} / {numéro} {voie} / DPE n° {numero} »', () => {
		expect(filAriane(document())).toEqual([
			{ libelle: 'Accueil', href: '/' },
			{ libelle: 'La Roche-sur-Yon', href: '/commune/85191' },
			{ libelle: '12 Rue des Lilas', href: '/adresse/85191_0420_00012' },
			{ libelle: `DPE n°${NB}${NUMERO}`, href: null }
		]);
		expect(lienAdresse(document())).toBe('/adresse/85191_0420_00012');
	});

	it('sans adresse BAN servie : fil court « Accueil / DPE n° {numero} », pas de « Voir l’adresse »', () => {
		const sansCle = document({
			adresse_ban: { etat: 'sans_cle', ban_id: null, libelle: null, numero: null, rep: null, nom_voie: null, commune: null, motif: null }
		});
		expect(lienAdresse(sansCle)).toBeNull();
		expect(filAriane(sansCle)).toEqual([
			{ libelle: 'Accueil', href: '/' },
			{ libelle: `DPE n°${NB}${NUMERO}`, href: null }
		]);
	});
});

describe('ligne d’identité : les dates servies, rien de calculé', () => {
	// P4 (inventory of 2026-11-09): the method was said three times (this line, the badge, « Méthode » of
	// « Provenance et validité »). It leaves this line; the badge and the provenance row keep it.
	it('« Établi le {date} · valable jusqu’au {date} », sans la méthode', () => {
		for (const methode of ['ancienne', 'actuelle', 'inconnue'] as const) {
			expect(ligneIdentite(document({ methode }))).toEqual([
				{ texte: 'Établi le 1 mars 2024' },
				{ texte: "valable jusqu'au 28 février 2034" }
			]);
		}
	});

	it('« Méthode » de « Provenance et validité » : les mêmes textes, initiale en capitale', () => {
		expect(methodeProvenance('ancienne')).toBe('Ancienne méthode');
		expect(methodeProvenance('actuelle')).toBe('Méthode actuelle (depuis juillet 2021)');
		expect(methodeProvenance('inconnue')).toBe('Méthode non déterminée');
	});

	it('une date non servie n’est pas écrite, et la fin de validité n’est jamais déduite', () => {
		const segments = ligneIdentite(document({ dates: { etablissement: '2024-03-01', fin_validite: null } }));

		expect(segments.map((s) => s.texte)).toEqual(['Établi le 1 mars 2024']);
	});

	it('badge « Ancienne méthode » seulement pour la méthode servie ancienne', () => {
		expect(badgesIdentite(document({ methode: 'ancienne' }))).toEqual(['Ancienne méthode']);
		expect(badgesIdentite(document())).toEqual([]);
	});

	it('badge « Expiré » lu sur l’axe calendaire servi, même pour un document remplacé', () => {
		const statut = { code: 'remplace', validite: 'expired', date: null, remplace_par: '2485E0000009R', remplace: null };
		expect(badgesIdentite(document({ methode: 'ancienne', statut }))).toEqual(['Ancienne méthode', 'Expiré']);
	});
});

describe('repère ① « Étiquette énergie » (UI-SPEC point 3)', () => {
	it('la classe servie en étiquette, la consommation par m² servie avec son unité (C1, plan 04.5-12)', () => {
		const [un] = reperesDocument(document());

		expect(un.id).toBe('energie');
		expect(un.rang).toBe(1);
		expect(un.libelle).toBe('Étiquette énergie');
		expect(un.lignes[0].etiquette).toBe('D');
		expect(un.lignes[0].fieldId).toBe('dpe.document.conso_ep_m2');
		expect(un.lignes[0].etat).toEqual({ kind: 'valeur', texte: '231', unite: 'kWhEP/m²/an' });
		expect(un.lignes[0].fenetre).toBe('par m² · 5 usages');
		expect(un.lignes[0].brut).toEqual({ fieldId: 'dpe.document.conso_ep_m2', valeur: '231', etat: { kind: 'valeur' } });
		expect(un.volet.find((e) => e.terme === 'Unité')).toEqual({ terme: 'Unité', valeur: 'kWhEP/m²/an' });
		expect(un.volet.find((e) => e.terme === 'Définition')).toEqual({
			terme: 'Définition',
			valeur: `${definitionPublique('dpe.document.etiquette_dpe')} ${definitionPublique('dpe.document.conso_ep_m2')}`
		});
		// The yearly total is no longer a figure of the landmark: it is in the detail.
		expect(JSON.stringify(un)).not.toContain('"dpe.document.conso_ep"');
		expect(un.volet.find((e) => e.terme === 'Objet')).toEqual({ terme: 'Objet', valeur: 'Document DPE' });
		expect(un.volet.find((e) => e.terme === 'Rattachement')).toEqual({
			terme: 'Rattachement',
			valeur: 'Adresse saisie par le diagnostiqueur (non vérifiée)'
		});
		// One entry per term: the panel keys its entries by term.
		const termes = un.volet.map((e) => e.terme);
		expect(new Set(termes).size).toBe(termes.length);
	});

	// P3 (inventory of 2026-11-09): « En clair » heads the panel, before the definition, as on the address fiche.
	it('« En clair » en tête du volet : les phrases simples de la classe et de la valeur par m²', () => {
		const [un, deux, trois, quatre] = reperesDocument(document());
		expect(un.volet[0]).toEqual({
			terme: 'En clair',
			valeur: `${enClairChamp('dpe.document.etiquette_dpe')} ${enClairChamp('dpe.document.conso_ep_m2')}`
		});
		expect(deux.volet[0]).toEqual({
			terme: 'En clair',
			valeur: `${enClairChamp('dpe.document.etiquette_ges')} ${enClairChamp('dpe.document.emissions_ges_m2')}`
		});
		expect(trois.volet[0].terme).toBe('En clair');
		expect(quatre.volet[0]).toEqual({ terme: 'En clair', valeur: enClairChamp('dpe.document.cout_annuel') });
		for (const repere of [un, deux, trois, quatre]) expect(repere.volet[1].terme).toBe('Définition');
	});

	// P5: « EP » of « kWhEP » is explained once, right after the unit, wherever the unit says it.
	it('« EP » dit en clair après l’unité kWhEP, et nulle part ailleurs', () => {
		const [un, deux] = reperesDocument(document());
		const termes = un.volet.map((e) => e.terme);
		expect(termes.indexOf('EP')).toBe(termes.indexOf('Unité') + 1);
		expect(un.volet.find((e) => e.terme === 'EP')).toEqual({ terme: 'EP', valeur: PHRASE_ENERGIE_PRIMAIRE });
		expect(deux.volet.find((e) => e.terme === 'EP')).toBeUndefined();
		const total = caracteristiquesDocument(document()).find((l) => l.fieldId === 'dpe.document.conso_ep')!;
		expect(total.volet.find((e) => e.terme === 'EP')).toEqual({ terme: 'EP', valeur: PHRASE_ENERGIE_PRIMAIRE });
	});

	it('champ vide d’un document existant : « Non renseigné », phrase sur le document, aucune classe inventée', () => {
		const doc = document({
			reperes: {
				...document().reperes!,
				etiquette_energie: {
					classe: valeur({ field_id: 'dpe.document.etiquette_dpe', etat: 'not_reported', valeur: null }),
					mesure: valeur({ field_id: 'dpe.document.conso_ep', etat: 'not_reported', valeur: null }),
					mesure_m2: valeur({ field_id: 'dpe.document.conso_ep_m2', etat: 'not_reported', valeur: null })
				}
			}
		});

		const [un] = reperesDocument(doc);

		expect(un.lignes[0].etiquette).toBeNull();
		expect(un.lignes[0].etat).toMatchObject({ kind: 'absence', etat: 'not_reported', aide: 'La source ne fournit pas cette valeur dans ce document.' });
	});

	it('classe non servie, mesure servie : la classe est dite « Non renseigné », jamais omise (FRONT WR-01)', () => {
		const doc = document({
			reperes: {
				...document().reperes!,
				etiquette_climat: {
					classe: valeur({ field_id: 'dpe.document.etiquette_ges', etat: 'not_reported', valeur: null }),
					mesure: valeur({ field_id: 'dpe.document.emissions_ges', valeur: 412.5, unite: 'kgCO2e_per_year' }),
					mesure_m2: valeur({ field_id: 'dpe.document.emissions_ges_m2', valeur: 9, unite: 'kg_co2_eq_per_m2_per_year' })
				}
			}
		});

		const [, deux] = reperesDocument(doc);

		expect(deux.lignes[0].etiquette).toBeNull();
		expect(deux.lignes[0]).toMatchObject({ fieldId: 'dpe.document.emissions_ges_m2', etat: { kind: 'valeur', texte: '9' } });
		expect(deux.lignes[1]).toMatchObject({ libelle: 'Classe', etat: { kind: 'absence', etat: 'not_reported' } });
		const absences = deux.volet.find((e) => e.terme === 'État et motif');
		expect(absences && 'absences' in absences ? absences.absences.map((a) => a.libelle) : null).toEqual(['Classe']);
		// A served class keeps one line, its tag next to the figure.
		expect(reperesDocument(document())[1].lignes).toHaveLength(1);
	});

	it('document retiré : aucun repère', () => {
		expect(reperesDocument(document({ disponibilite: 'retire', dates: null, reperes: null }))).toEqual([]);
	});
});

// D45-05, counter-review I3 of plan 04.5-12: the landmark shows the published per-m² value, never a
// quotient of the total by the surface. The served total 10 258 and surface 100 would give « 103 »
// (« 102 » truncated); the published value is a different number on purpose.
// Both labels (plan 04.5-12, task 4): energy and climate, each with its own per-m² field and unit.
const ETIQUETTES_M2 = [
	{ rang: 0, cle: 'etiquette_energie', total: 'dpe.document.conso_ep', champ: 'dpe.document.conso_ep_m2', unite: 'kWhEP/m²/an', uniteServie: 'kwh_ep_per_m2_per_year' },
	{ rang: 1, cle: 'etiquette_climat', total: 'dpe.document.emissions_ges', champ: 'dpe.document.emissions_ges_m2', unite: 'kg CO₂ éq/m²/an', uniteServie: 'kg_co2_eq_per_m2_per_year' }
] as const;

describe.each(ETIQUETTES_M2)('repère $champ : la valeur par m² publiée, jamais total ÷ surface (D45-05, I3)', ({ rang, cle, total, champ, unite, uniteServie }) => {
	function avec(mesure_m2: ValeurLieu) {
		const base = document();
		const surface = valeur({ field_id: 'dpe.document.surface_habitable', valeur: 100, unite: 'm2' });
		return document({
			reperes: {
				...base.reperes!,
				[cle]: { ...base.reperes![cle], mesure: valeur({ field_id: total, valeur: 10258 }), mesure_m2 },
				logement_decrit: { ...base.reperes!.logement_decrit, surface }
			},
			caracteristiques: base.caracteristiques.map((c) => (c.field_id === 'dpe.document.surface_habitable' ? surface : c))
		});
	}
	const sansQuotient = (repere: unknown) => expect(JSON.stringify(repere)).not.toMatch(/\b10[23]\b/);

	it('total 10 258, surface 100, valeur publiée 63 : le repère affiche 63', () => {
		const repere = reperesDocument(avec(valeur({ field_id: champ, valeur: 63, unite: uniteServie })))[rang];

		expect(repere.lignes[0].etat).toEqual({ kind: 'valeur', texte: '63', unite });
		sansQuotient(repere);
	});

	it('zéro publié avec un total non nul : « 0 », zéro mesuré', () => {
		const repere = reperesDocument(avec(valeur({ field_id: champ, valeur: 0, unite: uniteServie })))[rang];

		expect(repere.lignes[0].etat).toEqual({ kind: 'valeur', texte: '0', unite });
		expect(repere.lignes[0].fenetre).toBe('par m² · 5 usages');
		sansQuotient(repere);
	});

	it('valeur non publiée (`not_reported`), total et surface servis : « Non renseigné », jamais un quotient', () => {
		const repere = reperesDocument(avec(valeur({ field_id: champ, etat: 'not_reported', valeur: null })))[rang];

		expect(repere.lignes[0].etat).toMatchObject({ kind: 'absence', etat: 'not_reported', aide: 'La source ne fournit pas cette valeur dans ce document.' });
		expect(repere.lignes[0].fenetre).toBeNull();
		expect(repere.lignes[0].brut).toEqual({ fieldId: champ, valeur: 'null', etat: { kind: 'absence', etat: 'not_reported', motif: '' } });
		sansQuotient(repere);
	});

	it('le total reste dans le détail, servi tel quel (10 258)', () => {
		const ligne = caracteristiquesDocument(avec(valeur({ field_id: champ, valeur: 63, unite: uniteServie }))).find((c) => c.fieldId === total);

		expect(ligne?.etat).toMatchObject({ kind: 'valeur', texte: new Intl.NumberFormat('fr-FR').format(10258) });
	});
});

describe('lignes courtes sous les repères (2026-10-05, ligne 12)', () => {
	it('① et ② « par m² · 5 usages » (C1, plan 04.5-12), ③ rien, ④ « estimation à occupation standard »', () => {
		const reperes = reperesDocument(document());

		expect(reperes.map((repere) => repere.lignes[0].fenetre)).toEqual([
			'par m² · 5 usages',
			'par m² · 5 usages',
			null,
			'estimation à occupation standard'
		]);
		expect([LIGNE_COURTE_M2, LIGNE_COURTE_COUT]).toEqual(['par m² · 5 usages', 'estimation à occupation standard']);
		expect(JSON.stringify(reperes)).not.toContain('total annuel');
	});

	it('une valeur absente porte son badge, sans ligne courte', () => {
		const doc = document({
			reperes: { ...document().reperes!, cout_annuel: valeur({ field_id: 'dpe.document.cout_annuel', etat: 'not_reported', valeur: null }) }
		});

		expect(reperesDocument(doc)[3].lignes[0].fenetre).toBeNull();
	});
});

describe('repères ② ③ ④ : servis, rien d’inventé', () => {
	it('② classe climat en étiquette et émissions par m² servies avec leur unité (C1, plan 04.5-12)', () => {
		const [, deux] = reperesDocument(document());

		expect([deux.id, deux.rang, deux.libelle]).toEqual(['climat', 2, 'Étiquette climat']);
		expect(deux.lignes[0].etiquette).toBe('C');
		expect(deux.lignes[0].fieldId).toBe('dpe.document.emissions_ges_m2');
		expect(deux.lignes[0].etat).toEqual({ kind: 'valeur', texte: '41', unite: 'kg CO₂ éq/m²/an' });
		expect(deux.volet.find((e) => e.terme === 'Unité')).toEqual({ terme: 'Unité', valeur: 'kg CO₂ éq/m²/an' });
		expect(deux.volet.find((e) => e.terme === 'Définition')).toEqual({
			terme: 'Définition',
			valeur: `${definitionPublique('dpe.document.etiquette_ges')} ${definitionPublique('dpe.document.emissions_ges_m2')}`
		});
		expect(JSON.stringify(deux)).not.toContain('"dpe.document.emissions_ges"');
	});

	it('③ « type · surface habitable déclarée »', () => {
		const [, , trois] = reperesDocument(document());

		expect([trois.id, trois.libelle]).toEqual(['logement', 'Type et surface déclarés']);
		expect(trois.lignes[0].etat).toEqual({ kind: 'valeur', texte: `Maison · 79${NB}m²`, unite: null });
	});

	it('④ coût annuel : la valeur servie en €/an, jamais une fourchette', () => {
		const [, , , quatre] = reperesDocument(document());

		expect([quatre.id, quatre.rang, quatre.libelle]).toEqual(['cout', 4, 'Coût annuel estimé des énergies']);
		const texte = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(1834.2);
		expect(quatre.lignes[0].etat).toEqual({ kind: 'valeur', texte, unite: '€/an' });
	});
});

describe('« Ce que déclare le document » : les caractéristiques servies', () => {
	it('libellé par champ, valeur servie, champ vide « Non renseigné » sur le document', () => {
		const lignes = caracteristiquesDocument(document());

		expect(lignes.map((l) => l.libelle).slice(0, 4)).toEqual(['Type de bâtiment', 'Surface habitable', 'Période de construction', 'Isolation']);
		expect(lignes[0].etat).toEqual({ kind: 'valeur', texte: 'Maison', unite: null });
		expect(lignes[1].etat).toEqual({ kind: 'valeur', texte: `79${NB}m²`, unite: null });
		expect(lignes[3].etat).toMatchObject({ kind: 'absence', etat: 'not_reported', aide: 'La source ne fournit pas cette valeur dans ce document.' });
	});
});

describe('« Ce que déclare le document » : les totaux annuels lus dans `mesure` (C1, plan 04.5-12)', () => {
	it('après les caractéristiques servies, les deux totaux du report 4.1-12, chacun avec son unité annuelle', () => {
		const lignes = caracteristiquesDocument(document());
		const format = (valeur: number) => new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(valeur);

		expect(lignes.slice(4).map((l) => [l.fieldId, l.libelle, l.etat])).toEqual([
			['dpe.document.conso_ep', "Consommation annuelle d'énergie primaire", { kind: 'valeur', texte: format(17830.4), unite: 'kWhEP/an' }],
			['dpe.document.emissions_ges', 'Émissions annuelles de gaz à effet de serre', { kind: 'valeur', texte: format(3433.1), unite: 'kg CO₂ éq/an' }]
		]);
	});

	it('chaque ligne a son volet Source : définition, objet, rattachement, unité, source ; un manque y est dit (D45-08)', () => {
		const base = document();
		const doc = document({
			caracteristiques: base.caracteristiques,
			reperes: {
				...base.reperes!,
				etiquette_climat: { ...base.reperes!.etiquette_climat, mesure: valeur({ field_id: 'dpe.document.emissions_ges', etat: 'not_reported', valeur: null }) }
			}
		});
		for (const ligne of caracteristiquesDocument(doc)) {
			const terme = (nom: string) => ligne.volet.find((e) => e.terme === nom);
			expect(terme('Définition'), ligne.fieldId).toEqual({ terme: 'Définition', valeur: definitionPublique(ligne.fieldId) });
			// P3: « En clair » first, from the sentences of `explications-simples.ts`.
			expect(ligne.volet[0], ligne.fieldId).toEqual({ terme: 'En clair', valeur: enClairChamp(ligne.fieldId) });
			expect(terme('Objet')).toEqual({ terme: 'Objet', valeur: 'Document DPE' });
			expect(terme('Source')).toEqual({ terme: 'Source', valeur: SOURCE_DPE.nom, url: SOURCE_DPE.url });
			expect(ligne.source).toEqual({ nom: SOURCE_DPE.nom, url: SOURCE_DPE.url });
			expect(ligne.ligne.brut?.fieldId).toBe(ligne.fieldId);
		}
		const unite = (fieldId: string) => caracteristiquesDocument(doc).find((l) => l.fieldId === fieldId)?.volet.find((e) => e.terme === 'Unité');
		expect(unite('dpe.document.conso_ep')).toEqual({ terme: 'Unité', valeur: 'kWhEP/an' });
		expect(unite('dpe.document.emissions_ges')).toEqual({ terme: 'Unité', valeur: 'kg CO₂ éq/an' });
		expect(unite('dpe.document.surface_habitable')).toEqual({ terme: 'Unité', valeur: 'm²' });
		expect(unite('dpe.document.periode_construction')).toBeUndefined();
		const manque = caracteristiquesDocument(doc).find((l) => l.fieldId === 'dpe.document.emissions_ges')?.volet.find((e) => e.terme === 'État et motif');
		expect(manque && 'absences' in manque ? manque.absences.map((a) => [a.libelle, a.etat.etat]) : null).toEqual([
			['Émissions annuelles de gaz à effet de serre', 'not_reported']
		]);
	});

	it('après les caractéristiques servies, « Consommation annuelle d’énergie primaire » avec son unité annuelle', () => {
		const lignes = caracteristiquesDocument(document());
		const total = lignes.find((l) => l.fieldId === 'dpe.document.conso_ep');
		const texte = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(17830.4);

		expect(lignes.indexOf(total!)).toBe(4);
		expect(total).toMatchObject({ libelle: "Consommation annuelle d'énergie primaire", provisoire: false, etat: { kind: 'valeur', texte, unite: 'kWhEP/an' } });
	});

	it('total non renseigné : « Non renseigné » dans le détail, jamais omis', () => {
		const base = document();
		const doc = document({
			reperes: {
				...base.reperes!,
				etiquette_energie: { ...base.reperes!.etiquette_energie, mesure: valeur({ field_id: 'dpe.document.conso_ep', etat: 'not_reported', valeur: null }) }
			}
		});

		expect(caracteristiquesDocument(doc).find((l) => l.fieldId === 'dpe.document.conso_ep')?.etat).toMatchObject({ kind: 'absence', etat: 'not_reported' });
	});

	it('document retiré : aucun total', () => {
		expect(caracteristiquesDocument(document({ disponibilite: 'retire', reperes: null, caracteristiques: [] }))).toEqual([]);
	});
});

describe('« Adresse et rattachement » (P-ADR-01, LIEU-03)', () => {
	it('publiée : entre guillemets, telle que saisie ; divergente : la phrase de conservation', () => {
		const modele = modeleAdresse(document());

		expect(modele.declaree).toEqual({ kind: 'publiee', texte: `«${NNB}14 rue des Lilas${NNB}»` });
		expect(modele.divergente).toBe(true);
		expect(PHRASE_DIVERGENCE).toBe(`L'adresse déclarée diffère de l'adresse retenue${NNB}; les deux sont conservées.`);
		expect(modele.ban).toEqual({
			kind: 'retenue',
			href: '/adresse/85191_0420_00012',
			libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
			nature: NATURE_RAPPROCHEMENT
		});
		expect(NATURE_RAPPROCHEMENT).toBe('clé BAN déclarée par la source, non vérifiée');
	});

	it('masquée : « non affichée : {phrase du motif} », sans phrase de divergence', () => {
		const modele = modeleAdresse(document({ adresse_declaree: { etat: 'masquee', texte: null, motif: 'complement_present' } }));

		expect(modele.declaree).toEqual({ kind: 'masquee', texte: `non affichée${NB}: le document y joint un complément (étage, porte, bâtiment ou nom).` });
		expect(modele.divergente).toBe(false);
	});

	it('absente : « Non renseigné » ; BAN non retenue : état servi, jamais un lien', () => {
		const modele = modeleAdresse(
			document({
				adresse_declaree: { etat: 'absente', texte: null, motif: null },
				adresse_ban: { etat: 'hors_couverture', ban_id: null, libelle: null, numero: null, rep: null, nom_voie: null, commune: null, motif: 'source_sans_couverture' },
				adresse_divergente: null
			})
		);

		expect(modele.declaree).toMatchObject({ kind: 'etat', etat: { kind: 'absence', etat: 'not_reported' } });
		expect(modele.ban).toEqual({ kind: 'etat', etat: { kind: 'absence', etat: 'out_of_coverage', motif: 'source_sans_couverture', effectif: null } });
	});

	it('clé absente de la BAN : texte décidé le 2026-10-05 (ligne 9)', () => {
		const modele = modeleAdresse(
			document({ adresse_ban: { etat: 'cle_inconnue', ban_id: null, libelle: null, numero: null, rep: null, nom_voie: null, commune: null, motif: null } })
		);

		expect(modele.ban).toEqual({ kind: 'texte', texte: 'Clé BAN déclarée par la source, introuvable dans la BAN importée' });
	});

	it('adresse BAN retenue sans libellé servi (lignes contradictoires) : texte décidé (ligne 10), lien gardé', () => {
		const ban = { ...document().adresse_ban!, libelle: null };
		const modele = modeleAdresse(document({ adresse_ban: ban }));

		expect(modele.ban).toEqual({
			kind: 'retenue',
			href: '/adresse/85191_0420_00012',
			libelle: `Adresse de la BAN non déterminée${NB}: les sources ne s'accordent pas`,
			nature: NATURE_RAPPROCHEMENT
		});
	});

	it('état retenu sans clé servie : le libellé neutre reste le repli, marqué provisoire', () => {
		const ban = { ...document().adresse_ban!, ban_id: null };
		expect(modeleAdresse(document({ adresse_ban: ban })).ban).toEqual({ kind: 'provisoire' });
	});
});

describe('« Provenance et validité » : une ligne de statut, sans calcul de date', () => {
	const cas: [Record<string, unknown>, { texte: string; href?: string }[], boolean][] = [
		[{ code: 'en_vigueur' }, [{ texte: 'En vigueur' }], false],
		[{ code: 'expire', date: '2026-05-01' }, [{ texte: 'Expiré le 1 mai 2026' }], false],
		[
			{ code: 'remplace', remplace_par: '2485E0000009R' },
			[{ texte: 'Remplacé par le ' }, { texte: `DPE n°${NB}2485E0000009R`, href: '/dpe/2485E0000009R' }, { texte: " selon l'ADEME" }],
			false
		],
		[{ code: 'remplacement_non_verifie' }, [{ texte: 'Remplacement non vérifié' }], false],
		// Owner's wording of 2026-10-05 (04-DECISIONS-TEXTES, « Textes et choix de 04-04 », rows 3 to 8).
		[{ code: 'ancienne_methode' }, [{ texte: "Établi selon l'ancienne méthode (avant juillet 2021)" }], false],
		[{ code: 'validite_inconnue' }, [{ texte: 'Validité non déterminée' }], false],
		[{ code: 'dates_incoherentes' }, [{ texte: `Dates incohérentes${NB}: validité non déterminée` }], false],
		[{ code: 'etablissement_futur' }, [{ texte: `Date d'établissement postérieure à aujourd'hui${NB}: validité non déterminée` }], false],
		[{ code: 'expire', date: null }, [{ texte: 'Expiré' }], false],
		// A code served later: the neutral label, provisional.
		[{ code: 'code_futur' }, [{ texte: LIBELLE_NON_AFFICHABLE }], true],
		// Decision n° 24: no successor number served, the wording of the card.
		[{ code: 'remplace', remplace_par: null }, [{ texte: "Remplacé selon l'ADEME" }], false]
	];
	it.each(cas)('%o', (partiel, parties, provisoire) => {
		const statut = { code: 'en_vigueur', validite: null, date: null, remplace_par: null, remplace: null, ...partiel };

		expect(ligneStatut(document({ statut } as Partial<DocumentDpePublic>))).toEqual({ parties, provisoire });
	});

	it('document retiré : « Plus publié par l’ADEME » et la phrase de son volet (ligne 7)', () => {
		const statut = { code: 'retire', validite: null, date: null, remplace_par: null, remplace: null };

		expect(ligneStatut(document({ statut }))).toEqual({
			parties: [{ texte: "Plus publié par l'ADEME" }],
			provisoire: false,
			phrase: `La source ne publie plus ce document${NNB}; DataCommune conserve son numéro et son statut.`
		});
	});

	it('jeux ADEME : libellé du contrat V3 et page de catalogue servie', () => {
		const doc = document({
			source_identity: {
				reference: NUMERO,
				sources: [
					{ dataset: 'dpe03existant', url: 'https://data.ademe.fr/datasets/dpe03existant' },
					{ dataset: 'dpe02neuf', url: 'https://data.ademe.fr/datasets/dpe02neuf' }
				]
			}
		});

		expect(jeuxSources(doc)).toEqual([
			{ libelle: 'DPE des logements existants', url: 'https://data.ademe.fr/datasets/dpe03existant' },
			{ libelle: 'DPE des logements neufs', url: 'https://data.ademe.fr/datasets/dpe02neuf' }
		]);
	});
});

describe('états de la page (SEM-01)', () => {
	it('DPE inconnu : la couverture servie nommée, « Cela ne veut pas dire qu’il n’existe pas. »', () => {
		expect(phraseInconnu(['44', '85'])).toEqual({
			texte: `Aucun document DPE ne porte ce numéro dans la base importée par ce site, qui couvre Loire-Atlantique${NB}(44) et Vendée${NB}(85). Cela ne veut pas dire qu'il n'existe pas.`
		});
	});

	it('numéro mal formé : la saisie citée au plus 40 caractères, puis « … » (IN-05 de la relecture D-15)', () => {
		const longue = 'A'.repeat(500);

		expect(phraseMalForme(longue)).toBe(`«${NNB}${'A'.repeat(40)}…${NNB}» n'a pas la forme d'un numéro DPE de l'ADEME.`);
		expect(phraseMalForme('2485e0000003c')).toBe(`«${NNB}2485e0000003c${NNB}» n'a pas la forme d'un numéro DPE de l'ADEME.`);
	});

	it('DPE inconnu sans département servi : texte décidé le 2026-10-05 (ligne 11)', () => {
		expect(phraseInconnu([])).toEqual({ texte: "Aucun document DPE n'est importé par ce site." });
	});

	// Owner decision of 2026-11-09: since the national switch the sentence listed the 101 served
	// departements; above five the count replaces the list.
	it('DPE inconnu, France entière : au-delà de cinq départements, leur nombre remplace la liste', () => {
		// The served set of production: Saint-Pierre-et-Miquelon (975) is in the list of names, not served.
		const tous = DEPARTEMENTS.map((departement) => departement.code).filter((code) => code !== '975');
		expect(tous).toHaveLength(101);
		expect(phraseInconnu(tous)).toEqual({
			texte: `Aucun document DPE ne porte ce numéro dans la base importée par ce site (101${NB}départements). Cela ne veut pas dire qu'il n'existe pas.`
		});

		const cinq = ['44', '85', '49', '53', '72'];
		expect(phraseInconnu(cinq).texte).toContain(`qui couvre Loire-Atlantique${NB}(44), Vendée${NB}(85), Maine-et-Loire${NB}(49), Mayenne${NB}(53) et Sarthe${NB}(72).`);
		expect(phraseInconnu([...cinq, '35']).texte).toBe(
			`Aucun document DPE ne porte ce numéro dans la base importée par ce site (6${NB}départements). Cela ne veut pas dire qu'il n'existe pas.`
		);
	});

	it('définition publique de l’installation de chauffage : « individuelle, collective ou mixte » (ligne 17)', () => {
		expect(definitionPublique('dpe.document.installation_chauffage')).toContain('individuelle, collective ou mixte');
	});

	it('numéro mal formé : la saisie entre guillemets', () => {
		expect(phraseMalForme('abc')).toBe(`«${NNB}abc${NNB}» n'a pas la forme d'un numéro DPE de l'ADEME.`);
	});
});
