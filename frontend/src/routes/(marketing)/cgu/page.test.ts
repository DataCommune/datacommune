// Terms of use (04.7-14c, audit LIC-02, MEN-02, LIC-03): no ban that the Licence Ouverte contradicts,
// the quota the code applies, and the licence of the code with its public repository (05-07 lot 6, D-137).
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import SiteFooter from '$lib/components/SiteFooter.svelte';
import { ATTRIBUTION, CODE_SOURCE_URL } from '$lib/data/ouverture';
import Mentions from '../mentions-legales/+page.svelte';
import Cgu from './+page.svelte';

const RACINE = resolve(__dirname, '../../../../..');
const lire = (chemin: string) => readFileSync(resolve(RACINE, chemin), 'utf8');
const texte = (Page: typeof Cgu) => (render(Page).container.textContent ?? '').replace(/\s+/g, ' ');

describe('/cgu — réutilisation des données (LIC-02)', () => {
	it('n’interdit ni la revente ni la réutilisation commerciale', () => {
		const page = texte(Cgu);
		expect(page).not.toMatch(/revendre|revente|usage commercial interdit|non commercial/i);
		expect(page).toContain('La réutilisation, y compris commerciale, est permise dans ce cadre.');
	});

	it('garde les deux obligations qui s’imposent : citer la source, et les restrictions DVF', () => {
		const page = texte(Cgu);
		// Licence Ouverte: the source and the date of its last update (paternité).
		expect(page).toContain('Citer la source de chaque donnée réutilisée (son producteur) et la date de sa dernière mise à jour');
		// DVF reuse: neither the purpose nor the effect of re-identification, and prevent external indexing.
		expect(page).toContain('ait pour objet ou pour effet de permettre la réidentification des personnes concernées');
		expect(page).toContain('prendre les mesures qui empêchent leur indexation par un moteur de recherche externe');
		expect(page).toContain('R*112 A-3');
	});

	it('répercute chaque restriction DVF que le catalogue affiche, dont la finalité (04.2-04)', () => {
		// The DGFiP conditions as /datasets shows them (catalogue reference fixture): each one must reach
		// the reuser through the CGU, not only the purpose-free pair « réidentification + indexation ».
		const catalogue = lire('frontend/src/lib/data/__fixtures__/catalogue-inventaire-reference.json');
		const dvf = [...catalogue.matchAll(/"restrictions": \[([^\]]*R\*112 A-3[^\]]*)\]/g)].map((m) => m[1]);
		expect(dvf, 'restrictions DVF du catalogue').toHaveLength(1);
		const page = texte(Cgu);
		const reperes: [RegExp, string][] = [
			[/réidentification/, 'pour effet de permettre la réidentification'],
			[/indexation/, 'empêchent leur indexation par un moteur de recherche externe'],
			[/concourir à la transparence des marchés fonciers et immobiliers/, 'ne les réutiliser que pour concourir à la transparence des marchés fonciers et immobiliers']
		];
		for (const [dansLeCatalogue, dansLesCgu] of reperes) {
			expect(dvf[0]).toMatch(dansLeCatalogue);
			expect(page).toContain(dansLesCgu);
		}
	});

	it('dit la même consigne d’attribution que les mentions et llms.txt (revue 14c, IMPORTANT)', () => {
		// One rule for a reuser: the producer, the date of its last update, the producer's licence;
		// DataCommune is asked for, never imposed.
		for (const consigne of [ATTRIBUTION, texte(Cgu), texte(Mentions)]) {
			expect(consigne).toMatch(/source de chaque (?:valeur|donnée réutilisée) \(son producteur\) et la date de sa dernière mise à jour/);
			expect(consigne).toContain('DataCommune, avec le lien de la page, est bienvenu sans être une condition');
		}
	});
});

