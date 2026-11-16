// Words of the codes served in the details of an address theme that had none (raw labels such as
// « Documents dpe », « Exception copropriete », seen online on 2026-11-11).
//
// Validated on 2026-11-14: Ianis left the choice to the recommendations of the validation page (drafts of
// 2026-11-12 kept where they read well, rewritten where they used jargon or an unexplained acronym).
// Entries marked « hypothèse » rest on a reading of the source that was not checked.
export const LIBELLES_DETAILS: Readonly<Record<string, string>> = {
	// Bâti et énergie
	documents_dpe: 'Nombre de DPE',
	exception_copropriete: 'Exception en copropriété (travaux votés non réalisés)',
	interdictions_location: 'Calendrier des interdictions de louer',
	bail_signe_renouvele_ou_reconduit: 'Bail signé, renouvelé ou reconduit après la date',
	a_partir_du: 'À partir du',
	en_vigueur: 'En vigueur',
	aucun_document: 'Aucun DPE à cette adresse',
	etapes: 'Classes interdites, par date',
	portee: 'Baux concernés',
	unite: 'Décompte',
	batiments_parcelle: 'Bâtiments sur la parcelle',
	batiments_parcelle_hors_annexe: 'Bâtiments sur la parcelle, hors annexes',
	batiment_rnb_non_unique: 'Plusieurs bâtiments du registre à cette adresse',
	hauteur_etat: 'Hauteur',
	hauteur_motif: 'Pourquoi la hauteur manque',
	rnb: 'Registre national des bâtiments',
	annee_construction: 'Année de construction',
	batiment_groupe_id: 'Identifiant BDNB (base de données nationale des bâtiments)',
	// Loyers, encadrement, fiscalité
	code_insee: 'Code commune (Insee)',
	'1-2_pieces': '1 ou 2 pièces',
	'3+_pieces': '3 pièces et plus',
	id_zone: 'Zone de calcul de l’ANIL',
	r2_ajuste: 'Fiabilité du modèle (R², de 0 à 1)',
	type_prediction: 'Estimé à l’échelle',
	arrete: 'Arrêté préfectoral',
	arrete_precedent: 'Arrêté préfectoral précédent',
	decret: 'Décret',
	en_vigueur_du: 'Valable à partir du',
	en_vigueur_au: 'Valable jusqu’au',
	liste_verifiee_le: 'Liste des communes vérifiée le',
	loyers_reference: 'Loyers de référence',
	numero: 'Numéro',
	perimetre: 'Périmètre',
	url: 'Texte officiel',
	lue_sur_la_commune: 'Chiffre de la ville entière',
	taxe_logements_vacants: 'Taxe sur les logements vacants',
	categorie: 'Motif du classement',
	moyenne_departement: 'Moyenne du département',
	nom_departement: 'Département',
	departement: 'Numéro du département',
	moyenne_simple: 'Moyenne des communes, sans pondération',
	ecart_points: 'Écart de la commune (points)',
	majoration_thrs: 'Majoration de la taxe d’habitation (résidences secondaires)',
	majoree: 'Majorée',
	seule_commune_du_departement: 'Seule commune du département, pas de comparaison',
	non_concerne: 'Sans objet',
	valeur_communale: 'Chiffre de la ville entière',
	annee: 'Année',
	periode: 'Période',
	// Terrain et risques
	altitude_m: 'Altitude',
	publication_jo: 'Publication au Journal officiel',
	debut: 'Début',
	activite: 'État du site',
	cavites: 'Cavités',
	echelle: 'Échelle de la carte',
	prescriptions_etat: 'État des prescriptions',
	adresse_concordante: 'Adresse retrouvée par Géorisques',
	atlas_zones_inondables: 'Atlas des zones inondables',
	ecart_adresse_georisques_m: 'Distance au point de Géorisques',
	// Also the name of a source in the partial-outage line: the label must read out of its group.
	statut_adresse: 'Statut à l’adresse',
	statut_commune: 'Statut dans la commune',
	territoires_risque_important: 'Territoires à risque important d’inondation',
	tri_parcelle: 'Territoire à risque important sur la parcelle',
	present: 'Présent',
	risque_cotier: 'Risque côtier',
	feu_de_foret_commune: 'Risque de feu de forêt dans la commune',
	feu_de_foret_present: 'Risque de feu de forêt à l’adresse',
	// Quartier
	derniere: 'Dernière vente',
	categories_non_couvertes: 'Types de transport non couverts',
	frequence_pointe: 'Fréquence aux heures de pointe',
	plus_proche_connue: 'Liste des gares incomplète',
	arrets: 'Arrêts',
	sante_social: 'Santé et social',
	services_particuliers: 'Services aux particuliers',
	sports_loisirs_culture: 'Sports, loisirs et culture',
	zones_peb: 'Zones du plan d’exposition au bruit',
	zones_pgs: 'Zones du plan de gêne sonore',
	informations_plu: 'Informations lues au PLU (plan local d’urbanisme)',
	nombre_de_voies: 'Nombre de voies',
	electrifiee: 'Électrifiée',
	taux_commune: 'Taux de la commune',
	nombre_commune: 'Faits dans la commune',
	taux_departement: 'Taux du département',
	libelle: 'Niveau',
	aasqa: 'Observatoire régional de l’air (AASQA)',
	sous_indices: 'Indice par polluant',
	moyennes_annuelles: 'Moyennes annuelles',
	no2: 'Dioxyde d’azote (NO₂)',
	o3: 'Ozone (O₃)',
	pm10: 'Particules (PM10)',
	pm25: 'Particules fines (PM2,5)',
	so2: 'Dioxyde de soufre (SO₂)',
	filosofi_carreau: 'Carreau de 200 m (revenus Insee)',
	id: 'Identifiant',
	iris: 'Quartier IRIS (Insee)',
	millesime: 'Année des données',
	menages: 'Ménages',
	niveau_vie_moyen: 'Niveau de vie moyen',
	part_menages_pauvres: 'Part des ménages pauvres',
	nb_menages_fiscaux: 'Ménages fiscaux',
	population_15_ans_plus: 'Population de 15 ans ou plus',
	revenu_d1: '10 % gagnent moins de',
	revenu_d9: '10 % gagnent plus de',
	revenu_q1: '25 % gagnent moins de',
	revenu_q3: '25 % gagnent plus de',
	// PCS groups of the census make-up of an IRIS (hypothèse for 9: the census code of the people
	// without a professional activity other than the retired).
	'1': 'Agriculteurs exploitants',
	'2': 'Artisans, commerçants, chefs d’entreprise',
	'3': 'Cadres et professions intellectuelles supérieures',
	'4': 'Professions intermédiaires',
	'5': 'Employés',
	'6': 'Ouvriers',
	'7': 'Retraités',
	'9': 'Autres personnes sans activité professionnelle',
	college: 'Collège',
	college_de_secteur: 'Collège de secteur',
	elementaire: 'École élémentaire',
	lycee: 'Lycée',
	uai: 'Numéro d’établissement (UAI)',
	ips: 'Indice de position sociale (IPS)',
	ips_rentree: 'Année scolaire de l’indice',
	ips_national_public: 'IPS moyen du public en France',
	taux_reussite: 'Taux de réussite',
	valeur_ajoutee: 'Valeur ajoutée (écart au taux attendu)',
	parmi_les_plus_proches: 'Aussi parmi les collèges les plus proches',
	annee_reseaux: 'Année des réseaux',
	reseaux: 'Réseaux de distribution',
	reseaux_complets: 'Liste des réseaux complète',
	prelevements: 'Prélèvements',
	dernier_prelevement: 'Dernier prélèvement',
	dernier_conforme: 'Dernier prélèvement conforme',
	dernier_controle_conclu: 'Dernier contrôle conclu',
	dates_non_conformes: 'Dates non conformes',
	non_conformes: 'Prélèvements non conformes',
	sans_conclusion: 'Prélèvements sans conclusion',
	taux_conformite: 'Taux de conformité',
	lecture_complete: 'Tous les prélèvements lus',
	bacteriologique: 'Contrôle bactériologique (microbes)',
	physico_chimique: 'Contrôle physico-chimique',
	durete: 'Dureté',
	durete_f: 'Dureté (degrés français, °f)',
	durete_date: 'Date de la dureté',
	fenetre: 'Période des ventes'
};

