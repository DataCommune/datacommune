import { fireEvent, render } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const runtime = vi.hoisted(() => ({ goto: vi.fn() }));
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => runtime.goto(...args) }));

import ChoixArrondissements from './ChoixArrondissements.svelte';

// Entry to the comparison of arrondissements (Paris, Lyon, Marseille), asked by the owner on 2026-11-10:
// a folded GET form of `/comparer` on the fiche of the commune and on the fiche of an arrondissement.
// Without JavaScript the checked boxes travel as repeated `communes` parameters, which the comparator
// already reads; with it, the link is written in its usual form and the choice is held to two to four.

const PARIS = { code: '75056', nom: 'Paris' };
const LYON = { code: '69123', nom: 'Lyon' };

const cases = (container: HTMLElement) => [...container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
const caseDe = (container: HTMLElement, code: string) => cases(container).find((element) => element.value === code)!;

beforeEach(() => runtime.goto.mockReset());

describe('ChoixArrondissements', () => {
	it('is a folded native form of the comparator, one box per arrondissement of the commune', () => {
		const { container } = render(ChoixArrondissements, { ville: PARIS });
		const pli = container.querySelector('details')!;
		expect(pli.open).toBe(false);
		expect(pli.querySelector('summary')?.textContent?.trim()).toBe('Comparer des arrondissements de Paris');
		const formulaire = pli.querySelector('form')!;
		expect(formulaire.getAttribute('method')).toBe('GET');
		expect(formulaire.getAttribute('action')).toBe('/comparer');
		expect(cases(container).map((element) => element.value)).toEqual(Array.from({ length: 20 }, (_, i) => String(75101 + i)));
		expect(cases(container).every((element) => element.name === 'communes' && !element.checked)).toBe(true);
		expect(formulaire.querySelector('button[type="submit"]')?.textContent?.trim()).toBe('Comparer');
	});

	it('names each box by its rank, read whole by a screen reader', () => {
		const { getByRole } = render(ChoixArrondissements, { ville: LYON });
		expect(getByRole('group', { name: 'Arrondissements de Lyon' })).toBeTruthy();
		expect(getByRole('checkbox', { name: '1er arrondissement' })).toBeTruthy();
		expect(getByRole('checkbox', { name: '9e arrondissement' })).toBeTruthy();
	});

	it('on the fiche of an arrondissement, its own box is checked', () => {
		const { container } = render(ChoixArrondissements, { ville: PARIS, courant: '75111' });
		expect(cases(container).filter((element) => element.checked).map((element) => element.value)).toEqual(['75111']);
	});

	it('writes the link of the comparator, the arrondissement of the fiche first, then in the order of the list', async () => {
		const { container } = render(ChoixArrondissements, { ville: PARIS, courant: '75111' });
		await fireEvent.click(caseDe(container, '75118'));
		await fireEvent.click(caseDe(container, '75103'));
		await fireEvent.submit(container.querySelector('form')!);
		expect(runtime.goto).toHaveBeenCalledWith('/comparer?communes=75111,75103,75118');
	});

	it('holds the choice to two to four: the button waits for a second box, the other boxes close at four', async () => {
		const { container, getByRole } = render(ChoixArrondissements, { ville: PARIS });
		const bouton = getByRole('button', { name: 'Comparer' }) as HTMLButtonElement;
		expect(bouton.disabled).toBe(true);
		await fireEvent.click(caseDe(container, '75101'));
		expect(bouton.disabled).toBe(true);
		await fireEvent.click(caseDe(container, '75102'));
		expect(bouton.disabled).toBe(false);
		await fireEvent.click(caseDe(container, '75103'));
		await fireEvent.click(caseDe(container, '75104'));
		expect(cases(container).filter((element) => element.disabled).map((element) => element.value)).toHaveLength(16);
		expect(caseDe(container, '75104').disabled).toBe(false);
		// Unchecking one opens the others again.
		await fireEvent.click(caseDe(container, '75104'));
		expect(cases(container).some((element) => element.disabled)).toBe(false);
	});

	it('renders nothing for a commune without arrondissements', () => {
		const { container } = render(ChoixArrondissements, { ville: { code: '44109', nom: 'Nantes' } });
		expect(container.querySelector('details')).toBeNull();
	});
});
