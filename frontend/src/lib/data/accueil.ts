// Content of the home page beyond the search (accueil 04.6-01). Pure module, no Svelte import.
import type { Pictogramme } from '$lib/components/ds/RepereEchelle.svelte';
import type { IdPartie } from '$lib/data/questions';

// The national landmarks are official figures quoted as published, never computed by DataCommune
// and independent of the territories this site serves: each one keeps its producer, publication,
// reference date, field and provisional status. They are checked by hand against the publication
// and updated when a new edition comes out (Insee Focus every September, ONRE every November).

export type RepereNational = {
	/** The figure as published, French typography. */
	valeur: string;
	/** What the figure counts, in one sentence. */
	libelle: string;
	/** Reference date, field and status of the estimate. */
	portee: string;
	/** Producer and publication, as the link text. */
	source: string;
	url: string;
};

export const REPERES_NATIONAUX: readonly RepereNational[] = [
	{
		valeur: '7,6 %',
		libelle: 'Logements vacants',
		portee: '2,9 millions sur un parc de 38,6 millions · Au 1er janvier 2026 · France hors Mayotte · estimation provisoire',
		source: 'Insee Focus n° 388, septembre 2026',
		url: 'https://www.insee.fr/fr/statistiques/9055697'
	},
	{
		valeur: '33,7 %',
		libelle: 'Résidences principales en étiquette D du DPE',
		portee: 'Contre 8,6 % pour les étiquettes A et B réunies · Au 1er janvier 2025 · France métropolitaine · estimation sur 30,9 millions de résidences principales',
		source: 'ONRE, SDES, novembre 2025',
		url: 'https://www.statistiques.developpement-durable.gouv.fr/media/8813/download?inline='
	},
	{
		valeur: 'Près de 40 %',
		libelle: 'Part de la baisse estimée des passoires énergétiques due à la réforme du DPE',
		portee: 'Entre 2024 et 2025, réforme du calcul pour les petites surfaces · Résidences principales classées F ou G · 1er janvier 2024 au 1er janvier 2025 · France métropolitaine',
		source: 'ONRE, SDES, novembre 2025',
		url: 'https://www.statistiques.developpement-durable.gouv.fr/media/8813/download?inline='
	}
] as const;

export const DATE_RELEVE_REPERES = '29 octobre 2026';

/**
 * The frozen examples of the home page: three real fiches of different profiles (a large city, a mid-sized
 * town, a village, in three regions), so the page shows the site is national (Ianis, 11/11). Their figures
 * are copied from the report served on the date below and checked on the live fiche. Each one says it is
 * frozen and dated, so it never reads as a count of what the site covers (D-34): the page fetches nothing
 * (D-38) and no build reaches the served data, so these lines are updated by hand, as the national
 * landmarks are. The first one is the example of the site: the greyed text of the search field and the
 * examples of `/developpeurs` follow it. All share one shape, guarded by `page.test.ts`: the reader goes
 * from one to the other without the page moving.
 */
export interface TuileExemple {
	partie: IdPartie;
	libelle: string;
	echelle: 'commune' | 'adresse';
	valeurs: readonly { valeur: string; qui: string }[];
	classe?: { lettre: 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'G'; sous: string; note: string };
}

export interface ExempleFiche {
	id: string;
	/** What kind of place it is, the first half of its button. */
	profil: string;
	ville: string;
	insee: string;
	adresse: string;
	href: string;
	commune: string;
	date: string;
	/** Around the field: the value, what it is, the mark of its scale. */
	faits: readonly { valeur: string; libelle: string; pictogramme: Pictogramme }[];
	tuiles: readonly TuileExemple[];
	/**
	 * One row per category, its result with the figures in bold (`**`) and the count the fiche shows at
	 * the head of that category (« à l’adresse ou autour »).
	 */
	lignes: readonly { partie: IdPartie; resume: string; vigilances: number }[];
}

const DATE_RELEVE_EXEMPLES = '11 novembre 2026';

