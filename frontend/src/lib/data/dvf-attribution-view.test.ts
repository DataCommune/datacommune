import { readFileSync } from 'node:fs';
import { resolve as joinPath } from 'node:path';
import { describe, it, expect } from 'vitest';

import type { DvfAttribution, DvfMutation } from '$lib/api/resolve';
import {
	COPIE_DVF,
	attributionDvfInattendue,
	deriveDvfAttributionView,
	etiquetteMutation,
	type DvfAttributionView
} from './dvf-attribution-view';

const SRC_MODULE = joinPath(process.cwd(), 'src/lib/data/dvf-attribution-view.ts');

// Les 7 valeurs du vocabulaire fermé `app.etl_dvf.qualification.CLASSES_MUTATION`, recopiées ici
// pour que l'ajout d'une classe côté backend rende la barre rouge au lieu de rendre « nature non
// qualifiée » à l'écran pour une classe parfaitement connue.
const CLASSES_MUTATION = [
	'residentiel_maison',
	'residentiel_appartement',
	'ensemble_residentiel',
	'mixte',
	'commerciale',
	'terrain_seul',
	'hors_perimetre'
] as const;

// Les 13 clés du vocabulaire fermé `app.dvf.schemas.DVF_ATTRIBUTION_RAISONS`, avec l'état qui a le
// droit de les porter (miroir de `_RAISONS_PAR_ETAT`). Aucune ne doit rester sans copie.
const RAISONS: Array<[string, 'non_attribuable' | 'absente' | 'error']> = [
	['derniere_vente_mixte', 'non_attribuable'],
	['derniere_vente_commerciale', 'non_attribuable'],
	['vente_ensemble', 'non_attribuable'],
	['type_incompatible', 'non_attribuable'],
	['classe_non_residentielle', 'non_attribuable'],
	['vefa_sans_surface', 'non_attribuable'],
	// D-47 : la vente sur plan AVEC surface — distincte de `vefa_sans_surface`, qui dit
	// l'absence de surface. Deux faits différents, deux raisons, jamais confondues.
	['vente_sur_plan', 'non_attribuable'],
	['nature_hors_vente', 'non_attribuable'],
	['type_bien_inconnu', 'non_attribuable'],
	['aucune_mutation', 'absente'],
	['parcelle_indisponible', 'absente'],
	['entree_illisible', 'error'],
	['temporary_error', 'error']
];

function mutation(partiel: Partial<DvfMutation> = {}): DvfMutation {
	return {
		id_mutation: '2023-123456',
		date_mutation: '2023-09-12',
		nature_mutation: 'Vente',
		valeur_fonciere: 168000,
		type_local: 'Appartement',
		surface_reelle_bati: 78,
		nombre_pieces_principales: 3,
		prix_m2: 2154,
		id_parcelle: '85047000AB0042',
		classe_mutation: 'residentiel_appartement',
		prix_m2_raison: null,
		mutation_mono_local: true,
		surface_bati_totale: 78,
		hors_stats_vefa: false,
		...partiel
	};
}

const COMPATIBLE: DvfAttribution = {
	status: 'compatible_parcelle',
	mutation: mutation(),
	rule_version: '57-17/D-47',
	evidence: ['type_compatible', 'mono_local', 'nature_vente', 'surface_presente'],
	perimetre_ensemble: false,
	nb_locaux_residentiel: 1,
	nb_locaux_commercial: 0
};

const EXACTE: DvfAttribution = {
	status: 'exacte',
	mutation: mutation(),
	preuve_locale: 'lot_identifie',
	evidence: ['type_compatible', 'mono_local', 'nature_vente'],
	perimetre_ensemble: false
};

// --- 1. Les cinq états ------------------------------------------------------------------------

