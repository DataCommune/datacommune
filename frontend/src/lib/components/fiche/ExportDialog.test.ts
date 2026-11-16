import { fireEvent, render } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ExportDialog from './ExportDialog.svelte';
import { downloadCsv } from '$lib/utils/csv-download';
import type { ThemeModel } from '$lib/types/fiche';

vi.mock('$lib/utils/csv-download', () => ({ downloadCsv: vi.fn() }));

const themes: ThemeModel[] = [{
	key: 'marche', emoji: '€', name: 'Marché', hint: '', sources: '', rows: [
		{ label: 'Prix', value: '2 500 €/m²', state: 'value', presentation: { state: 'value', grain: 'Commune', reserves: ['Période : 2025 → 2026', 'Source : DVF'] } },
		{ label: 'Variation', value: 'Non qualifiée', available: false, state: 'not_reported', presentation: { state: 'not_reported', grain: 'Commune', reserves: ['Motif source : échantillon insuffisant'] } },
		{ kind: 'context', label: 'Ancrage', value: 'Révisions possibles' },
		{ label: 'Ancien indicateur', value: '42', state: 'value', presentation: { state: 'value' } }
	]
}];

describe('ExportDialog', () => {
	beforeEach(() => vi.mocked(downloadCsv).mockReset());

	it('exports selected values, motivated absences and honest row context', async () => {
		const onclose = vi.fn();
		const view = render(ExportDialog, { open: true, themes, filenameBase: 'commune-44109', onclose });
		expect(view.getByText('4 sélectionnés')).toBeVisible();
		await fireEvent.click(view.getByRole('button', { name: /Télécharger/ }));
		expect(downloadCsv).toHaveBeenCalledWith(
			['Thème', 'Indicateur', 'Valeur ou motif', 'État', 'Maille', 'Période, population, source et réserves'],
			[
				['Marché', 'Prix', '2 500 €/m²', 'value', 'Commune', 'Période : 2025 → 2026 · Source : DVF'],
				['Marché', 'Variation', 'Non qualifiée', 'not_reported', 'Commune', 'Motif source : échantillon insuffisant'],
				['Marché', 'Ancrage', 'Révisions possibles', 'context', 'Maille non renseignée', ''],
				['Marché', 'Ancien indicateur', '42', 'value', 'Maille non renseignée', '']
			],
			'commune-44109-export.csv'
		);
		expect(onclose).toHaveBeenCalledOnce();
	});

	it('preserves the user row selection', async () => {
		const view = render(ExportDialog, { open: true, themes, filenameBase: 'commune-44109', onclose: vi.fn() });
		const variation = view.getByText('Variation').closest('label')?.querySelector('input');
		if (!variation) throw new Error('Missing variation checkbox');
		await fireEvent.click(variation);
		await fireEvent.click(view.getByRole('button', { name: /Télécharger/ }));
		const exportedRows = vi.mocked(downloadCsv).mock.calls[0][1];
		expect(exportedRows.some((row) => row[1] === 'Variation')).toBe(false);
	});

	// R-01 (review of 2026-11-10): the dialog said `aria-modal` on a plain `div`. It is a native modal
	// `<dialog>` like the source panel: the focus enters it, stays in it and goes back where it was.
	describe('R-01 — a modal that takes, keeps and gives back the focus', () => {
		let declencheur: HTMLButtonElement;
		afterEach(() => declencheur?.remove());

		function ouvrirDepuisUnBouton() {
			declencheur = document.createElement('button');
			declencheur.textContent = 'Ouvrir';
			document.body.append(declencheur);
			declencheur.focus();
			const onclose = vi.fn();
			const view = render(ExportDialog, { open: true, themes, filenameBase: 'commune-44109', onclose });
			return { declencheur, onclose, view };
		}

		it('is a native dialog, opened as a modal when the browser can', () => {
			const showModal = vi.fn(function (this: HTMLDialogElement) {
				this.setAttribute('open', '');
			});
			Object.assign(HTMLDialogElement.prototype, { showModal });
			try {
				const { view } = ouvrirDepuisUnBouton();
				const dialogue = view.container.querySelector('dialog.export-dialog');
				expect(dialogue).not.toBeNull();
				expect(showModal).toHaveBeenCalledOnce();
				expect(dialogue?.getAttribute('aria-labelledby')).toBe(view.getByRole('heading', { name: 'Exporter les données' }).id);
			} finally {
				delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal;
			}
		});

		it('moves the focus into the dialog when it opens', () => {
			const { view } = ouvrirDepuisUnBouton();
			expect(view.container.querySelector('.export-dialog')?.contains(document.activeElement)).toBe(true);
		});

		it('Tab from the last control comes back to the first, Shift+Tab from the first goes to the last', async () => {
			const { view } = ouvrirDepuisUnBouton();
			const premier = view.getByRole('button', { name: 'Fermer' });
			const dernier = view.getByRole('button', { name: /Télécharger/ });
			dernier.focus();
			await fireEvent.keyDown(dernier, { key: 'Tab' });
			expect(document.activeElement).toBe(premier);
			await fireEvent.keyDown(premier, { key: 'Tab', shiftKey: true });
			expect(document.activeElement).toBe(dernier);
		});

		it.each([
			['« Annuler »', 'Annuler', 'clic'],
			['« Fermer »', 'Fermer', 'clic'],
			['Escape', 'Annuler', 'echap']
		] as const)('%s closes and gives the focus back to what opened the dialog', async (_nom, cible, geste) => {
			const { declencheur, onclose, view } = ouvrirDepuisUnBouton();
			// The visitor is inside the dialog when they close it.
			view.getByRole('button', { name: 'Annuler' }).focus();
			const bouton = view.getByRole('button', { name: cible });
			await (geste === 'clic' ? fireEvent.click(bouton) : fireEvent.keyDown(bouton, { key: 'Escape' }));
			expect(onclose).toHaveBeenCalledOnce();
			expect(document.activeElement).toBe(declencheur);
		});
	});

	it('exports a legacy unavailable row as not reported without inventing a reason', async () => {
		const legacy = [{ ...themes[0], rows: [{ label: 'Donnée historique', value: 'Non disponible', available: false }] }];
		const view = render(ExportDialog, { open: true, themes: legacy, filenameBase: 'legacy', onclose: vi.fn() });
		await fireEvent.click(view.getByRole('button', { name: /Télécharger/ }));
		expect(vi.mocked(downloadCsv).mock.calls[0][1]).toEqual([
			['Marché', 'Donnée historique', 'Non disponible', 'not_reported', 'Maille non renseignée', '']
		]);
	});
});
