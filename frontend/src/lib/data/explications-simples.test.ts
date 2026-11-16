// « En clair » sentences (owner review of 09/11): every served theme and every covered field has its plain
// sentence, each keeps the writing rule, and the technical flags of a read never show as « non ».
import { readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import LigneCritere from '$lib/components/adresse/question/LigneCritere.svelte';
import { projeterTheme } from '$lib/api/rapport';
import { aplatirDetails, type NoeudDetail } from './adresse-rapport';
import { COVERED_FIELD_IDS } from './explications';
import {
	DRAPEAUX_MUETS_SI_FAUX,
	EN_CLAIR_CHAMPS,
	EN_CLAIR_THEMES,
	LIBELLE_EN_CLAIR,
	RESERVES_LECTURE,
	enClairChamp,
	enClairTheme,
	nombreDeMots,
	raisonReserve
} from './explications-simples';
import { volet } from './adresse-fiche';
import { voletComparables } from './adresse-ventes';

const DOSSIER_THEMES = join(resolve(process.cwd(), '..'), 'api/backend/app/rapport/themes');

/** Theme ids of the backend registry, one `THEME = DefinitionTheme(id=...)` per file. */
function themesServis(): string[] {
	return readdirSync(DOSSIER_THEMES)
		.filter((f) => f.endsWith('.py') && f !== '__init__.py')
		.map((f) => {
			const source = readFileSync(join(DOSSIER_THEMES, f), 'utf8');
			return /id="([^"]+)"/.exec(source.slice(source.indexOf('THEME = DefinitionTheme(')))?.[1] ?? f;
		});
}

const PHRASES: [string, string][] = [
	...Object.entries(EN_CLAIR_THEMES).map(([id, p]): [string, string] => [`thème ${id}`, p]),
	...Object.entries(EN_CLAIR_CHAMPS).map(([id, p]): [string, string] => [`champ ${id}`, p])
];

// Acronyms a reader may meet as a name, explained by the words right after them.
const SIGLES_NOMS = new Set(['ATMO']);

describe('couverture', () => {
	it('chaque thème servi par le registre a sa phrase, et aucune phrase ne vise un thème disparu', () => {
		const servis = themesServis();
		expect(servis.length).toBeGreaterThan(40);
		expect(servis.filter((id) => enClairTheme(id) === null)).toEqual([]);
		expect(Object.keys(EN_CLAIR_THEMES).filter((id) => !servis.includes(id))).toEqual([]);
	});

	it('chaque champ couvert a sa phrase, et seulement eux', () => {
		expect(COVERED_FIELD_IDS.filter((id) => enClairChamp(id) === null)).toEqual([]);
		expect(Object.keys(EN_CLAIR_CHAMPS).sort()).toEqual([...COVERED_FIELD_IDS].sort());
	});

	it('un identifiant inconnu ou hérité de Object.prototype n’a pas de phrase', () => {
		expect(enClairChamp('constructor')).toBeNull();
		expect(enClairChamp(null)).toBeNull();
		expect(enClairTheme('toString')).toBeNull();
	});
});

