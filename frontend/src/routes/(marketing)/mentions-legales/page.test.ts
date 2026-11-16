// Legal notice and privacy durations (audit C16, PRIV-01, 04.7-14): the director of publication is
// named, the main sources are those of the shared list, and no retention period contradicts the code
// (weekly purge of rows older than 7 days, container logs bounded by size).
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import { SOURCES_PRINCIPALES } from '$lib/data/ouverture';
import { CANAUX, cheminMesure } from '$lib/mesure-audience';
import Mentions from './+page.svelte';
import Confidentialite from '../confidentialite/+page.svelte';

const RACINE = resolve(__dirname, '../../../../..');
const lire = (chemin: string) => readFileSync(resolve(RACINE, chemin), 'utf8');
const texte = (Page: typeof Mentions) => (render(Page).container.textContent ?? '').replace(/\s+/g, ' ');

describe('/mentions-legales', () => {
	it('nomme l’éditeur et le directeur de la publication, et date la page', () => {
		const page = texte(Mentions);
		expect(page).toContain('Directeur de la publication : Ianis P.');
		expect(page).toMatch(/Dernière mise à jour : \d{1,2} \S+ \d{4}/);
	});

	it('liste les sources principales et leur licence, renvoie à la liste complète et aux limites', () => {
		const { container } = render(Mentions);
		const page = (container.textContent ?? '').replace(/\s+/g, ' ');
		for (const source of SOURCES_PRINCIPALES) expect(page).toContain(`${source.donnees} : ${source.producteur}, ${source.licence}`);
		const liens = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href'));
		expect(liens).toEqual(expect.arrayContaining(['/datasets', '/ce-que-nous-ne-montrons-pas', '/confidentialite']));
	});

	it('04.8-06 : email et directeur de la publication en dl.tab, même ordre, mêmes valeurs (D-13)', () => {
		const { container } = render(Mentions);
		const lignes = [...container.querySelectorAll('dl.tab > div.tab-l')];
		expect(lignes.map((l) => l.querySelector('dt')?.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'Email :',
			'Directeur de la publication :'
		]);
		expect(lignes[0].querySelector('dd a')?.getAttribute('href')).toBe('mailto:contact@datacommune.fr');
		expect(lignes[1].querySelector('dd')?.textContent).toBe('Ianis P., éditeur du site.');
		expect(container.querySelector('.public-article')).toBeNull();
	});

	it('ne promet aucune durée de journaux', () => {
		expect(texte(Mentions)).not.toMatch(/\d+ jours/);
	});
});

describe('/confidentialite — mesure d’audience (04.7-14b)', () => {
	it('nomme GoatCounter chez l’éditeur, sans cookie, et les conditions de la CNIL avec leur source', () => {
		const { container } = render(Confidentialite);
		const page = (container.textContent ?? '').replace(/\s+/g, ' ');
		expect(page).toContain("Le logiciel libre GoatCounter, installé chez l'éditeur");
		expect(page).toContain("elle ne dépose aucun cookie et n'écrit rien dans votre navigateur");
		expect(page).toContain("aucun recoupement avec d'autres traitements ni transmission à un tiers");
		expect(page).toContain('par page et par heure, gardés deux ans au plus');
		expect(page).toContain('effacée par la purge quotidienne qui suit ses 30 jours');
		expect(page).not.toMatch(/Cloudflare Web Analytics|aucun outil de mesure d'audience n'est activé/);
		const liens = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href') ?? '');
		expect(liens.some((href) => href.startsWith('https://www.cnil.fr/fr/cookies-et-autres-traceurs/'))).toBe(true);
	});

	it('les chemins cités sont ceux que le site envoie pour une adresse et pour un document', () => {
		const page = texte(Confidentialite);
		expect(page).toContain(`« ${cheminMesure({ id: '/(marketing)/adresse/[id]', params: { id: '44109_zz04_00004' }, pathname: '/adresse/44109_zz04_00004', statut: 200 })} » pour toute fiche d'adresse`);
		expect(page).toContain(`« ${cheminMesure({ id: '/(marketing)/dpe/[numero]', params: { numero: '2644E8000030X' }, pathname: '/dpe/2644E8000030X', statut: 200 })} » pour tout document DPE`);
		expect(page).toContain("Ni votre adresse IP, ni la page d'où vous venez, ni le titre de la page ne lui parviennent.");
	});
});

