// Paris, Lyon, Marseille: a value published at the other mesh, on the fiche and in its CSV (R-09).
//
// The fixtures are production exports of 2026-11-10 (`/commune/75111.json`, `/commune/75056.json`) passed
// through the API projection of this branch (`app.commune.autre_maille`): the 11e serves the ABC zone and the
// taxes of Paris with their origin (`heritages`), Paris serves no rent and says it is published by
// arrondissement (`etats[…].publiee_a`). The fiche computes nothing: it says the scale and where to look.
import { describe, expect, it } from 'vitest';
import type { CommuneSynthese } from '$lib/api/commune';
import onzieme from './__fixtures__/commune-75111-autre-maille.json';
import paris from './__fixtures__/commune-75056-autre-maille.json';
import { COMMUNE_85191_OBSERVED } from './__fixtures__/commune-85191-20261012';
import { ANCRE_ARRONDISSEMENTS, arrondissementsDe, chapoArrondissementAvecHeritage } from './arrondissements';
import { themesCommune, themesPourExport, type LigneTheme } from './commune-grid';
import { phrasePublieeAilleurs } from './etats-libelles';

const NBSP = String.fromCharCode(0xa0);
const NNBSP = String.fromCharCode(0x202f);

const lignes = (synthese: unknown): LigneTheme[] => themesCommune(synthese as CommuneSynthese).flatMap((t) => t.lignes);
const ligne = (synthese: unknown, fieldId: string): LigneTheme => {
	const trouvee = lignes(synthese).find((l) => l.fieldId === fieldId);
	if (!trouvee) throw new Error(`no row ${fieldId}`);
	return trouvee;
};

describe('an arrondissement and the values of its commune (A)', () => {
	const synthese = onzieme.donnees;

	it.each(['zonage_abc.zone', 'fiscalite.taux_tfpb', 'fiscalite.taux_th_rs', 'fiscalite.taux_teom', 'fiscalite.nom_epci'])(
		'%s shows the value of Paris, marked with its scale and its commune',
		(fieldId) => {
			const l = ligne(synthese, fieldId);
			expect(l.etat.kind).toBe('valeur');
			expect(l.echelle).toEqual({ pictogramme: 'commune', mot: 'Commune' });
			expect(l.objet).toBe('Commune de Paris');
			expect(l.volet).toContainEqual({ terme: 'Objet', valeur: 'Commune de Paris' });
		}
	);

	it('keeps its own values unmarked', () => {
		const loyer = ligne(synthese, 'loyers.loyer_m2_appartement');
		expect(loyer.etat.kind).toBe('valeur');
		expect(loyer.echelle).toBeUndefined();
		expect(loyer.objet).not.toContain('Paris');
	});

	it('points to the fiche of Paris for a statistic of the whole commune, without taking it', () => {
		const fibre = ligne(synthese, 'connectivite.fibre_pct');
		expect(fibre.etat).toMatchObject({ kind: 'absence', etat: 'not_reported', aide: phrasePublieeAilleurs('commune', 'Paris') });
		expect(fibre.renvoi).toEqual({ href: '/commune/75056', texte: 'Fiche de Paris' });
		expect(fibre.echelle).toBeUndefined();
	});

	it('exports the scale and the commune as the scope of the CSV row', () => {
		const zone = themesPourExport(themesCommune(synthese as unknown as CommuneSynthese))
			.flatMap((g) => g.rows)
			.find((r) => r.id === 'zonage_abc.zone');
		expect(zone).toMatchObject({ value: 'Abis', state: 'value', presentation: { grain: 'Commune de Paris' } });
	});

	it('words the head of the fiche for the marked rows', () => {
		expect(chapoArrondissementAvecHeritage('Paris')).toBe(
			`Les valeurs de cette page valent pour l’arrondissement entier, sauf celles marquées «${NNBSP}Commune${NNBSP}», qui valent pour tout Paris${NNBSP}; jamais pour une adresse.`
		);
	});
});

describe('a commune whose source publishes by arrondissement (B)', () => {
	const synthese = paris.donnees;

	it.each([
		'loyers.loyer_m2_appartement',
		'vacance.taux_vacance',
		'equipements.nb_equipements',
		'scolaire.nb_etablissements_scolaires',
		'monuments.nb_monuments',
		'foncier_pm.nb_proprietaires_pm'
	])('%s keeps its absence and leads to the arrondissements', (fieldId) => {
		const l = ligne(synthese, fieldId);
		expect(l.etat).toMatchObject({
			kind: 'absence',
			etat: 'not_reported',
			aide: `Publié par arrondissement${NBSP}: voir les arrondissements de Paris.`
		});
		expect(l.renvoi).toEqual({ href: `#${ANCRE_ARRONDISSEMENTS}`, texte: 'Arrondissements de Paris' });
		expect(l.brut[0].valeur).toBe('null'); // never a value made of its parts
	});

	it('marks nothing as inherited on a commune', () => {
		expect(lignes(synthese).filter((l) => l.echelle)).toEqual([]);
	});

	it('lists the twenty arrondissements of Paris, in code order', () => {
		const liste = arrondissementsDe('75056');
		expect(liste).toHaveLength(20);
		expect(liste[0]).toEqual({ code: '75101', libelle: '1er arrondissement' });
		expect(liste.at(-1)).toEqual({ code: '75120', libelle: '20e arrondissement' });
		expect(arrondissementsDe('69123')).toHaveLength(9);
		expect(arrondissementsDe('13055')).toHaveLength(16);
		expect(arrondissementsDe('44109')).toEqual([]);
	});
});

describe('an ordinary commune', () => {
	it('has no marked row and no pointer', () => {
		const toutes = lignes(COMMUNE_85191_OBSERVED);
		expect(toutes.length).toBeGreaterThan(10);
		expect(toutes.filter((l) => l.echelle || l.renvoi)).toEqual([]);
	});
});