describe('règle de rédaction', () => {
	it.each(PHRASES)('%s : au plus 25 mots, une phrase, apostrophe typographique', (_, phrase) => {
		expect(nombreDeMots(phrase)).toBeLessThanOrEqual(25);
		expect(phrase).toMatch(/^\p{Lu}/u);
		expect(phrase).toMatch(/[^.]\.$/);
		expect(phrase.slice(0, -1)).not.toMatch(/[.!?]\s/);
		expect(phrase).not.toContain("'");
	});

	it.each(PHRASES)('%s : ni promesse, ni jugement, ni vocabulaire des badges', (_, phrase) => {
		expect(phrase).not.toMatch(/sans danger|aucun risque|garanti|sûr à|sécuris|\bbon\b|\bmauvais\b|trop cher|favorable|vigilance|sans objet|évitez|vérifiez/i);
	});

	// The forbidden wordings of tests/e2e/honnetete-publique.spec.ts (D-01, F44, T13, 04-UI-SPEC): these
	// sentences are rendered in the panels of every surface, so they obey the strictest list.
	const INTERDITS: [string, RegExp][] = [
		['jargon de niveau interne', /\bL[012]\b/],
		['verdict normatif', /\bFavorable\b|\bConforme\b|\bVigilance\b/],
		['verdict « passoire »', /\bpassoires?\b/i],
		['« bien » pour une adresse', /\b(du|ce|au) bien\b/i],
		['« maille »', /\bmailles?\b/i],
		['« Diagnostics » (T13)', /Diagnostics(?! de performance énergétique| et risques officiels| officiels)/i],
		['« le logement » sujet d’une valeur DPE', /le logement (consomme|émet|est classé|a une étiquette)/i],
		['« non disponible »', /\bnon disponible\b/i],
		['« instantané »', /instantané/i]
	];
	it.each(PHRASES)('%s : aucun texte interdit des surfaces publiques', (_, phrase) => {
		expect(INTERDITS.filter(([, motif]) => motif.test(phrase)).map(([nom]) => nom)).toEqual([]);
	});

	it.each(PHRASES)('%s : tout sigle est développé à côté de lui', (_, phrase) => {
		const sigles = [...new Set(phrase.match(/\b[A-Z][A-Z0-9₂]{1,}\b/g) ?? [])].filter((s) => !/^[A-G]$/.test(s) && s !== '5G');
		for (const sigle of sigles) {
			if (SIGLES_NOMS.has(sigle)) continue;
			// « mots (SIGLE) » or « SIGLE (mots) »
			expect(phrase.includes(`(${sigle})`) || phrase.includes(`(${sigle},`) || phrase.includes(`${sigle} (`), `${sigle} dans « ${phrase} »`).toBe(true);
		}
	});

	it('compte les mots comme un lecteur : une élision fait un mot, la ponctuation aucun', () => {
		expect(nombreDeMots('Usine qui stocke des produits dangereux ; elle peut imposer des règles autour d’elle en cas d’accident.')).toBe(16);
	});

	it('l’exemple d’Ianis pour Seveso est repris tel quel', () => {
		expect(enClairTheme('seveso')).toBe('Usine qui stocke des produits dangereux ; elle peut imposer des règles autour d’elle en cas d’accident.');
	});
});

describe('affichage en tête du détail', () => {
	const seveso = projeterTheme({
		id: 'seveso',
		section: 'risques',
		ordre: 62,
		titre: 'Sites Seveso',
		etat: 'zero',
		ton: 'rassurant',
		verdict: 'Aucun site Seveso à moins de 5 km.',
		chiffres: [],
		granularite: { niveau: 'rayon', rayon_m: 5000 },
		details: { sites: [], liste_incomplete: false },
		sources: [{ cle: 'georisques', nom: 'Géorisques', producteur: 'BRGM', url: 'https://www.georisques.gouv.fr', licence: 'Licence Ouverte 2.0', millesime: null, mode: 'direct' }],
		pannes_partielles: []
	})!;

	it('la phrase « En clair » ouvre le détail d’un critère, avant les chiffres et la source', () => {
		const { container } = render(LigneCritere, { props: { theme: seveso, niveauTitre: 4 } });
		const premier = container.querySelector('.det-in')?.firstElementChild;
		expect(premier?.className).toContain('en-clair');
		expect(premier?.textContent).toBe(enClairTheme('seveso'));
	});

	it('« Liste incomplète : non » n’est plus écrit', () => {
		const { container } = render(LigneCritere, { props: { theme: seveso, niveauTitre: 4 } });
		expect(container.querySelector('.det-in')?.textContent).not.toMatch(/Liste incompl[eè]te/);
	});

	it('un volet de source commence par « En clair »', () => {
		const entrees = volet({
			fieldId: 'adresse.parcelle.contenance',
			objet: 'Parcelle',
			valeur: { field_id: 'adresse.parcelle.contenance', valeur: 420 } as never,
			lignes: []
		});
		expect(entrees[0]).toEqual({ terme: LIBELLE_EN_CLAIR, valeur: enClairChamp('adresse.parcelle.contenance') });
		expect(entrees[1].terme).toBe('Définition');
	});
});

