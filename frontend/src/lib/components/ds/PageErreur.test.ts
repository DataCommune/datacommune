// Shared error page (socle of 2026-11-09, T5): one template for the three error boundaries of the public
// site (`routes/+error.svelte`, `adresse/+error.svelte`, `dpe/[numero]/+error.svelte`), none of whose states
// may be lost: a 404 with its ways out, a 429 with the date the allowance comes back, a 503 announced with
// its reload.
import { createRawSnippet } from 'svelte';
import { fireEvent, render, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import PageErreur from './PageErreur.svelte';

const SORTIES = [
	{ href: '/', titre: 'L’accueil' },
	{ href: '/communes', titre: 'Toutes les communes' }
];
const texte = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

describe('PageErreur', () => {
	it('404: the title, the message as the lead, the ways out; no status line, no panel, no button', () => {
		const { container } = render(PageErreur, {
			titre: 'Page introuvable',
			message: 'Cette adresse ou cette page n’existe pas sur DataCommune.',
			liens: SORTIES
		});
		expect(container.querySelector('.simple.gabarit')).not.toBeNull();
		expect([...container.querySelectorAll('h1')].map(texte)).toEqual(['Page introuvable']);
		expect(texte(container.querySelector('.erreur-chapo'))).toBe('Cette adresse ou cette page n’existe pas sur DataCommune.');
		expect(container.querySelector('.erreur-statut')).toBeNull();
		expect(container.querySelector('.notice')).toBeNull();
		expect(container.querySelector('button')).toBeNull();
		expect(container.querySelector('nav')).toBeNull();
		const sorties = container.querySelector('section.panneau');
		expect(texte(sorties!.querySelector('h2'))).toBe('Pour repartir');
		expect([...sorties!.querySelectorAll('ul.liste-liens a')].map((a) => [a.getAttribute('href'), texte(a.querySelector('.lien-t'))])).toEqual([
			['/', 'L’accueil'],
			['/communes', 'Toutes les communes']
		]);
	});

	it('503: the status above the title, the outage announced in a state panel, the reload busy while it runs', async () => {
		let finir!: () => void;
		const action = vi.fn(() => new Promise<void>((resolve) => (finir = resolve)));
		const { container, getByRole } = render(PageErreur, {
			titre: 'Adresse momentanément indisponible',
			statut: 503,
			ariane: 'Adresse',
			etat: 'Les données de cette adresse sont momentanément indisponibles',
			panne: true,
			message: "Le service n'a pas répondu. Aucune valeur n'est affichée plutôt qu'une valeur incomplète.",
			relance: { libelle: 'Recharger la fiche', action },
			liens: SORTIES
		});
		// The status is read before the title, the breadcrumb before both.
		const ordre = [...container.querySelectorAll('nav, .erreur-statut, h1')].map((el) => el.tagName === 'P' ? texte(el) : el.tagName.toLowerCase());
		expect(ordre).toEqual(['nav', 'Erreur 503', 'h1']);
		const alerte = getByRole('alert');
		expect(alerte.classList.contains('notice-warn')).toBe(true);
		expect(texte(alerte.querySelector('h2'))).toBe('Les données de cette adresse sont momentanément indisponibles');
		expect(texte(alerte.querySelector('p'))).toBe("Le service n'a pas répondu. Aucune valeur n'est affichée plutôt qu'une valeur incomplète.");
		expect(container.querySelector('.erreur-chapo')).toBeNull();

		const bouton = getByRole('button', { name: 'Recharger la fiche' }) as HTMLButtonElement;
		expect(alerte.contains(bouton)).toBe(true);
		expect(bouton.type).toBe('button');
		expect(bouton.classList.contains('bouton-secondaire')).toBe(true);
		expect(bouton.disabled).toBe(false);
		await fireEvent.click(bouton);
		expect(action).toHaveBeenCalledTimes(1);
		await waitFor(() => expect(bouton.disabled).toBe(true));
		expect(bouton.getAttribute('aria-busy')).toBe('true');
		finir();
		await waitFor(() => expect(bouton.disabled).toBe(false));
		expect(bouton.getAttribute('aria-busy')).toBe('false');
		// One h1, then the panel and the ways out as h2: no level skipped.
		expect([...container.querySelectorAll('h1, h2, h3')].map((h) => h.tagName)).toEqual(['H1', 'H2', 'H2']);
	});

	it('503: a reload that fails gives the button back', async () => {
		const action = vi.fn(() => Promise.reject(new Error('still down')));
		const { getByRole } = render(PageErreur, { titre: 'Indisponible', message: 'Le service n’a pas répondu.', panne: true, relance: { libelle: 'Réessayer', action } });
		const bouton = getByRole('button', { name: 'Réessayer' }) as HTMLButtonElement;
		await fireEvent.click(bouton);
		await waitFor(() => expect(action).toHaveBeenCalledTimes(1));
		await waitFor(() => expect(bouton.disabled).toBe(false));
		// Without a state panel the lead itself is the announcement, and the button follows it.
		expect(getByRole('alert').classList.contains('erreur-chapo')).toBe(true);
	});

	it('429: the served sentence keeps its threshold and its date, in a state panel that is not an outage', () => {
		const message = 'Vous avez consulté 50 fiches cette semaine. La consultation reprend le lundi 16 novembre 2026.';
		const { container, getByRole, queryByRole } = render(PageErreur, {
			titre: 'Limite de consultation atteinte',
			statut: 429,
			etat: 'Limite de consultation atteinte',
			message,
			liens: [{ href: '/', titre: 'Revenir à la recherche' }]
		});
		expect(texte(container.querySelector('.erreur-statut'))).toBe('Erreur 429');
		expect(queryByRole('alert')).toBeNull();
		const etat = getByRole('status');
		expect(etat.classList.contains('notice-warn')).toBe(false);
		expect(texte(etat.querySelector('p'))).toBe(message);
		expect(container.querySelector('button')).toBeNull();
	});

	it('spells out an acronym of the message once, never in a title nor inside a quotation; `developper` off keeps the text as given', () => {
		const props = {
			titre: 'Numéro DPE non valide',
			etat: 'Numéro DPE non valide',
			message: '« DPE-X » n’a pas la forme d’un numéro DPE.'
		};
		const { container, unmount } = render(PageErreur, props);
		expect(texte(container.querySelector('h1'))).toBe('Numéro DPE non valide');
		expect(texte(container.querySelector('.notice h2'))).toBe('Numéro DPE non valide');
		expect(texte(container.querySelector('.notice p'))).toBe('« DPE-X » n’a pas la forme d’un numéro DPE (diagnostic de performance énergétique).');
		unmount();
		const brut = render(PageErreur, { ...props, developper: false });
		expect(texte(brut.container.querySelector('.notice p'))).toBe(props.message);
	});

	it('renders what the page adds between the message and the ways out', () => {
		const { container } = render(PageErreur, {
			titre: 'Page introuvable',
			message: 'Cette page n’existe pas.',
			liens: SORTIES,
			children: createRawSnippet(() => ({ render: () => '<div class="champ-de-la-page">Chercher</div>' }))
		});
		const ordre = [...container.querySelectorAll('.erreur-chapo, .champ-de-la-page, section.panneau')].map((el) => el.className.split(' ')[0]);
		expect(ordre).toEqual(['erreur-chapo', 'champ-de-la-page', 'panneau']);
	});

	it('without ways out there is no empty panel', () => {
		const { container } = render(PageErreur, { titre: 'Une erreur est survenue', statut: 500, message: 'Le service n’a pas répondu.' });
		expect(container.querySelector('section.panneau')).toBeNull();
		expect(texte(container.querySelector('.erreur-statut'))).toBe('Erreur 500');
	});

	// Props added for the boundaries that must keep today's wording (2026-11-10): each defaults to the socle's form.
	it('`racine` names the first step of the breadcrumb (default « Accueil »), the link still goes home', () => {
		const defaut = render(PageErreur, { titre: 'Indisponible', ariane: 'Adresse', message: 'Le service n’a pas répondu.' });
		expect([...defaut.container.querySelectorAll('nav a')].map(texte)).toEqual(['Accueil']);
		defaut.unmount();
		const { container } = render(PageErreur, { titre: 'Indisponible', ariane: 'Adresse', racine: 'Recherche', message: 'Le service n’a pas répondu.' });
		const liens = [...container.querySelectorAll('nav a')];
		expect(liens.map(texte)).toEqual(['Recherche']);
		expect(liens[0].getAttribute('href')).toBe('/');
		expect(texte(container.querySelector('nav [aria-current="page"]'))).toBe('Adresse');
	});

	it('`sousTitre` is the line under the h1, before the message', () => {
		const { container } = render(PageErreur, { titre: 'DPE n° 2485E0000003C', sousTitre: 'Diagnostic de performance énergétique', message: 'Le service n’a pas répondu.' });
		const ordre = [...container.querySelectorAll('h1, .fiche-sous, .erreur-chapo')].map((el) => el.tagName === 'H1' ? 'h1' : el.className.split(' ')[0]);
		expect(ordre).toEqual(['h1', 'fiche-sous', 'erreur-chapo']);
		expect(texte(container.querySelector('.fiche-sous'))).toBe('Diagnostic de performance énergétique');
		expect(container.querySelectorAll('.fiche-sous')).toHaveLength(1);
	});

	it('`surtitre` replaces « Erreur {statut} » above the title, and stands without a status', () => {
		const avecStatut = render(PageErreur, { titre: 'Adresse momentanément indisponible', statut: 503, surtitre: 'Adresse', message: 'Le service n’a pas répondu.' });
		expect(texte(avecStatut.container.querySelector('.erreur-statut'))).toBe('Adresse');
		expect(avecStatut.container.textContent).not.toContain('Erreur 503');
		avecStatut.unmount();
		const sansStatut = render(PageErreur, { titre: 'Adresse inconnue', surtitre: 'Adresse', message: 'Aucune adresse.' });
		expect(texte(sansStatut.container.querySelector('.erreur-statut'))).toBe('Adresse');
		sansStatut.unmount();
		const defaut = render(PageErreur, { titre: 'Erreur', statut: 429, message: 'Trop de requêtes.' });
		expect(texte(defaut.container.querySelector('.erreur-statut'))).toBe('Erreur 429');
	});
});
