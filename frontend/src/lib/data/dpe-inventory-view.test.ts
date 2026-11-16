import { describe, expect, it } from 'vitest';

import type { DpeConsultedDocument, DpeInventoryPage, DpeLifecycle } from '$lib/api/dpe-inventory';
import {
	deriveDpeDocumentView,
	deriveDpeInventoryView,
	formatDpeAnnualValue
} from './dpe-inventory-view';

function inventory(overrides: Partial<DpeInventoryPage> = {}): DpeInventoryPage {
	return {
		version: 2,
		state: 'ready',
		context_ref: 'opaque-context',
		items: [
			{
				document_ref: 'opaque-document',
				type_batiment: null,
				surface_habitable: null,
				etiquette_dpe: null,
				etiquette_ges: 'C',
				date_etablissement_dpe: null,
				date_fin_validite_dpe: null
			}
		],
		total: 137,
		distribution: { A: 1, B: 0, C: 4, D: 0, E: 0, F: 0, G: 0, unknown: 132 },
		page_size: 20,
		has_more: true,
		next_cursor: 'opaque-cursor',
		read_state: 'complete',
		association: 'source_ban_equality',
		address_check: 'unverified',
		source: 'ADEME',
		source_coverage: 'unknown',
		graph_state: 'not_checked',
		imported_at: null,
		...overrides
	};
}

function document(overrides: Partial<DpeConsultedDocument> = {}): DpeConsultedDocument {
	return {
		version: 2,
		context_ref: 'opaque-context',
		document_ref: 'opaque-document',
		type_batiment: 'Appartement',
		surface_habitable: 61.6,
		etiquette_dpe: 'D',
		etiquette_ges: 'C',
		date_etablissement_dpe: '2023-04-15',
		date_fin_validite_dpe: '2033-04-14',
		annee_construction: 1985,
		periode_construction: '1975-1988',
		type_installation_chauffage: 'Individuel',
		type_energie_principale_chauffage: 'Électricité',
		qualite_isolation_enveloppe: 'Moyenne',
		conso_5_usages_ep: 31566,
		emission_ges_5_usages: 1234.5,
		cout_total_5_usages: 2955,
		validity_by_date: 'not_expired_by_date',
		association: 'source_ban_equality',
		address_check: 'unverified',
		source: 'ADEME',
		source_coverage: 'unknown',
		graph_state: 'not_checked',
		imported_at: '2026-09-03T12:00:00Z',
		...overrides
	};
}