describe('drapeaux techniques d’une lecture', () => {
	const valeurs = (noeuds: NoeudDetail[]): string[] =>
		noeuds.flatMap((n) => (n.type === 'groupe' ? valeurs(n.enfants) : n.type === 'valeur' ? [`${n.libelle} = ${n.valeur}`] : []));

	it.each(Object.entries(RESERVES_LECTURE))('%s non levé : rien n’est affiché', (cle, { quand }) => {
		expect(aplatirDetails({ [cle]: !quand }, 0, false, 'seveso')).toEqual([]);
	});

	it.each(Object.entries(RESERVES_LECTURE))('%s levé : la réserve et sa raison', (cle, { quand, libelle }) => {
		expect(valeurs(aplatirDetails({ [cle]: quand }, 0, false, 'seveso'))).toEqual([`${libelle} = ${raisonReserve(cle, 'seveso')}`]);
	});

	it('la raison de « Liste incomplète » dépend du thème : lecture coupée, rayon hors couverture, liste écourtée', () => {
		expect(raisonReserve('liste_incomplete', 'seveso')).toMatch(/qu’une partie de la liste/);
		expect(raisonReserve('liste_incomplete', 'equipements')).toMatch(/département que DataCommune ne couvre pas/);
		expect(raisonReserve('liste_incomplete', 'copropriete')).toMatch(/Seule une partie/);
		expect(raisonReserve('liste_incomplete', 'theme_inconnu')).toMatch(/pas permis de tout lister/);
	});

	it('une réserve imbriquée (eau du robinet) suit la même règle', () => {
		const ok = aplatirDetails({ bacteriologique: { prelevements: 12, lecture_complete: true } }, 0, false, 'eau_potable');
		expect(JSON.stringify(ok)).not.toMatch(/Lecture/);
		const partielle = aplatirDetails({ bacteriologique: { prelevements_lus: 4, lecture_complete: false } }, 0, false, 'eau_potable');
		expect(valeurs(partielle)).toContain(`Lecture incomplète = ${raisonReserve('lecture_complete', 'eau_potable')}`);
	});

	it.each([...DRAPEAUX_MUETS_SI_FAUX])('%s à faux ne s’écrit pas « non »', (cle) => {
		expect(aplatirDetails({ [cle]: false })).toEqual([]);
		expect(valeurs(aplatirDetails({ [cle]: true }))[0]).toMatch(/= oui$/);
	});

	it('un booléen qui informe garde son « non » (dernier contrôle conforme, voie électrifiée)', () => {
		expect(valeurs(aplatirDetails({ dernier_conforme: false, electrifiee: false }))).toEqual(['Dernier prélèvement conforme = non', 'Électrifiée = non']);
	});

	it('« Liste complète : oui » n’est jamais écrit', () => {
		const tout = JSON.stringify([aplatirDetails({ liste_incomplete: false }), aplatirDetails({ lecture_complete: true })]);
		expect(tout).not.toMatch(/compl[eè]te/i);
	});

	it('les ventes ressemblantes disent pourquoi la liste est incomplète, et rien quand elle ne l’est pas', () => {
		const servi = { effectif: 3, liste_incomplete: true, criteres_appliques: null, rattachement: null, source: null, ventes: [] } as never;
		const incomplete = voletComparables(servi);
		expect(incomplete.find((e) => e.terme === 'Liste incomplète')).toMatchObject({ valeur: expect.stringMatching(/département que DataCommune ne couvre pas/) });
		const complete = voletComparables({ ...(servi as object), liste_incomplete: false } as never);
		expect(complete.some((e) => e.terme === 'Liste incomplète')).toBe(false);
	});
});
