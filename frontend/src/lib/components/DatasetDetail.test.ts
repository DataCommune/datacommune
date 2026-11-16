// Metadata of one source of the catalogue (phase 04.5, plan 11, B7; UI-029, D45-08, D45-11). The
// oracle is the full inventory of what the card of each dataset rendered at the starting SHA of the
// plan, frozen in `catalogue-inventaire-reference.json` before any extraction: identity,
// qualification and its reason, facts (term, value, links), reuse conditions, ordered restrictions,
// limits of the source and links. The reference is written once (`DC_CATALOGUE_REFERENCE_WRITE=1`,
// `DC_CATALOGUE_BASE_REVISION=<full SHA>`), only when `src/` is that revision, and never rewritten.
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import DatasetCard from './DatasetCard.svelte';
import DatasetDetail from './DatasetDetail.svelte';
import type { DatasetPublic } from '$lib/api/datasets';
import fixture from '$lib/data/__fixtures__/datasets-api.json';

const JEUX = fixture.donnees as DatasetPublic[];
const INSTANCE = ['44', '85'];
const RACINE_FRONTEND = process.cwd();
const CHEMIN_REFERENCE = join(RACINE_FRONTEND, 'src/lib/data/__fixtures__/catalogue-inventaire-reference.json');
const REGLE =
	"Inventaire complet de chaque jeu du catalogue tel que le rendait sa carte au SHA de départ du plan 04.5-11 : identité, qualification et motif, faits (terme, valeur, liens), conditions, restrictions ordonnées, limites et liens. Le détail d'une source doit rendre exactement cet inventaire. Écrit une fois, jamais réécrit.";

type Lien = { nom: string; href: string | null };
type InventaireJeu = {
	identite: { titre: string; producteur: string | null };
	qualification: { etat: string; quarantaine: string[] };
	faits: { terme: string; valeur: string; liens: Lien[] }[];
	conditions: string | null;
	restrictions: string[] | null;
	limites: { chapeau: string; puces: string[] } | null;
	liens: Lien[];
};
type Reference = {
	regle: string;
	baseRevision: string;
	capturedAt: string;
	departementsInstance: string[];
	entrees: Record<string, InventaireJeu>;
};

const texte = (element: Element | null | undefined) => (element?.textContent ?? '').replace(/\s+/g, ' ').trim();
const liensDe = (racine: Element): Lien[] =>
	[...racine.querySelectorAll('a')].map((a) => ({ nom: texte(a), href: a.getAttribute('href') }));

/** The block of the metadata titled `titre` (a section labelled by its heading). */
function bloc(racine: Element, titre: string): Element | null {
	return (
		[...racine.querySelectorAll('section[aria-labelledby]')].find(
			(section) => texte(racine.ownerDocument.getElementById(section.getAttribute('aria-labelledby')!)) === titre
		) ?? null
	);
}

/** Everything the metadata of one dataset renders under `racine`, read on the DOM. */
function inventaireJeu(racine: Element): InventaireJeu {
	const conditions = bloc(racine, 'Conditions de réutilisation');
	const restrictions = bloc(racine, 'Restrictions de réutilisation');
	const limites = bloc(racine, 'Limites de la source');
	return {
		identite: {
			titre: texte(racine.querySelector('h2, h3')),
			producteur: racine.querySelector('.dataset-producer') ? texte(racine.querySelector('.dataset-producer')) : null
		},
		qualification: {
			etat: texte(racine.querySelector('.dataset-state')),
			quarantaine: [...racine.querySelectorAll('.dataset-quarantine p')].map(texte)
		},
		faits: [...racine.querySelectorAll('dt')].map((dt) => {
			const dd = dt.nextElementSibling!;
			return { terme: texte(dt), valeur: texte(dd), liens: liensDe(dd) };
		}),
		conditions: conditions ? [...conditions.querySelectorAll('p')].map(texte).join(' ') : null,
		restrictions: restrictions ? [...restrictions.querySelectorAll('li')].map(texte) : null,
		limites: limites
			? { chapeau: texte(limites.querySelector('p')), puces: [...limites.querySelectorAll('li')].map(texte) }
			: null,
		liens: liensDe(racine)
	};
}

