// Historical detail pages of the former dataset catalogue (`/datasets/{slug}`), kept as permanent
// redirects towards the card of « Données & méthode » (H4, D-40). Static table, read once from the
// removed frontend catalogue: the slug of an old link → the key of the canonical backend catalogue
// that `GET /v1/datasets` serves and that anchors the card (`/datasets#{key}`). Aliases follow the
// resolver map of the backend (`risques`/`pollutions` → `georisques`, `entreprises` → `sirene`,
// `transports` → its first source). The redirect target is built from this table only: the slug
// of the request is never reflected (T-03-11-01).
export const DATASET_SLUG_KEYS: Readonly<Record<string, string>> = Object.freeze({
	ban: 'ban',
	cadastre: 'cadastre',
	dvf: 'dvf',
	dpe: 'dpe',
	loyers: 'loyers',
	risques: 'georisques',
	pollutions: 'georisques',
	urbanisme: 'urbanisme',
	batiments: 'batiments',
	monuments: 'monuments',
	connectivite: 'connectivite',
	entreprises: 'sirene',
	scolaire: 'scolaire',
	transports: 'transports',
	bdnb: 'bdnb',
	errial: 'errial',
	rnb: 'rnb',
	'zonage-abc': 'zonage_abc',
	fiscalite: 'fiscalite',
	'encadrement-loyers': 'encadrement_loyers',
	revenus: 'revenus',
	demographie: 'demographie',
	copropriete: 'copropriete',
	permis: 'permis',
	antennes: 'antennes',
	equipements: 'equipements',
	vacance: 'vacance'
});

/** Catalogue key of a historical slug, or null when the slug never existed. */
export function cleDuSlug(slug: string): string | null {
	return Object.hasOwn(DATASET_SLUG_KEYS, slug) ? DATASET_SLUG_KEYS[slug] : null;
}
