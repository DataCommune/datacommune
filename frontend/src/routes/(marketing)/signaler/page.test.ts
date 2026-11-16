// The page of the error report as rendered: the context shown before sending and sent back by the
// form, the fields and their names, the refusals worded next to their field, the confirmation, and
// nothing typed or joined ever rendered as markup. Wordings are read from the module to validate:
// this file checks where they go, not what they say.
import { render } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import { ADRESSE_SIGNALEMENT, LIBELLES } from '$lib/signalement/libelles';

vi.mock('$app/forms', () => ({ enhance: () => ({ destroy() {} }) }));

import Page from './+page.svelte';

const CONTEXTE = {
	page: '/commune/44109',
	titre: 'Prix médian des maisons',
	lignes: 'dvf.prix_m2_median_maison=312',
	source: 'DVF',
	version: '81d3926f'
};
const DATA = { contexte: CONTEXTE, navigateur: 'Mozilla/5.0 (test)' };

const texte = (noeud: Element | null) => (noeud?.textContent ?? '').replace(/\s+/g, ' ').trim();

function joint(container: HTMLElement): Record<string, string> {
	return Object.fromEntries([...container.querySelectorAll('.signaler-joint > div')].map((ligne) => [texte(ligne.querySelector('dt')), texte(ligne.querySelector('dd'))]));
}

function champs(container: HTMLElement): Record<string, string> {
	return Object.fromEntries([...container.querySelectorAll<HTMLInputElement>('input[type="hidden"]')].map((champ) => [champ.name, champ.value]));
}

describe('/signaler — the form', () => {
	it('is a plain POST form with a required message, an optional e-mail address and one button', () => {
		const { container, getByLabelText, getByRole } = render(Page, { data: DATA });

		const formulaire = container.querySelector('form')!;
		expect(formulaire.getAttribute('method')).toBe('POST');
		expect(formulaire.hasAttribute('action')).toBe(false);
		const commentaire = getByLabelText(LIBELLES.commentaire) as HTMLTextAreaElement;
		expect(commentaire.name).toBe('commentaire');
		expect(commentaire.required).toBe(true);
		expect(commentaire.maxLength).toBe(2000);
		const email = getByLabelText(LIBELLES.email) as HTMLInputElement;
		expect([email.name, email.type, email.required]).toEqual(['email', 'email', false]);
		expect(getByRole('button', { name: LIBELLES.envoyer }).getAttribute('type')).toBe('submit');
		expect(texte(getByRole('heading', { level: 1 }))).toBe(LIBELLES.titre);
	});

	it('shows what is joined before sending, and sends exactly that', () => {
		const { container } = render(Page, { data: DATA });

		expect(joint(container)).toEqual({
			[LIBELLES.termes.page]: '/commune/44109',
			[LIBELLES.termes.valeur]: 'Prix médian des maisons dvf.prix_m2_median_maison=312',
			[LIBELLES.termes.source]: 'DVF',
			[LIBELLES.termes.version]: '81d3926f',
			[LIBELLES.termes.largeur]: `${window.innerWidth} px`,
			[LIBELLES.termes.navigateur]: 'Mozilla/5.0 (test)'
		});
		expect(champs(container)).toEqual({
			page: '/commune/44109',
			titre: 'Prix médian des maisons',
			lignes: 'dvf.prix_m2_median_maison=312',
			source: 'DVF',
			version: '81d3926f',
			largeur: String(window.innerWidth)
		});
	});

	it('from the footer, joins the version only and says nothing of what it does not know', () => {
		const { container } = render(Page, {
			data: { contexte: { page: null, titre: null, lignes: null, source: null, version: 'aaaa1111' }, navigateur: null }
		});

		expect(Object.keys(joint(container))).toEqual([LIBELLES.termes.version, LIBELLES.termes.largeur]);
		expect(champs(container)).toMatchObject({ page: '', titre: '', lignes: '', source: '' });
	});

	it('keeps the honeypot out of sight, of the tab order and of the accessibility tree', () => {
		const { container } = render(Page, { data: DATA });

		const piege = container.querySelector<HTMLInputElement>('input[name="site_web"]')!;
		expect(piege.tabIndex).toBe(-1);
		expect(piege.getAttribute('autocomplete')).toBe('off');
		expect(piege.closest('[aria-hidden="true"]')).not.toBeNull();
	});

	it('keeps the other way in sight: the address to write to, and the privacy page', () => {
		const { container } = render(Page, { data: DATA });

		expect(container.querySelector(`a[href="mailto:${ADRESSE_SIGNALEMENT}"]`)).not.toBeNull();
		expect(container.querySelector('a[href="/confidentialite"]')).not.toBeNull();
	});

	it('never interprets what is joined as markup', () => {
		const { container } = render(Page, {
			data: { contexte: { ...CONTEXTE, titre: '<img src=x onerror=alert(1)>', lignes: '<script>alert(1)</script>' }, navigateur: '<b>agent</b>' }
		});

		expect(container.querySelector('img, script, b')).toBeNull();
		expect(joint(container)[LIBELLES.termes.navigateur]).toBe('<b>agent</b>');
	});
});