/**
 * Single-word codes whose capitalised form already reads as a French word (« Commune », « Zone »):
 * the details show them as is, the guard lets them through. A code with an accent, an acronym or
 * several words never belongs here.
 */
export const MOTS_LISIBLES: ReadonlySet<string> = new Set([
	'absences',
	'appartement',
	'axes',
	'base',
	'classe',
	'code',
	'commerces',
	'commune',
	'communes',
	'composition',
	'derniers',
	'documents',
	'enseignement',
	'exposition',
	'fin',
	'formation',
	'gare',
	'groupes',
	'identifiant',
	'importance',
	'indice',
	'indicateur',
	'indicateurs',
	'individus',
	'lignes',
	'liste',
	'maison',
	'mention',
	'mouvements',
	'nature',
	'niveau',
	'nom',
	'nombre',
	'parts',
	'prescriptions',
	'reclassement',
	'registres',
	'risque',
	'risques',
	'secteurs',
	'seuil',
	'session',
	'sites',
	'source',
	'statut',
	'supports',
	'taux',
	'territoire',
	'tourisme',
	'transports',
	'type',
	'types',
	'voies',
	'zone',
	'zones'
]);

/**
 * One-word values served by the source that the details write in words (validated 2026-11-14): a code
 * without its accent, or a technical state. Theme and key where each one was read (real details of
 * 2026-11-12) in comment.
 */
export const VALEURS_LIBELLEES: Readonly<Record<string, string>> = {
	agglomeration: 'agglomération de plus de 50 000 habitants', // zone_tendue · categorie
	arret: 'arrêt', // transports · nature
	maille: 'groupe de communes', // loyers · type_prediction
	ok: 'disponible', // dpe, fiscalite, revenus, transports · etat
	value: 'disponible' // batiment · hauteur_etat, batiment · etat, urbanisme · prescriptions_etat
};

/**
 * One-word values served as is because they already read as French (validated 2026-11-14,
 * « Seuil : bas »). The guard of `libelles-details.test.ts` fails on any other one-word value served
 * raw, and on a value of this list that is no longer served raw.
 */
export const VALEURS_LISIBLES: ReadonlySet<string> = new Set([
	'arrondissement', // delinquance · niveau
	'bas', // seveso · seuil
	'commune', // delinquance · niveau, encadrement_loyers · perimetre, loyers · type_prediction, qualite_air · type
	'douce', // eau_potable · durete
	'dure', // eau_potable · durete
	'moyen' // qualite_air · libelle
]);
