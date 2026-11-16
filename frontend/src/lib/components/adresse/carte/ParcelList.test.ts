// List of the parcels shown (04.10 UI contract § 2.6, 04.10-08): the path without the map. The same
// information as the map (reference, surface, sales when there are some), ordered from the parcel of the
// address outwards, chosen by a button; the selected line is marked by a word for screen readers
// (`aria-current`) and a check, never by a colour alone. No parcel, cadastre out of coverage and reading
// are three displays (SEM-01).
import { readFileSync } from 'node:fs';
import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { ContexteCarte, ParcelleCarte } from '$lib/api/carte-parcelles';
import ParcelList from './ParcelList.svelte';

const NB = String.fromCharCode(0xa0);
const ADRESSE = '00000000ZA0001';

function parcelle(id: string, etiquette: [number, number], contenance: number | null, ventes: number | null): ParcelleCarte {
	const [lon, lat] = etiquette;
	return {
		type: 'Feature',
		id,
		geometry: {
			type: 'Polygon',
			coordinates: [[[lon - 0.0001, lat - 0.0001], [lon + 0.0001, lat - 0.0001], [lon + 0.0001, lat + 0.0001], [lon - 0.0001, lat + 0.0001], [lon - 0.0001, lat - 0.0001]]]
		},
		properties: { id_parcelle: id, contenance, etiquette, ventes }
	};
}

// The four parcels of the contract example (`tuile-parcelles.json`), given out of order.
const EXEMPLE = [
	parcelle('00000000ZA0004', [-3.50045, 46.5], 380, 1),
	parcelle('00000000ZA0002', [-3.49955, 46.5], 612, 0),
	parcelle('00000000ZA0001', [-3.5, 46.5], 450, 4),
	parcelle('00000000ZA0003', [-3.5, 46.50028], null, 1)
];

const CONTEXTE: ContexteCarte = {
	centre: { lon: -3.5, lat: 46.5 },
	emprise: { ouest: -3.513733, sud: 46.490546, est: -3.486267, nord: 46.509452 },
	parcelle_adresse: { etat: 'value', id_parcelle: ADRESSE, motif: null, nombre_parcelles: null },
	parcelles: { etat: 'value', motif: null },
	ventes: { etat: 'value', motif: null, periode: '2021-2025', annee_debut: 2021 },
	cadastre_millesime: '2026-07',
	fond: { attribution: '© IGN (Plan IGN)', licence: 'Licence Ouverte 2.0' }
};

/** *n* parcels on a line going east from the address, none of them that of the address. */
const rangee = (n: number) =>
	Array.from({ length: n }, (_, i) => parcelle(`00000000ZB${String(i + 1).padStart(4, '0')}`, [-3.5 + 0.0003 * (i + 1), 46.5], 100 + i, i % 2));

function afficher(props: Partial<{
	parcelles: ParcelleCarte[];
	idAdresse: string | null;
	selection: string | null;
	groupe: readonly string[];
	contexte: ContexteCarte | null;
	occupe: boolean;
	partielle: boolean;
	sansCarte: boolean;
	onchoisir: (p: ParcelleCarte) => void;
}> = {}) {
	return render(ParcelList, {
		parcelles: EXEMPLE,
		idAdresse: ADRESSE,
		selection: null,
		groupe: [],
		contexte: CONTEXTE,
		occupe: false,
		onchoisir: () => {},
		...props
	});
}

const lignes = (conteneur: HTMLElement) => [...conteneur.querySelectorAll<HTMLButtonElement>('ul.parcelles-lignes > li > button')];
const references = (conteneur: HTMLElement) => lignes(conteneur).map((b) => b.querySelector('.ligne-ref')!.textContent!.trim());
// Ordinary white space only: the no-break spaces of the texts are part of what is checked.
const texte = (el: Element) => el.textContent!.replace(/[ \t\n\r]+/g, ' ').trim();

