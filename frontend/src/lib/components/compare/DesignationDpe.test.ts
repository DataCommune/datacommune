import { fireEvent, render, within } from '@testing-library/svelte';
import { tick } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DocumentDpeCarte, DocumentsDpeAdresse, IssueDocumentsDpe } from '$lib/api/adresses';

// Designation of a DPE on a compared address (UI-SPEC « Désignation d'un DPE sur une adresse
// comparée », D-102, 04-11): the documents served for the address in a group of radio buttons,
// nothing chosen for the visitor, the number handed to the page (the link is the only state), the
// focus back on « Désigner un DPE » when the group closes.

const lecture = vi.hoisted(() => ({ documents: vi.fn() }));
vi.mock('$lib/api/adresses', async (original) => ({
	...(await original<typeof import('$lib/api/adresses')>()),
	getDocumentsDpe: (...args: unknown[]) => lecture.documents(...args)
}));

import DesignationDpe from './DesignationDpe.svelte';
import source from './DesignationDpe.svelte?raw';

const BAN = '85191_0420_00012';
const ADRESSE = '12 Rue des Lilas, 85000 La Roche-sur-Yon';

function carte(numero: string, partiel: Partial<DocumentDpeCarte> = {}): DocumentDpeCarte {
	return {
		numero,
		etiquette_dpe: 'D',
		etiquette_ges: 'C',
		type_batiment: 'maison',
		surface_habitable: 79,
		date_etablissement: '2024-03-01',
		date_fin_validite: '2034-02-28',
		badges: [],
		adresse_declaree: null,
		...partiel
	};
}

function page(documents: DocumentDpeCarte[], numero = 1, pages = 1, total = documents.length): IssueDocumentsDpe {
	const lus: DocumentsDpeAdresse = {
		etat: 'value',
		motif: null,
		source: null,
		total,
		page: numero,
		pages,
		par_page: 10,
		generation: '0123456789abcdef',
		distribution: null,
		date_import: '2026-09-30',
		documents,
		selection: null,
		signal_plusieurs_logements: null,
		constat_d110: null
	};
	return { kind: 'ok', documents: lus };
}

const texte = (element: Element | null) => element?.textContent?.replace(/\s+/g, ' ').trim() ?? null;

beforeEach(() => lecture.documents.mockReset());
afterEach(() => vi.restoreAllMocks());