describe('les cinq états backend restent cinq modèles de vue distincts (D-16)', () => {
	it('compatible parcelle : la mutation est publiée AVEC la mention de non-attribution (D-37)', () => {
		const v = deriveDvfAttributionView(COMPATIBLE);
		expect(v.status).toBe('compatible_parcelle');
		expect(v.mutation?.prix_m2).toBe(2154);
		expect(v.gapState).toBeNull();
		expect(v.mention).toBe(COPIE_DVF.etats.compatible_parcelle.mention);
		expect(v.mentionDetaillee).toBe(COPIE_DVF.etats.compatible_parcelle.detail);
		// La phrase doit dire les DEUX choses : ce qui est observé, et ce qui n'est pas prouvé.
		expect(v.mentionDetaillee).toContain('observée sur cette parcelle');
		expect(v.mentionDetaillee).toContain('non attribuée au bien');
	});

	it('exacte : traité par le switch, bien que le backend ne le produise jamais en 57 (D-37)', () => {
		const v = deriveDvfAttributionView(EXACTE);
		expect(v.status).toBe('exacte');
		expect(v.mutation?.id_mutation).toBe('2023-123456');
		expect(v.gapState).toBeNull();
		expect(v.mention).toBe(COPIE_DVF.etats.exacte.mention);
	});

	it('non attribuable : aucune mutation publiée, un écart de couverture et une raison', () => {
		const v = deriveDvfAttributionView({ status: 'non_attribuable', reason: 'vente_ensemble' });
		expect(v.mutation).toBeNull();
		expect(v.gapState).toBe('out_of_coverage');
		expect(v.mention).toBe(COPIE_DVF.raisons.vente_ensemble.mention);
	});

	it('absente : une absence métier, jamais présentée comme une panne', () => {
		const v = deriveDvfAttributionView({ status: 'absente', reason: 'aucune_mutation' });
		expect(v.mutation).toBeNull();
		expect(v.gapState).toBe('out_of_coverage');
		expect(v.mention).toBe(COPIE_DVF.raisons.aucune_mutation.mention);
	});

	it('error : une panne, et un état technique distinct de l’absence (D-16)', () => {
		const panne = deriveDvfAttributionView({ status: 'error', reason: 'temporary_error' });
		const absence = deriveDvfAttributionView({ status: 'absente', reason: 'aucune_mutation' });
		expect(panne.gapState).toBe('temporary_error');
		expect(absence.gapState).toBe('out_of_coverage');
		expect(panne.mention).not.toBe(absence.mention);
		expect(panne.mention).toBe(COPIE_DVF.raisons.temporary_error.mention);
	});

	it('une décision absente est une indisponibilité, jamais « aucune vente » (D-14/D-16)', () => {
		for (const vide of [null, undefined]) {
			const v = deriveDvfAttributionView(vide);
			expect(v.status).toBe('error');
			expect(v.gapState).toBe('temporary_error');
			expect(v.mutation).toBeNull();
			expect(v.mention).toBe(COPIE_DVF.raisons.temporary_error.mention);
		}
	});

	it('les trois états sans attribution ne portent JAMAIS de mutation publiable', () => {
		const sansMutation: DvfAttribution[] = [
			{ status: 'non_attribuable', reason: 'type_bien_inconnu' },
			{ status: 'absente', reason: 'parcelle_indisponible' },
			{ status: 'error', reason: 'entree_illisible' }
		];
		for (const attribution of sansMutation) {
			expect(deriveDvfAttributionView(attribution).mutation).toBeNull();
		}
	});

	it('le périmètre d’ensemble est reporté tel quel, jamais recalculé', () => {
		const ensemble = deriveDvfAttributionView({
			status: 'non_attribuable',
			reason: 'vente_ensemble',
			perimetre_ensemble: true,
			nb_locaux_residentiel: 4
		});
		expect(ensemble.perimetreEnsemble).toBe(true);
		expect(deriveDvfAttributionView(COMPATIBLE).perimetreEnsemble).toBe(false);
	});
});

// --- 2. Couverture du vocabulaire fermé -------------------------------------------------------

describe('couverture : chaque raison et chaque classe du backend a sa copie', () => {
	for (const [raison, statut] of RAISONS) {
		it(`la raison « ${raison} » rend une ligne lisible et non technique`, () => {
			const v = deriveDvfAttributionView({
				status: statut,
				reason: raison
			} as unknown as DvfAttribution);
			expect(v.mention, raison).toBeTruthy();
			expect(v.mentionDetaillee, raison).toBeTruthy();
			// Une ligne, vraiment : c'est ce que le KPI a la place d'afficher.
			expect(v.mention!.split('\n')).toHaveLength(1);
			expect(v.mention).not.toContain('_');
			expect(v.mentionDetaillee).not.toContain('_');
		});
	}

	it('une raison hors vocabulaire ne rend RIEN plutôt qu’un mot inventé (fail-closed)', () => {
		const v = deriveDvfAttributionView({
			status: 'non_attribuable',
			reason: 'raison_inventee'
		} as unknown as DvfAttribution);
		expect(v.mention).toBeNull();
		expect(v.mentionDetaillee).toBeNull();
		// L'ÉTAT, lui, survit : l'absence reste une absence même sans phrase pour l'expliquer.
		expect(v.gapState).toBe('out_of_coverage');
		expect(v.mutation).toBeNull();
	});

	for (const classe of CLASSES_MUTATION) {
		it(`la classe « ${classe} » a une étiquette lisible et distincte`, () => {
			const etiquette = etiquetteMutation(classe);
			expect(etiquette, classe).toBeTruthy();
			expect(etiquette).not.toContain('_');
			expect(etiquette).not.toBe(COPIE_DVF.classes.inconnue);
		});
	}

	it('les 7 étiquettes sont deux à deux distinctes (aucune classe n’en recouvre une autre)', () => {
		const etiquettes = CLASSES_MUTATION.map((c) => etiquetteMutation(c));
		expect(new Set(etiquettes).size).toBe(CLASSES_MUTATION.length);
	});

	it('une classe absente ou inconnue est nommée non qualifiée, jamais rapprochée d’une voisine', () => {
		expect(etiquetteMutation(null)).toBe(COPIE_DVF.classes.inconnue);
		expect(etiquetteMutation('classe_du_futur')).toBe(COPIE_DVF.classes.inconnue);
	});

	it('une vente sur plan porte son suffixe EN PLUS de sa classe (D-12/D-18)', () => {
		const etiquette = etiquetteMutation('residentiel_appartement', true);
		expect(etiquette).toContain(COPIE_DVF.classes.residentiel_appartement);
		expect(etiquette).toContain(COPIE_DVF.suffixeVefa);
		// D-46 : le suffixe est entre parenthèses, plus jamais après un tiret cadratin.
		expect(etiquette).toBe(
			`${COPIE_DVF.classes.residentiel_appartement} (${COPIE_DVF.suffixeVefa})`
		);
		expect(etiquette).not.toContain('—');
	});

	it('D-47 : `vente_sur_plan` et `vefa_sans_surface` restent deux raisons distinctes', () => {
		const avecSurface = deriveDvfAttributionView({
			status: 'non_attribuable',
			reason: 'vente_sur_plan'
		} as unknown as DvfAttribution);
		const sansSurface = deriveDvfAttributionView({
			status: 'non_attribuable',
			reason: 'vefa_sans_surface'
		} as unknown as DvfAttribution);
		expect(avecSurface.gapState).toBe('out_of_coverage');
		expect(avecSurface.mutation).toBeNull();
		expect(avecSurface.mention).toBe(COPIE_DVF.raisons.vente_sur_plan.mention);
		expect(avecSurface.mention).not.toBe(sansSurface.mention);
		expect(avecSurface.mentionDetaillee).not.toBe(sansSurface.mentionDetaillee);
	});
});

