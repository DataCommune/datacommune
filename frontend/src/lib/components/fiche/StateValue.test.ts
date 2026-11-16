import { render } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import StateValue from './StateValue.svelte';

// The four served states, rendered by the one component (UI-SPEC « Les quatre états servis »).
// Assertions read the rendered text: a technical key or a stand-in 0 must never reach it.

const CLES_TECHNIQUES = /out_of_coverage|not_reported|insufficient_sample|not_available|zero_denominator|temporary_error|territoire_hors_perimetre/;

describe('StateValue — zéro', () => {
	it('renders a served 0 as a value, with its unit, never as an absence', () => {
		const { container } = render(StateValue, { etat: { kind: 'valeur', texte: '0', unite: '%' } });
		expect(container.querySelector('[data-state="value"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('0 %');
		expect(container.querySelector('.state-badge')).toBeNull();
	});
});

describe('StateValue — manque', () => {
	it('renders a neutral badge carrying the served sub-state, and its sentence as help', () => {
		const { container, getByText } = render(StateValue, {
			etat: { kind: 'absence', etat: 'insufficient_sample', motif: '3 ventes, seuil : 5.', effectif: 3 }
		});
		const badge = getByText('Effectif insuffisant');
		expect(badge.classList.contains('state-badge')).toBe(true);
		expect(badge.classList.contains('state-badge-warn')).toBe(false);
		expect(getByText("Trop peu d'observations (3) pour publier une valeur.")).toBeTruthy();
		expect(container.textContent).not.toMatch(/(^|\s)0(\s|$)/);
		expect(container.textContent).not.toMatch(/introuvable/i);
		expect(container.textContent).not.toMatch(CLES_TECHNIQUES);
	});

	it.each([
		['not_reported', 'Non renseigné', 'La source ne fournit pas cette valeur pour cette commune.'],
		['not_available', 'Non publiée', 'La source ne publie pas cette valeur pour ce territoire.'],
		['zero_denominator', 'Sans base de calcul', 'Aucun élément ne permet de calculer ce taux.']
	] as const)('%s → « %s »', (etat, libelle, phrase) => {
		const { getByText, container } = render(StateValue, { etat: { kind: 'absence', etat, motif: 'Motif servi.', effectif: null } });
		expect(getByText(libelle).classList.contains('state-badge-warn')).toBe(false);
		expect(getByText(phrase)).toBeTruthy();
		expect(container.textContent).not.toMatch(CLES_TECHNIQUES);
	});

	it('can render the badge alone, without the help sentence', () => {
		const { container } = render(StateValue, {
			etat: { kind: 'absence', etat: 'not_reported', motif: 'x', effectif: null }, aide: false
		});
		expect(container.textContent?.trim()).toBe('Non renseigné');
	});

	it('a decided reason of a value not published is its help sentence (04-DECISIONS-TEXTES row 44)', () => {
		const { container } = render(StateValue, {
			etat: { kind: 'absence', etat: 'not_available', motif: 'adresse_non_verifiable', effectif: null }
		});
		expect(container.querySelector('.state-badge')?.textContent).toBe('Non publiée');
		expect(container.querySelector('.state-help')?.textContent).toBe(
			"L'adresse publiée par l'Arcep ne correspond pas exactement à celle de la BAN ; l'éligibilité n'est pas affichée."
		);
	});

	it('a decided label replaces the glossary one, the served state stays (④, 04-DECISIONS-TEXTES row 39)', () => {
		const { container } = render(StateValue, {
			etat: { kind: 'absence', etat: 'insufficient_sample', motif: '', effectif: null },
			libelle: 'Effectif insuffisant pour les maisons et les appartements',
			aide: false
		});
		expect(container.querySelector('.state-badge')?.textContent).toBe('Effectif insuffisant pour les maisons et les appartements');
		expect(container.querySelector('[data-state]')?.getAttribute('data-state')).toBe('insufficient_sample');
	});
});

describe('StateValue — hors couverture', () => {
	it('renders a warn badge carrying the label of its reason, and the reason sentence', () => {
		const { getByText, container } = render(StateValue, {
			etat: { kind: 'absence', etat: 'out_of_coverage', motif: 'source_sans_couverture', effectif: null }
		});
		const badge = getByText('Hors couverture · non couvert par la source');
		expect(badge.classList.contains('state-badge-warn')).toBe(true);
		expect(getByText('La source ne couvre pas ce territoire.')).toBeTruthy();
		expect(container.textContent).not.toMatch(CLES_TECHNIQUES);
	});

	it('says « Hors couverture » when the reason is not one of the three contract reasons', () => {
		const { getByText } = render(StateValue, {
			etat: { kind: 'absence', etat: 'out_of_coverage', motif: 'Bloc DPE hors couverture.', effectif: null }
		});
		expect(getByText('Hors couverture').classList.contains('state-badge-warn')).toBe(true);
	});
});

describe('StateValue — aide écrite par l’interface, état inconnu', () => {
	it('shows the interface help in place of the glossary sentence when the model gives one', () => {
		const aide = 'Tendance datée non publiée par cette instance : les indicateurs DVF datés ne sont pas servis.';
		const { getByText, queryByText } = render(StateValue, {
			etat: { kind: 'absence', etat: 'not_available', motif: aide, effectif: null, aide }
		});
		expect(getByText('Non publiée')).toBeTruthy();
		expect(getByText(aide)).toBeTruthy();
		expect(queryByText('La source ne publie pas cette valeur pour ce territoire.')).toBeNull();
	});

	it('refuses a state the glossary does not know: nothing rendered, named on the console, never « Non renseigné »', () => {
		const erreur = vi.spyOn(console, 'error').mockImplementation(() => {});
		const { container } = render(StateValue, {
			etat: { kind: 'absence', etat: 'etat_inconnu' as never, motif: 'x', effectif: null }
		});
		expect(container.textContent?.trim()).toBe('');
		expect(container.querySelector('.state-badge')).toBeNull();
		expect(erreur).toHaveBeenCalledWith(expect.stringContaining('etat_inconnu'));
		erreur.mockRestore();
	});
});

describe('StateValue — panne', () => {
	it('words an outage « Indisponible », distinct from out of coverage', () => {
		const { getByText, queryByText } = render(StateValue, {
			etat: { kind: 'absence', etat: 'temporary_error' as never, motif: '', effectif: null }
		});
		expect(getByText('Indisponible')).toBeTruthy();
		expect(queryByText(/Hors couverture/)).toBeNull();
	});
});
