// « Ce que nous ne montrons pas » (04.7-14): every limit has a reason and a base, the recency window
// quoted is the one the API applies, and the five states carry the labels the fiches print.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import { LIBELLE_STATUT } from '$lib/data/adresse-rapport';
import { ETATS, FENETRE_VENTE_ISOLEE_MOIS, LIMITES } from '$lib/data/ce-que-nous-ne-montrons-pas';
import Page from './+page.svelte';

/** Every kind of space (the no-break spaces of French typography included) as one space. */
const espaces = (s: string) => s.replace(/\s+/g, ' ');
const texte = () => espaces(render(Page).container.textContent ?? '');

describe('/ce-que-nous-ne-montrons-pas', () => {
	it('chaque limite dit quoi, pourquoi et sa base, et la page les montre toutes', () => {
		const page = texte();
		for (const limite of LIMITES) {
			expect(limite.quoi.trim(), limite.id).not.toBe('');
			expect(limite.pourquoi.trim(), limite.id).not.toBe('');
			expect(limite.base.trim(), limite.id).not.toBe('');
			expect(page).toContain(espaces(limite.titre));
			expect(page).toContain(espaces(limite.base));
		}
	});

	it('nomme les quatre limites de la ligne 14 : parties DVF, vente isolée récente, occupants, indexation', () => {
		const ids = LIMITES.map((limite) => limite.id);
		expect(ids).toEqual(expect.arrayContaining(['parties-dvf', 'vente-isolee', 'occupants', 'indexation']));
	});

	it('cite la fenêtre de récence que l’API applique (FENETRE_RECENCE_MOIS)', () => {
		const ventes = readFileSync(resolve(__dirname, '../../../../../api/backend/app/adresse/ventes.py'), 'utf8');
		const servie = Number(/^FENETRE_RECENCE_MOIS = (\d+)$/m.exec(ventes)?.[1]);
		expect(servie).toBe(FENETRE_VENTE_ISOLEE_MOIS);
		expect(texte()).toContain(`${servie} derniers mois`);
	});

	it('définit le zéro, le manque, le hors couverture, la panne et le sans objet avec les mots des fiches', () => {
		const page = texte();
		expect(ETATS.map((etat) => etat.cle)).toEqual(['zero', 'manque', 'hors_couverture', 'panne', 'non_concerne']);
		for (const etat of ETATS) {
			expect(etat.libelle).toBe(LIBELLE_STATUT[etat.cle]);
			const definition = page.indexOf(espaces(etat.definition));
			expect(definition, etat.cle).toBeGreaterThan(0);
			// The label is printed right before its definition (dt, then dd).
			expect(page.slice(0, definition).trimEnd().endsWith(etat.libelle), etat.cle).toBe(true);
		}
		expect(page).toContain('Une absence de couverture ne vaut jamais zéro.');
	});
});
