// Phase 57 (D-19/D-44) — test d'APPLICATION de la copie DVF arbitrée.
//
// Ce test ne compare pas le code à lui-même : sa source de vérité est le DOSSIER D'ARBITRAGE
// `57-COPIE-DVF.md` (suivi de l'ancien dépôt), lu sur le disque, tranché mot à mot par Ianis le
// 2026-08-25 (« Arbitrage rendu le 2026-08-25 par Ianis »). Il exige que chaque formulation
// RETENUE y figure à l'identique dans la copie exécutable, et qu'aucune formulation RETIRÉE ou
// REMPLACÉE n'y subsiste. Une reformulation silencieuse au fil d'un refactor devient rouge.
//
// Un seul écart typographique est toléré, parce qu'il est systématique et ne change aucun mot :
// le dossier écrit l'apostrophe typographique U+2019, les littéraux TypeScript de ce frontend
// écrivent l'apostrophe droite. Les deux côtés sont normalisés avant comparaison.

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render, within } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import DatasetDetail from '$lib/components/DatasetDetail.svelte';
import type { DatasetPublic } from '$lib/api/datasets';
import { COPIE_DVF, etiquetteMutation } from './dvf-attribution-view';
import { EXPLICATIONS } from './explications';
import datasetsApi from './__fixtures__/datasets-api.json';

// Transfert 2026-09-20 (plan 01-02) : le dossier d'arbitrage vivait dans le suivi de
// l'ancien depot. Ce depot-ci garde son propre suivi interne a part, et D-07 prevoit de
// le sortir du depot avant toute publication publique — un test qui l'y lirait deviendrait
// rouge le jour de l'ouverture. Le dossier est donc transfere tel quel (meme SHA-256 qu'a
// `3394395a`) comme FIXTURE de ce test, a cote des autres fixtures de ce repertoire. Le
// contenu arbitre par Ianis le 2026-08-25 n'est pas modifie : seul son emplacement change.
const DOSSIER_PATH = resolve(process.cwd(), 'src/lib/data/__fixtures__/57-COPIE-DVF.md');

/** Le SEUL écart toléré entre le dossier et le code : U+2019 → apostrophe droite. */
function normaliser(texte: string): string {
	return texte.replace(/’/g, "'");
}

const DOSSIER = normaliser(readFileSync(DOSSIER_PATH, 'utf8'));

/** Titre exact de l'arbitrage complémentaire du soir (D-46 / D-47), qui supersède le premier. */
const SECTION_D46 =
	'## Arbitrage complémentaire rendu le 2026-08-25 (soir) par Ianis — D-46 / D-47';

// ── Lecture du dossier ────────────────────────────────────────────────────

interface LigneDossier {
	famille: string;
	id: string;
	proposition: string;
	arbitrage: string;
}

const LIGNE_SEPARATRICE = /^:?-+:?$/;

/** Les lignes des tableaux à 4 colonnes des familles 0 à 5, dans l'ordre du dossier. */
function lignesDuDossier(): LigneDossier[] {
	const lignes: LigneDossier[] = [];
	let famille = '';
	for (const brute of DOSSIER.split('\n')) {
		if (brute.startsWith('## ')) famille = brute.slice(3).trim();
		if (!brute.startsWith('|')) continue;
		const cellules = brute
			.split('|')
			.slice(1, -1)
			.map((c) => c.trim());
		// Les tableaux à 2 colonnes (mode d'emploi, récapitulatif final) sont lus ailleurs.
		if (cellules.length !== 4) continue;
		if (cellules[0] === 'Identifiant technique' || LIGNE_SEPARATRICE.test(cellules[0])) continue;
		lignes.push({ famille, id: cellules[0], proposition: cellules[2], arbitrage: cellules[3] });
	}
	return lignes;
}