// Provenance by channel (2026-11-11), sentences agreed word for word by the owner.
describe('/confidentialite — provenance par canal (2026-11-11)', () => {
	it('dit qu’un lien diffusé par l’éditeur peut porter un nom de canal, compté sans identifiant', () => {
		const page = texte(Confidentialite);
		expect(page).toContain(
			"sans recherche ni paramètre. Un lien vers le site diffusé par l'éditeur lui-même peut porter le nom d'un canal, par exemple « reddit » ou « mail » : le site compte alors une arrivée par ce canal, sans identifiant, et seulement si ce nom figure dans une liste fixée d'avance. Le logiciel libre GoatCounter, installé chez l'éditeur"
		);
		// The examples named are channels of the closed list.
		expect(CANAUX).toEqual(expect.arrayContaining(['reddit', 'mail']));
	});

	it('la page d’où vient le visiteur n’est toujours pas lue : la phrase reste, mot pour mot', () => {
		expect(texte(Confidentialite)).toContain("Ni votre adresse IP, ni la page d'où vous venez, ni le titre de la page ne lui parviennent.");
		// The code keeps that promise: the referrer is read nowhere, and the count is sent without it.
		const module = lire('frontend/src/lib/mesure-audience.ts');
		expect(module).not.toMatch(/document\.referrer/);
		expect(lire('frontend/src/routes/+layout.svelte')).not.toMatch(/referrer/);
		expect(module).toContain("referrerPolicy: 'no-referrer'");
	});

	it('finalité et date de mise à jour', () => {
		const page = texte(Confidentialite);
		expect(page).toContain(
			"Mesure d'audience : savoir quelles pages servent et par quels liens diffusés par l'éditeur les lecteurs arrivent, en nombre de pages vues, pour améliorer le site."
		);
		expect(page).toContain('Dernière mise à jour : 11 novembre 2026');
	});

	it('rien n’est écrit dans le navigateur par la mesure (ni cookie, ni stockage)', () => {
		const sources = lire('frontend/src/lib/mesure-audience.ts') + lire('frontend/src/routes/+layout.svelte');
		expect(sources).not.toMatch(/localStorage|sessionStorage|document\.cookie|indexedDB/);
	});
});

describe('/confidentialite — carte de l’en-tête d’une fiche d’adresse (04.7-17)', () => {
	it('dit que le navigateur ne contacte jamais l’IGN et ce que l’IGN peut apprendre', () => {
		const page = texte(Confidentialite);
		expect(page).toContain('récupérée et mise en cache par notre serveur');
		expect(page).toContain('Pour l’afficher, votre navigateur ne contacte jamais l’IGN');
		// The Géoportail link is the one way to the IGN, by the visitor's own click: said, not hidden.
		expect(page).toContain('si vous le suivez, c’est votre navigateur qui le contacte, avec la position de l’adresse');
		expect(page).toContain('(adresse IP, en-têtes, cookies)');
		expect(page).toContain('l’IGN peut apprendre qu’une adresse est consultée à un moment donné, mais pas par qui');
		// Date of the last change of the page (provenance by channel, agreed by the owner on 2026-11-11).
		expect(page).toMatch(/Dernière mise à jour : 11 novembre 2026/);
	});
});

describe('/confidentialite — durées des journaux (PRIV-01)', () => {
	it('dit la purge que le code programme : hebdomadaire, des lignes de plus de 7 jours', () => {
		expect(lire('api/backend/app/scheduler/jobs.py')).toContain("INTERVAL '7 days'");
		expect(lire('infra/systemd/datacommune-job@cleanup-api-log.timer')).toMatch(/^OnCalendar=Sun /m);
		const page = texte(Confidentialite);
		expect(page).toContain('purgé une fois par semaine des lignes de plus de 7 jours');
		expect(page).toContain('au plus 14 jours');
		expect(page).not.toContain('au plus 30 jours');
		// True in production only once the purge timer runs on the public server (audit C1): a
		// prerequisite of publication kept by the orchestrator, not a sentence of this page.
	});

	it('dit ce que le journal des requêtes garde : le gabarit de route, jamais l’adresse consultée', () => {
		expect(lire('api/backend/app/middleware.py')).toContain('never the real path (decision n° 26)');
		const page = texte(Confidentialite);
		expect(page).toContain("jamais l'adresse ni la commune consultée");
		expect(page).not.toContain('adresse demandée');
	});

	it('dit la rotation par taille des journaux des services, telle que la pile publique la fixe', () => {
		const compose = lire('api/docker-compose.public.yml');
		expect(compose).toMatch(/max-size: "10m"/);
		expect(compose).toMatch(/max-file: "3"/);
		expect(texte(Confidentialite)).toContain('trois fichiers de 10 Mo par service');
	});
});