describe('deriveDpeInventoryView', () => {
	it('compte des documents, jamais des logements, et conserve les inconnues', () => {
		const view = deriveDpeInventoryView(inventory());

		expect(view.totalLabel).toBe('137 documents connus');
		expect(view.totalLabel).not.toContain('logement');
		expect(view.distribution).toEqual([
			{ label: 'A', count: 1 },
			{ label: 'B', count: 0 },
			{ label: 'C', count: 4 },
			{ label: 'D', count: 0 },
			{ label: 'E', count: 0 },
			{ label: 'F', count: 0 },
			{ label: 'G', count: 0 },
			{ label: 'Classes non disponibles', count: 132 }
		]);
	});

	it('nomme chaque absence sans déduire une surface depuis une autre source', () => {
		const [row] = deriveDpeInventoryView(inventory()).rows;

		expect(row.typeLabel).toBe('Type non renseigné');
		expect(row.surfaceLabel).toBe('Surface non renseignée');
		expect(row.dpeLabel).toBe('Classe non renseignée');
		expect(row.establishedLabel).toBe('Date non renseignée');
		expect(row.documentRef).toBe('opaque-document');
	});

	it('projette entièrement une ligne inventaire renseignée', () => {
		const [row] = deriveDpeInventoryView(
			inventory({
				items: [
					{
						document_ref: 'opaque-complete-document',
						type_batiment: 'Maison',
						surface_habitable: 99.1,
						etiquette_dpe: 'F',
						etiquette_ges: 'E',
						date_etablissement_dpe: '2024-01-02',
						date_fin_validite_dpe: '2034-01-01'
					}
				]
			})
		).rows;

		expect(row).toEqual({
			documentRef: 'opaque-complete-document',
			ordinal: 1,
			documentLabel: 'Document 01',
			typeLabel: 'Maison',
			surfaceLabel: '99,1 m² habitables DPE',
			dpeClass: 'F',
			dpeLabel: 'Classe F',
			gesLabel: 'Classe E',
			establishedLabel: 'Établi le 02/01/2024',
			validUntilLabel: 'Fin de validité déclarée : 01/01/2034',
			showValidUntil: true,
			lifecycleRows: [{ label: 'Cycle de vie', value: 'Cycle de vie non disponible' }]
		});
	});

	it('ne préfixe jamais une date vide ou invalide comme si elle était renseignée', () => {
		const [row] = deriveDpeInventoryView(
			inventory({
				items: [
					{
						...inventory().items[0],
						date_etablissement_dpe: 'date-invalide',
						date_fin_validite_dpe: '   '
					}
				]
			})
		).rows;

		expect(row.establishedLabel).toBe('Date non renseignée');
		expect(row.validUntilLabel).toBe('Fin de validité non renseignée');
		expect(`${row.establishedLabel} ${row.validUntilLabel}`).not.toContain(
			'le Non renseignée'
		);
	});

	it('distingue total inconnu, lecture partielle, suite et graphe incohérent', () => {
		const view = deriveDpeInventoryView(
			inventory({
				total: null,
				read_state: 'partial',
				graph_state: 'incoherent',
				distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 1 }
			}),
			3
		);

		expect(view.totalLabel).toBe('Effectif documentaire non disponible');
		expect(view.pageLabel).toBe('Page 3 · total inconnu');
		expect(view.hasMore).toBe(true);
		expect(view.notes).toEqual(
			expect.arrayContaining([
				'Lecture partielle du périmètre importé.',
				'Couverture de la source ADEME : inconnue.',
				'Graphe de remplacement incohérent : aucun document n’est écarté silencieusement.'
			])
		);
	});

	it('calcule la plage depuis la page sans transformer le total en preuve d’identité', () => {
		const view = deriveDpeInventoryView(
			inventory({ total: 41, items: inventory().items, page_size: 20 }),
			3
		);

		expect(view.rangeLabel).toBe('Document 41 sur 41');
		expect(view.pageLabel).toBe('Page 3 sur 3');
	});

	it.each([
		['absent', 'Aucun document disponible à cette adresse'],
		['identity_unavailable', "L’identité de l’adresse ne permet pas de constituer cet inventaire."],
		['unavailable', 'Les diagnostics sont momentanément indisponibles.']
	] as const)('distingue l’état %s dans le résumé', (state, expected) => {
		const view = deriveDpeInventoryView(
			inventory({
				state,
				context_ref: null,
				items: [],
				total: state === 'absent' ? 0 : null,
				distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 },
				has_more: false,
				next_cursor: null
			})
		);

		expect(view.emptyLabel).toBe(expected);
	});

	it('décrit une page ready vide sans nier les documents du périmètre', () => {
		const view = deriveDpeInventoryView(
			inventory({
				items: [],
				total: 24,
				distribution: { A: 0, B: 1, C: 5, D: 14, E: 4, F: 0, G: 0, unknown: 0 },
				has_more: false,
				next_cursor: null
			}),
			3
		);

		expect(view.emptyLabel).toBe('Aucun document chargé sur cette page.');
		expect(view.emptyLabel).not.toContain('périmètre');
	});

	it('projette une allowlist exacte malgré des sentinelles interdites présentes au runtime', () => {
		const sentinelValues = [
			'ADEME-INTERDIT',
			'BAN-INTERDIT',
			'ADRESSE-INTERDITE',
			'COMPLEMENT-INTERDIT',
			'COORDONNEE-INTERDITE',
			'ETAGE-INTERDIT',
			'PORTE-INTERDITE',
			'ID-TECHNIQUE-INTERDIT',
			'SELECTION-INTERDITE',
			'KPI-INTERDIT'
		];
		const base = inventory();
		const poisoned = {
			...base,
			identifiant_ban: sentinelValues[1],
			adresse_brute: sentinelValues[2],
			coordinates: sentinelValues[4],
			ficheSelection: sentinelValues[8],
			kpi: sentinelValues[9],
			items: [
				{
					...base.items[0],
					numero_dpe: sentinelValues[0],
					complement_adresse: sentinelValues[3],
					latitude: sentinelValues[4],
					etage: sentinelValues[5],
					porte: sentinelValues[6],
					id_technique: sentinelValues[7]
				}
			]
		} as unknown as DpeInventoryPage;

		const view = deriveDpeInventoryView(poisoned);
		expect(Object.keys(view).sort()).toEqual([
			'addressCheckLabel',
			'distribution',
			'emptyLabel',
			'hasMore',
			'importedAtLabel',
			'nextCursor',
			'notes',
			'pageLabel',
			'rangeLabel',
			'rows',
			'scopeNote',
			'sourceLabel',
			'title',
			'totalLabel'
		]);
		expect(Object.keys(view.rows[0]).sort()).toEqual([
			'documentLabel',
			'documentRef',
			'dpeClass',
			'dpeLabel',
			'establishedLabel',
			'gesLabel',
			'lifecycleRows',
			'ordinal',
			'showValidUntil',
			'surfaceLabel',
			'typeLabel',
			'validUntilLabel'
		]);
		for (const sentinel of sentinelValues) {
			expect(JSON.stringify(view)).not.toContain(sentinel);
		}
	});
});

