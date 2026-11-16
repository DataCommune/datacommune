// French typography of the commune screens (03-17 T-1, X1, V7, V24, D-09, D-10, C-08): the visible
// strings use a no-break space (U+00A0) before « : » and between a number and its unit, a narrow
// no-break space (U+202F) inside « » and before « ; ? ! », and a no-break space between a
// departement name and its « (code) ». An ordinary space there lets the line break before the
// punctuation on a phone.
//
// The guard reads the source text of the files that render these screens and checks only what a
// reader can see: string literals (quotes and template literals, their `${…}` expressions excluded)
// and Svelte text nodes and attribute values. Comments, `<style>` blocks and code are ignored.
import { readFileSync, readdirSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const FICHIERS = [
	'src/lib/data/recherche-code-postal.ts',
	'src/lib/data/recherche-lieux.ts',
	'src/lib/data/etats-libelles.ts',
	'src/lib/data/panne.ts',
	'src/lib/data/commune-kpi.ts',
	'src/lib/data/commune-indicators.ts',
	'src/lib/data/commune-tendance.ts',
	// 04.7-15: DPE distribution and computed FAQ of the commune page.
	'src/lib/data/commune-repartition-dpe.ts',
	'src/lib/data/commune-faq.ts',
	'src/lib/components/fiche/RepartitionDpe.svelte',
	'src/lib/data/commune-comparison.ts',
	'src/lib/data/commune-comparison-selection.ts',
	'src/lib/data/commune-grid.ts',
	'src/lib/data/commune-fiche.ts',
	'src/lib/data/adresse-fiche.ts',
	'src/lib/data/document-dpe.ts',
	'src/lib/data/adresse-ventes.ts',
	// 04-06: themes of the place and CSV export of the address page.
	'src/lib/data/adresse-lieu.ts',
	'src/lib/data/adresse-export.ts',
	// 04.7-03: address fiche on the report format.
	'src/lib/data/adresse-rapport.ts',
	'src/lib/components/adresse/rapport/ChiffresCles.svelte',
	'src/lib/components/adresse/rapport/DetailGenerique.svelte',
	// 04.7-14: what the site does not show, the legal notice and privacy policy, the route 404.
	'src/lib/data/ce-que-nous-ne-montrons-pas.ts',
	'src/routes/(marketing)/ce-que-nous-ne-montrons-pas/+page.svelte',
	'src/routes/(marketing)/mentions-legales/+page.svelte',
	// 04.7-14c: terms of use (licence of the data, quota, code source).
	'src/routes/(marketing)/cgu/+page.svelte',
	'src/routes/(marketing)/confidentialite/+page.svelte',
	'src/routes/+error.svelte',
	'src/lib/components/CommuneSearch.svelte',
	'src/lib/components/fiche/Kpi.svelte',
	'src/lib/components/fiche/SourcePanel.svelte',
	'src/lib/components/adresse/DpeDocumentList.svelte',
	'src/lib/components/adresse/VenteList.svelte',
	'src/lib/components/adresse/Pagination.svelte',
	'src/lib/components/adresse/ComparablesForm.svelte',
	'src/lib/components/compare/CompareTable.svelte',
	'src/lib/components/compare/ChoixArrondissements.svelte',
	// 04-10: nature switch of the comparator.
	'src/lib/components/compare/NatureNav.svelte',
	// 04-11: designation of a DPE on a compared address.
	'src/lib/components/compare/DesignationDpe.svelte',
	'src/lib/components/SiteFooter.svelte',
	'src/routes/(marketing)/communes/+page.svelte',
	// 04.6-02: directory of communes.
	'src/lib/data/annuaire-communes.ts',
	'src/routes/(marketing)/communes/AjoutComparaison.svelte',
	// 04.8-07: the home page and what it writes (entries, scale legend, publics, landmarks).
	'src/routes/(marketing)/+page.svelte',
	'src/lib/data/accueil.ts',
	'src/routes/(marketing)/commune/[code]/+page.svelte',
	// 04.7-10: the departement pages and what they say.
	'src/lib/data/departement.ts',
	'src/lib/data/departement-presentation.ts',
	'src/routes/(marketing)/departement/[code]/+page.svelte',
	'src/routes/(marketing)/departements/+page.svelte',
	'src/routes/(marketing)/adresse/[id]/+page.svelte',
	'src/routes/(marketing)/adresse/+error.svelte',
	'src/routes/(marketing)/dpe/[numero]/+page.svelte',
	'src/routes/(marketing)/dpe/[numero]/+error.svelte',
	'src/routes/(marketing)/comparer/+page.svelte',
	// 04.8-02: base components of the v4 grammar (amendment 7) and the shared atoms ported with them.
	'src/lib/components/ds/Panneau.svelte',
	'src/lib/components/ds/Tuile.svelte',
	'src/lib/components/ds/ListeLiens.svelte',
	'src/lib/components/ds/RepereEchelle.svelte',
	'src/lib/components/ds/IconeCategorie.svelte',
	'src/lib/components/ds/BadgeEtat.svelte',
	'src/lib/components/ds/FilAriane.svelte',
	// 04.8-03: the shared interfaces of the fiche that write text.
	'src/lib/components/ds/EchelleDpe.svelte',
	'src/lib/data/echelles.ts',
	'src/lib/data/questions.ts',
	'src/lib/data/lire-une-fiche.ts',
	'src/lib/components/adresse/question/LigneCritere.svelte',
	'src/lib/components/adresse/question/BilanRisques.svelte',
	'src/lib/components/adresse/rapport/Sommaire.svelte',
	'src/lib/components/adresse/question/PartieQuestion.svelte',
	'src/lib/components/adresse/question/EnBref.svelte',
	'src/lib/components/ds/LireUneFiche.svelte',
	'src/lib/components/ds/ListeValeurs.svelte',
	// 04.8-06: template of the simple pages.
	'src/lib/components/ds/PageSimple.svelte',
	// Socle (2026-11-09): the shared pieces of the public pages.
	'src/lib/components/ds/MetaPartage.svelte',
	'src/lib/components/ds/PageErreur.svelte',
	'src/lib/components/ds/Sommaire.svelte',
	'src/lib/components/adresse/rapport/CarteAdresse.svelte',
	'src/lib/components/fiche/EnTetePrint.svelte',
	'src/lib/components/fiche/SourcesPrint.svelte',
	'src/lib/components/fiche/Notice.svelte',
	'src/lib/components/fiche/SourceButton.svelte',
	'src/lib/components/fiche/StateValue.svelte',
	'src/lib/components/fiche/ExportDialog.svelte',
	// 04.8-08: full forms of the acronyms, written into the prose of the pages.
	'src/lib/utils/sigles.ts',
	// 04.10: the interactive parcel map, its table of texts and its components.
	'src/lib/data/parcel-map.ts',
	'src/lib/components/adresse/carte/ParcelMapDialog.svelte',
	'src/lib/components/adresse/carte/ParcelPanel.svelte',
	'src/lib/components/adresse/carte/ParcelList.svelte'
] as const;

/** One visible piece of text and the line it starts on. */
interface Texte {
	ligne: number;
	texte: string;
}

/** French patterns written with an ordinary space (U+0020), each with its expected space. */
const MOTIFS: ReadonlyArray<[string, RegExp]> = [
	['espace ordinaire après « (U+202F attendu)', /« /],
	['espace ordinaire avant » (U+202F attendu)', / »/],
	['espace ordinaire avant « : » d’un libellé (U+00A0 attendu)', /(?:Source|Motif|Évaluation|Qualification|porte sur|masqué) :/],
	// At the edge of a text node: « </strong> : … » or « Contact : <a> » (04.7-14c, Codex pass 2).
	['espace ordinaire avant « : » au bord d’une balise (U+00A0 attendu)', /^\s* :|\p{L} :\s*$/u],
	['espace ordinaire avant « ! » (U+202F attendu)', /\p{L} !/u],
	['espace ordinaire avant « ; » (U+202F attendu)', /\p{L} ;/u],
	['espace ordinaire avant « ? » (U+202F attendu)', /\p{L} \?/u],
	['espace ordinaire entre un nombre et « mois » (U+00A0 attendu)', /\d mois\b/],
	['espace ordinaire entre un département et son code (U+00A0 attendu)', /\p{L} \(\d{2}\)/u],
	// Same rules in template literals, where `\u0000` stands for an interpolated expression.
	['espace ordinaire entre un nombre et son unité (U+00A0 attendu)', /\u0000 (?:mois|habitants|ventes|diagnostics)\b/],
	['espace ordinaire entre un nom et son « (code) » (U+00A0 attendu)', /\u0000 \(\u0000\)/]
];

const lineOf = (source: string, index: number) => source.slice(0, index).split('\n').length;

/**
 * Strings of a TypeScript fragment, with the line they start on. A small scanner, not a parser:
 * it knows comments, the three string forms, template expressions and regex literals, enough for
 * the files above.
 */
function chainesTs(source: string, depart = 0, fin = source.length, sortie: Texte[] = []): Texte[] {
	let i = depart;
	let precedent = '';
	while (i < fin) {
		const c = source[i];
		const suivant = source[i + 1];
		if (c === '/' && suivant === '/') {
			i = source.indexOf('\n', i);
			if (i < 0) i = fin;
			continue;
		}
		if (c === '/' && suivant === '*') {
			const bout = source.indexOf('*/', i + 2);
			i = bout < 0 ? fin : bout + 2;
			continue;
		}
		if (c === "'" || c === '"') {
			let j = i + 1;
			let texte = '';
			while (j < fin && source[j] !== c && source[j] !== '\n') {
				if (source[j] === '\\') {
					texte += source.slice(j, j + 2);
					j += 2;
					continue;
				}
				texte += source[j++];
			}
			sortie.push({ ligne: lineOf(source, i), texte });
			i = j + 1;
			precedent = c;
			continue;
		}
		if (c === '`') {
			let j = i + 1;
			let texte = '';
			while (j < fin && source[j] !== '`') {
				if (source[j] === '\\') {
					texte += source.slice(j, j + 2);
					j += 2;
					continue;
				}
				if (source[j] === '$' && source[j + 1] === '{') {
					const bout = accoladeFermante(source, j + 1);
					chainesTs(source, j + 2, bout, sortie);
					// The expression is not visible text: a neutral marker keeps the pieces apart.
					texte += '\u0000';
					j = bout + 1;
					continue;
				}
				texte += source[j++];
			}
			sortie.push({ ligne: lineOf(source, i), texte });
			i = j + 1;
			precedent = c;
			continue;
		}
		if (c === '/' && (precedent === '' || '(,=:[!&|?{};+-*%<>~^'.includes(precedent))) {
			// Regex literal: skipped up to its unescaped closing slash (character classes kept).
			let j = i + 1;
			let classe = false;
			while (j < fin && source[j] !== '\n') {
				if (source[j] === '\\') j += 2;
				else if (source[j] === '[') (classe = true), j++;
				else if (source[j] === ']') (classe = false), j++;
				else if (source[j] === '/' && !classe) break;
				else j++;
			}
			i = j + 1;
			precedent = '/';
			continue;
		}
		if (!/\s/.test(c)) precedent = c;
		i++;
	}
	return sortie;
}

/** Index of the brace closing the one at `ouvrante`, strings and nested braces respected. */
function accoladeFermante(source: string, ouvrante: number): number {
	let profondeur = 0;
	for (let i = ouvrante; i < source.length; i++) {
		const c = source[i];
		if (c === "'" || c === '"' || c === '`') {
			i++;
			while (i < source.length && source[i] !== c) {
				if (source[i] === '\\') i++;
				i++;
			}
			continue;
		}
		if (c === '{') profondeur++;
		else if (c === '}' && --profondeur === 0) return i;
	}
	return source.length;
}

/** Visible text of a Svelte file: script strings, markup text nodes, attributes, expressions. */
function textesSvelte(source: string): Texte[] {
	const sortie: Texte[] = [];
	// Comments and styles are not rendered: blanked, line breaks kept for the line numbers.
	const masquer = (motif: RegExp, texte: string) => texte.replace(motif, (bloc) => bloc.replace(/[^\n]/g, ' '));
	let src = masquer(/<!--[\s\S]*?-->/g, source);
	src = masquer(/<style[\s\S]*?<\/style>/g, src);
	const script = /<script[^>]*>([\s\S]*?)<\/script>/g;
	let bloc: RegExpExecArray | null;
	while ((bloc = script.exec(src))) {
		const debut = bloc.index + bloc[0].indexOf('>') + 1;
		chainesTs(src, debut, debut + bloc[1].length, sortie);
	}
	src = masquer(/<script[\s\S]*?<\/script>/g, src);

	let i = 0;
	let texte = '';
	let debutTexte = 0;
	const pousser = () => {
		if (texte.trim()) sortie.push({ ligne: lineOf(src, debutTexte), texte });
		texte = '';
	};
	while (i < src.length) {
		const c = src[i];
		if (c === '{') {
			pousser();
			const bout = accoladeFermante(src, i);
			chainesTs(src, i + 1, bout, sortie);
			i = bout + 1;
			debutTexte = i;
			continue;
		}
		if (c === '<') {
			pousser();
			// A tag: quoted attribute values are visible (titles, labels), braces are expressions.
			let j = i + 1;
			while (j < src.length && src[j] !== '>') {
				if (src[j] === '"' || src[j] === "'") {
					const q = src[j];
					const fin = src.indexOf(q, j + 1);
					const valeur = src.slice(j + 1, fin);
					const ligne = lineOf(src, j);
					// Literal parts of the value, then the strings of its `{…}` expressions.
					sortie.push({ ligne, texte: valeur.replace(/\{[^}]*\}/g, '\u0000') });
					for (const expression of valeur.matchAll(/\{([^}]*)\}/g)) {
						for (const item of chainesTs(expression[1])) sortie.push({ ligne, texte: item.texte });
					}
					j = fin + 1;
					continue;
				}
				if (src[j] === '{') {
					const bout = accoladeFermante(src, j);
					chainesTs(src, j + 1, bout, sortie);
					j = bout + 1;
					continue;
				}
				j++;
			}
			i = j + 1;
			debutTexte = i;
			continue;
		}
		if (!texte) debutTexte = i;
		texte += c;
		i++;
	}
	pousser();
	return sortie;
}

