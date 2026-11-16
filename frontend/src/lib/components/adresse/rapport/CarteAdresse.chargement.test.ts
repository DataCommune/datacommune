// The interactive map is not in the first view of the fiche (plan 04.10-12, weight of the first view):
// its dialog module is asked only when the visitor goes for the button, and a module that cannot be
// fetched leaves the button and a line that says so, never a click without effect.
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CarteRapport } from '$lib/api/rapport';

const chargerDialogue = vi.fn();
vi.mock('$lib/components/adresse/carte/chargeur-dialogue', () => ({ chargerDialogue }));

const { default: CarteAdresse } = await import('./CarteAdresse.svelte');
const { GABARITS } = await import('$lib/data/parcel-map');

const CARTE: CarteRapport = {
	plan: '/carte/44109_zz01_00002/plan.png',
	parcelles: '/carte/44109_zz01_00002/parcelles.png',
	empriseM: 300,
	attribution: '© IGN (Plan IGN, Parcellaire Express)',
	licence: 'Licence Ouverte 2.0',
	geoportail: null
};
const PROPS = { carte: CARTE, adresse: '12 rue des Lilas, Nantes', banId: '44109_9999_00002' };
const bouton = () => screen.getByRole('button', { name: 'Ouvrir la carte des parcelles' });

describe('CarteAdresse — dialog of the map loaded on demand (04.10-12)', () => {
	beforeEach(() => {
		chargerDialogue.mockReset();
	});
	afterEach(() => cleanup());

	it('asks nothing of the dialog module at mount', () => {
		render(CarteAdresse, PROPS);
		expect(chargerDialogue).not.toHaveBeenCalled();
	});

	it.each(['pointerEnter', 'focus'] as const)('starts fetching the dialog module on %s of the button', async (evenement) => {
		chargerDialogue.mockReturnValue(new Promise(() => {}));
		render(CarteAdresse, PROPS);
		await fireEvent[evenement](bouton());
		expect(chargerDialogue).toHaveBeenCalledTimes(1);
	});

	it('a module that cannot be fetched leaves the button and says it, without a dialog', async () => {
		chargerDialogue.mockRejectedValue(new TypeError('Failed to fetch dynamically imported module'));
		const { container } = render(CarteAdresse, PROPS);
		await fireEvent.click(bouton());
		const etat = await screen.findByRole('status');
		expect(etat.textContent).toBe(GABARITS['T-43']);
		expect(container.querySelector('dialog')).toBeNull();
		expect(bouton()).toBeTruthy();
		expect(bouton().getAttribute('aria-describedby')).toBe(etat.id);
	});
});
