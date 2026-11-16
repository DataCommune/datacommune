// Groups of the catalogue of « Données & méthode » (review of 2026-11-11, Ianis): the sources are read in the
// order of the address fiche — its four parts, titled as in `questions.ts`, then the territory — and no
// longer in the alphabetical order of a key the reader never sees. A presentation table, like the search
// aliases of the page: the API serves no theme. A served key absent from it is never hidden: it falls
// into « Autres sources », last.

export type IdGroupeCatalogue = 'prix' | 'logement' | 'quotidien' | 'risques' | 'territoire' | 'autres';

export interface GroupeCatalogue {
	id: IdGroupeCatalogue;
	titre: string;
	/** Catalogue keys, in reading order. */
	cles: readonly string[];
}

export const GROUPES_CATALOGUE: readonly GroupeCatalogue[] = [
	{
		id: 'prix',
		titre: 'Prix et ventes',
		cles: ['dvf', 'loyers', 'fiscalite', 'encadrement_communes', 'encadrement_loyers', 'zonage_abc', 'zone_tendue']
	},
	{
		id: 'logement',
		titre: 'Logement et DPE',
		cles: ['dpe', 'bdnb', 'rnb', 'batiments', 'cadastre', 'copropriete', 'connectivite', 'urbanisme', 'monuments', 'permis']
	},
	{
		id: 'quotidien',
		titre: 'Autour de l’adresse',
		cles: [
			'equipements',
			'scolaire',
			'annuaire_education',
			'transports',
			'gares',
			'axes_transport',
			'peb',
			'delinquance',
			'qualite_air',
			'eau_potable',
			'antennes',
			'sirene'
		]
	},
	{ id: 'risques', titre: 'Risques', cles: ['georisques', 'errial', 'altimetrie', 'geologie'] },
	{
		id: 'territoire',
		titre: 'Territoire et population',
		cles: ['ban', 'demographie', 'revenus', 'filosofi_carreaux', 'iris', 'vacance', 'foncier_pm']
	},
	{ id: 'autres', titre: 'Autres sources', cles: [] }
];

const GROUPE_PAR_CLE = new Map<string, IdGroupeCatalogue>(
	GROUPES_CATALOGUE.flatMap((groupe) => groupe.cles.map((cle) => [cle, groupe.id] as const))
);

/** Group of a served key; « autres » for a key the table does not know yet. */
export function groupeDuJeu(cle: string): IdGroupeCatalogue {
	return GROUPE_PAR_CLE.get(cle) ?? 'autres';
}

/**
 * The served datasets, group by group in reading order; inside a group, the order of the table, then the
 * unknown keys as served. A group without dataset is left out.
 */
export function grouperJeux<T extends { nom: string }>(jeux: readonly T[]): Array<{ groupe: GroupeCatalogue; jeux: T[] }> {
	return GROUPES_CATALOGUE.map((groupe) => {
		const rang = new Map(groupe.cles.map((cle, index) => [cle, index]));
		const siens = jeux.filter((jeu) => groupeDuJeu(jeu.nom) === groupe.id);
		// A stable sort: the unknown keys of « autres » keep the served order.
		siens.sort((a, b) => (rang.get(a.nom) ?? Number.MAX_SAFE_INTEGER) - (rang.get(b.nom) ?? Number.MAX_SAFE_INTEGER));
		return { groupe, jeux: siens };
	}).filter(({ jeux: siens }) => siens.length > 0);
}