// --- 3. Hygiène du module ---------------------------------------------------------------------

describe('hygiène : une seule dérivation, aucune règle métier reconstruite', () => {
	const source = readFileSync(SRC_MODULE, 'utf8');

	it('ne lit ni bloc dataset, ni état grossier, ni coordonnée', () => {
		expect(source.length).toBeGreaterThan(1000); // non-vacuité : c'est bien le module lu
		for (const interdit of ['datasets', 'dataset_states', 'latitude', 'longitude', 'id_parcelle']) {
			expect(source, `le mapper ne doit pas mentionner « ${interdit} »`).not.toContain(interdit);
		}
	});

	it('n’importe ni Svelte, ni client API, ni I/O — c’est une fonction pure', () => {
		expect(source).not.toMatch(/from\s+'svelte/);
		expect(source).not.toMatch(/\$app\//);
		expect(source).not.toMatch(/from\s+'node:/);
		expect(source).not.toMatch(/\bfetch\(/);
		const imports = [...source.matchAll(/^import .*$/gm)].map((m) => m[0]);
		expect(imports).toHaveLength(1);
		expect(imports[0]).toMatch(/^import type \{/);
	});

	it('n’écrit aucune branche `default` silencieuse : l’exhaustivité est vérifiée par `never`', () => {
		expect(source).not.toMatch(/\bdefault:/);
		expect(source).toMatch(/attributionDvfInattendue/);
	});

	it('expose une assertion d’exhaustivité qui échoue bruyamment sur un statut inconnu', () => {
		expect(() => attributionDvfInattendue({ status: 'sixieme_etat' } as never)).toThrow(
			/attribution DVF/i
		);
	});

	it('produit un modèle de vue dont TOUTES les clés sont décidées (aucune indéfinie)', () => {
		const etats: DvfAttribution[] = [
			EXACTE,
			COMPATIBLE,
			{ status: 'non_attribuable', reason: 'derniere_vente_mixte' },
			{ status: 'absente', reason: 'aucune_mutation' },
			{ status: 'error', reason: 'temporary_error' }
		];
		for (const attribution of etats) {
			const v: DvfAttributionView = deriveDvfAttributionView(attribution);
			for (const [cle, valeur] of Object.entries(v)) {
				expect(valeur, `clé ${cle} indéfinie`).not.toBeUndefined();
			}
		}
	});

	it('le libellé du KPI ne revendique plus aucune attribution (D-37)', () => {
		for (const attribution of [COMPATIBLE, { status: 'absente', reason: 'aucune_mutation' }]) {
			const v = deriveDvfAttributionView(attribution as DvfAttribution);
			expect(v.kpiLabel).toBe(COPIE_DVF.kpiLabel);
			expect(v.kpiLabel).not.toMatch(/dernière vente|vente de ce bien|prix du bien/i);
		}
	});

	it('aucune copie ne laisse fuir un identifiant technique ni un code d’état', () => {
		const copies = [
			COPIE_DVF.kpiLabel,
			...Object.values(COPIE_DVF.etats).flatMap((e) => [e.mention, e.detail]),
			...Object.values(COPIE_DVF.raisons).flatMap((r) => [r.mention, r.detail]),
			...Object.values(COPIE_DVF.classes),
			COPIE_DVF.suffixeVefa,
			...Object.values(COPIE_DVF.lignesBien),
			...Object.values(COPIE_DVF.lignesCommune)
		];
		for (const copie of copies) {
			expect(copie, copie).not.toMatch(/_|out_of_coverage|not_reported|temporary_error|\bL[012]\b/);
			// D-01 (honnêteté publique) : aucun verdict normatif dans une copie d'état.
			expect(copie, copie).not.toMatch(/\bFavorable\b|\bConforme\b|\bVigilance\b/);
		}
	});
});
