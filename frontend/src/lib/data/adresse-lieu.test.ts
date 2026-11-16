import { describe, expect, it } from 'vitest';
import type { FicheAdressePublique, ValeurTheme } from '$lib/api/adresses';
import { batimentsSurLaParcelle, distanceTransport, libelleChamp, texteChamp } from './adresse-lieu';
import { ADRESSE_44109_zz04_00004_OBSERVED } from './__fixtures__/adresse-44109-zz04-00004-20261013';
import { ADRESSE_85191_zz05_00011_OBSERVED } from './__fixtures__/adresse-85191-zz05-00011-20261012';
import LISTES_FIGEES from './__fixtures__/adresse-export-listes-reference.json';

// 04-06 (D-104, LIEU-02): the wording of the served fields of the place (label, value text, distance)
// that the comparators and the declared sales read; nothing is recomputed.

const NB = ' ';
const NNB = ' ';
const SOURCE_BATIMENTS = { cle: 'batiments', nom: 'BD TOPO — Bâtiments', producteur: 'IGN', url: 'https://geoservices.ign.fr/bdtopo' };

function champ(partiel: Partial<ValeurTheme> & { field_id: string }): ValeurTheme {
	return {
		etat: 'value',
		valeur: null,
		unite: null,
		periode: null,
		motif: null,
		source: SOURCE_BATIMENTS,
		rattachement: 'parcelle_du_point',
		reference: null,
		elements: [],
		liste_incomplete: false,
		rayon_m: null,
		...partiel
	};
}

describe('adresse-lieu — wording of the served fields', () => {
	it('singulier : « 1 bâtiment sur la parcelle » ; zéro : « 0 bâtiment sur la parcelle »', () => {
		expect(batimentsSurLaParcelle(1)).toBe(`1${NB}bâtiment sur la parcelle`);
		expect(batimentsSurLaParcelle(0)).toBe(`0${NB}bâtiment sur la parcelle`);
		expect(batimentsSurLaParcelle(12)).toBe(`12${NB}bâtiments sur la parcelle`);
	});

	it('texteChamp: the text of a served value, null for a value that is not served', () => {
		expect(texteChamp(champ({ field_id: 'adresse.batiments.nombre_parcelle', valeur: 2, unite: 'count' }))).toBe(`2${NB}bâtiments sur la parcelle`);
		expect(texteChamp(champ({ field_id: 'adresse.connectivite.eligibilite_fibre', valeur: false }))).toBe('Non');
		expect(texteChamp(champ({ field_id: 'adresse.connectivite.eligibilite_fibre', valeur: true }))).toBe('Oui');
		expect(texteChamp(champ({ field_id: 'adresse.batiment.rnb_id', valeur: 'RNB1ABC234DE' }))).toBe('RNB1ABC234DE');
		expect(texteChamp(champ({ field_id: 'adresse.parcelle.contenance', valeur: 1412, unite: 'm2' }))).toBe(`1${NNB}412`);
		expect(texteChamp(champ({ field_id: 'adresse.parcelle.contenance', etat: 'not_reported' }))).toBeNull();
	});

	it('libelleChamp: the row label of a served field, null without a wording', () => {
		expect(libelleChamp('adresse.batiments.nombre_parcelle')).toBe('Bâtiments');
		expect(libelleChamp('adresse.parcelle.contenance')).toBe('Contenance de la parcelle');
		expect(libelleChamp('adresse.inconnu.champ')).toBeNull();
	});

	it('distanceTransport: « à {d} m (à vol d’oiseau, pas un temps de trajet) »', () => {
		expect(distanceTransport(42)).toBe(`à 42${NB}m (à vol d'oiseau, pas un temps de trajet)`);
		expect(distanceTransport(118)).toBe(`à 118${NB}m (à vol d'oiseau, pas un temps de trajet)`);
		expect(distanceTransport(1000)).toBe(`à 1${NNB}000${NB}m (à vol d'oiseau, pas un temps de trajet)`);
	});
});

describe('texteChamp — texte des champs liste inchangé (04.5-04)', () => {
	const FICHES: Record<string, { donnees: unknown }> = {
		'adresse-44109-zz04-00004': ADRESSE_44109_zz04_00004_OBSERVED,
		'adresse-85191-zz05-00011': ADRESSE_85191_zz05_00011_OBSERVED
	};

	it.each(Object.keys(FICHES))('%s : texte joint des risques, de la zone du PLU et des prescriptions égal à la référence figée', (cle) => {
		const fiche = FICHES[cle].donnees as FicheAdressePublique;
		const champs = (fiche.themes ?? []).flatMap((theme) => theme.groupes.flatMap((groupe) => groupe.champs));
		const figes = LISTES_FIGEES.adresses[cle as keyof typeof LISTES_FIGEES.adresses].texteChamp;
		const textes = Object.fromEntries(Object.keys(figes).map((fieldId) => [fieldId, texteChamp(champs.find((champ) => champ.field_id === fieldId)!)]));
		expect(textes).toEqual(figes);
		expect(figes['adresse.risques.liste_commune'].split(`${NNB}; `)).toHaveLength(cle === 'adresse-44109-zz04-00004' ? 22 : 11);
	});
});
