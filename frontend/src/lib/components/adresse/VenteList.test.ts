// Cards of DVF sales (04-05; UI-SPEC « /adresse/[id] » point 5, « Contrat de rédaction »; D-114,
// D-115, LIEU-02). The component renders the served sales in the served order: date and nature,
// composition and built surface, then the price and price per m², or the neutral badge « Prix non
// affiché » when the API withholds the price. It never filters, sorts nor counts, and a withheld
// amount cannot reach the HTML.
import { render } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { describe, expect, it } from 'vitest';

import type { CarteVente } from '$lib/api/adresses';
import { projeterCarteVente } from '$lib/api/adresses';
import VenteList from './VenteList.svelte';

/** Every no-break space as an ordinary one, and the formatting whitespace collapsed. */
function espaces(texte: string | null | undefined): string {
	return (texte ?? '').replace(/[  ]/g, ' ').replace(/\s+/g, ' ').trim();
}

function vente(partiel: Partial<CarteVente> = {}): CarteVente {
	return {
		date: '2025-11-20',
		nature: 'Vente',
		composition: { maisons: 1, appartements: 0, dependances: 1, locaux_activite: 0 },
		surface_batie: 90,
		prix: 238000,
		prix_m2: 2644,
		prix_affiche: true,
		minimisation: null,
		distance_m: null,
		hors_stats_vefa: false,
		...partiel
	};
}

const RETENUE = vente({
	date: '2025-06-01',
	composition: { maisons: 1, appartements: 0, dependances: 0, locaux_activite: 0 },
	surface_batie: 95,
	prix: null,
	prix_m2: null,
	prix_affiche: false,
	minimisation: { motif: 'vente_isolee_recente' }
});

const lignes = (li: Element) => [...li.querySelectorAll('p')].map((p) => espaces(p.textContent));