describe('deriveDpeDocumentView', () => {
	it('conserve les totaux annuels, les unités et la surface DPE', () => {
		const view = deriveDpeDocumentView(document());
		expect(view.rows.map((row) => row.label)).toEqual([
			'Type de bâtiment',
			'Surface habitable',
			'Étiquette DPE',
			'Étiquette GES',
			'Date d’établissement',
			'Fin de validité déclarée',
			'Année de construction',
			'Période de construction',
			'Installation de chauffage',
			'Énergie principale de chauffage',
			'Isolation de l’enveloppe',
			'Consommation primaire (5 usages)',
			'Émissions (5 usages)',
			'Coût estimé (5 usages)'
		]);
		expect(Object.fromEntries(view.rows.map((row) => [row.label, row.value]))).toEqual({
			'Type de bâtiment': 'Appartement',
			'Surface habitable': '61,6 m² habitables DPE',
			'Étiquette DPE': 'Classe D',
			'Étiquette GES': 'Classe C',
			'Date d’établissement': '15/04/2023',
			'Fin de validité déclarée': '14/04/2033',
			'Année de construction': '1985',
			'Période de construction': '1975-1988',
			'Installation de chauffage': 'Individuel',
			'Énergie principale de chauffage': 'Électricité',
			'Isolation de l’enveloppe': 'Moyenne',
			'Consommation primaire (5 usages)': '31 566 kWhEP/an',
			'Émissions (5 usages)': '1 234,5 kgCO₂eq/an',
			'Coût estimé (5 usages)': '2 955 €/an'
		});
	});

	it('verrouille la forme exacte des lignes ordinaires et énergétiques du détail', () => {
		const view = deriveDpeDocumentView(document());

		for (const row of view.rows) {
			const expectedKeys =
				row.label === 'Étiquette DPE' || row.label === 'Étiquette GES'
					? ['energyClass', 'label', 'value']
					: ['label', 'value'];
			expect(Object.keys(row).sort()).toEqual(expectedKeys);
		}
	});

	it('ne transforme pas une date absente en diagnostic valide', () => {
		const view = deriveDpeDocumentView(
			document({ date_fin_validite_dpe: null, validity_by_date: 'unknown' })
		);
		const values = new Map(view.rows.map((row) => [row.label, row.value]));

		expect(values.get('Fin de validité déclarée')).toBe('Non disponible');
		expect(view.validityNote).toContain('inconnue');
	});

	it('qualifie prudemment une date future sans garantir l’absence de remplacement', () => {
		const view = deriveDpeDocumentView(document());

		expect(view.validityNote).toContain('selon la date déclarée');
		expect(view.validityNote).toContain('ne garantit pas');
		expect(view.validityNote).toContain('remplacement');
	});

	it('nomme tous les champs source absents seulement dans le détail chargé', () => {
		const view = deriveDpeDocumentView(
			document({
				type_batiment: null,
				surface_habitable: null,
				etiquette_dpe: null,
				etiquette_ges: null,
				date_etablissement_dpe: null,
				date_fin_validite_dpe: null,
				annee_construction: null,
				periode_construction: null,
				type_installation_chauffage: null,
				type_energie_principale_chauffage: null,
				qualite_isolation_enveloppe: null,
				conso_5_usages_ep: null,
				emission_ges_5_usages: null,
				cout_total_5_usages: null,
				validity_by_date: 'unknown',
				imported_at: null
			})
		);

		expect(view.rows).toHaveLength(14);
		expect(view.rows.every((row) => row.value === 'Non disponible')).toBe(true);
		expect(view.importedAtLabel).toBe("Fraîcheur de l’import : non disponible");
	});

	it('nomme séparément expiration et graphe incohérent', () => {
		const view = deriveDpeDocumentView(
			document({
				date_fin_validite_dpe: '2024-04-14',
				validity_by_date: 'expired',
				graph_state: 'incoherent'
			})
		);

		expect(view.validityNote).toContain('est dépassée');
		expect(view.validityNote).toContain('reste inconnu');
		expect(view.graphNote).toContain('incohérent');
		expect(view.graphNote).toContain('sans conclusion');
	});

	it('ne propage aucun champ runtime hors allowlist dans le détail', () => {
		const sentinelValues = [
			'ADEME-DETAIL-INTERDIT',
			'BAN-DETAIL-INTERDIT',
			'ADRESSE-DETAIL-INTERDITE',
			'COMPLEMENT-DETAIL-INTERDIT',
			'COORDONNEE-DETAIL-INTERDITE',
			'ETAGE-DETAIL-INTERDIT',
			'PORTE-DETAIL-INTERDITE',
			'ID-DETAIL-INTERDIT',
			'SELECTION-DETAIL-INTERDITE',
			'KPI-DETAIL-INTERDIT'
		];
		const poisoned = {
			...document(),
			numero_dpe: sentinelValues[0],
			identifiant_ban: sentinelValues[1],
			adresse_brute: sentinelValues[2],
			complement_adresse: sentinelValues[3],
			latitude: sentinelValues[4],
			longitude: sentinelValues[4],
			etage: sentinelValues[5],
			porte: sentinelValues[6],
			id_technique: sentinelValues[7],
			ficheSelection: sentinelValues[8],
			kpi: sentinelValues[9]
		} as unknown as DpeConsultedDocument;

		const view = deriveDpeDocumentView(poisoned);
		expect(Object.keys(view).sort()).toEqual([
			'addressCheckLabel',
			'graphNote',
			'importedAtLabel',
			'reserve',
			'rows',
			'sourceLabel',
			'title',
			'validityNote'
		]);
		for (const sentinel of sentinelValues) {
			expect(JSON.stringify(view)).not.toContain(sentinel);
		}
	});

	it.each([null, '', 'date-import-invalide'] as const)(
		'présente une fraîcheur indisponible pour imported_at=%s dans le détail',
		(importedAt) => {
			const view = deriveDpeDocumentView(document({ imported_at: importedAt }));

			expect(view.importedAtLabel).toBe("Fraîcheur de l’import : non disponible");
		}
	);
});