export const EXEMPLES_FICHES: readonly ExempleFiche[] = [
	{
		id: 'grande-ville',
		profil: 'Grande ville',
		ville: 'Toulouse',
		insee: '31555',
		adresse: '8 rue de Metz, Toulouse',
		href: '/adresse/31555_5904_00008',
		commune: '/commune/31555',
		date: DATE_RELEVE_EXEMPLES,
		faits: [
			{ valeur: '3 250 €/m²', libelle: 'Prix médian, appartement. Commune', pictogramme: 'commune' },
			{ valeur: '14 €/m²', libelle: 'Loyer par mois, appartement. Commune', pictogramme: 'commune' },
			{ valeur: '1 174 commerces', libelle: 'À moins d’1 km', pictogramme: 'rayon' },
			{ valeur: 'Classe D', libelle: 'Diagnostic énergétique le plus fréquent. Adresse', pictogramme: 'adresse' },
			{ valeur: '7 ventes', libelle: 'Sur la parcelle, dernière en octobre 2024', pictogramme: 'parcelle' },
			{ valeur: '16 m', libelle: 'Jusqu’à l’arrêt le plus proche', pictogramme: 'rayon' }
		],
		tuiles: [
			{
				partie: 'q-prix',
				libelle: 'Prix au m²',
				echelle: 'commune',
				valeurs: [
					{ valeur: '3 250 €', qui: 'appartement' },
					{ valeur: '3 697 €', qui: 'maison' }
				]
			},
			{
				partie: 'q-prix',
				libelle: 'Loyer au m², par mois',
				echelle: 'commune',
				valeurs: [
					{ valeur: '14 €', qui: 'appartement' },
					{ valeur: '12,9 €', qui: 'maison' }
				]
			},
			{
				partie: 'q-logement',
				libelle: 'Diagnostic énergétique (DPE)',
				echelle: 'adresse',
				valeurs: [],
				classe: { lettre: 'D', sous: 'classe la plus fréquente', note: '9 DPE déclarés' }
			}
		],
		lignes: [
			{ partie: 'q-prix', resume: 'Loyer d’appartement de **11,17** à **17,51 €/m²/mois**, **zone tendue**', vigilances: 0 },
			{ partie: 'q-logement', resume: '**2 DPE sur 9** concernés par l’interdiction de louer dès 2034, bâtiment de **1850**, **5 étages**', vigilances: 2 },
			{ partie: 'q-quotidien', resume: 'Arrêt à **16 m**, école à **370 m**, **1 174 commerces** à moins d’1 km', vigilances: 1 },
			{ partie: 'q-risques', resume: '**Argiles** : exposition moyenne à l’adresse, **50 catastrophes** reconnues, dont 8 en 10 ans', vigilances: 2 }
		]
	},
	{
		id: 'ville-moyenne',
		profil: 'Ville moyenne',
		ville: 'Quimper',
		insee: '29232',
		adresse: '24 rue du Parc, Quimper',
		href: '/adresse/29232_4750_00024',
		commune: '/commune/29232',
		date: DATE_RELEVE_EXEMPLES,
		faits: [
			{ valeur: '2 143 €/m²', libelle: 'Prix médian, appartement. Commune', pictogramme: 'commune' },
			{ valeur: '12 €/m²', libelle: 'Loyer par mois, appartement. Commune', pictogramme: 'commune' },
			{ valeur: '263 commerces', libelle: 'À moins d’1 km', pictogramme: 'rayon' },
			{ valeur: 'Classe D', libelle: 'Diagnostic énergétique le plus fréquent. Adresse', pictogramme: 'adresse' },
			{ valeur: '6 ventes', libelle: 'Sur la parcelle, dernière en mars 2025', pictogramme: 'parcelle' },
			{ valeur: '49 m', libelle: 'Jusqu’à l’arrêt le plus proche', pictogramme: 'rayon' }
		],
		tuiles: [
			{
				partie: 'q-prix',
				libelle: 'Prix au m²',
				echelle: 'commune',
				valeurs: [
					{ valeur: '2 143 €', qui: 'appartement' },
					{ valeur: '2 273 €', qui: 'maison' }
				]
			},
			{
				partie: 'q-prix',
				libelle: 'Loyer au m², par mois',
				echelle: 'commune',
				valeurs: [
					{ valeur: '12 €', qui: 'appartement' },
					{ valeur: '11,1 €', qui: 'maison' }
				]
			},
			{
				partie: 'q-logement',
				libelle: 'Diagnostic énergétique (DPE)',
				echelle: 'adresse',
				valeurs: [],
				classe: { lettre: 'D', sous: 'classe la plus fréquente', note: '5 DPE déclarés' }
			}
		],
		lignes: [
			{ partie: 'q-prix', resume: 'Loyer d’appartement de **9,74** à **14,76 €/m²/mois**, **hors zone tendue**', vigilances: 0 },
			{ partie: 'q-logement', resume: '**Aucun DPE sur 5** concerné par l’interdiction de louer, bâtiment de **1850**, **6 étages**', vigilances: 2 },
			{ partie: 'q-quotidien', resume: 'Arrêt à **49 m**, école à **283 m**, **263 commerces** à moins d’1 km', vigilances: 1 },
			{ partie: 'q-risques', resume: '**Inondation** et **remontée de nappe** recensées à l’adresse, **20 catastrophes** reconnues, dont 5 en 10 ans', vigilances: 3 }
		]
	},
	{
		id: 'village',
		profil: 'Village',
		ville: 'Barfleur',
		insee: '50030',
		adresse: '42 rue Saint Thomas Becket, Barfleur',
		href: '/adresse/50030_bys9nl_00042',
		commune: '/commune/50030',
		date: DATE_RELEVE_EXEMPLES,
		faits: [
			{ valeur: '3 299 €/m²', libelle: 'Prix médian, appartement. Commune', pictogramme: 'commune' },
			{ valeur: '9,5 €/m²', libelle: 'Loyer par mois, appartement. Commune', pictogramme: 'commune' },
			{ valeur: '11 commerces', libelle: 'À moins d’1 km', pictogramme: 'rayon' },
			{ valeur: 'Classe D', libelle: 'Diagnostic énergétique le plus fréquent. Adresse', pictogramme: 'adresse' },
			{ valeur: '7 ventes', libelle: 'Sur la parcelle, dernière en octobre 2025', pictogramme: 'parcelle' },
			{ valeur: '91 m', libelle: 'Jusqu’à l’arrêt le plus proche', pictogramme: 'rayon' }
		],
		tuiles: [
			{
				partie: 'q-prix',
				libelle: 'Prix au m²',
				echelle: 'commune',
				valeurs: [
					{ valeur: '3 299 €', qui: 'appartement' },
					{ valeur: '3 029 €', qui: 'maison' }
				]
			},
			{
				partie: 'q-prix',
				libelle: 'Loyer au m², par mois',
				echelle: 'commune',
				valeurs: [
					{ valeur: '9,5 €', qui: 'appartement' },
					{ valeur: '9,9 €', qui: 'maison' }
				]
			},
			{
				partie: 'q-logement',
				libelle: 'Diagnostic énergétique (DPE)',
				echelle: 'adresse',
				valeurs: [],
				classe: { lettre: 'D', sous: 'classe la plus fréquente', note: '7 DPE déclarés' }
			}
		],
		lignes: [
			{ partie: 'q-prix', resume: 'Loyer d’appartement de **7,53** à **12,04 €/m²/mois**, estimé sur un territoire plus large', vigilances: 0 },
			{ partie: 'q-logement', resume: '**1 DPE sur 7** concerné par l’interdiction de louer dès 2034, bâtiment de **1968**, **4 étages**', vigilances: 0 },
			{ partie: 'q-quotidien', resume: 'Arrêt à **91 m**, école à **1,3 km**, **11 commerces** à moins d’1 km', vigilances: 1 },
			{ partie: 'q-risques', resume: '**Argiles** : exposition moyenne à l’adresse, **3 cavités** à moins de 500 m, **recul du trait de côte** dans la commune', vigilances: 2 }
		]
	}
];