describe('/cgu — quota des fiches d’adresse (MEN-02)', () => {
	it('cite la valeur que le code applique, et la pile publique par défaut', () => {
		const quota = Number(/^export const DEFAULT_WEEKLY_QUOTA = (\d+);$/m.exec(lire('frontend/src/lib/server/fiche-quota.ts'))?.[1]);
		expect(quota).toBe(100);
		expect(lire('api/docker-compose.public.yml')).toContain(`FICHE_QUOTA_WEEKLY: \${FICHE_QUOTA_WEEKLY:-${quota}}`);
		expect(texte(Cgu)).toContain(`limitée à ${quota} fiches différentes par période de 7 jours`);
	});

	it('documente le seuil dans frontend/.env.example, à la même valeur (04.2-04, tâche 2)', () => {
		const quota = Number(/^export const DEFAULT_WEEKLY_QUOTA = (\d+);$/m.exec(lire('frontend/src/lib/server/fiche-quota.ts'))?.[1]);
		const exemple = lire('frontend/.env.example');
		expect(exemple).toMatch(new RegExp(`^FICHE_QUOTA_WEEKLY=${quota}\\r?$`, 'm'));
		// The two traps a deployer meets: 0 switches the quota off, and `restart` keeps the old value.
		expect(exemple).toContain('0 turns the quota off');
		expect(exemple).toContain('--force-recreate');
	});

	it('décrit la période comme le code : sept jours, ouverte par la première fiche, pas glissante', () => {
		const quota = lire('frontend/src/lib/server/fiche-quota.ts');
		expect(quota).toMatch(/^export const WEEK_MS = 7 \* 24 \* 60 \* 60 \* 1000;$/m);
		expect(quota).toContain('a fixed window of');
		const page = texte(Cgu);
		expect(page).toContain('la période commençant à la première fiche ouverte');
		expect(page).not.toMatch(/glissant/i);
	});
});

describe('Code source (LIC-03)', () => {
	it.each([
		['/cgu', Cgu],
		['/mentions-legales', Mentions]
	])('%s : AGPL-3.0-or-later, le dépôt public et son seul lien, sans date', (_chemin, Page) => {
		const { container } = render(Page);
		const page = (container.textContent ?? '').replace(/\s+/g, ' ');
		expect(page).toContain(
			'GNU Affero General Public License, version 3 ou toute version ultérieure (AGPL-3.0-or-later), dans le dépôt public github.com/DataCommune/datacommune'
		);
		const liens = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href') ?? '');
		expect(liens.filter((href) => /github|gitlab|codeberg|forge|\.git\b/i.test(href))).toEqual([CODE_SOURCE_URL]);
		expect(CODE_SOURCE_URL).toBe('https://github.com/DataCommune/datacommune');
		// The whole « Code source » block, not its first sentence (Codex 14c n° 4): its section in the
		// mentions, its paragraph in CGU § 5.
		const titre = [...container.querySelectorAll('h2, strong')].find((el) => el.textContent?.trim() === 'Code source');
		expect(titre, 'bloc « Code source »').toBeDefined();
		const bloc = ((titre!.tagName === 'H2' ? titre!.closest('section') : titre!.closest('p'))?.textContent ?? '').replace(/\s+/g, ' ');
		expect(bloc).toContain('AGPL-3.0-or-later');
		// Present tense since the repository is open: served only from a commit on its master (D-135).
		expect(bloc).toContain('est diffusé sous la licence');
		expect(bloc).not.toContain('sera diffusé');
		expect(bloc, 'aucune date promise').not.toMatch(/\b(19|20)\d{2}\b|janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|prochainement|bientôt/i);
	});

	it('le code n’est plus dit « propriété de l’éditeur »', () => {
		expect(texte(Cgu)).not.toMatch(/code source du Service, son design[^.]*propriété/);
		expect(texte(Mentions)).not.toContain('(textes, design, code source) est la propriété');
	});

	it('le pied de page mène au dépôt public par un seul lien « Code source »', () => {
		const { getAllByRole } = render(SiteFooter);
		const liens = getAllByRole('link');
		expect(liens.filter((a) => a.textContent?.trim() === 'Code source').map((a) => a.getAttribute('href'))).toEqual([CODE_SOURCE_URL]);
		expect(liens.map((a) => a.getAttribute('href') ?? '').filter((href) => /github|gitlab|codeberg|forge|\.git\b/i.test(href))).toEqual([CODE_SOURCE_URL]);
	});
});