describe('/signaler — after sending', () => {
	it('a refused field keeps what was typed and is described by its refusal', () => {
		const { getByLabelText, container } = render(Page, {
			data: { contexte: { page: null, titre: null, lignes: null, source: null, version: 'aaaa1111' }, navigateur: null },
			form: { erreurs: { commentaire: 'court', email: 'invalide' }, valeurs: { commentaire: 'court', email: 'pas-un-email' }, contexte: CONTEXTE }
		});

		const commentaire = getByLabelText(LIBELLES.commentaire) as HTMLTextAreaElement;
		expect(commentaire.value).toBe('court');
		expect(commentaire.getAttribute('aria-invalid')).toBe('true');
		const decrit = commentaire.getAttribute('aria-describedby')!.split(' ').map((id) => texte(container.querySelector(`#${CSS.escape(id)}`)));
		expect(decrit).toEqual([LIBELLES.aideCommentaire, LIBELLES.erreurs.court]);
		const email = getByLabelText(LIBELLES.email) as HTMLInputElement;
		expect(email.value).toBe('pas-un-email');
		expect(email.getAttribute('aria-invalid')).toBe('true');
		// The context the form was sent with, not the one of the reloaded page.
		expect(champs(container).page).toBe('/commune/44109');
	});

	it.each(['debit', 'envoi'] as const)('a failure (%s) is announced, with the address to write to, and keeps the message', (panne) => {
		const { getByRole, getByLabelText } = render(Page, {
			data: DATA,
			form: { panne, valeurs: { commentaire: 'Le prix est faux.', email: '' }, contexte: CONTEXTE }
		});

		const alerte = getByRole('alert');
		expect(texte(alerte)).toBe(`${LIBELLES.pannes[panne]}${ADRESSE_SIGNALEMENT}.`);
		expect(alerte.querySelector('a')?.getAttribute('href')).toBe(`mailto:${ADRESSE_SIGNALEMENT}`);
		expect((getByLabelText(LIBELLES.commentaire) as HTMLTextAreaElement).value).toBe('Le prix est faux.');
	});

	it('a report received replaces the form by its confirmation, its reference and the way back', () => {
		const { container, getByRole } = render(Page, { data: DATA, form: { recu: { reference: '0A1B2C3D4E', retour: '/commune/44109' } } });

		expect(container.querySelector('form')).toBeNull();
		expect(texte(getByRole('heading', { level: 1 }))).toBe(LIBELLES.recu);
		expect(texte(container.querySelector('.signaler-recu'))).toBe(`${LIBELLES.merci} ${LIBELLES.reference}0A1B2C3D4E.`);
		expect(getByRole('link', { name: LIBELLES.retour }).getAttribute('href')).toBe('/commune/44109');
	});

	it('without a page to go back to or a reference, the confirmation shows neither', () => {
		const { container, queryByRole } = render(Page, { data: DATA, form: { recu: { reference: null, retour: null } } });

		expect(texte(container.querySelector('.signaler-recu'))).toBe(LIBELLES.merci);
		expect(queryByRole('link', { name: LIBELLES.retour })).toBeNull();
	});
});
