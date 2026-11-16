import { fireEvent, render, waitFor, within } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { DpeConsultedDocument } from '$lib/api/dpe-inventory';
import DpeDocument from './DpeDocument.svelte';

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

describe('DpeDocument.svelte', () => {
	it('affiche le détail autorisé, sa provenance et la réserve permanente', () => {
		const { container, getByRole, getByText } = render(DpeDocument, {
			props: {
				document: document(),
				state: 'ready',
				presentationLabel: 'Document 03',
				onRetry: vi.fn(),
				onClose: vi.fn()
			}
		});

		expect(getByRole('heading', { level: 2, name: 'Diagnostic consulté' })).toBeInTheDocument();
		expect(getByText('Document 03')).toBeInTheDocument();
		expect(getByText("Ce document n'est pas attribué au logement recherché")).toBeInTheDocument();
		expect(getByText('31 566 kWhEP/an')).toBeInTheDocument();
		expect(getByText('1 234,5 kgCO₂eq/an')).toBeInTheDocument();
		expect(getByText('2 955 €/an')).toBeInTheDocument();
		expect(getByText('61,6 m² habitables DPE')).toBeInTheDocument();
		expect(getByText('Source : ADEME')).toBeInTheDocument();
		expect(getByText('Adresse brute ADEME : non vérifiée')).toBeInTheDocument();
		expect(container.innerHTML).not.toContain('opaque-document');
		expect(container.innerHTML).not.toContain('opaque-context');
	});

	it('rend tous les champs absents comme non disponibles une fois le détail chargé', () => {
		const { getAllByText, getByText } = render(DpeDocument, {
			props: {
				document: document({
					annee_construction: null,
					periode_construction: null,
					type_installation_chauffage: null,
					type_energie_principale_chauffage: null,
					qualite_isolation_enveloppe: null,
					conso_5_usages_ep: null,
					emission_ges_5_usages: null,
					cout_total_5_usages: null,
					date_fin_validite_dpe: null,
					validity_by_date: 'unknown'
				}),
				state: 'ready',
				onRetry: vi.fn(),
				onClose: vi.fn()
			}
		});

		expect(getAllByText('Non disponible').length).toBeGreaterThanOrEqual(8);
		expect(getByText(/validité par date est inconnue/i)).toBeInTheDocument();
	});

	it('distingue le chargement d’une série de champs absents', () => {
		const { container, getByRole, getByText } = render(DpeDocument, {
			props: { document: null, state: 'loading', onRetry: vi.fn(), onClose: vi.fn() }
		});

		expect(getByRole('region')).toHaveAttribute('aria-busy', 'true');
		expect(getByText('Chargement du document…')).toBeInTheDocument();
		expect(container.querySelector('dl')).toBeNull();
		expect(container.textContent).not.toContain('Non renseigné');
	});

	it('localise une panne et réessaie le détail sans effacer sa nature documentaire', async () => {
		const onRetry = vi.fn();
		const { getByRole, getByText } = render(DpeDocument, {
			props: { document: null, state: 'error', onRetry, onClose: vi.fn() }
		});

		expect(getByRole('status')).toHaveTextContent('momentanément indisponible');
		expect(getByText("Ce document n'est pas attribué au logement recherché")).toBeInTheDocument();
		await fireEvent.click(getByRole('button', { name: 'Réessayer le détail' }));
		expect(onRetry).toHaveBeenCalledOnce();
	});

	it('place le focus sur le titre et le restitue au déclencheur à la fermeture', async () => {
		const trigger = window.document.createElement('button');
		window.document.body.append(trigger);
		const onClose = vi.fn();
		const { getByRole } = render(DpeDocument, {
			props: {
				document: document(),
				state: 'ready',
				onRetry: vi.fn(),
				onClose,
				returnFocusTo: trigger
			}
		});

		const title = getByRole('heading', { level: 2, name: 'Diagnostic consulté' });
		await waitFor(() => expect(title).toHaveFocus());
		await fireEvent.click(getByRole('button', { name: 'Fermer et revenir à la liste' }));
		await waitFor(() => expect(trigger).toHaveFocus());
		expect(onClose).toHaveBeenCalledOnce();
		trigger.remove();
	});

	it('revient au titre de l’inventaire si le déclencheur a disparu avec la page', async () => {
		const inventoryTitle = window.document.createElement('h2');
		inventoryTitle.id = 'dpe-inventory-title';
		inventoryTitle.tabIndex = -1;
		window.document.body.append(inventoryTitle);
		const trigger = window.document.createElement('button');
		window.document.body.append(trigger);
		const onClose = vi.fn(() => trigger.remove());
		const { getByRole } = render(DpeDocument, {
			props: {
				document: document(),
				state: 'ready',
				onRetry: vi.fn(),
				onClose,
				returnFocusTo: trigger
			}
		});

		await fireEvent.click(getByRole('button', { name: 'Fermer et revenir à la liste' }));
		await waitFor(() => expect(inventoryTitle).toHaveFocus());
		expect(trigger.isConnected).toBe(false);
		expect(onClose).toHaveBeenCalledOnce();
		inventoryTitle.remove();
	});

	it('échappe les textes source et exclut tout le panneau de l’impression', () => {
		const { container, getByText } = render(DpeDocument, {
			props: {
				document: document({ type_batiment: '<img src=x onerror=alert(1)>' }),
				state: 'ready',
				onRetry: vi.fn(),
				onClose: vi.fn()
			}
		});

		expect(getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
		expect(container.querySelector('img')).toBeNull();
		expect(within(container).getByRole('region')).toHaveClass('print:hidden');
	});

	it('ne rend rien tant qu’aucun document n’est consulté', () => {
		const { container } = render(DpeDocument, {
			props: { document: null, state: 'idle', onRetry: vi.fn(), onClose: vi.fn() }
		});
		expect(container.textContent?.trim()).toBe('');
	});
});

function lifecycleDocument(overrides: Partial<DpeConsultedDocument> = {}): DpeConsultedDocument {
 return document({...overrides, lifecycle: {
  source_state: 'present', source_snapshot_date: '2026-09-08', source_scope:'44/85', regimes:['existant','neuf'],
  replacement_state:'replaced',graph_issues:['fork'],validity:'expired',as_of_date:'2026-09-08',
  revision_status:'unverified',revision_kind:null,revision_date:null,detail_availability:'available',
  ...overrides.lifecycle
 }, date_fin_validite_dpe: '2025-02-03'});
}

it('renders independent lifecycle definition rows and the explicit revision limitation', () => {
 const {getByText, container} = render(DpeDocument,{props:{document:lifecycleDocument(),state:'ready',onRetry:vi.fn(),onClose:vi.fn()}});
 for(const text of ['Validité expirée depuis le 03/02/2025','Remplacé selon la source','Plusieurs remplacements signalés','Périmètre 44/85','Logements existants · Logements neufs','Date de référence : 08/09/2026']) {
  expect(getByText(text).closest('dd')).not.toBeNull();
 }
 expect(getByText('Étiquette fournie par la source. La prise en compte d’une attestation de révision n’est pas établie avec les données disponibles.')).toBeInTheDocument();
 expect(container.textContent).not.toContain('remplacement ou d’annulation reste inconnu');
});

it('shows the known end date without claiming it is missing or qualifying validity', () => {
 const base = lifecycleDocument();
 const value = { ...base, date_etablissement_dpe: null, date_fin_validite_dpe: '2030-01-01',
  validity_by_date: 'unknown' as const, lifecycle: { ...base.lifecycle!, validity: 'unknown' as const }
 };
 const { getByText, container } = render(DpeDocument, {
  props: { document: value, state: 'ready', onRetry: vi.fn(), onClose: vi.fn() }
 });
 expect(getByText('01/01/2030')).toBeVisible();
 expect(getByText('Date d’établissement non renseignée — validité indéterminée')).toBeVisible();
 expect(container.textContent).not.toMatch(/Fin de validité non renseignée|validité n’est pas dépassée/);
});

it('hides every historical descriptive field for a reconciled document even if present at runtime', () => {
 const value=lifecycleDocument();
 const {container,getByText} = render(DpeDocument,{props:{document:{...value,lifecycle:{...value.lifecycle!,source_state:'absent_from_complete_scope',detail_availability:'source_reconciled',validity:'unknown'},type_batiment:'OLD-DESCRIPTIVE-SECRET'},state:'ready',onRetry:vi.fn(),onClose:vi.fn()}});
 expect(getByText('Détail non disponible après réconciliation de la source')).toBeInTheDocument();
 for(const forbidden of ['OLD-DESCRIPTIVE-SECRET','61,6','31 566','Étiquette DPE','Année de construction','Installation de chauffage']) expect(container.textContent).not.toContain(forbidden);
 expect(getByText('Absent du dernier instantané complet du périmètre — motif inconnu')).toBeInTheDocument();
});

it('shows a legacy lifecycle fallback without inventing an attestation', () => {
 const {getByText} = render(DpeDocument,{props:{document:document(),state:'ready',onRetry:vi.fn(),onClose:vi.fn()}});
 expect(getByText('Cycle de vie non disponible')).toBeInTheDocument();
 expect(getByText('Les informations reçues ne permettent pas de décrire le cycle de vie.')).toBeInTheDocument();
});

it('uses a verified attestation date only when explicitly projected', () => {
 const value=lifecycleDocument();
 const {getByText,queryByText} = render(DpeDocument,{props:{document:{...value,lifecycle:{...value.lifecycle!,revision_status:'verified_attestation',revision_kind:'2026',revision_date:'2026-02-01'}},state:'ready',onRetry:vi.fn(),onClose:vi.fn()}});
 expect(getByText('Étiquette issue d’une attestation — 01/02/2026')).toBeInTheDocument();
 expect(queryByText(/prise en compte d’une attestation de révision n’est pas établie/)).toBeNull();
});