describe('VenteList — cartes de vente', () => {
	it('une carte par vente servie, dans l’ordre servi, en liste ordonnée', () => {
		const ventes = [vente(), vente({ date: '2019-03-15', prix: 210000, prix_m2: 2471 }), vente({ date: '2021-06-30' })];
		const { container } = render(VenteList, { ventes });

		const items = container.querySelectorAll('ol.ventes-cartes > li');
		expect(items).toHaveLength(3);
		// Served order, never re-sorted by date.
		expect([...items].map((li) => lignes(li)[0])).toEqual(['novembre 2025 · Vente', 'mars 2019 · Vente', 'juin 2021 · Vente']);
	});

	it('les trois lignes du contrat : date et nature, composition et surface, prix et prix au m²', () => {
		const { container } = render(VenteList, { ventes: [vente()] });

		expect(lignes(container.querySelector('li')!)).toEqual([
			'novembre 2025 · Vente',
			'1 maison, 1 dépendance · surface bâtie déclarée 90 m²',
			'238 000 € · 2 644 €/m²'
		]);
	});

	it('prix retenu par l’API : badge neutre « Prix non affiché », aucun montant', () => {
		const { container } = render(VenteList, { ventes: [RETENUE] });

		const badge = container.querySelector('.ventecard-badge');
		expect(badge?.textContent).toBe('Prix non affiché');
		expect(badge?.getAttribute('data-minimisation')).toBe('vente_isolee_recente');
		expect(lignes(container.querySelector('li')!)[1]).toBe('1 maison · surface bâtie déclarée 95 m²');
		expect(container.innerHTML).not.toMatch(/€/);
	});

	it('un montant servi à côté d’une minimisation ne passe jamais la projection', () => {
		const carte = projeterCarteVente({
			date: '2025-06-01',
			nature: 'Vente',
			composition: { maisons: 1 },
			surface_batie: 95,
			prix: 312345,
			prix_m2: 3288,
			prix_affiche: true,
			minimisation: { motif: 'vente_isolee_recente' },
			id_mutation: '2025-B-1',
			valeur_fonciere: 312345
		});
		const { container } = render(VenteList, { ventes: [carte] });

		expect(carte).toMatchObject({ prix: null, prix_m2: null, prix_affiche: false });
		expect(JSON.stringify(carte)).not.toMatch(/312345|3288|2025-B-1/);
		expect(container.innerHTML).not.toMatch(/312|3 288|2025-B-1/);
	});

	it('un prix non affichable sans motif servi reste retenu', () => {
		const carte = projeterCarteVente({ date: '2025-06-01', composition: {}, prix: 100000, prix_affiche: false });
		expect(carte).toMatchObject({ prix: null, prix_affiche: false, minimisation: { motif: 'isolement_indetermine' } });
	});

	it('vente d’ensemble sans prix au m² : le montant seul, aucun prix au m² inventé', () => {
		const ensemble = vente({
			composition: { maisons: 0, appartements: 2, dependances: 1, locaux_activite: 0 },
			surface_batie: null,
			prix: 225000,
			prix_m2: null
		});
		const { container } = render(VenteList, { ventes: [ensemble] });

		expect(lignes(container.querySelector('li')!).slice(1)).toEqual([
			'2 appartements, 1 dépendance · surface bâtie non renseignée',
			'225 000 €'
		]);
	});

	it('local d’activité ou mutation sans local : textes décidés (04-DECISIONS-TEXTES ligne 35), aucun repli', () => {
		const { container } = render(VenteList, {
			ventes: [
				vente({ composition: { maisons: 1, appartements: 0, dependances: 0, locaux_activite: 1 } }),
				vente({ composition: { maisons: 0, appartements: 0, dependances: 0, locaux_activite: 0 } }),
				vente({ composition: { maisons: 0, appartements: 0, dependances: 0, locaux_activite: 1 } })
			]
		});

		const deuxiemes = [...container.querySelectorAll('li')].map((li) => lignes(li)[1]);
		expect(deuxiemes).toEqual([
			"1 maison, avec un local d'activité · surface bâtie déclarée 90 m²",
			'sans local décrit · surface bâtie déclarée 90 m²',
			"avec un local d'activité · surface bâtie déclarée 90 m²"
		]);
		expect(container.querySelectorAll('[data-provisoire]')).toHaveLength(0);
	});

	it('plusieurs locaux d’activité : « avec des locaux d’activité » (ligne 57), sans marque provisoire', () => {
		const { container } = render(VenteList, {
			ventes: [vente({ composition: { maisons: 0, appartements: 1, dependances: 0, locaux_activite: 2 } })]
		});

		expect(lignes(container.querySelector('li')!)[1]).toBe("1 appartement, avec des locaux d'activité · surface bâtie déclarée 90 m²");
		expect(container.querySelectorAll('[data-provisoire]')).toHaveLength(0);
	});

	it('prix d’une vente affichée mais non servi : « Non renseigné », jamais « 0 »', () => {
		const { container } = render(VenteList, { ventes: [vente({ prix: null, prix_m2: null })] });

		expect(container.querySelector('.ventecard-badge')?.textContent).toBe('Non renseigné');
		expect(container.querySelector('.ventecard-l3')?.textContent).not.toMatch(/\b0\b/);
	});

	it('distance servie : « à {d} m (à vol d’oiseau) » sur la première ligne', () => {
		const { container } = render(VenteList, { ventes: [vente({ distance_m: 1234 })] });

		expect(lignes(container.querySelector('li')!)[0]).toBe("novembre 2025 · Vente · à 1 234 m (à vol d'oiseau)");
	});
});

// C4 (plan 04.5-07, report 4.1-15): a VEFA served in a list is shown and marked with the neutral badge
// of the report, on the template of « Prix non affiché »; a sale never carries it. The text is
// written here by hand, word for word the report (read in a block by Ianis, R10).
const MARQUE_VEFA_DU_REPORT = 'VEFA, hors statistiques';

