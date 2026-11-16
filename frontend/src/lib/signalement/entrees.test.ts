// The two entries of the error report: the link of the footer and « Signaler cette valeur » at the
// bottom of the information panel. Switched off (the default), both components render what they
// rendered before: their own test files, left untouched, say what that is; this file adds that the
// report leaves no trace in them. Switched on, the footer links to the page and the panel carries
// what it knows of the value.
import { render } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { EntreeVolet, ValeurServie } from '$lib/data/commune-kpi';

const etat = vi.hoisted(() => ({ actif: false }));
vi.mock('$lib/signalement/reglage', () => ({
	get SIGNALEMENT() {
		return etat.actif;
	}
}));
vi.mock('$lib/config', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/config')>()),
	DEPLOYMENT_SHA: '81d3926f'
}));
vi.mock('$app/stores', async () => {
	const { readable } = await import('svelte/store');
	return { page: readable({ route: { id: null }, data: {}, url: new URL('http://localhost/') }) };
});

import SiteFooter from '$lib/components/SiteFooter.svelte';
import SourcePanel from '$lib/components/fiche/SourcePanel.svelte';

const ENTREES: EntreeVolet[] = [
	{ terme: 'Définition', valeur: 'Médiane communale du prix au mètre carré des maisons.' },
	{ terme: 'Source', valeur: 'DVF' }
];
const BRUT: ValeurServie[] = [{ fieldId: 'dvf.indicateurs.maison.mediane_courante', valeur: '3120', etat: { kind: 'valeur' } }];
const VOLET = { titre: 'Prix médian des maisons', entrees: ENTREES, brut: BRUT, source: { nom: 'DVF', url: 'https://example.org/dvf' } };

function dernierLienDuPied(): [string | undefined, string | null] {
	const { getAllByRole } = render(SiteFooter);
	const lien = getAllByRole('link').at(-1)!;
	return [lien.textContent?.trim(), lien.getAttribute('href')];
}

function ouvrirVolet() {
	const rendu = render(SourcePanel, VOLET);
	const declencheur = document.createElement('button');
	document.body.append(declencheur);
	rendu.component.ouvrir(declencheur);
	return rendu;
}

beforeEach(() => {
	etat.actif = false;
	window.history.replaceState(null, '', '/commune/44109?onglet=prix#logement');
});

describe('switched off (the default)', () => {
	it('the footer keeps its mailto', () => {
		expect(dernierLienDuPied()).toEqual(['Signaler une erreur', 'mailto:signalement@datacommune.fr']);
	});

	it('the information panel has no link to the report, closed or opened', async () => {
		const ferme = render(SourcePanel, VOLET);
		const liensFerme = ferme.container.querySelectorAll('a').length;
		ferme.unmount();

		const { container } = ouvrirVolet();
		await Promise.resolve();

		expect(container.querySelector('a[href^="/signaler"]')).toBeNull();
		expect(container.textContent).not.toContain('Signaler');
		// Opening adds no link and no paragraph: the panel is what it was before the report existed.
		expect(container.querySelectorAll('a')).toHaveLength(liensFerme);
		expect(container.querySelector('.source-panel-signaler')).toBeNull();
	});
});

describe('switched on', () => {
	beforeEach(() => {
		etat.actif = true;
	});

	it('the footer links to the page of the report, same wording, same place', () => {
		expect(dernierLienDuPied()).toEqual(['Signaler une erreur', '/signaler']);
	});

	it('the opened panel ends with « Signaler cette valeur », carrying the page, the value, the source and the version', async () => {
		const { container } = ouvrirVolet();
		await Promise.resolve();

		const liens = [...container.querySelectorAll('a')];
		const lien = liens.at(-1)!;
		expect(lien.textContent).toBe('Signaler cette valeur');
		const url = new URL(lien.getAttribute('href')!, 'http://localhost');
		expect(url.pathname).toBe('/signaler');
		expect(Object.fromEntries(url.searchParams)).toEqual({
			de: '/commune/44109',
			titre: 'Prix médian des maisons',
			lignes: 'dvf.indicateurs.maison.mediane_courante=3120',
			source: 'DVF',
			v: '81d3926f'
		});
		// After the raw data: the last thing of the panel, never on the face of a row.
		expect(lien.compareDocumentPosition(container.querySelector('.source-panel-raw')!) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
	});

	it('a panel without raw data still reports its title and its source', async () => {
		const rendu = render(SourcePanel, { ...VOLET, brut: [], source: null });
		const declencheur = document.createElement('button');
		document.body.append(declencheur);
		rendu.component.ouvrir(declencheur);
		await Promise.resolve();

		const lien = rendu.container.querySelector('a[href^="/signaler"]')!;
		expect(Object.fromEntries(new URL(lien.getAttribute('href')!, 'http://localhost').searchParams)).toEqual({
			de: '/commune/44109',
			titre: 'Prix médian des maisons',
			v: '81d3926f'
		});
	});
});