/**
 * The metadata of a dataset as the catalogue renders it. At the starting SHA the reference was read on
 * the article of DatasetCard; since the extraction (B7) the card is a row which, chosen, mounts the
 * one rendering of the metadata, DatasetDetail.
 */
function inventaireCarte(jeu: DatasetPublic): InventaireJeu {
	const { container, unmount } = render(DatasetCard, {
		props: { jeu: structuredClone(jeu), departementsInstance: INSTANCE, href: `/datasets?jeu=${jeu.nom}`, choisi: true }
	});
	const details = container.querySelectorAll('.dataset-detail');
	expect(details).toHaveLength(1);
	const inventaire = inventaireJeu(details[0]);
	// Review of 09/11: name, producer and state are read in the row above the detail, which no longer
	// repeats them; the detail keeps only a state worded longer than the row's (« Lu en direct… »).
	const ligne = container.querySelector('article')!;
	inventaire.identite = {
		titre: texte(ligne.querySelector('.dataset-ligne-nom')),
		producteur: ligne.querySelector('.dataset-ligne-producteur') ? texte(ligne.querySelector('.dataset-ligne-producteur')) : null
	};
	// Review of 2026-11-11: the row words a dataset that is read in plain text, its date after the word
	// (« Importé le 22 sept. 2026 »); the state of the inventory is that word.
	inventaire.qualification.etat ||= texte(ligne.querySelector('.dataset-badge, .dataset-unserved, .dataset-etat-mot'));
	unmount();
	return inventaire;
}

/** DatasetDetail alone; `muer` alters its DOM before the inventory is read (mutations of the oracle). */
function inventaireDetail(jeu: DatasetPublic, muer?: (racine: Element) => void): InventaireJeu {
	const { container, unmount } = render(DatasetDetail, { props: { jeu: structuredClone(jeu), departementsInstance: INSTANCE } });
	const racine = container.querySelector('.dataset-detail')!;
	muer?.(racine);
	const inventaire = inventaireJeu(racine);
	unmount();
	return inventaire;
}

function lireReference(): Reference | null {
	return existsSync(CHEMIN_REFERENCE) ? (JSON.parse(readFileSync(CHEMIN_REFERENCE, 'utf8')) as Reference) : null;
}

/** `src/` must be the declared base revision when the reference is written. */
function verifierArbreAuSha(base: string) {
	execFileSync(
		'git',
		[
			'diff', '--quiet', base, '--', 'src',
			':(exclude,glob)src/**/*.test.ts',
			':(exclude)src/lib/data/__fixtures__/catalogue-inventaire-reference.json'
		],
		{ cwd: RACINE_FRONTEND, stdio: 'inherit' }
	);
}

function ecrireReference() {
	const base = process.env.DC_CATALOGUE_BASE_REVISION ?? '';
	if (!/^[0-9a-f]{40}$/.test(base)) throw new Error('DC_CATALOGUE_BASE_REVISION must be the full starting SHA');
	if (lireReference()) throw new Error('catalogue-inventaire-reference.json exists: it is never rewritten');
	verifierArbreAuSha(base);
	const entrees = Object.fromEntries(JEUX.map((jeu) => [jeu.nom, inventaireCarte(jeu)]));
	const reference: Reference = {
		regle: REGLE,
		baseRevision: base,
		capturedAt: new Date().toISOString(),
		departementsInstance: INSTANCE,
		entrees
	};
	writeFileSync(CHEMIN_REFERENCE, `${JSON.stringify(reference, null, '\t')}\n`);
}

function reference(): Reference {
	const lue = lireReference();
	if (!lue) throw new Error('Missing catalogue-inventaire-reference.json');
	return lue;
}

describe('catalogue — inventaire figé au SHA de départ (UI-029, D45-01)', () => {
	it.runIf(process.env.DC_CATALOGUE_REFERENCE_WRITE === '1')('writes the reference from the cards of the starting SHA', () => {
		ecrireReference();
	});

	it('the reference carries its rule, its starting revision and the 28 datasets of the fixture', () => {
		const lue = reference();
		expect(lue.regle).toBe(REGLE);
		expect(lue.baseRevision).toMatch(/^[0-9a-f]{40}$/);
		expect(Object.keys(lue.entrees)).toEqual(JEUX.map((jeu) => jeu.nom));
		expect(Object.keys(lue.entrees)).toHaveLength(28);
		expect(lue.departementsInstance).toEqual(INSTANCE);
	});

	it.each(JEUX.map((jeu) => jeu.nom))('%s: the card renders the frozen inventory', (nom) => {
		expect(inventaireCarte(JEUX.find((jeu) => jeu.nom === nom)!)).toEqual(reference().entrees[nom]);
	});

	it.each(JEUX.map((jeu) => jeu.nom))('%s: DatasetDetail renders the frozen inventory', (nom) => {
		expect(inventaireDetail(JEUX.find((jeu) => jeu.nom === nom)!)).toEqual(reference().entrees[nom]);
	});
});