function textesVisibles(fichier: string): Texte[] {
	const source = readFileSync(fichier, 'utf8').replace(/\r\n/g, '\n');
	return fichier.endsWith('.svelte') ? textesSvelte(source) : chainesTs(source);
}

// The legal pages are read as prose end to end (04.7-14c, independent review): there, any « : » after
// an ordinary space is a fault, and so is a number before « jours ». The other files keep the targeted
// patterns above (their remaining faults are a known debt, see the 14c SUMMARY).
const PAGES_LEGALES: ReadonlySet<string> = new Set([
	'src/routes/(marketing)/cgu/+page.svelte',
	'src/routes/(marketing)/mentions-legales/+page.svelte',
	'src/routes/(marketing)/confidentialite/+page.svelte',
	'src/routes/(marketing)/ce-que-nous-ne-montrons-pas/+page.svelte'
]);
const MOTIFS_PROSE: ReadonlyArray<[string, RegExp]> = [
	['espace ordinaire avant « : » (U+00A0 attendu)', /[^\s \u0000] :(?=\s|$)/u],
	['espace ordinaire entre un nombre et « jours » (U+00A0 attendu)', /\d jours\b/]
];

function fautes(fichier: string): string[] {
	const motifs = PAGES_LEGALES.has(fichier) ? [...MOTIFS, ...MOTIFS_PROSE] : MOTIFS;
	return textesVisibles(fichier).flatMap(({ ligne, texte }) =>
		motifs.filter(([, motif]) => motif.test(texte)).map(([nom]) => `${fichier}:${ligne} ${nom} — ${JSON.stringify(texte.trim())}`)
	);
}