describe('DesignationDpe', () => {
	it('opens under the header the documents served for the address, nothing chosen, and hands the number over', async () => {
		lecture.documents.mockResolvedValue(
			page([carte('2485E0000003C', { badges: ['plus_recent'] }), carte('2285E0000002B', { etiquette_dpe: null, date_etablissement: null })])
		);
		const ondesigner = vi.fn();
		const vue = render(DesignationDpe, { banId: BAN, adresse: ADRESSE, designation: null, ondesigner });
		const bouton = vue.getByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` });
		expect(bouton).toHaveAttribute('aria-expanded', 'false');
		expect(lecture.documents).not.toHaveBeenCalled();

		await fireEvent.click(bouton);
		await vi.waitFor(() => expect(vue.getAllByRole('radio')).toHaveLength(2));
		expect(bouton).toHaveAttribute('aria-expanded', 'true');
		expect(lecture.documents).toHaveBeenCalledWith(fetch, BAN, '1', null);
		const groupe = vue.getByRole('group', { name: `Désigner un DPE pour ${ADRESSE}` });
		const options = within(groupe).getAllByRole('radio');
		// The radio buttons are named by the decided legend (04-DECISIONS-TEXTES row 66).
		expect(within(groupe).getByRole('group', { name: 'Documents DPE déclarés à cette adresse' })).toBeInTheDocument();
		// The label of the contract, and the served badges; a part the API did not serve is left out.
		expect(texte(options[0].closest('label'))).toBe('DPE n° 2485E0000003C · classe D · mars 2024 Le plus récent');
		expect(texte(options[1].closest('label'))).toBe('DPE n° 2285E0000002B');
		// Nothing is chosen for the visitor: no radio checked, « Désigner ce DPE » disabled.
		expect(options.every((option) => !(option as HTMLInputElement).checked)).toBe(true);
		const designer = vue.getByRole('button', { name: 'Désigner ce DPE' });
		expect(designer).toBeDisabled();

		await fireEvent.click(options[1]);
		expect(designer).toBeEnabled();
		await fireEvent.click(designer);
		expect(ondesigner).toHaveBeenCalledWith('2285E0000002B');
	});

	it('closes on a second click or Escape and gives the focus back to « Désigner un DPE »', async () => {
		lecture.documents.mockResolvedValue(page([carte('2485E0000003C')]));
		const vue = render(DesignationDpe, { banId: BAN, adresse: ADRESSE, designation: null });
		const bouton = vue.getByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` });
		await fireEvent.click(bouton);
		const radio = await vi.waitFor(() => vue.getByRole('radio'));
		radio.focus();
		await fireEvent.keyDown(radio, { key: 'Escape' });
		await tick();
		expect(vue.queryByRole('radio')).toBeNull();
		expect(bouton).toHaveAttribute('aria-expanded', 'false');
		expect(document.activeElement).toBe(bouton);

		await fireEvent.click(bouton);
		await vi.waitFor(() => vue.getByRole('radio'));
		await fireEvent.click(bouton);
		await tick();
		expect(vue.queryByRole('radio')).toBeNull();
		expect(document.activeElement).toBe(bouton);
		// The list is read once: closing and opening again does not read it again.
		expect(lecture.documents).toHaveBeenCalledTimes(1);
	});

	it('reads ten documents per page; « Documents suivants » reads the next one under the same snapshot', async () => {
		const premiers = Array.from({ length: 10 }, (_, index) => carte(`2485E00000${String(index).padStart(2, '0')}A`));
		lecture.documents.mockResolvedValueOnce(page(premiers, 1, 2, 12)).mockResolvedValueOnce(page([carte('2285E0000099B'), carte('2285E0000098B')], 2, 2, 12));
		const vue = render(DesignationDpe, { banId: BAN, adresse: ADRESSE, designation: null });
		await fireEvent.click(vue.getByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` }));
		await vi.waitFor(() => expect(vue.getAllByRole('radio')).toHaveLength(10));

		await fireEvent.click(vue.getByRole('button', { name: 'Documents suivants' }));
		await vi.waitFor(() => expect(vue.getAllByRole('radio')).toHaveLength(12));
		expect(lecture.documents).toHaveBeenLastCalledWith(fetch, BAN, '2', '0123456789abcdef');
		expect(vue.queryByRole('button', { name: 'Documents suivants' })).toBeNull();
		await vi.waitFor(() => expect(document.activeElement).toBe(vue.getAllByRole('radio')[10]));
	});

	it('says no document, or an outage with « Recharger les documents DPE », never an empty group', async () => {
		lecture.documents.mockResolvedValueOnce(page([], 1, 1, 0));
		const vide = render(DesignationDpe, { banId: BAN, adresse: ADRESSE, designation: null });
		await fireEvent.click(vide.getByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` }));
		await vi.waitFor(() => expect(vide.getByText('Aucun DPE depuis juillet 2021 à cette adresse ; les diagnostics plus anciens ne sont plus valables.')).toBeInTheDocument());
		expect(vide.queryByRole('button', { name: 'Désigner ce DPE' })).toBeNull();
		vide.unmount();

		lecture.documents.mockResolvedValueOnce({ kind: 'panne', panne: { statut: 503 } } as never).mockResolvedValueOnce(page([carte('2485E0000003C')]));
		const panne = render(DesignationDpe, { banId: BAN, adresse: ADRESSE, designation: null });
		await fireEvent.click(panne.getByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` }));
		const alerte = await vi.waitFor(() => panne.getByRole('alert'));
		expect(texte(alerte)).toBe("Les documents DPE n'ont pas pu être lus. Le reste de la page reste affiché.");
		await fireEvent.click(panne.getByRole('button', { name: 'Recharger les documents DPE' }));
		await vi.waitFor(() => expect(panne.getAllByRole('radio')).toHaveLength(1));
	});

	it('once designated: the header names the DPE, « Retirer la désignation » without confirmation', async () => {
		const onretirer = vi.fn();
		const vue = render(DesignationDpe, {
			banId: BAN,
			adresse: ADRESSE,
			designation: { numero: '2185E0000001A', texte: 'DPE désigné : n° 2185E0000001A · classe D · mai 2020' },
			onretirer
		});
		expect(texte(vue.container.querySelector('.designation-texte'))).toBe('DPE désigné : n° 2185E0000001A · classe D · mai 2020');
		expect(vue.queryByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` })).toBeNull();
		await fireEvent.click(vue.getByRole('button', { name: `Retirer la désignation du DPE pour ${ADRESSE}` }));
		expect(onretirer).toHaveBeenCalledTimes(1);
		expect(lecture.documents).not.toHaveBeenCalled();
	});

	it('focaliser() puts the focus on the visible control', async () => {
		const vue = render(DesignationDpe, { banId: BAN, adresse: ADRESSE, designation: null });
		expect(vue.component.focaliser()).toBe(true);
		expect(document.activeElement).toBe(vue.getByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` }));
		await vue.rerender({ banId: BAN, adresse: ADRESSE, designation: { numero: '2185E0000001A', texte: 'DPE désigné : n° 2185E0000001A' } });
		expect(vue.component.focaliser()).toBe(true);
		expect(document.activeElement).toBe(vue.getByRole('button', { name: `Retirer la désignation du DPE pour ${ADRESSE}` }));
	});

	it('D-15 FRONT WR-01: designating then removing the designation leaves the group closed, nothing chosen', async () => {
		lecture.documents.mockResolvedValue(page([carte('2485E0000003C'), carte('2285E0000002B')]));
		const ondesigner = vi.fn();
		const vue = render(DesignationDpe, { banId: BAN, adresse: ADRESSE, designation: null, ondesigner });
		await fireEvent.click(vue.getByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` }));
		const options = await vi.waitFor(() => {
			const radios = vue.getAllByRole('radio');
			expect(radios).toHaveLength(2);
			return radios;
		});
		await fireEvent.click(options[1]);
		await fireEvent.click(vue.getByRole('button', { name: 'Désigner ce DPE' }));
		expect(ondesigner).toHaveBeenCalledWith('2285E0000002B');

		// The page writes the link, then the visitor removes the designation.
		await vue.rerender({ banId: BAN, adresse: ADRESSE, designation: { numero: '2285E0000002B', texte: 'DPE désigné : n° 2285E0000002B' } });
		await vue.rerender({ banId: BAN, adresse: ADRESSE, designation: null });
		await tick();

		expect(vue.queryAllByRole('radio')).toHaveLength(0);
		expect(vue.queryByRole('button', { name: 'Désigner ce DPE' })).toBeNull();
		const bouton = vue.getByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` });
		expect(bouton).toHaveAttribute('aria-expanded', 'false');

		// Opened again: the list is read again (a newer publication), nothing chosen.
		await fireEvent.click(bouton);
		const relues = await vi.waitFor(() => {
			const radios = vue.getAllByRole('radio');
			expect(radios).toHaveLength(2);
			return radios;
		});
		expect(relues.every((option) => !(option as HTMLInputElement).checked)).toBe(true);
		expect(vue.getByRole('button', { name: 'Désigner ce DPE' })).toBeDisabled();
		expect(lecture.documents).toHaveBeenCalledTimes(2);
	});

	it('D-15 FRONT WR-02: the open group is carried into the row the table gives, and told open then closed', async () => {
		lecture.documents.mockResolvedValue(page([carte('2485E0000003C')]));
		const cible = document.createElement('div');
		document.body.appendChild(cible);
		const onbasculer = vi.fn();
		const vue = render(DesignationDpe, { banId: BAN, adresse: ADRESSE, designation: null, cible, onbasculer });
		const bouton = vue.getByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` });
		expect(onbasculer).toHaveBeenLastCalledWith(false);

		await fireEvent.click(bouton);
		await vi.waitFor(() => expect(within(cible).getAllByRole('radio')).toHaveLength(1));
		expect(onbasculer).toHaveBeenLastCalledWith(true);
		expect(vue.container.querySelector('.designation-groupe')).toBeNull();
		// The number on its own line, the details after it; one text for every reader.
		const label = within(cible).getByRole('radio').closest('label')!;
		expect(texte(label.querySelector('.designation-numero'))).toBe('DPE n° 2485E0000003C');
		expect(texte(label)).toBe('DPE n° 2485E0000003C · classe D · mars 2024');

		await fireEvent.keyDown(within(cible).getByRole('radio'), { key: 'Escape' });
		await tick();
		expect(cible.querySelector('.designation-groupe')).toBeNull();
		expect(onbasculer).toHaveBeenLastCalledWith(false);
		expect(document.activeElement).toBe(bouton);
		cible.remove();
	});

	it('D-15 FRONT WR-04: documents out of coverage are a state, a refusal keeps its own message', async () => {
		const hors = page([]);
		if (hors.kind === 'ok') hors.documents = { ...hors.documents, etat: 'out_of_coverage', motif: 'dataset_non_heberge', total: null };
		lecture.documents.mockResolvedValueOnce(hors);
		const vue = render(DesignationDpe, { banId: BAN, adresse: ADRESSE, designation: null });
		await fireEvent.click(vue.getByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` }));
		await vi.waitFor(() => expect(vue.getByText(/Hors couverture/)).toBeInTheDocument());
		expect(vue.queryByRole('alert')).toBeNull();
		expect(vue.queryByRole('button', { name: 'Recharger les documents DPE' })).toBeNull();
		vue.unmount();

		lecture.documents.mockResolvedValueOnce({ kind: 'panne', panne: { statut: 429, titre: 'Limite atteinte', message: 'Trop de demandes : réessayez dans une minute.' } } as never);
		const refus = render(DesignationDpe, { banId: BAN, adresse: ADRESSE, designation: null });
		await fireEvent.click(refus.getByRole('button', { name: `Désigner un DPE pour ${ADRESSE}` }));
		const alerte = await vi.waitFor(() => refus.getByRole('alert'));
		expect(texte(alerte)).toBe('Trop de demandes : réessayez dans une minute.');
	});

	it('keeps no designation in any storage, gives its actions 44 px and no accent background', () => {
		expect(source).not.toMatch(/localStorage|sessionStorage/);
		expect(source).toMatch(/\.designation-action \{[^}]*min-height: 44px;/);
		expect(source).toMatch(/\.designation-secondaire \{[^}]*min-height: 44px;/);
		expect(source).not.toMatch(/background: var\(--color-accent/);
	});
});
