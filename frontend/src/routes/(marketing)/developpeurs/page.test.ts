// /developpeurs (owner decisions of 2026-11-09 and 2026-11-11). Without accounts (production:
// VITE_ACCOUNTS_ENABLED=false) nobody can get a key: the page opens on what answers without one, the JSON
// export of a commune fiche, as a command that works as written (the example of the URL models, shared
// with /llms.txt), copiable, with an extract of the real answer; the API with a key comes last and says
// that keys are not open. With accounts the last panel keeps the personal key and its example.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fireEvent, render } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ accountsEnabled: true }));

// `ACCOUNTS_ENABLED` is a build-time constant; the mock reads it live so one module graph serves both states.
vi.mock('$lib/config', async (importOriginal) => {
	const mod = { ...(await importOriginal<typeof import('$lib/config')>()) };
	Object.defineProperty(mod, 'ACCOUNTS_ENABLED', { get: () => state.accountsEnabled, enumerable: true });
	return mod;
});

import { API_URL, SITE_URL } from '$lib/config';
import { EXTRAITS_EXPORT_COMMUNE } from '$lib/data/exemple-export-commune';
import { MODELES_URL } from '$lib/data/ouverture';
import Page from './+page.svelte';

const ROUTES = resolve(__dirname, '../..');
const CONTRAT = JSON.parse(readFileSync(resolve(__dirname, '../../../../../contracts/openapi.v1.json'), 'utf8')) as {
	paths: Record<string, unknown>;
};

/** One pattern per route of the repository: groups dropped, `[param]` any segment text, `[...rest]` anything. */
function motifsDesRoutes(dossier = ROUTES, segments: string[] = []): RegExp[] {
	const motifs: RegExp[] = [];
	if (['+page.svelte', '+server.ts'].some((f) => existsSync(join(dossier, f)))) {
		const chemin = segments
			.filter((s) => !/^\(.*\)$/.test(s))
			.map((s) =>
				s
					.split(/(\[\.\.\.[^\]]+\]|\[[^\]]+\])/)
					.map((part) =>
						part.startsWith('[...') ? '.*' : part.startsWith('[') ? '[^/]+' : part.replace(/[.*+?^${}()|\\]/g, '\\$&')
					)
					.join('')
			)
			.join('/');
		motifs.push(new RegExp(`^/${chemin}$`));
	}
	for (const entree of readdirSync(dossier, { withFileTypes: true })) {
		if (entree.isDirectory()) motifs.push(...motifsDesRoutes(join(dossier, entree.name), [...segments, entree.name]));
	}
	return motifs;
}

const MOTIFS = motifsDesRoutes();
const routeConnue = (chemin: string) => MOTIFS.some((m) => m.test(chemin));

const exempleDe = (modele: string) => {
	const exemple = MODELES_URL.find((m) => m.modele === modele)?.exemple;
	if (!exemple) throw new Error(`pas d'exemple pour ${modele}`);
	return exemple;
};
const EXPORT = exempleDe('/commune/{code_insee}.json');
const CODE_EXEMPLE = /\/commune\/([^/]+)\.json$/.exec(EXPORT)![1];

const rendre = (accountsEnabled: boolean) => {
	state.accountsEnabled = accountsEnabled;
	const { container } = render(Page);
	const commandes = [...container.querySelectorAll('.dev-exemple pre')].map((pre) => pre.textContent ?? '');
	return {
		container,
		texte: (container.textContent ?? '').replace(/\s+/g, ' '),
		titres: [...container.querySelectorAll('h2')].map((h) => h.textContent),
		commandes: commandes.filter((c) => c.startsWith('curl ')),
		liens: [...container.querySelectorAll('a')].map((a) => a.getAttribute('href') ?? '')
	};
};

afterEach(() => vi.unstubAllGlobals());

describe('la mécanique du test : les routes du dépôt', () => {
	it('reconnaît une page, un export à extension et une route à paramètre, et refuse une adresse inventée', () => {
		expect(routeConnue('/developpeurs')).toBe(true);
		expect(routeConnue('/commune/00000.json')).toBe(true);
		expect(routeConnue('/adresse/00000_0000_00000')).toBe(true);
		expect(routeConnue('/llms.txt')).toBe(true);
		expect(routeConnue('/inventee')).toBe(false);
		expect(routeConnue('/commune/00000/inventee')).toBe(false);
	});
});

