// GET form of the similar sales (04-05; UI-SPEC « /adresse/[id] » point 5, « Réactivité, impression,
// accessibilité »; D-114, Q6). No type is ever checked by default: only the type read in the URL
// checks its radio. Visible labels, `fieldset`/`legend`, the UI-SPEC defaults, the other lists kept
// by hidden fields, the secondary button « Afficher les ventes ressemblantes ».
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import { lireCriteres } from '$lib/data/adresse-ventes';
import ComparablesForm from './ComparablesForm.svelte';

const formulaire = (search: string, caches: [string, string][] = []) =>
	render(ComparablesForm, { criteres: lireCriteres(new URLSearchParams(search)), caches });

const choisi = (container: HTMLElement, nom: string) =>
	(container.querySelector(`select[name="${nom}"]`) as HTMLSelectElement).value;

describe('ComparablesForm — critères visibles et modifiables', () => {
	it('a GET form to the anchor of the section, the secondary button of the contract', () => {
		const { container, getByRole } = formulaire('');

		const form = container.querySelector('form')!;
		expect(form.getAttribute('method')).toBe('get');
		expect(form.getAttribute('action')).toBe('#ventes-ressemblantes');
		const bouton = getByRole('button', { name: 'Afficher les ventes ressemblantes' });
		expect(bouton.getAttribute('type')).toBe('submit');
	});

	it('« Type » is a fieldset with its legend; no radio is checked without a type in the URL (Q6)', () => {
		const { container, getByRole } = formulaire('');

		const groupe = getByRole('group', { name: 'Type' });
		expect(groupe.tagName).toBe('FIELDSET');
		const radios = [...container.querySelectorAll<HTMLInputElement>('input[type="radio"][name="type"]')];
		expect(radios.map((radio) => radio.value)).toEqual(['maison', 'appartement']);
		expect(radios.some((radio) => radio.checked)).toBe(false);
		expect(getByRole('radio', { name: 'Maison' })).toBeTruthy();
		expect(getByRole('radio', { name: 'Appartement' })).toBeTruthy();
	});

	it('the type of the URL, and only it, checks its radio', () => {
		const { getByRole } = formulaire('type=appartement');

		expect((getByRole('radio', { name: 'Appartement' }) as HTMLInputElement).checked).toBe(true);
		expect((getByRole('radio', { name: 'Maison' }) as HTMLInputElement).checked).toBe(false);
	});

	it('visible labels and the UI-SPEC defaults: surface empty, ± 20 %, 500 m, 5 years', () => {
		const { container, getByLabelText } = formulaire('');

		expect((getByLabelText('Surface (m²)') as HTMLInputElement).value).toBe('');
		expect(getByLabelText('Tolérance').tagName).toBe('SELECT');
		expect(getByLabelText('Rayon').tagName).toBe('SELECT');
		expect(getByLabelText('Période').tagName).toBe('SELECT');
		expect([choisi(container, 'tolerance'), choisi(container, 'rayon'), choisi(container, 'periode')]).toEqual(['20', '500', '5']);
		const options = (nom: string) => [...container.querySelectorAll(`select[name="${nom}"] option`)].map((o) => o.textContent?.replace(/ /g, ' '));
		expect(options('tolerance')).toEqual(['± 10 %', '± 20 %', '± 30 %']);
		expect(options('rayon')).toEqual(['250 m', '500 m', '1 km', 'toute la commune']);
		expect(options('periode')).toEqual(['1 an', '3 ans', '5 ans']);
	});

	it('the criteria of the URL pre-fill the form, the shareable state of the page', () => {
		const { container, getByLabelText } = formulaire('type=maison&surface=90&tolerance=10&rayon=1000&periode=3');

		expect((getByLabelText('Surface (m²)') as HTMLInputElement).value).toBe('90');
		expect([choisi(container, 'tolerance'), choisi(container, 'rayon'), choisi(container, 'periode')]).toEqual(['10', '1000', '3']);
	});

	it('the other lists keep their place through hidden fields', () => {
		const { container } = formulaire('', [
			['page', '2'],
			['g', '0123456789abcdef'],
			['ventes_page', '3'],
			['vg', 'fedcba9876543210']
		]);

		const caches = [...container.querySelectorAll<HTMLInputElement>('input[type="hidden"]')].map((input) => [input.name, input.value]);
		expect(caches).toEqual([
			['page', '2'],
			['g', '0123456789abcdef'],
			['ventes_page', '3'],
			['vg', 'fedcba9876543210']
		]);
	});

	it('no criterion is evaluated here: the form has no handler, it only submits', () => {
		const { container } = formulaire('type=maison');
		expect(container.querySelector('form')?.getAttribute('onsubmit')).toBeNull();
	});
});

describe('ComparablesForm — provenance du préremplissage (UI-007, plan 04.5-06)', () => {
	const NB = '\u00a0';
	const avec = (props: Record<string, unknown>) => render(ComparablesForm, { criteres: lireCriteres(new URLSearchParams('')), ...props });

	it('a surface prefilled from the served selection: « Surface issue du DPE n° … », linked to its page, with its qualification', () => {
		const { container, getByRole } = avec({
			provenance: { numero: '2485E0000003C', qualification: 'expiré', type: false }
		});

		const lien = getByRole('link', { name: `Surface issue du DPE n°${NB}2485E0000003C` });
		expect(lien.getAttribute('href')).toBe('/dpe/2485E0000003C');
		expect(container.querySelector('.comparables-origine')?.textContent).toBe(
			`Surface issue du DPE n°${NB}2485E0000003C, le plus récent de cette adresse (expiré).`
		);
	});

	it('type and surface prefilled: the origin of the type, then the document of the surface', () => {
		const { container } = avec({
			provenance: { numero: '2485E0000003C', qualification: null, type: true }
		});

		expect(container.querySelector('.comparables-origine')?.textContent).toBe(
			`Type d'après les DPE de cette adresse. Surface issue du DPE n°${NB}2485E0000003C, le plus récent de cette adresse.`
		);
	});

	it('the type alone keeps its sentence, without any number; nothing prefilled, no provenance', () => {
		const seul = avec({ origine: "Type d'après les DPE de cette adresse.", provenance: null });
		expect(seul.container.querySelector('.comparables-origine')?.textContent).toBe("Type d'après les DPE de cette adresse.");
		expect(seul.container.querySelector('.comparables-origine a')).toBeNull();
		seul.unmount();
		const rien = avec({});
		expect(rien.container.querySelector('.comparables-origine')).toBeNull();
	});
});
