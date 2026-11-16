import { describe, expect, it } from 'vitest';
import { DEPARTEMENTS } from './departements';
import { couvertureCodePostal, phraseRechercheBornee } from './recherche-code-postal';

// Review 2026-11-10, finding R-03 (red on purpose, no fix here).
//
// A postal-code search that finds nothing says where it looked, by naming every served departement.
// Written for two departements (« Loire-Atlantique (44), Vendée (85) »), the sentence now names 101:
// on datacommune.fr, `/communes?q=75000` (2026-11-09) answers « La recherche par code postal porte
// sur : Ain (01), Aisne (02), … Mayotte (976). », in the page and in the list under the search field.
// 75000, 13000 and 69000 are the codes people type for Paris, Marseille and Lyon; none exists.

// The 101 departements; the referential also lists the collectivity 975, which is no departement.
const TOUS = DEPARTEMENTS.map((d) => d.code).filter((code) => code !== '975');
const nomDepartement = (code: string) => `${DEPARTEMENTS.find((d) => d.code === code)?.nom ?? code} (${code})`;

describe('R-03 — code postal sans résultat sur une instance nationale', () => {
	it('témoin : la réponse nationale de /v1/communes/search borne la recherche aux 101 départements', () => {
		const couverture = couvertureCodePostal('75000', { hebergement: { ban: { etat: 'heberge', motif: null, departements: TOUS } } });
		expect(couverture).toEqual({ kind: 'bornee', departements: TOUS });
		expect(TOUS).toHaveLength(101);
	});

	it('la phrase ne nomme pas les 101 départements un par un', () => {
		const phrase = phraseRechercheBornee(TOUS, nomDepartement);
		const nommes = DEPARTEMENTS.filter((d) => phrase.includes(`(${d.code})`)).length;
		// The directory names the served departements up to six, then says « tous les départements ».
		expect(nommes, phrase.slice(0, 160) + '…').toBeLessThanOrEqual(6);
		expect(phrase.length).toBeLessThanOrEqual(200);
	});
});
