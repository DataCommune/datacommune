// R-03 (2026-11-10): a postal code without result on a national instance. Ianis's decision of 09/11
// (`phraseInconnu`, document-dpe.ts): a sentence that listed the served departements gives their
// NUMBER beyond five. And 75000, 13000, 69000 — what one types for Paris, Marseille and Lyon — do not
// exist: those three get the arrondissement advice (wording validated by Ianis, 2026-11-10).
import { describe, expect, it } from 'vitest';
import { DEPARTEMENTS } from './departements';
import {
	CONSEIL_CODE_POSTAL_PLM,
	CONSEIL_RECHERCHE_PAR_NOM,
	conseilCodePostal,
	phraseRechercheBornee
} from './recherche-code-postal';

const NB = ' ';
const TOUS = DEPARTEMENTS.map((d) => d.code).filter((code) => code !== '975');
const nom = (code: string) => `${DEPARTEMENTS.find((d) => d.code === code)?.nom ?? code}${NB}(${code})`;

describe('phraseRechercheBornee() — beyond five departements, their number', () => {
	it('101 departements: one short sentence with the count', () => {
		expect(phraseRechercheBornee(TOUS, nom)).toBe(`La recherche par code postal porte sur 101${NB}départements.`);
	});

	it('six departements: counted', () => {
		expect(phraseRechercheBornee(TOUS.slice(0, 6), nom)).toBe(`La recherche par code postal porte sur 6${NB}départements.`);
	});

	it('five departements: still named, in the served order', () => {
		expect(phraseRechercheBornee(['44', '85', '49', '53', '72'], nom)).toBe(
			`La recherche par code postal porte sur${NB}: Loire-Atlantique${NB}(44), Vendée${NB}(85), Maine-et-Loire${NB}(49), Mayenne${NB}(53), Sarthe${NB}(72).`
		);
	});
});

describe('conseilCodePostal() — Paris, Lyon, Marseille (validated by Ianis, 2026-11-10)', () => {
	it('the wording is the proposed one, one constant', () => {
		expect(CONSEIL_CODE_POSTAL_PLM).toBe(
			`Paris, Lyon et Marseille ont un code postal par arrondissement${NB}: essayez 75011, 69003 ou 13001, ou tapez le nom de la ville.`
		);
	});

	it.each(['75000', '13000', '69000', ' 75000 '])('%s gets the arrondissement advice', (q) => {
		expect(conseilCodePostal(q)).toBe(CONSEIL_CODE_POSTAL_PLM);
	});

	it.each(['75011', '44000', '35999', 'paris'])('%s keeps the name advice', (q) => {
		expect(conseilCodePostal(q)).toBe(CONSEIL_RECHERCHE_PAR_NOM);
	});
});