/**
 * The greyed example of the search field: the first example as one types it, street then postal code.
 * The field leaves 180 px to its text at 375 px (measured on 11/11): « 8 rue de Metz, Toulouse » takes
 * 207 px in the font of the field, this form 176. `marketing-truth.spec.ts` measures it.
 */
export const EXEMPLE_SAISIE = '8 rue de Metz 31000';

export type Usage = { titre: string; texte: string; action: string; ancre: string };

/** What brings a reader here, each leading to the place of the shown example that answers it. */
export const USAGES: readonly Usage[] = [
	{
		titre: 'Vous achetez',
		texte: 'Le prix médian de la commune, les ventes de la parcelle, le DPE et ce qui touche l’adresse.',
		action: 'Voir les prix de cet exemple',
		ancre: '#critere-prix_commune'
	},
	{
		titre: 'Vous louez',
		texte: 'Le loyer estimé, l’encadrement, la zone tendue et les DPE concernés par l’interdiction de louer.',
		action: 'Voir le loyer de cet exemple',
		ancre: '#critere-loyers'
	},
	{
		titre: 'Vous rénovez',
		texte: 'Les DPE déclarés, le bâtiment, la copropriété et ce que dit le plan d’urbanisme.',
		action: 'Voir les DPE de cet exemple',
		ancre: '#critere-dpe'
	}
] as const;

/**
 * « Lire l’échelle » on the home page (04.8-07, UI-SPEC § 6.6): five scale marks of the table of the
 * served levels (`NIVEAUX`) and what each one covers, from the mockup without any example of territory.
 * The mockup’s 200 m square is not a served level: the IRIS quarter takes its pictogram (§ 3.5).
 */
export const ECHELLES_ACCUEIL = [
	{ niveau: 'adresse', phrase: 'Le point de l’adresse : argiles, altitude, zone du PLU, DPE déclarés à l’adresse.' },
	{ niveau: 'parcelle', phrase: 'La parcelle cadastrale : surface, bâtiments, sols pollués.' },
	{
		niveau: 'rayon',
		phrase: 'Un rayon autour de l’adresse, de 500 m à 10 km selon le critère : arrêts, voies ferrées, sites Seveso, écoles.'
	},
	{ niveau: 'iris', phrase: 'Le quartier statistique de l’Insee qui contient l’adresse.' },
	{
		niveau: 'commune',
		phrase: 'Toute la commune : prix médians, sismicité, délinquance. Ne dit pas si l’adresse est concernée.'
	}
] as const;
