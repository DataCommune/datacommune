import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/svelte';
import EnTetePrint from './EnTetePrint.svelte';
import { SOURCE_INFO } from '$lib/api/admin';

// D-11 / D-12 / D-14 — bloc d'en-tête print-only des deux fiches publiques.
//
// Toute chaîne assertée ici est VERROUILLÉE par le Copywriting Contract du 50-UI-SPEC : elle est
// recopiée au caractère près, jamais paraphrasée. Le titre de la fiche n'est PAS assertée : il
// n'est pas reproduit dans ce bloc (le <h1> de la page s'imprime depuis la task 1 du plan 50-07,
// le dupliquer donnerait deux titres sur le PDF).

const MENTION = 'Synthèse informative — ne remplace pas les diagnostics officiels';

describe('EnTetePrint.svelte — en-tête du document imprimé (D-11)', () => {
	it('rend la date de consultation au libellé verrouillé (D-12)', () => {
		const { container } = render(EnTetePrint, { props: { consulteLe: '29/07/2026' } });

		expect(container.textContent).toContain('Consulté le 29/07/2026');
	});

	it('rend la mention de qualification au caractère près (D-11)', () => {
		const { container } = render(EnTetePrint, { props: { consulteLe: '29/07/2026' } });

		expect(container.textContent).toContain(MENTION);
	});

	it('rend les deux URL officielles en texte clair (D-11)', () => {
		const { container } = render(EnTetePrint, { props: { consulteLe: '29/07/2026' } });

		expect(container.textContent).toContain('https://errial.georisques.gouv.fr');
		expect(container.textContent).toContain('https://georisques.gouv.fr');
		// Nommer les deux services, pas seulement leurs adresses.
		expect(container.textContent).toContain('ERRIAL');
		expect(container.textContent).toContain('Géorisques');
	});

	it('ne recopie pas des URL inventées : elles sont celles du catalogue du dépôt', () => {
		// Verrou de dérive : les deux valeurs sont les URL canoniques de SOURCE_INFO
		// (src/lib/api/admin.ts). Le catalogue n'est PAS importé par le composant (ce serait
		// tirer le client API admin dans le bundle public) — c'est ce test qui pin l'égalité.
		const { container } = render(EnTetePrint, { props: { consulteLe: '29/07/2026' } });

		expect(container.textContent).toContain(SOURCE_INFO.errial.url);
		expect(container.textContent).toContain(SOURCE_INFO.georisques.url);
	});

	it('typographie française : l’espace avant « : » du renvoi est insécable (U+00A0), la ligne ne se coupe pas devant', () => {
		const { container } = render(EnTetePrint, { props: { consulteLe: '29/07/2026' } });

		expect(container.querySelector('.renvoi')?.textContent).toContain('officiels : ERRIAL');
	});

	it('est masqué à l’écran, affiché à l’impression, et non coupé entre deux pages (D-14)', () => {
		const { container } = render(EnTetePrint, { props: { consulteLe: '29/07/2026' } });

		const bloc = container.querySelector('.bloc-impression');
		expect(bloc).not.toBeNull();
		// `hidden` = display:none à l'écran ; `print:block` le rétablit sous @media print.
		expect(bloc?.classList.contains('hidden')).toBe(true);
		expect(bloc?.classList.contains('print:block')).toBe(true);
		// `.bloc-impression` est le point d'accroche du break-inside posé en task 1 (app.css).
	});

	it('sans date de consultation : AUCUNE ligne de date, jamais « Consulté le null »', () => {
		const { container } = render(EnTetePrint, { props: { consulteLe: null } });

		expect(container.textContent).not.toContain('Consulté le');
		expect(container.textContent).not.toContain('null');
		expect(container.textContent).not.toContain('undefined');
		// Le reste du bloc survit : la mention et le renvoi ne dépendent pas de la date.
		expect(container.textContent).toContain(MENTION);
		expect(container.textContent).toContain('https://errial.georisques.gouv.fr');
	});
});