describe('VenteList — VEFA marquée (04.5-07, C4)', () => {
	const VEFA = vente({ date: '2024-03-14', nature: "Vente en l'état futur d'achèvement", hors_stats_vefa: true });

	it('une VEFA porte la marque du report mot pour mot, une vente ne la porte pas', () => {
		const { container } = render(VenteList, { ventes: [vente(), VEFA, vente({ date: '2021-06-30' })] });

		const items = [...container.querySelectorAll('ol.ventes-cartes > li')];
		const marques = items.map((li) => [...li.querySelectorAll('[data-vefa]')].map((badge) => badge.textContent));
		expect(marques).toEqual([[], [MARQUE_VEFA_DU_REPORT], []]);
		// Same template as « Prix non affiché »: the neutral badge, after the price of the card.
		const marque = items[1].querySelector('[data-vefa]')!;
		expect(marque.classList.contains('ventecard-badge')).toBe(true);
		expect(marque.closest('.ventecard-l3')).not.toBeNull();
		expect(lignes(items[1])).toEqual([
			"mars 2024 · Vente en l'état futur d'achèvement",
			'1 maison, 1 dépendance · surface bâtie déclarée 90 m²',
			`238 000 € · 2 644 €/m² ${MARQUE_VEFA_DU_REPORT}`
		]);
		expect(container.textContent?.match(/VEFA, hors statistiques/g)).toHaveLength(1);
	});

	it('une VEFA au prix retenu garde « Prix non affiché » et porte aussi la marque', () => {
		const { container } = render(VenteList, { ventes: [{ ...RETENUE, hors_stats_vefa: true }] });

		expect([...container.querySelectorAll('.ventecard-badge')].map((badge) => badge.textContent)).toEqual(['Prix non affiché', MARQUE_VEFA_DU_REPORT]);
		expect(container.innerHTML).not.toMatch(/€/);
	});

	it('une carte servie sans la clé, ou avec une valeur autre que true, n’est pas marquée', () => {
		const cartes = [{ date: '2024-03-14', nature: 'Vente', composition: {}, prix_affiche: true, prix: 1 }, { date: '2024-03-14', nature: 'Vente', composition: {}, hors_stats_vefa: 'true' }].map(projeterCarteVente);
		const { container } = render(VenteList, { ventes: cartes });

		expect(container.querySelectorAll('[data-vefa]')).toHaveLength(0);
	});
});

// 04.10-07: the panel of the parcel map adds, at the end of a card, what a sale of several parcels says.
// The snippet is optional; without it the cards are the ones above, unchanged.
describe('VenteList — pied de carte (04.10-07)', () => {
	const pied = createRawSnippet((carte: () => CarteVente, rang: () => number) => ({
		render: () => `<p class="pied-test">${rang()} ${carte().date}</p>`
	}));

	it('without `pied`, nothing is added after the three lines of a card', () => {
		const { container } = render(VenteList, { ventes: [vente(), RETENUE] });
		for (const li of container.querySelectorAll('li')) {
			expect([...li.children].map((e) => e.className.replace(/\s*svelte-\S+/g, ''))).toEqual(['ventecard-l1', 'ventecard-l2', 'ventecard-l3']);
		}
	});

	it('with `pied`, the snippet ends each card, given the card and its rank', () => {
		const { container } = render(VenteList, { ventes: [vente(), RETENUE], pied });
		const cartes = [...container.querySelectorAll('ol.ventes-cartes > li')];
		expect(cartes.map((li) => li.lastElementChild!.textContent)).toEqual(['0 2025-11-20', '1 2025-06-01']);
		// The three lines of the card come first, unchanged.
		expect(lignes(cartes[1]).slice(0, 3)).toEqual(['juin 2025 · Vente', '1 maison · surface bâtie déclarée 95 m²', 'Prix non affiché']);
	});
});