describe('catalogue — the oracle fails when the detail loses a piece of metadata (mutations)', () => {
	const jeu = (nom: string) => JEUX.find((item) => item.nom === nom)!;
	const retirer = (selecteur: string, index = 0) => (racine: Element) => {
		const cibles = racine.querySelectorAll(selecteur);
		expect(cibles.length).toBeGreaterThan(index);
		cibles[index].remove();
	};
	const regionTitree = (titre: string) => (racine: Element) =>
		[...racine.querySelectorAll('section[aria-labelledby]')].find(
			(section) => texte(racine.ownerDocument.getElementById(section.getAttribute('aria-labelledby')!)) === titre
		)!;

	it.each([
		['ban', 'a reuse condition removed', (racine: Element) => regionTitree('Conditions de réutilisation')(racine).querySelector('p')!.remove()],
		['dvf', 'the first restriction removed', (racine: Element) => regionTitree('Restrictions de réutilisation')(racine).querySelector('li')!.remove()],
		['sirene', 'the last restriction removed', (racine: Element) => [...regionTitree('Restrictions de réutilisation')(racine).querySelectorAll('li')].at(-1)!.remove()],
		['dvf', 'two restrictions swapped', (racine: Element) => {
			const liste = regionTitree('Restrictions de réutilisation')(racine).querySelector('ul')!;
			liste.insertBefore(liste.children[1], liste.children[0]);
		}],
		['dvf', 'a limit removed', (racine: Element) => regionTitree('Limites de la source')(racine).querySelectorAll('li')[2].remove()],
		['permis', 'the quarantine reason removed', retirer('.dataset-quarantine p', 1)],
		['dvf', 'the cadence fact removed', (racine: Element) => [...racine.querySelectorAll('dt')].find((dt) => texte(dt) === 'Cadence')!.parentElement!.remove()],
		['dvf', 'the licence link removed', (racine: Element) => racine.querySelector('a')!.replaceWith('Licence Ouverte 2.0')],
		['antennes', 'the coverage reason removed', (racine: Element) => racine.querySelector('.dataset-state')!.replaceChildren()]
	])('%s: %s fails the equality with the reference', (nom, _mutation, muer) => {
		const attendu = reference().entrees[nom];
		expect(inventaireDetail(jeu(nom))).toEqual(attendu);
		expect(() => expect(inventaireDetail(jeu(nom), muer)).toEqual(attendu)).toThrow();
	});
});

describe('DatasetDetail — the rights come from `reutilisation`, never from `provenance.licence`', () => {
	it('words the published licence of `reutilisation` and never the provenance licence', () => {
		const dvf = structuredClone(JEUX.find((item) => item.nom === 'dvf')!);
		dvf.provenance!.licence = 'LICENCE-DE-PROVENANCE';
		const { container } = render(DatasetDetail, { props: { jeu: dvf, departementsInstance: INSTANCE } });
		expect(container.textContent).not.toContain('LICENCE-DE-PROVENANCE');
		const licence = [...container.querySelectorAll('a')].find((a) => a.textContent === dvf.reutilisation!.licence);
		expect(licence?.getAttribute('href')).toBe(dvf.reutilisation!.licence_url);
	});

	it('says « Non servie » when the API serves no reuse terms, whatever the provenance says', () => {
		const dvf = structuredClone(JEUX.find((item) => item.nom === 'dvf')!);
		dvf.reutilisation = null;
		const inventaire = inventaireDetail(dvf);
		expect(inventaire.faits.find((fait) => fait.terme === 'Licence')?.valeur).toBe('Non servie');
		expect(inventaire.conditions).toBeNull();
		expect(inventaire.restrictions).toBeNull();
	});
});