/** Les littéraux d'une cellule, c'est-à-dire ce que le dossier écrit entre accents graves. */
function litteraux(cellule: string): string[] {
	return [...cellule.matchAll(/`([^`]+)`/g)].map((m) => m[1]);
}

/** Le corps d'une section `##`, borné à la section SUIVANTE — jamais jusqu'à la fin du fichier. */
function sectionDuDossier(titre: string): string {
	return (DOSSIER.split(titre)[1] ?? '').split('\n## ')[0];
}

/** Les rangs d'un tableau à 2 colonnes `| Clé | … |` d'une section donnée. */
function rangsDeuxColonnes(section: string): Array<{ cle: string; retenue: string }> {
	const rangs: Array<{ cle: string; retenue: string }> = [];
	for (const brute of section.split('\n')) {
		if (!brute.startsWith('|')) continue;
		const cellules = brute
			.split('|')
			.slice(1, -1)
			.map((c) => c.trim());
		if (cellules.length !== 2) continue;
		if (cellules[0] === 'Clé' || LIGNE_SEPARATRICE.test(cellules[0])) continue;
		rangs.push({ cle: cellules[0], retenue: cellules[1] });
	}
	return rangs;
}

/** Le tableau récapitulatif des sept remplacements Codex, sous « Arbitrage rendu le … ». */
function recapitulatifDesRemplacements(): Array<{ cle: string; retenue: string }> {
	return rangsDeuxColonnes(sectionDuDossier('## Arbitrage rendu le 2026-08-25 par Ianis'));
}

/**
 * Le tableau de l'arbitrage complémentaire du soir (D-46 / D-47). Il SUPERSÈDE le récapitulatif
 * ci-dessus partout où une clé apparaît dans les deux : c'est pourquoi les deux sections sont lues
 * séparément, et jamais l'une jusqu'à la fin du fichier.
 */
function recapitulatifComplementaire(): Array<{ cle: string; retenue: string }> {
	return rangsDeuxColonnes(sectionDuDossier(SECTION_D46));
}

/** Les formulations que D-46 retire : la liste à puces de la section complémentaire. */
function formulationsRetirees(): string[] {
	const section = sectionDuDossier(SECTION_D46);
	return section
		.split('\n')
		.filter((l) => l.startsWith('- `'))
		.map((l) => litteraux(l)[0]);
}

// ── Corpus exécutable ─────────────────────────────────────────────────────

/** Toutes les chaînes d'un objet de copie, quelle que soit sa profondeur. */
function chainesDe(valeur: unknown, acc: string[] = []): string[] {
	if (typeof valeur === 'string') acc.push(normaliser(valeur));
	else if (Array.isArray(valeur)) for (const v of valeur) chainesDe(v, acc);
	else if (valeur != null && typeof valeur === 'object')
		for (const v of Object.values(valeur)) chainesDe(v, acc);
	return acc;
}

// La copie affichée par la Fiche Bien et la Fiche Commune : les étiquettes et mentions vivent
// dans `COPIE_DVF`, les phrases des panneaux ℹ️ dans `EXPLICATIONS`. Les deux sont du texte
// arbitré, donc les deux entrent dans le corpus vérifié. La famille 5 vit ailleurs — les
// limites de la source que `GET /v1/datasets` sert pour DVF, depuis le catalogue du backend
// (D-40), rendues par la carte DVF de « Données & méthode » — et a son propre corpus plus bas.
const FAMILLES_FICHE = ['Famille 0', 'Famille 1', 'Famille 2', 'Famille 3', 'Famille 4'];

const CORPUS_FICHE = [...chainesDe(COPIE_DVF), ...chainesDe(EXPLICATIONS)];

/**
 * La réponse de `GET /v1/datasets` (fixture générée par le backend et épinglée au catalogue par
 * `test_datasets_reutilisation.py`) : l'entrée DVF porte la famille 5 dans `limites_source`.
 */
const JEUX = datasetsApi.donnees as DatasetPublic[];
const DATASET_DVF = JEUX.find((d) => d.nom === 'dvf')!;
const CORPUS_DATASET = chainesDe(DATASET_DVF.limites_source);

/** Le détail de la source DVF tel que « Données & méthode » le rend (DatasetDetail depuis 04.5-11). */
function carteDvf() {
	const { container } = render(DatasetDetail, {
		props: { jeu: DATASET_DVF, departementsInstance: DATASET_DVF.hebergement?.departements ?? [] }
	});
	return container.querySelector<HTMLElement>('.dataset-detail')!;
}

function estDansLeCorpus(corpus: string[], attendue: string): boolean {
	return corpus.some((c) => c === attendue || c.includes(attendue));
}

// ── 1. Le dossier est bien celui qui a été tranché ────────────────────────

describe('dossier de copie DVF — entrée de l’application (D-19)', () => {
	it('porte la ligne d’arbitrage datée, sans laquelle rien ne doit être appliqué', () => {
		expect(DOSSIER).toContain('Arbitrage rendu le 2026-08-25 par Ianis');
	});

	it('porte l’arbitrage complémentaire du soir (D-46 / D-47), qui supersède le premier', () => {
		expect(DOSSIER).toContain(SECTION_D46);
	});

	it('n’a aucune case d’arbitrage vide : 68 lignes, toutes tranchées', () => {
		const lignes = lignesDuDossier();
		expect(lignes).toHaveLength(68);
		const parDecision = { retenu: 0, remplace: 0, retire: 0 };
		for (const l of lignes) {
			// « Retenu tel quel » (arbitrage du matin) et « Retenu » (D-47, formulation nouvelle
			// retenue telle que proposée) comptent pareil : dans les deux cas la colonne
			// « Proposition exacte » EST la copie à appliquer.
			if (l.arbitrage.startsWith('Retenu')) parDecision.retenu += 1;
			else if (l.arbitrage.startsWith('**Remplacé')) parDecision.remplace += 1;
			else if (l.arbitrage.startsWith('Retiré')) parDecision.retire += 1;
			else throw new Error(`arbitrage non reconnu sur « ${l.id} » : ${l.arbitrage}`);
		}
		expect(parDecision).toEqual({ retenu: 56, remplace: 11, retire: 1 });
	});

	it('récapitule exactement sept remplacements le matin, sept formulations le soir', () => {
		expect(recapitulatifDesRemplacements()).toHaveLength(7);
		expect(recapitulatifComplementaire()).toHaveLength(7);
	});
});

// ── 2. Chaque formulation RETENUE est appliquée à l’identique ─────────────

describe('copie DVF des Fiches — application mot pour mot du dossier arbitré', () => {
	const lignesFiche = lignesDuDossier().filter((l) =>
		FAMILLES_FICHE.some((f) => l.famille.startsWith(f))
	);

	it('couvre les familles 0 à 4 du dossier, sans en sauter une', () => {
		// Garde-fou de complétude : si le dossier gagnait une ligne, elle ne pourrait pas passer
		// sous le radar de ce fichier.
		expect(lignesFiche).toHaveLength(61);
		expect(new Set(lignesFiche.map((l) => l.famille.slice(0, 9)))).toEqual(
			new Set(FAMILLES_FICHE)
		);
	});

	const retenues = lignesFiche.filter((l) => l.arbitrage.startsWith('Retenu'));

	it.each(retenues.map((l) => [l.id, litteraux(l.proposition)[0]] as const))(
		'« %s » : la formulation retenue est appliquée telle quelle',
		(_id, attendue) => {
			expect(attendue).toBeTruthy();
			expect(estDansLeCorpus(CORPUS_FICHE, attendue)).toBe(true);
		}
	);

	it('les 52 formulations retenues des familles 0 à 4 sont toutes vérifiées', () => {
		// D-46/D-47 déplacent deux lignes de « Retenu » vers « Remplacé » et en ajoutent deux
		// (`vente_sur_plan`) : le total est stable, la composition non.
		expect(retenues).toHaveLength(52);
	});
});

// ── 3. Aucune formulation écartée ne subsiste ─────────────────────────────

describe('copie DVF des Fiches — les formulations écartées ont disparu', () => {
	const lignesFiche = lignesDuDossier().filter((l) =>
		FAMILLES_FICHE.some((f) => l.famille.startsWith(f))
	);

	// Une ligne « Remplacé » dont la colonne « Proposition exacte » ne porte qu'un seul littéral :
	// ce littéral EST l'ancienne chaîne, et elle ne doit plus exister nulle part dans la copie.
	// La seule ligne à deux littéraux (famille 3, « hors VEFA » + texte d'infobulle) est traitée
	// par le bloc « hors ventes sur plan » ci-dessous, où l'infobulle est au contraire exigée.
	const remplacees = lignesFiche
		.filter((l) => l.arbitrage.startsWith('**Remplacé'))
		.map((l) => litteraux(l.proposition))
		.filter((lits) => lits.length === 1)
		.map((lits) => lits[0]);

	it('sept anciennes chaînes remplacées sont identifiées dans le dossier', () => {
		expect(remplacees).toHaveLength(7);
	});

	it.each(remplacees)('« %s » ne subsiste nulle part dans la copie', (ancienne) => {
		expect(estDansLeCorpus(CORPUS_FICHE, ancienne)).toBe(false);
	});

	it('le libellé de KPI retiré — « Prix au m² (dernière vente) » — a disparu', () => {
		const retiree = lignesFiche.find((l) => l.arbitrage.startsWith('Retiré'));
		const barree = /~~([^~]+)~~/.exec(retiree?.proposition ?? '');
		expect(barree?.[1]).toBe('Prix au m² (dernière vente)');
		expect(estDansLeCorpus(CORPUS_FICHE, barree![1])).toBe(false);
	});
});

// ── 4. Les six remplacements Codex, un par un ─────────────────────────────

const CLE_INFOBULLE_VEFA = 'Infobulle VEFA des 3 lignes de prix commune (famille 3)';

describe('copie DVF — les remplacements du 2026-08-25 (revue Codex, validés Ianis)', () => {
	const recap = new Map(recapitulatifDesRemplacements().map((r) => [r.cle, r.retenue]));

	// `kpiLabel` et `etats.compatible_parcelle.mention` ne sont PLUS vérifiées ici : l'arbitrage
	// complémentaire du soir (D-46) les supersède, et c'est le bloc suivant qui les tient.

	it.each([['`limites.neuf`'], ['`limites.delai`'], ['`limites.declaratif`']])(
		'%s porte la formulation retenue sur la page dataset, au caractère près',
		(cle) => {
			const retenue = litteraux(recap.get(cle) ?? '')[0];
			expect(retenue).toBeTruthy();
			expect(estDansLeCorpus(CORPUS_DATASET, retenue)).toBe(true);
		}
	);

	it('les libellés visibles disent « hors ventes sur plan » et jamais l’acronyme', () => {
		// Sixième remplacement : l'acronyme quitte le libellé et n'existe plus que dans l'ℹ️.
		const cle = [...recap.keys()].find((k) => k.includes('hors VEFA'));
		expect(cle, 'la ligne « libellés visibles » du récapitulatif est introuvable').toBeTruthy();
		expect(recap.get(cle!)).toContain('hors ventes sur plan');

		const lignesPrix = [
			COPIE_DVF.lignesCommune.prixMedian,
			COPIE_DVF.lignesCommune.prixMedianMaison,
			COPIE_DVF.lignesCommune.prixMedianAppartement
		];
		for (const libelle of lignesPrix) {
			expect(libelle).toContain('hors ventes sur plan');
			expect(libelle).not.toContain('VEFA');
		}
		// … et l'acronyme est développé dans l'infobulle des trois lignes, jamais laissé nu.
		// Septième remplacement (correctif final Ianis, 2026-08-25) : la phrase de l'ℹ️ est
		// factuelle — elle dit l'exclusion et son motif, sans invoquer une couverture du neuf
		// dont ce projet n'a jamais publié la mesure. Le libellé exact vient du dossier.
		const infobulle = litteraux(recap.get(CLE_INFOBULLE_VEFA) ?? '')[0];
		expect(infobulle, 'la ligne « infobulle VEFA » du récapitulatif est introuvable').toBeTruthy();
		for (const fieldId of [
			'dvf.prix_m2_median',
			'dvf.prix_m2_median_maison',
			'dvf.prix_m2_median_appartement'
		] as const) {
			expect(EXPLICATIONS[fieldId].definition, fieldId).toContain(infobulle);
			expect(EXPLICATIONS[fieldId].definition, fieldId).not.toContain('couverture du neuf');
		}
	});
});

// ── 4 bis. L'arbitrage complémentaire du soir : D-46 (copie courte) et D-47 ─

describe('copie DVF — arbitrage complémentaire du 2026-08-25 (soir), D-46 / D-47', () => {
	const recap = new Map(recapitulatifComplementaire().map((r) => [r.cle, r.retenue]));

	it.each([
		['`kpiLabel`'],
		['`etats.compatible_parcelle.mention`'],
		['`etats.compatible_parcelle.detail`'],
		['`suffixeVefa`'],
		['`raisons.vente_sur_plan` — mention'],
		['`raisons.vente_sur_plan` — detail']
	])('%s porte la formulation retenue le soir, au caractère près', (cle) => {
		const retenue = litteraux(recap.get(cle) ?? '')[0];
		expect(retenue, `la ligne « ${cle} » du récapitulatif du soir est introuvable`).toBeTruthy();
		expect(estDansLeCorpus(CORPUS_FICHE, retenue)).toBe(true);
	});

	it('les quatre formulations retirées par D-46 ont disparu de la copie', () => {
		const retirees = formulationsRetirees();
		expect(retirees).toHaveLength(4);
		for (const ancienne of retirees) {
			expect(estDansLeCorpus(CORPUS_FICHE, ancienne), ancienne).toBe(false);
		}
	});

	it('D-46 : plus AUCUNE chaîne affichée de la copie DVF ne porte de tiret cadratin', () => {
		// L'invariant, pas seulement ses instances : une reformulation qui réintroduirait un
		// « — » dans un libellé, une mention ou une phrase d'ℹ️ rougit ici.
		for (const chaine of chainesDe(COPIE_DVF)) {
			expect(chaine, chaine).not.toContain('—');
		}
		for (const fieldId of Object.keys(EXPLICATIONS)) {
			if (!fieldId.includes('dvf.')) continue;
			const definition = EXPLICATIONS[fieldId as keyof typeof EXPLICATIONS].definition;
			expect(definition, fieldId).not.toContain('—');
		}
	});

	it('D-47 : la raison `vente_sur_plan` existe, se distingue de `vefa_sans_surface`', () => {
		const nouvelle = COPIE_DVF.raisons.vente_sur_plan;
		const sansSurface = COPIE_DVF.raisons.vefa_sans_surface;
		expect(nouvelle.mention).not.toBe(sansSurface.mention);
		expect(nouvelle.detail).not.toBe(sansSurface.detail);
		// Le cas « avec surface » dit le motif marché, jamais l'absence de surface.
		expect(nouvelle.detail).not.toContain('surface');
		expect(sansSurface.detail).toContain('surface');
	});

	it('le suffixe VEFA est rendu ENTRE PARENTHÈSES, jamais après un tiret (D-46)', () => {
		const etiquette = etiquetteMutation('residentiel_appartement', true);
		expect(etiquette).toBe("Vente d'un appartement seul (vente sur plan)");
	});
});

// ── 5. Famille 5 — la section « limites de la source » de la page dataset ─

describe('page dataset DVF — limites de la source (D-20), appliquées mot pour mot', () => {
	const lignes5 = lignesDuDossier().filter((l) => l.famille.startsWith('Famille 5'));

	it('couvre les sept lignes de la famille 5', () => {
		expect(lignes5).toHaveLength(7);
	});

	const retenues5 = lignes5.filter((l) => l.arbitrage.startsWith('Retenu tel quel'));

	it.each(retenues5.map((l) => [l.id, litteraux(l.proposition)[0]] as const))(
		'« %s » : la formulation retenue est appliquée telle quelle',
		(_id, attendue) => {
			expect(attendue).toBeTruthy();
			// Le titre de la section est l'intitulé du bloc rendu par la carte ; le chapeau et les
			// puces sont servis par l'API. Les deux entrent dans le corpus de la famille 5.
			const titre = within(carteDvf()).getByRole('heading', { name: 'Limites de la source' });
			const corpus = [...CORPUS_DATASET, normaliser(titre.textContent ?? '')];
			expect(estDansLeCorpus(corpus, attendue)).toBe(true);
		}
	);

	const remplacees5 = lignes5
		.filter((l) => l.arbitrage.startsWith('**Remplacé'))
		.map((l) => litteraux(l.proposition)[0]);

	it.each(remplacees5)('« %s » ne subsiste nulle part sur la page dataset', (ancienne) => {
		expect(estDansLeCorpus(CORPUS_DATASET, ancienne)).toBe(false);
	});

	it('la section porte le titre, le chapeau et les cinq puces arbitrés, dans l’ordre', () => {
		expect(DATASET_DVF.limites_source?.puces).toHaveLength(5);
		const section = within(carteDvf()).getByRole('region', { name: 'Limites de la source' });
		expect(section.textContent).toContain(DATASET_DVF.limites_source!.chapeau);
		expect(
			within(section)
				.getAllByRole('listitem')
				.map((li) => li.textContent)
		).toEqual(DATASET_DVF.limites_source!.puces);
	});

	it('la copie arbitrée DVF reste propre à DVF, sans être copiée dans une autre réserve', () => {
		const copieSpecifiqueDvf = chainesDe({
			chapeau: DATASET_DVF.limites_source?.chapeau,
			puces: DATASET_DVF.limites_source?.puces
		});
		expect(copieSpecifiqueDvf).toHaveLength(6);
		for (const dataset of JEUX.filter((d) => d.nom !== 'dvf')) {
			const autreCopie = chainesDe({
				limites: dataset.limites_source,
				quarantaine: dataset.quarantaine,
				restrictions: dataset.reutilisation?.restrictions
			});
			for (const formulation of copieSpecifiqueDvf) {
				expect(autreCopie, `${dataset.nom} reprend une formulation propre à DVF`).not.toContain(formulation);
			}
		}
	});

	it('la réserve d’encadrement décrit sa quarantaine, distincte des limites DVF', () => {
		// Seul DVF sert des limites de la source ; la réserve d'encadrement est servie comme le
		// motif de sa quarantaine (catalogue du backend, D-40), phrase par phrase, à l'identique.
		expect(JEUX.filter((d) => d.limites_source != null).map((d) => d.nom)).toEqual(['dvf']);
		const encadrement = JEUX.find((d) => d.nom === 'encadrement_loyers')!;
		expect(encadrement.statut).toBe('quarantined');
		for (const phrase of [
			'Service indisponible',
			"Cette indisponibilité ne signifie pas absence d'encadrement des loyers.",
			'Réponse HTTP 503, y compris avec une sélection manuelle de quartier.',
			'La proximité du centroïde ne démontre pas le périmètre réglementaire applicable à une adresse.',
			'La publication reprendra après qualification des périmètres, des catégories de logement et du millésime de référence.'
		]) {
			expect(encadrement.quarantaine).toContain(phrase);
		}
	});
});

// ── 6. Déduplication (57-15 Task 3c) — pas deux fois, pas contradictoire ──

describe('carte DVF — les affirmations doublonnées ont été RETIRÉES', () => {
	// Depuis D-40 la carte DVF de « Données & méthode » remplace la page dataset : la règle 57-15
	// porte sur tout ce qu'elle rend (limites, restrictions, conditions de réutilisation).

	it('la cadence « N+1 / 6 derniers mois » a disparu : la puce semestrielle fait foi', () => {
		// Deux affirmations contradictoires sur la même page valent moins que l'absence des deux.
		const texte = normaliser(carteDvf().textContent ?? '');
		expect(texte).not.toContain('aucune vente des 6 derniers mois');
		expect(texte).not.toContain('N+1');
	});

	it('Bas-Rhin et Mayotte ne sont plus dits qu’une seule fois, dans les limites de la source', () => {
		// LIC-01 (02-02, relecture D-15) : les restrictions de réutilisation sont sur la même carte ;
		// un territoire qui y serait répété tomberait sous la même règle.
		const texte = carteDvf().textContent ?? '';
		const restrictions = chainesDe(DATASET_DVF.reutilisation?.restrictions);
		for (const territoire of ['Bas-Rhin', 'Mayotte']) {
			expect(texte.split(territoire)).toHaveLength(2);
			expect(CORPUS_DATASET.filter((l) => l.includes(territoire))).toHaveLength(1);
			expect(restrictions.filter((l) => l.includes(territoire))).toHaveLength(0);
		}
	});
});