describe('/developpeurs sans comptes', () => {
	it('s’intitule « Développeurs » et commence par ce qui répond sans clé ; l’API avec clé vient en dernier', () => {
		const { container, titres, texte } = rendre(false);
		expect(container.querySelector('h1')?.textContent).toBe('Développeurs');
		expect(texte).toContain("Les données d'une commune en JSON, sans compte ni clé.");
		expect(titres).toEqual(['Une commune en JSON, sans clé', 'Une adresse', 'Réutiliser les données', 'API avec clé']);
	});

	it('donne une commande qui marche telle quelle : l’exemple des modèles d’URL, sans texte à remplacer ni clé', () => {
		const { commandes } = rendre(false);
		expect(commandes).toEqual([`curl "${SITE_URL}${EXPORT}"`]);
		expect(commandes[0]).not.toContain('CODE_INSEE');
		expect(commandes[0]).not.toContain('X-API-Key');
	});

	it('copie la commande au clic et le dit sur le bouton', async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
		const { container } = rendre(false);
		const bouton = container.querySelector<HTMLButtonElement>('.dev-exemple button')!;
		expect(bouton.textContent?.trim()).toBe('Copier');
		await fireEvent.click(bouton);
		expect(writeText).toHaveBeenCalledWith(`curl "${SITE_URL}${EXPORT}"`);
		await vi.waitFor(() => expect(bouton.textContent?.trim()).toBe('Copié'));
	});

	it('montre, replié, un extrait de la réponse de la commune de l’exemple', () => {
		const { container } = rendre(false);
		const pli = container.querySelector('details.depliant')!;
		expect(pli.hasAttribute('open')).toBe(false);
		expect(pli.querySelector('summary')?.textContent?.trim()).toBe('Voir un extrait de la réponse');
		const extrait = pli.querySelector('pre')?.textContent ?? '';
		expect(extrait).toBe(EXTRAITS_EXPORT_COMMUNE[CODE_EXEMPLE]);
		expect(extrait).toContain(`"code_insee": "${CODE_EXEMPLE}"`);
	});

	it('explique les champs, le code et les erreurs', () => {
		const { container, texte } = rendre(false);
		expect([...container.querySelectorAll('dl.dev-champs dt')].map((dt) => dt.textContent?.trim())).toEqual([
			'etat',
			'sources',
			'donnees',
			'lu_le'
		]);
		expect(texte).toContain("Une absence n'est jamais un zéro.");
		expect(texte).toContain('La réponse est gardée en cache une heure.');
		expect(texte).toContain("Le code INSEE a cinq caractères et n'est pas le code postal ; en Corse il commence par 2A ou 2B.");
		expect(texte).toContain('Les erreurs sont au format application/problem+json.');
	});

	it('dit qu’une adresse n’a pas d’export JSON et où se lit sa fiche', () => {
		const { texte, liens } = rendre(false);
		expect(texte).toContain("Il n'y a pas encore d'export JSON pour une adresse.");
		expect(liens).toContain(exempleDe('/adresse/{cle_ban}'));
		expect(liens).toContain(exempleDe('/communes?q={texte}'));
	});

	it('dit la licence et l’attribution, et mène aux sources, à /llms.txt, aux conditions et au signalement', () => {
		const { texte, liens } = rendre(false);
		expect(texte).toContain('mentionner DataCommune est bienvenu, sans être une condition.');
		for (const href of ['/datasets', '/llms.txt', '/cgu', 'mailto:signalement@datacommune.fr']) expect(liens).toContain(href);
		// The public source repository (D-137), as an external link.
		expect(texte).toContain('Code source');
		expect(liens).toContain('https://github.com/DataCommune/datacommune');
	});

	it('dit que l’API n’est pas ouverte aux clés personnelles, sans proposer d’en obtenir une', () => {
		const { texte } = rendre(false);
		// Kept word for word: `marketing-truth.spec.ts` holds the first sentence.
		expect(texte).toContain('Ce site ne propose pas de comptes.');
		expect(texte).toContain("L'API n'est pas encore ouverte aux clés personnelles : sans clé, elle répond 401.");
		expect(texte).toContain('Le contrat est en bêta publique : il peut encore changer.');
		expect(texte).not.toContain('clé publique commune');
		expect(texte).not.toContain('Obtenir une clé API');
	});

	it('décrit la page pour ce qu’elle offre, sans promettre de clés', () => {
		rendre(false);
		// Tab title (owner, 2026-11-11): the name of the page alone, no separator sign.
		expect(document.title).toBe('Développeurs');
		const description =document.head.querySelector('meta[name="description"]')?.getAttribute('content');
		expect(description).toBe(
			"Les données d'une commune en JSON, sans compte ni clé ; contrat OpenAPI de l'API DataCommune."
		);
	});
});

describe('/developpeurs avec comptes', () => {
	it('garde la clé personnelle et son exemple dans le dernier panneau', () => {
		const { texte, titres, commandes } = rendre(true);
		expect(titres.at(-1)).toBe('API avec clé');
		expect(texte).toContain('Obtenir une clé API');
		expect(texte).not.toContain('Ce site ne propose pas de comptes.');
		expect(commandes).toEqual([
			`curl "${SITE_URL}${EXPORT}"`,
			`curl -H "X-API-Key: VOTRE_CLE" "${API_URL}/v1/commune?code_insee=CODE_INSEE"`
		]);
	});
});

describe.each([false, true])('chaque exemple affiché est une adresse connue (comptes : %s)', (comptes) => {
	it('les liens internes et les commandes sur le site mènent à une route du dépôt', () => {
		const { liens, commandes } = rendre(comptes);
		const internes = [
			...liens.filter((href) => href.startsWith('/')),
			...commandes.map((c) => /"([^"]+)"$/.exec(c)![1]).filter((url) => url.startsWith(SITE_URL)).map((url) => url.slice(SITE_URL.length))
		];
		// Non-vacuity: the export, the address example, the search and the four pages of the reuse panel.
		expect(internes.length).toBeGreaterThanOrEqual(6);
		for (const adresse of internes) expect(routeConnue(new URL(adresse, SITE_URL).pathname), adresse).toBe(true);
	});

	it('les adresses de l’API sont celles de sa référence ou un chemin de son contrat', () => {
		const { liens, commandes } = rendre(comptes);
		const api = [...liens, ...commandes.map((c) => /"([^"]+)"$/.exec(c)![1])].filter((url) => url.startsWith(API_URL));
		expect(api).toContain(`${API_URL}/openapi.json`);
		for (const url of api) {
			const chemin = new URL(url).pathname;
			expect(['/openapi.json', '/docs'].includes(chemin) || chemin in CONTRAT.paths, url).toBe(true);
		}
	});
});