/** Faults of an inline Svelte source, for the non-vacuity checks. */
function fautes_de(source: string): string[] {
	return textesSvelte(source).flatMap(({ texte }) => MOTIFS.filter(([, motif]) => motif.test(texte)).map(([nom]) => nom));
}

// Amendment 7 of the 4.8 guard (UI-SPEC v4 § 2.6): every component the phase creates under ds/ renders
// text and is read above, from the slice that creates it. The icons are drawings, without text.
const COMPOSANTS_DS = 'src/lib/components/ds';

describe('typographie française des écrans communes (03-17 T-1, X1)', () => {
	it.each(FICHIERS)('%s', (fichier) => {
		expect(fautes(fichier)).toEqual([]);
	});

	it('every component of ds/ (icons apart) is read by this guard (amendment 7)', () => {
		const composants = readdirSync(COMPOSANTS_DS)
			.filter((nom) => nom.endsWith('.svelte'))
			.map((nom) => `${COMPOSANTS_DS}/${nom}`);
		// Non-vacuity: the six base components of 04.8-02 at least.
		expect(composants.length).toBeGreaterThanOrEqual(6);
		expect(composants.filter((chemin) => !(FICHIERS as readonly string[]).includes(chemin))).toEqual([]);
	});

	it('every component of the parcel map is read by this guard (04.10)', () => {
		const dossier = 'src/lib/components/adresse/carte';
		const composants = readdirSync(dossier)
			.filter((nom) => nom.endsWith('.svelte'))
			.map((nom) => `${dossier}/${nom}`);
		// Non-vacuity: the dialog and the panel of the tracer at least.
		expect(composants.length).toBeGreaterThanOrEqual(2);
		expect(composants.filter((chemin) => !(FICHIERS as readonly string[]).includes(chemin))).toEqual([]);
	});

	it('a « followed by an ordinary space fails in a new component (amendment 7)', () => {
		expect(fautes_de('<a href="/"><span class="lien-t">« Challans»</span></a>')).toContain('espace ordinaire après « (U+202F attendu)');
	});

	it('the scanner reads what a reader sees, and only that (non-vacuity)', () => {
		const svelte = textesSvelte(
			'<script>\n\t// Source : comment\n\tconst a = \'Source : x\';\n\tconst b = n > 1 ? `${n} ventes` : c;\n</script>\n<p title="Copié !">Texte ; ici {x ? \'oui\' : \'non\'}</p>\n<style>.a { content: "« x »"; }</style>'
		).map((item) => item.texte);
		expect(svelte).toContain('Source : x');
		expect(svelte).toContain('Copié !');
		expect(svelte).toContain('Texte ; ici ');
		expect(svelte).toContain('oui');
		expect(svelte.join('|')).not.toContain('comment');
		expect(svelte.join('|')).not.toContain('« x »');
		expect(svelte.join('|')).not.toMatch(/n > 1/);
		expect(fautes_de('<p><strong>Licence</strong> : x</p><p>Contact :<a href="/">y</a></p>')).toHaveLength(2);
		// A ternary of an expression is code, not text: never a fault.
		expect(fautes_de("<p>{a ? b : c}</p><p>{x ? 'oui' : 'non'}</p>")).toEqual([]);
		// The prose patterns of the legal pages catch « : » inside a text node and a number of days.
		const prose = (texte: string) => MOTIFS_PROSE.filter(([, motif]) => motif.test(texte)).map(([nom]) => nom);
		expect(prose('Délai de réponse : 30 jours')).toHaveLength(2);
		expect(prose('Délai de réponse : 30 jours')).toEqual([]);
		const ts = chainesTs("const r = /'[^']*'/; const s = 'Motif : a';").map((item) => item.texte);
		expect(ts).toEqual(['Motif : a']);
	});
});
