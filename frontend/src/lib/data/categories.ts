// The four categories of the address fiche and the themes each one holds, moved out of `questions.ts`
// (2026-11-13): a leaf module, so the home page, which lists the categories, no longer loads the shared
// chunk of the address report through `questions.ts`. The doctrine of the categories is in `questions.ts`.

export type IdPartie = 'q-prix' | 'q-logement' | 'q-quotidien' | 'q-risques';

/** A group of rows, titled only where a long list needs it. */
export interface GroupeTable {
	titre: string | null;
	ids: readonly string[];
}

export interface PartieTable {
	id: IdPartie;
	titre: string;
	/** Rows of the category, each group in the order of the classement. */
	groupes: readonly GroupeTable[];
	/** The rest of the category, in the order of the classement, after its groups. */
	autres: readonly string[];
	/** The themes whose served headline announces the result on the closed row. */
	annonce: readonly string[];
	/** Entries of the summary under the category, in the order of the page. */
}

const sansTitre = (...ids: string[]): GroupeTable[] => [{ titre: null, ids }];

const GROUPES_QUOTIDIEN: readonly GroupeTable[] = [
	{ titre: 'Services et transports', ids: ['equipements', 'ecoles', 'transports'] },
	{ titre: 'Bruit', ids: ['grand_axe', 'secteur_bruit', 'voie_ferree'] },
	{ titre: 'Sécurité et air', ids: ['delinquance', 'qualite_air'] }
];

const GROUPES_RISQUES: readonly GroupeTable[] = [
	{ titre: 'Eau et sol', ids: ['inondation', 'argiles', 'mouvements_terrain'] },
	{ titre: 'Arrêtés et risques recensés', ids: ['catnat', 'seveso', 'risques_commune'] }
];

/**
 * The 43 themes of the registry. Ianis decided the four titles (the last two on 06/11, with the charter), the
 * noise in « Autour de l’adresse » and the property tax in « Prix et ventes »; the order of the categories and of each group is the classement's, a
 * group going by its best rank. `prix_commune`, `loyers` and `dpe` answer in the head: a closed row never
 * says them a second time.
 */
export const QUESTIONS: readonly PartieTable[] = [
	{
		id: 'q-prix',
		titre: 'Prix et ventes',
		groupes: sansTitre('prix_commune', 'loyers', 'ventes_parcelle', 'fiscalite'),
		autres: ['encadrement_loyers', 'zonage_abc', 'zone_tendue'],
		annonce: ['ventes_parcelle', 'fiscalite'],
	},
	{
		id: 'q-logement',
		titre: 'Logement et DPE',
		groupes: sansTitre('dpe', 'fibre', 'construction', 'copropriete', 'batiment'),
		autres: ['parcelle', 'urbanisme'],
		annonce: ['construction', 'batiment'],
	},
	{
		id: 'q-quotidien',
		titre: 'Autour de l’adresse',
		groupes: GROUPES_QUOTIDIEN,
		autres: ['peb', 'eau_potable', 'antennes_5g', 'revenus'],
		annonce: ['equipements', 'ecoles', 'transports'],
	},
	{
		id: 'q-risques',
		titre: 'Risques',
		groupes: GROUPES_RISQUES,
		autres: [
			'trait_de_cote',
			'sismicite',
			'altitude',
			'rupture_barrage',
			'radon',
			'pollution_sols',
			'nucleaire',
			'remontee_nappe',
			'icpe_commune',
			'debroussaillement',
			'geologie'
		],
		annonce: ['catnat', 'risques_commune'],
	}
];
