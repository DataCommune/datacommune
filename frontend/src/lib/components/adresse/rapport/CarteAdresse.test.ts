// Header map of the address fiche: the pictures are the served relative paths (never a third-party
// origin), the caption carries the licence, and a failed picture leaves a bare line, not an empty frame.
import { fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CarteRapport } from '$lib/api/rapport';
import CarteAdresse from './CarteAdresse.svelte';

const CARTE: CarteRapport = {
	plan: '/carte/44109_zz01_00002/plan.png',
	parcelles: '/carte/44109_zz01_00002/parcelles.png',
	empriseM: 300,
	attribution: '© IGN (Plan IGN, Parcellaire Express)',
	licence: 'Licence Ouverte 2.0',
	geoportail: 'https://www.geoportail.gouv.fr/carte?c=-1.5,47.2&z=18'
};
const ADRESSE = '12 rue des Lilas, Nantes';
const BAN_ID = '44109_9999_00002';

/** Both pictures: the plan has an alt, the parcels are decorative (`alt=""`, so no `img` role). */
const images = (conteneur: HTMLElement) => Array.from(conteneur.querySelectorAll('img'));

describe('CarteAdresse — impression (04.7-17)', () => {
	it('printed only once both pictures are loaded: a frame still loading would print empty', async () => {
		const { container } = render(CarteAdresse, { carte: CARTE, adresse: ADRESSE, banId: BAN_ID });
		const figure = container.querySelector('figure.carte') as HTMLElement;
		const [plan, parcelles] = [...container.querySelectorAll('img')];
		expect(figure.classList.contains('carte-chargee')).toBe(false);
		await fireEvent.load(plan);
		expect(figure.classList.contains('carte-chargee')).toBe(false);
		await fireEvent.load(parcelles);
		expect(figure.classList.contains('carte-chargee')).toBe(true);
	});

	it('the same picture loaded twice never stands for the other one', async () => {
		const { container } = render(CarteAdresse, { carte: CARTE, adresse: ADRESSE, banId: BAN_ID });
		const figure = container.querySelector('figure.carte') as HTMLElement;
		const [plan] = [...container.querySelectorAll('img')];
		await fireEvent.load(plan);
		await fireEvent.load(plan);
		expect(figure.classList.contains('carte-chargee')).toBe(false);
	});
});

describe('CarteAdresse', () => {
	it('serves the two pictures from the given relative paths, the plan alt citing the address', () => {
		const { container } = render(CarteAdresse, { carte: CARTE, adresse: ADRESSE, banId: BAN_ID });
		expect(images(container)).toHaveLength(2);
		const [plan, parcelles] = images(container);
		expect(plan.getAttribute('src')).toBe(CARTE.plan);
		expect(parcelles.getAttribute('src')).toBe(CARTE.parcelles);
		expect(plan.getAttribute('alt')).toContain(ADRESSE);
		expect(parcelles.getAttribute('alt')).toBe('');
	});

	it('never points at a third-party origin', () => {
		const { container } = render(CarteAdresse, { carte: CARTE, adresse: ADRESSE, banId: BAN_ID });
		for (const img of images(container)) expect(img.getAttribute('src')).not.toMatch(/^https?:/);
	});

	it('says the width on the ground with a no-break space, and the attribution and licence', () => {
		const { container } = render(CarteAdresse, { carte: CARTE, adresse: ADRESSE, banId: BAN_ID });
		const legende = container.querySelector('figcaption')!.textContent!;
		expect(legende).toContain('environ 300 m de large');
		expect(legende).toContain(CARTE.attribution);
		expect(legende).toContain(CARTE.licence);
	});

	it('links the Géoportail in the same tab, only when served', () => {
		const { unmount } = render(CarteAdresse, { carte: CARTE, adresse: ADRESSE, banId: BAN_ID });
		const lien = screen.getByRole('link', { name: 'Ouvrir dans le Géoportail' });
		expect(lien.getAttribute('href')).toBe(CARTE.geoportail);
		expect(lien.getAttribute('target')).toBeNull();
		expect(lien.getAttribute('rel')).toBe('noopener noreferrer');
		unmount();
		render(CarteAdresse, { carte: { ...CARTE, geoportail: null }, adresse: ADRESSE, banId: BAN_ID });
		expect(screen.queryByRole('link')).toBeNull();
	});

	it.each([
		['plan', 0],
		['parcelles', 1]
	])('drops the frame and the pictures when the %s picture fails', async (_nom, rang) => {
		const { container } = render(CarteAdresse, { carte: CARTE, adresse: ADRESSE, banId: BAN_ID });
		await fireEvent.error(images(container)[rang]);
		expect(container.querySelectorAll('img')).toHaveLength(0);
		expect(container.querySelector('.carte-image')).toBeNull();
		expect(container.textContent).toContain('Carte indisponible.');
		expect(container.textContent).not.toContain(CARTE.licence);
		// The Géoportail link survives, to reach the map elsewhere.
		expect(screen.getByRole('link', { name: 'Ouvrir dans le Géoportail' }).getAttribute('href')).toBe(CARTE.geoportail);
	});

	it('keeps only the line when the picture fails and there is no Géoportail link', async () => {
		const { container } = render(CarteAdresse, { carte: { ...CARTE, geoportail: null }, adresse: ADRESSE, banId: BAN_ID });
		await fireEvent.error(images(container)[0]);
		expect(container.querySelector('figcaption')!.textContent!.trim()).toBe('Carte indisponible.');
	});
});

