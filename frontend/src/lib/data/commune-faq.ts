// Factual FAQ of a commune fiche (04.7-15 T4, Origami R10): computed from the served answer, never
// written by hand. Pure module, zero Svelte imports.
//
// SAME FIGURES AS THE MARKERS: the price answers read the lines of `communeKpis`, the DPE answers
// the served block that the distribution chart reads, so a figure of the FAQ is the figure of the
// card above it. A question whose value or period is not served (absence of any kind, null block,
// no served window) is left out: it is never answered by a zero nor by « non renseigné ». The
// JSON-LD repeats the visible text word for word (`faqJsonLd`).
//
// The name of the commune opens the question (« Le Mans : … ») and is the subject of a sentence,
// never after « à » or « de », which would need an elision (« à Le Mans »).
//
// Not asked: the rent (a prediction whose grain varies, commune, EPCI or mesh: a short answer would
// read as a commune fact) and the risks (the commune synthesis serves none).
import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import { communeKpis, type KpiCommune } from './commune-kpi';
import { repartitionDpe, resultatDpeServi } from './commune-repartition-dpe';
import { etatDeColonne } from './etats-colonnes';
import { formatBareNumber } from './formatters';

export interface QuestionFaq {
	id: 'prix-maisons' | 'prix-appartements' | 'part-fg' | 'classe-frequente' | 'population';
	question: string;
	reponse: string;
}

const NB = ' ';
const NNB = ' ';

export type TypeBien = 'maison' | 'appartement';

/** The window of a median is served: the dated block's current window, else the flat DVF window. */
function fenetreServie(commune: CommuneSynthese, type: TypeBien): boolean {
	const block = commune.indicateurs_dvf;
	if (block && 'version_contrat' in block) {
		return (type === 'maison' ? block.residentiel_maison : block.residentiel_appartement)?.courante?.fenetre != null;
	}
	return !block && commune.fenetre_mediane_dvf != null;
}

/** A median price as its card says it: the value with its unit, the served size and period. */
export interface PrixServi {
	valeur: string;
	effectif: string;
	fenetre: string;
}

/**
 * The served median price of *type*, with its served size and period; null when any of the three is not
 * served (the FAQ and the meta description then say nothing of it, never a zero).
 */
export function prixServi(commune: CommuneSynthese, kpis: KpiCommune[], type: TypeBien): PrixServi | null {
	if (!fenetreServie(commune, type)) return null;
	const ligne = kpis.find((kpi) => kpi.id === (type === 'maison' ? 'prix-maisons' : 'prix-appartements'))?.lignes[0];
	if (!ligne || ligne.etat.kind !== 'valeur' || !ligne.effectif || !ligne.fenetre) return null;
	return { valeur: [ligne.etat.texte, ligne.etat.unite].filter(Boolean).join(NB), effectif: ligne.effectif, fenetre: ligne.fenetre };
}

export function faqCommune(
	commune: CommuneSynthese,
	sources: SourceAttribution[] | null | undefined,
	nom: string,
	kpis: KpiCommune[] = communeKpis(commune, sources)
): QuestionFaq[] {
	const questions: QuestionFaq[] = [];
	// An arrondissement is not a commune: the wording follows the served `type_zone`.
	const arrondissement = commune.type_zone === 'arrondissement';
	const zone = arrondissement ? 'dans l’arrondissement' : 'dans la commune';
	const prix = (id: 'prix-maisons' | 'prix-appartements', type: TypeBien, objet: string) => {
		const servi = prixServi(commune, kpis, type);
		if (!servi) return;
		const nomSource = kpis.find((kpi) => kpi.id === id)?.source?.nom;
		const source = nomSource ? ` Source${NB}: ${nomSource}.` : '';
		const reponse = `Le prix médian au mètre carré des ${objet} ${zone} est de ${servi.valeur} (${servi.effectif}, ${servi.fenetre}).${source}`;
		questions.push({ id, question: `${nom}${NB}: quel est le prix médian des ${type}s${NNB}?`, reponse });
	};
	prix('prix-maisons', 'maison', 'maisons vendues');
	prix('prix-appartements', 'appartement', 'appartements vendus');

	const resultat = resultatDpeServi(commune);
	if (repartitionDpe(commune) && resultat) {
		const connues = `${formatBareNumber('dpe.indicateurs.population.total', resultat.population.classes_connues)}${NB}diagnostics`;
		if (resultat.part_fg_pct.etat === 'value') {
			questions.push({
				id: 'part-fg',
				question: `${nom}${NB}: quelle part des DPE est classée F ou G${NNB}?`,
				reponse: `${formatBareNumber('dpe.indicateurs.part_fg_pct', resultat.part_fg_pct.valeur)}${NB}% des ${connues} de performance énergétique de classe connue sont classés F ou G. Un DPE est un document, pas un logement${NB}: ce n’est pas une part du parc de logements.`
			});
		}
		const modes = resultat.modes;
		questions.push({
			id: 'classe-frequente',
			question: `${nom}${NB}: quelle est la classe DPE la plus fréquente${NNB}?`,
			reponse: modes.length === 1
				? `La classe ${modes[0]} est la plus fréquente parmi les ${connues} de classe connue.`
				: `Les classes ${modes.slice(0, -1).join(', ')} et ${modes.at(-1)} sont les plus fréquentes, à égalité, parmi les ${connues} de classe connue.`
		});
	}

	const population = etatDeColonne(commune, 'population');
	if (population.kind === 'valeur') {
		const annee = commune.annee_demographie != null ? ` (${commune.annee_demographie}, INSEE)` : '';
		// A served zero (a village « mort pour la France », 55039) is worded as such, never « 0 habitants »:
		// the same words as the meta description (`habitantsServis`).
		const n = Number(population.valeur);
		questions.push({
			id: 'population',
			question: `${nom}${NB}: combien d’habitants compte ${arrondissement ? 'l’arrondissement' : 'la commune'}${NNB}?`,
			reponse: n === 0
				? `${nom} ne compte aucun habitant recensé${annee}.`
				: `${nom} compte ${formatBareNumber('demographie.population', n)}${NB}${n === 1 ? 'habitant' : 'habitants'}${annee}.`
		});
	}
	return questions;
}

/** schema.org `FAQPage` of the visible questions, word for word; null when there is none. */
export function faqJsonLd(questions: QuestionFaq[]): Record<string, unknown> | null {
	if (!questions.length) return null;
	return {
		'@context': 'https://schema.org',
		'@type': 'FAQPage',
		mainEntity: questions.map((item) => ({
			'@type': 'Question',
			name: item.question,
			acceptedAnswer: { '@type': 'Answer', text: item.reponse }
		}))
	};
}
