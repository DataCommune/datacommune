// Truth guard of the port (D-08, UI-SPEC § 12, threat T-04.8-28): the mock-up of 31/10 computes its own
// short values, state sentences and summary. None of that layer is ported: the front shows what the API
// serves. The walk fails if one of its identifiers comes back anywhere in frontend/src. The tokens are
// assembled at run time, so that this file does not find itself.
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

const assembler = (...parties: string[]) => parties.join('');
const JETONS_COUCHE_DE_CALCUL = [
	// short value of a row (`shared/model.mjs`, VALEUR_COURTE)
	assembler('VALEUR', '_', 'COURTE'),
	assembler('valeur', 'Courte'),
	// state sentence and generic sentence (`a/pages.mjs`)
	assembler('ETAT', '_', 'PHRASE'),
	assembler('etat', 'Phrase'),
	assembler('PHRASE', '_', 'GENERIQUE'),
	assembler('phrase', 'Generique'),
	// sentence of its own for some themes (`a/pages.mjs`, ETAT_PROPRE)
	assembler('ETAT', '_', 'PROPRE'),
	assembler('etat', 'Propre'),
	// rule of its own for the air quality (`shared/model.mjs`, REGLE_AIR)
	assembler('REGLE', '_', 'AIR'),
	assembler('regle', 'Air'),
	// counter of favourable rows, recomputed from the rows
	assembler('favorables', '.length'),
	assembler('nb', 'Favorables'),
	assembler('compte', 'Favorables'),
	// counts and scale rules of the mock-up's summary (`a/pages.mjs`, `shared/model.mjs`)
	assembler('comptes', '('),
	assembler('est', 'Tuile'),
	assembler('est', 'Communale'),
	assembler('vaut', 'Pour'),
	assembler('SECTION', '_DU_', 'THEME'),
	// tones computed by the mock-up
	assembler('ton', 'Dpe'),
	assembler('plusieurs', 'Rnb'),
	assembler('code', '_', 'qual'),
	assembler('badge', 'De'),
	// « not attached » state derived on the client
	assembler('non', '_', 'rattache'),
	assembler('non', 'Rattache'),
	assembler('NON', '_', 'RATTACHE'),
	assembler('MOTIFS', '_', 'RATTACHEMENT')
];
// The counter Ianis refused (R3, A-10) rebuilt from the served lists, which are read one by one.
const MOTIF_COMPTEUR_FAVORABLES = new RegExp(assembler('favorables', '(Adresse|Secteur)', '\\.length'));
// The derived word itself, outside the tests, which name it to refuse it.
const MOT_NON_RATTACHE = assembler('Non', ' rattach');

/** The identifiers of the mock-up's computation layer found in a text. */
export function jetonsTrouves(texte: string): string[] {
	const jetons = JETONS_COUCHE_DE_CALCUL.filter((jeton) => texte.includes(jeton));
	return MOTIF_COMPTEUR_FAVORABLES.test(texte) ? [...jetons, MOTIF_COMPTEUR_FAVORABLES.source] : jetons;
}

const RACINE = resolve(__dirname, '..', '..');
const CE_FICHIER = 'lib/data/verite-portage.test.ts';

function fichiersSources(dossier: string): string[] {
	return readdirSync(dossier, { withFileTypes: true }).flatMap((entree) => {
		const chemin = join(dossier, entree.name);
		if (entree.isDirectory()) return fichiersSources(chemin);
		return /\.(ts|svelte|js)$/.test(entree.name) ? [chemin] : [];
	});
}

describe('Truth of the port: the computation layer of the mock-up stays out (D-08, UI-SPEC § 12)', () => {
	it('walks the real source tree, the address fiche included', () => {
		const chemins = fichiersSources(RACINE).map((chemin) => relative(RACINE, chemin).split(sep).join('/'));
		expect(chemins.length).toBeGreaterThan(300);
		expect(chemins).toContain('routes/(marketing)/adresse/[id]/+page.svelte');
		expect(chemins).toContain('lib/data/adresse-rapport.ts');
		expect(chemins).toContain(CE_FICHIER);
	});

	it('finds no identifier of that layer in frontend/src', () => {
		const trouves = fichiersSources(RACINE)
			.map((chemin) => ({ fichier: relative(RACINE, chemin).split(sep).join('/'), jetons: jetonsTrouves(readFileSync(chemin, 'utf8')) }))
			.filter(({ fichier, jetons }) => fichier !== CE_FICHIER && jetons.length > 0);
		expect(trouves).toEqual([]);
	});

	it.each(JETONS_COUCHE_DE_CALCUL)('fails on a text that holds %s (red case)', (jeton) => {
		expect(jetonsTrouves(`const x = ${jeton}[theme.id];`)).toContain(jeton);
	});

	it('fails on the favourable counter rebuilt from the served lists (red case)', () => {
		const liste = assembler('resume.favorables', 'Secteur', '.length');
		expect(jetonsTrouves(`const n = ${liste};`)).toEqual([MOTIF_COMPTEUR_FAVORABLES.source]);
	});

	it('writes the derived « not attached » word in no source outside the tests', () => {
		const trouves = fichiersSources(RACINE)
			.filter((chemin) => !/\.test\.ts$|__fixtures__/.test(chemin) && readFileSync(chemin, 'utf8').includes(MOT_NON_RATTACHE))
			.map((chemin) => relative(RACINE, chemin).split(sep).join('/'));
		expect(trouves).toEqual([]);
	});

	it('passes a text that holds none of them, the served words included', () => {
		expect(jetonsTrouves("const f = resume.favorablesAdresse; const statut = 'non_concerne';")).toEqual([]);
	});
});