// 04.10 UI contract § 2.1: the entry to the interactive map of the parcels. What the server renders is
// in `CarteAdresse.rendu-serveur.test.ts`; the height of the row and the whole path are in the e2e spec.
describe('CarteAdresse — bouton de la carte des parcelles (04.10)', () => {
	afterEach(() => vi.unstubAllGlobals());

	/** Children of the figure, in order: a block by its class, the caption by its tag. */
	const blocs = (conteneur: HTMLElement) =>
		[...conteneur.querySelector('figure.carte')!.children].map((e) => (e.tagName === 'FIGCAPTION' ? 'figcaption' : e.classList[0]));

	it('once mounted, shows the button in its row, under the picture and above its caption', () => {
		const { container } = render(CarteAdresse, { carte: CARTE, adresse: ADRESSE, banId: BAN_ID });
		const bouton = screen.getByRole('button', { name: 'Ouvrir la carte des parcelles' });
		expect(bouton.getAttribute('type')).toBe('button');
		expect(bouton.getAttribute('aria-haspopup')).toBe('dialog');
		// A secondary button: the one plain action of the fiche stays « Comparer avec une autre adresse ».
		expect([...bouton.classList]).toContain('bouton-secondaire');
		expect([...bouton.classList]).not.toContain('bouton-primaire');
		expect(bouton.parentElement!.classList.contains('carte-action')).toBe(true);
		expect(blocs(container)).toEqual(['carte-image', 'carte-action', 'figcaption']);
	});

	it.each([
		['plan', 0],
		['parcelles', 1]
	])('keeps the button when the %s picture fails: the parcels do not depend on the base map', async (_nom, rang) => {
		const { container } = render(CarteAdresse, { carte: CARTE, adresse: ADRESSE, banId: BAN_ID });
		await fireEvent.error(images(container)[rang]);
		expect(container.querySelector('.carte-image')).toBeNull();
		expect(screen.getByRole('button', { name: 'Ouvrir la carte des parcelles' })).toBeTruthy();
		expect(container.querySelector('figcaption')!.textContent).toContain('Carte indisponible.');
		// The button comes before the line, in the same row as when the picture is there.
		expect(container.querySelector('.carte-action + figcaption')).not.toBeNull();
	});

	it('mounts no dialog and asks nothing of the map before the button is activated', () => {
		const lectures = vi.fn();
		vi.stubGlobal('fetch', lectures);
		const { container } = render(CarteAdresse, { carte: CARTE, adresse: ADRESSE, banId: BAN_ID });
		expect(container.querySelector('dialog')).toBeNull();
		expect(lectures).not.toHaveBeenCalled();
	});

	it('the button mounts the dialog, which reads the context of this address; « Fermer » takes it away', async () => {
		// The context does not answer here: the dialog is there all the same, and says the outage.
		const lectures = vi.fn(async (_chemin: RequestInfo | URL) => new Response('{}', { status: 503 }));
		vi.stubGlobal('fetch', lectures);
		const { container } = render(CarteAdresse, { carte: CARTE, adresse: ADRESSE, banId: BAN_ID });
		await fireEvent.click(screen.getByRole('button', { name: 'Ouvrir la carte des parcelles' }));
		// The dialog module is fetched on the click (04.10-12): the dialog comes once it is there. Its first
		// import is also its first transform in vitest, longer than the default second of `waitFor`.
		const dialogue = await vi.waitFor(
			() => {
				expect(container.textContent, 'le module du dialogue ne s’est pas chargé (T-43)').not.toContain('Rechargez la page');
				const d = container.querySelector('dialog');
				expect(d).not.toBeNull();
				return d!;
			},
			{ timeout: 10_000 }
		);
		expect(document.getElementById(dialogue.getAttribute('aria-labelledby')!)!.textContent).toBe('Parcelles autour de l’adresse');
		await vi.waitFor(() => expect(lectures).toHaveBeenCalledTimes(1));
		expect(lectures.mock.calls[0][0]).toBe(`/carte/${BAN_ID}/contexte`);
		await vi.waitFor(() => expect(dialogue.querySelector('[data-state="temporary_error"]')).not.toBeNull());
		expect(dialogue.textContent).not.toContain('Chargement de la carte…');

		await fireEvent.click(screen.getByRole('button', { name: 'Fermer' }));
		expect(container.querySelector('dialog')).toBeNull();
		// The button is still there to open the map again, and has the focus back even though the click
		// never focused it (jsdom, like Safari: matrix of 04.10-10).
		const bouton = screen.getByRole('button', { name: 'Ouvrir la carte des parcelles' });
		expect(document.activeElement).toBe(bouton);
	});
});