describe('formatDpeAnnualValue', () => {
	it('refuse de formater une absence comme zéro', () => {
		expect(formatDpeAnnualValue(null, 'kWhEP/an')).toBe('Non disponible');
		expect(formatDpeAnnualValue(0, 'kWhEP/an')).toBe('0 kWhEP/an');
	});
});

function lifecycle(overrides: Partial<DpeLifecycle> = {}): DpeLifecycle {
 return { source_state: 'present', source_snapshot_date: '2026-09-08', source_scope: '44/85',
 regimes: ['existant', 'neuf'], replacement_state: 'replaced', graph_issues: [], validity: 'expired',
 as_of_date: '2026-09-08', revision_status: 'unverified', revision_kind: null, revision_date: null,
 detail_availability: 'available', ...overrides };
}

describe('documentary lifecycle presentation', () => {
 it('keeps a known end date while establishment is missing and validity remains indeterminate', () => {
  const page = inventory({ items: [{ ...inventory().items[0],
   date_fin_validite_dpe: '2030-01-01', lifecycle: lifecycle({ validity: 'unknown' })
  }] });
  const row = deriveDpeInventoryView(page).rows[0];
  expect(row.validUntilLabel).toBe('Fin de validité déclarée : 01/01/2030');
  expect(row.lifecycleRows.find(entry => entry.label === 'Validité')?.value)
   .toBe('Date d’établissement non renseignée — validité indéterminée');
  expect(JSON.stringify(row)).not.toMatch(/Fin de validité non renseignée|validité n’est pas dépassée/);
 });
 it('shows simultaneous expiration, replacement, both regimes and dated scope without a winner', () => {
  const page = inventory({ as_of_date: '2026-09-08', items: [{...inventory().items[0], date_fin_validite_dpe: '2025-02-03', lifecycle: lifecycle()}] });
  const row = deriveDpeInventoryView(page).rows[0];
  expect(row.lifecycleRows.map(entry => entry.value)).toEqual(expect.arrayContaining([
   'Validité expirée depuis le 03/02/2025', 'Remplacé selon la source',
   'Présent dans l’instantané source du 08/09/2026', 'Périmètre 44/85',
   'Logements existants · Logements neufs', 'Date de référence : 08/09/2026'
  ]));
  expect(JSON.stringify(row)).not.toMatch(/courant confirmé|dernier DPE valide|gagnant/);
 });
 it.each([
  ['cycle', 'Cycle de remplacement détecté'], ['fork', 'Plusieurs remplacements signalés'],
  ['missing_target', 'Lien de remplacement hors des documents consultables'],
  ['truncated', 'Chaîne de remplacement incomplète'], ['ambiguous_target', 'Cible de remplacement ambiguë']
 ] as const)('keeps graph reservation %s visible on each row', (issue, text) => {
  const page = inventory({ items: [{...inventory().items[0], lifecycle: lifecycle({ graph_issues: [issue] })}] });
  expect(deriveDpeInventoryView(page).rows[0].lifecycleRows.map(entry => entry.value)).toContain(text);
 });
 it.each([
  ['unknown', 'Fin de validité non renseignée'],
  ['inconsistent_dates', 'Dates incohérentes — validité indéterminée'],
  ['future_establishment', 'Date d’établissement future — validité indéterminée']
 ] as const)('does not promote validity %s', (validity, text) => {
  const page = inventory({ items: [{...inventory().items[0], lifecycle: lifecycle({ validity })}] });
  expect(deriveDpeInventoryView(page).rows[0].lifecycleRows.map(entry => entry.value)).toContain(text);
 });
 it('preserves documentary total and unknown count for a reconciled source with unknown reason', () => {
  const page = inventory({ total: 1, distribution: {A:0,B:0,C:0,D:0,E:0,F:0,G:0,unknown:1}, items: [{...inventory().items[0], lifecycle: lifecycle({ source_state: 'absent_from_complete_scope', detail_availability: 'source_reconciled', validity: 'unknown' })}] });
  const view = deriveDpeInventoryView(page);
  expect(view.totalLabel).toBe('1 document connu');
  expect(view.distribution).toContainEqual({label:'Classes non disponibles',count:1});
  expect(view.rows[0].lifecycleRows.map(entry=>entry.value)).toContain('Absent du dernier instantané complet du périmètre — motif inconnu');
 });
 it('does not serialize unknown lifecycle enum text', () => {
  const page = inventory({items:[{...inventory().items[0], lifecycle: lifecycle({source_state:'PRIVATE-UNKNOWN' as DpeLifecycle['source_state']})}]});
  expect(JSON.stringify(deriveDpeInventoryView(page))).not.toContain('PRIVATE-UNKNOWN');
 });
});