describe('ParcelList', () => {
	it('is a section named by its title « Parcelles affichées »', () => {
		afficher();
		const titre = screen.getByRole('heading', { level: 3, name: 'Parcelles affichées' });
		expect(titre.closest('section')!.getAttribute('aria-labelledby')).toBe(titre.id);
	});

	it('puts the parcel of the address first, then from the nearest to the farthest, ties by reference', () => {
		const { container } = afficher();
		expect(references(container)).toEqual(['ZA 0001', 'ZA 0003', 'ZA 0002', 'ZA 0004']);
	});

	it('a line is a button in an item: reference, surface, and the sales only when there are some', () => {
		const { container } = afficher();
		const [adresse, nord, est, ouest] = lignes(container);
		for (const bouton of lignes(container)) {
			expect(bouton.getAttribute('type')).toBe('button');
			expect(bouton.parentElement!.tagName).toBe('LI');
		}
		expect(texte(adresse)).toBe(`ZA 0001 Adresse 450${NB}m² · 4${NB}ventes`);
		expect(texte(ouest)).toBe(`ZA 0004 380${NB}m² · 1${NB}vente`);
		// No sale over the period: the line says nothing of the sales (the zero is read in the panel).
		expect(texte(est)).toBe(`ZA 0002 612${NB}m²`);
		expect(texte(est)).not.toMatch(/vente/);
		// No surface served: the label of the glossary, never « 0 m² ».
		expect(texte(nord)).toBe(`ZA 0003 Non renseigné · 1${NB}vente`);
		expect(texte(nord)).not.toMatch(/0\s?m²/);
	});

	it('the separator dot is for the eye: the name of a line reads without it', () => {
		const { container } = afficher();
		const point = lignes(container)[0].querySelector('[aria-hidden="true"]');
		expect(point?.textContent).toBe('·');
	});

	it('the parcel of the address is told by a word (T-32), on its line only', () => {
		const { container } = afficher();
		const badges = [...container.querySelectorAll('.ligne-badge')].map(texte);
		expect(badges).toEqual(['Adresse']);
		expect(lignes(container)[0].querySelector('.ligne-badge')!.textContent).toBe('Adresse');
	});

	it('without a parcel of the address, no line carries its badge', () => {
		const { container } = afficher({ idAdresse: null });
		expect(container.querySelectorAll('.ligne-badge')).toHaveLength(0);
		expect(references(container)[0]).toBe('ZA 0001');
	});

	it('the parcels of the shown group carry « Vendue ensemble » (T-31)', () => {
		const { container } = afficher({ groupe: ['00000000ZA0002', '00000000ZA0004'] });
		const parLigne = lignes(container).map((b) => [...b.querySelectorAll('.ligne-badge')].map(texte));
		expect(parLigne).toEqual([['Adresse'], [], ['Vendue ensemble'], ['Vendue ensemble']]);
	});

	it('the selected line is current (`aria-current`) and checked; the others are not', () => {
		const { container } = afficher({ selection: '00000000ZA0003' });
		const etats = lignes(container).map((b) => [b.getAttribute('aria-current'), b.querySelector('svg') !== null]);
		expect(etats).toEqual([
			[null, false],
			['true', true],
			[null, false],
			[null, false]
		]);
		expect(lignes(container)[1].querySelector('svg')!.getAttribute('aria-hidden')).toBe('true');
	});

	it('activating a line hands its parcel to the dialog', async () => {
		const onchoisir = vi.fn();
		const { container } = afficher({ onchoisir });
		await fireEvent.click(lignes(container)[2]);
		expect(onchoisir).toHaveBeenCalledTimes(1);
		expect(onchoisir.mock.calls[0][0].id).toBe('00000000ZA0002');
	});

	it('says the count of the view with the served period (T-29)', () => {
		const { container } = afficher();
		expect(texte(container.querySelector('.parcelles-compte')!)).toBe('4 parcelles sur la carte, dont 3 avec une vente (2021-2025).');
	});

	it('the count agrees: one parcel, and none with a sale', () => {
		const un = afficher({ parcelles: [EXEMPLE[2]] });
		expect(texte(un.container.querySelector('.parcelles-compte')!)).toBe('1 parcelle sur la carte, dont 1 avec une vente (2021-2025).');
		un.unmount();
		const aucune = afficher({ parcelles: [EXEMPLE[1]] });
		expect(texte(aucune.container.querySelector('.parcelles-compte')!)).toBe('1 parcelle sur la carte, aucune avec une vente (2021-2025).');
	});

	it('without the map, the count says « autour de l’adresse » (T-29, variant)', () => {
		const { container } = afficher({ sansCarte: true });
		expect(texte(container.querySelector('.parcelles-compte')!)).toBe('4 parcelles autour de l’adresse, dont 3 avec une vente (2021-2025).');
	});

	it('an absent period is the label of the glossary, never a hole', () => {
		const contexte = { ...CONTEXTE, ventes: { ...CONTEXTE.ventes, periode: null } };
		const { container } = afficher({ contexte });
		expect(texte(container.querySelector('.parcelles-compte')!)).toBe('4 parcelles sur la carte, dont 3 avec une vente (Non renseigné).');
	});

	it('the box (T-30) keeps the parcels with a sale only; the count does not change', async () => {
		const { container } = afficher();
		const caseACocher = screen.getByRole('checkbox', { name: 'Seulement les parcelles avec une vente' });
		expect((caseACocher as HTMLInputElement).checked).toBe(false);
		await fireEvent.click(caseACocher);
		expect(references(container)).toEqual(['ZA 0001', 'ZA 0003', 'ZA 0004']);
		expect(texte(container.querySelector('.parcelles-compte')!)).toBe('4 parcelles sur la carte, dont 3 avec une vente (2021-2025).');
		await fireEvent.click(caseACocher);
		expect(references(container)).toEqual(['ZA 0001', 'ZA 0003', 'ZA 0002', 'ZA 0004']);
	});

	it('sales not counted in the view: the count of parcels without sales (T-47, S5 of 2026-11-15), no box; a line not counted says no sale', () => {
		const sansVentes = EXEMPLE.map((p) => ({ ...p, properties: { ...p.properties, ventes: p.id === ADRESSE ? null : p.properties.ventes } }));
		const { container } = afficher({ parcelles: sansVentes });
		// Never a smaller count of sales: the parcels are said, and that their sales are not counted.
		expect(texte(container.querySelector('.parcelles-compte')!)).toBe('4 parcelles sur la carte. Les ventes n’ont pas pu être comptées.');
		expect(screen.queryByRole('checkbox')).toBeNull();
		expect(texte(lignes(container)[0])).toBe(`ZA 0001 Adresse 450${NB}m²`);
		expect(lignes(container)).toHaveLength(4);
	});

	it('twenty lines, then a button for the next twenty; the focus goes to the first line added', async () => {
		const { container } = afficher({ parcelles: rangee(45), idAdresse: null });
		expect(lignes(container)).toHaveLength(20);
		const plus = screen.getByRole('button', { name: 'Afficher 20 parcelles de plus' });
		expect(plus.classList.contains('bouton-secondaire')).toBe(true);
		await fireEvent.click(plus);
		expect(lignes(container)).toHaveLength(40);
		expect(document.activeElement).toBe(lignes(container)[20]);
		expect(references(container)[20]).toBe('ZB 0021');
		// The last batch says what is left.
		const reste = screen.getByRole('button', { name: 'Afficher 5 parcelles de plus' });
		await fireEvent.click(reste);
		expect(lignes(container)).toHaveLength(45);
		expect(document.activeElement).toBe(lignes(container)[40]);
		expect(screen.queryByRole('button', { name: /^Afficher/ })).toBeNull();
	});

	it('exactly twenty parcels: no button for more', () => {
		const { container } = afficher({ parcelles: rangee(20), idAdresse: null });
		expect(lignes(container)).toHaveLength(20);
		expect(screen.queryByRole('button', { name: /^Afficher/ })).toBeNull();
	});

	it('no parcel in the view: T-37, no list, no count', () => {
		const { container } = afficher({ parcelles: [] });
		expect(texte(container.querySelector('.parcelles-vide')!)).toBe('Aucune parcelle cadastrale dans cette vue.');
		expect(container.querySelector('ul')).toBeNull();
		expect(container.querySelector('.parcelles-compte')).toBeNull();
	});

	it('no parcel around the address, without the map: the variant of T-37', () => {
		const { container } = afficher({ parcelles: [], sansCarte: true });
		expect(texte(container.querySelector('.parcelles-vide')!)).toBe('Aucune parcelle cadastrale autour de l’adresse.');
	});

	it('while the parcels are read: busy, never « Aucune parcelle »', () => {
		const { container } = afficher({ parcelles: [], occupe: true });
		expect(container.querySelector('section')!.getAttribute('aria-busy')).toBe('true');
		expect(container.textContent).not.toMatch(/Aucune parcelle/);
	});

	it('the lines read so far stay while the next ones are read', () => {
		const { container } = afficher({ occupe: true });
		expect(container.querySelector('section')!.getAttribute('aria-busy')).toBe('true');
		expect(lignes(container)).toHaveLength(4);
	});

	it('not busy, the list says so', () => {
		const { container } = afficher();
		expect(container.querySelector('section')!.getAttribute('aria-busy')).toBe('false');
	});

	it('a part of the view not read: never « Aucune parcelle », and no count that would read as fewer parcels', () => {
		const vide = afficher({ parcelles: [], partielle: true });
		expect(vide.container.textContent).not.toMatch(/Aucune parcelle/);
		vide.unmount();
		const partielle = afficher({ partielle: true });
		expect(lignes(partielle.container)).toHaveLength(4);
		expect(partielle.container.querySelector('.parcelles-compte')).toBeNull();
	});

	it('cadastre out of coverage: the state of the glossary in place of the list, never « Aucune parcelle »', () => {
		const contexte: ContexteCarte = { ...CONTEXTE, parcelles: { etat: 'out_of_coverage', motif: 'territoire_hors_perimetre' } };
		const { container } = afficher({ parcelles: [], contexte });
		expect(container.querySelector('[data-state="out_of_coverage"]')).not.toBeNull();
		expect(container.querySelector('ul')).toBeNull();
		expect(container.textContent).not.toMatch(/Aucune parcelle/);
		expect(screen.getByRole('heading', { level: 3, name: 'Parcelles affichées' })).toBeTruthy();
	});

	it('never resets the radius of a button, never names an owner', () => {
		const source = readFileSync('src/lib/components/adresse/carte/ParcelList.svelte', 'utf8');
		expect(source).not.toMatch(/border-radius\s*:\s*0/);
		expect(source).not.toMatch(/propriétaire|unité foncière|terrain de|jamais vendue|estimation/i);
	});
});
