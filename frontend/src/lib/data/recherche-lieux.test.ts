import { describe, expect, it } from 'vitest';
import type { CommuneSearchResult } from '$lib/api/communes';
import type {
	CorrespondanceNumeroRecherche,
	HebergementBanRecherche,
	ResultatRechercheAdresse
} from '$lib/api/adresses';
import {
	prefixesProches,
	variantesOrthographe,
	annonceResultats,
	composerOptions,
	EXEMPLES_ACCUEIL,
	libelleGroupeAdresses,
	libelleDepartement,
	libelleVoie,
	messageCorrespondanceNumero,
	saisieVoie,
	titreAdressesHorsCouverture
} from './recherche-lieux';

describe('EXEMPLES_ACCUEIL — generic entries, no count and no place (04.8-07, D-34)', () => {
	it('lists the four entries of the plan, each towards a page of the site', () => {
		expect(EXEMPLES_ACCUEIL.map((exemple) => [exemple.label, exemple.href])).toEqual([
			['Parcourir l’annuaire des communes', '/communes'],
			['Parcourir les départements', '/departements'],
			['Comparer des communes', '/comparer'],
			['Comment lire une fiche', '/datasets#lire']
		]);
	});

	it('writes no figure, no INSEE code, no BAN identifier and no place name', () => {
		for (const exemple of EXEMPLES_ACCUEIL) {
			const texte = Object.values(exemple).join(' ');
			expect(texte, texte).not.toMatch(/\d/);
			expect(texte, texte).not.toMatch(/Nantes|Roche-sur-Yon|Jaurès|Loire-Atlantique|Vendée/);
		}
	});
});

// UI-SPEC « Champ unique » (D-105..D-107): at most 8 options in the served order — the head type
// up to 5, the other up to 3, the remainder filled by the type that has more; never a sort inside
// a type. Announcements agree in number; a street is put back in the field without a number.

function commune(code: string): CommuneSearchResult {
	return {
		code_insee: code,
		nom: `Commune ${code}`,
		code_postal: null,
		departement: code.slice(0, 2),
		population: null,
		population_annee: null,
		population_source: null,
		hebergement: { etat: 'heberge', motif: null, departements: [] }
	};
}

function adresse(numero: string): ResultatRechercheAdresse {
	return {
		type_resultat: 'adresse',
		ban_id: `85191_0420_${numero.padStart(5, '0')}`,
		numero,
		rep: null,
		nom_voie: 'Rue des Lilas',
		code_postal: '85000',
		code_commune: '85191',
		nom_commune: 'La Roche-sur-Yon',
		departement: { code: '85', nom: 'Vendée' }
	};
}

const communes = (n: number) => Array.from({ length: n }, (_, i) => commune(`35${String(100 + i)}`));
const adresses = (n: number) => Array.from({ length: n }, (_, i) => adresse(String(i + 1)));

/** « a5 c3 »: the kinds of the composed options, run-length encoded. */
function forme(options: ReturnType<typeof composerOptions>): string {
	const runs: string[] = [];
	for (const option of options) {
		const lettre = option.kind === 'adresse' ? 'a' : 'c';
		const dernier = runs.at(-1);
		if (dernier?.startsWith(lettre)) runs[runs.length - 1] = `${lettre}${Number(dernier.slice(1)) + 1}`;
		else runs.push(`${lettre}1`);
	}
	return runs.join(' ');
}

describe('composerOptions — served order, 5 / 3, remainder', () => {
	it('« 12 rue des Lilas » (addresses first): up to 5 addresses, then up to 3 communes', () => {
		expect(forme(composerOptions(communes(4), adresses(6), ['adresse', 'commune']))).toBe('a5 c3');
	});

	it('« Nantes » (communes first): up to 5 communes, then up to 3 addresses', () => {
		expect(forme(composerOptions(communes(6), adresses(6), ['commune', 'adresse']))).toBe('c5 a3');
	});

	it('the remainder is filled by the type that has more, never beyond 8', () => {
		expect(forme(composerOptions(communes(10), adresses(2), ['adresse', 'commune']))).toBe('a2 c6');
		expect(forme(composerOptions(communes(1), adresses(7), ['adresse', 'commune']))).toBe('a7 c1');
		expect(forme(composerOptions(communes(2), adresses(10), ['commune', 'adresse']))).toBe('c2 a6');
		expect(composerOptions(communes(20), adresses(20), ['adresse', 'commune'])).toHaveLength(8);
	});

	it('keeps the served order inside each type: nothing is sorted in the browser', () => {
		const servies = [adresse('9'), adresse('2'), adresse('14')];
		const options = composerOptions([], servies, ['adresse', 'commune']);

		expect(options.map((option) => option.result)).toEqual(servies);
	});

	it('one type only (the other failed or was not asked): its results in order, at most 8', () => {
		expect(forme(composerOptions(communes(10), [], ['commune', 'adresse']))).toBe('c8');
		expect(forme(composerOptions([], adresses(3), ['commune', 'adresse']))).toBe('a3');
	});
});

describe('annonceResultats — announcements agree in number', () => {
	it.each([
		[1, 3, '1 commune et 3 adresses proposées'],
		[0, 2, '2 adresses proposées'],
		[1, 1, '1 commune et 1 adresse proposées'],
		[1, 0, '1 commune proposée'],
		[0, 1, '1 adresse proposée'],
		[3, 0, '3 communes proposées']
	])('%i commune(s), %i adresse(s): « %s »', (nCommunes, nAdresses, attendu) => {
		expect(annonceResultats(nCommunes, nAdresses, 'q')).toBe(attendu);
	});

	// 04.7-09 (review Codex 4): an arrondissement is never announced as a commune.
	it.each([
		[1, 0, 1, '1 arrondissement proposé'],
		[2, 0, 1, '1 commune et 1 arrondissement proposés'],
		[3, 1, 2, '1 commune et 2 arrondissements et 1 adresse proposés'],
	])('%i commune(s) dont %i arrondissement(s)', (nCommunes, nAdresses, nArr, attendu) => {
		expect(annonceResultats(nCommunes, nAdresses, 'q', [], nArr)).toBe(attendu);
	});

	it('names streets from the served result types, including a mixed list', () => {
		const voie = { ...adresse('1'), type_resultat: 'voie' as const, ban_id: null, numero: null };
		expect(annonceResultats(0, 1, 'q', [voie])).toBe('1 voie proposée');
		expect(annonceResultats(0, 2, 'q', [adresse('1'), voie])).toBe('1 adresse et 1 voie proposées');
		expect(libelleGroupeAdresses([voie])).toBe('Voies');
		expect(libelleGroupeAdresses([adresse('1'), voie])).toBe('Adresses et voies');
	});

	it('nothing proposed: the contract sentence, never silent', () => {
		expect(annonceResultats(0, 0, 'zzz')).toBe('Aucune commune ni adresse proposée pour « zzz »');
	});

	it('a type not searched (or failed) is not counted as a zero', () => {
		expect(annonceResultats(2, null, 'q')).toBe('2 communes proposées');
		expect(annonceResultats(0, null, 'zzz')).toBe('Aucune commune proposée pour « zzz »');
		expect(annonceResultats(null, 0, 'zzz')).toBe('Aucune adresse proposée pour « zzz »');
	});
});

describe('messageCorrespondanceNumero — served scope only', () => {
	const proches: CorrespondanceNumeroRecherche = {
		etat: 'proches',
		numero_demande: 12,
		suffixe_demande: null,
		portee: 'resultats_retournes'
	};

	it('uses the served number and suffix without claiming anything outside the returned list', () => {
		expect(messageCorrespondanceNumero(proches)).toBe(
			"Le numéro 12 n'apparaît pas dans ces résultats. Voici les résultats proches renvoyés."
		);
		expect(messageCorrespondanceNumero({ ...proches, suffixe_demande: 'bis' })).toBe(
			"Le numéro 12 bis n'apparaît pas dans ces résultats. Voici les résultats proches renvoyés."
		);
	});

	it.each([null, { ...proches, etat: 'exacte' as const }, { ...proches, etat: 'aucune_dans_resultats' as const }])(
		'has no message for null, exact or empty qualifications',
		(qualification) => {
			expect(messageCorrespondanceNumero(qualification)).toBeNull();
		}
	);
});

describe('labels of an address result', () => {
	it('« {numéro}{indice} {voie} », as the API words its label', () => {
		expect(libelleVoie('12', null, 'Rue des Lilas')).toBe('12 Rue des Lilas');
		expect(libelleVoie('3', 'bis', 'Allée des Pins')).toBe('3bis Allée des Pins');
		expect(libelleVoie(null, null, 'Rue de la Paix')).toBe('Rue de la Paix');
	});

	it('« Département (code) » with a no-break space; the code alone when no name is served', () => {
		expect(libelleDepartement({ code: '85', nom: 'Vendée' })).toBe('Vendée (85)');
		expect(libelleDepartement({ code: '35', nom: null })).toBe('Département 35');
	});

	it('a street is put back in the field as « {voie}, {CP} {Commune} » (D-107)', () => {
		const voie: ResultatRechercheAdresse = {
			...adresse('1'),
			type_resultat: 'voie',
			ban_id: null,
			numero: null,
			nom_voie: 'Rue de la Paix'
		};
		expect(saisieVoie(voie)).toBe('Rue de la Paix, 85000 La Roche-sur-Yon');
	});
});

describe('titreAdressesHorsCouverture — the territory served, never guessed (D-105)', () => {
	const base: HebergementBanRecherche = {
		etat: 'hors_couverture',
		motif: 'territoire_hors_perimetre',
		departements: []
	};

	it('a commune typed: « {Commune} ({dép.}) : adresses hors couverture »', () => {
		const ban = { ...base, territoire: { code_insee: '35238', nom: 'Rennes', departement: { code: '35', nom: 'Ille-et-Vilaine' } } };
		expect(titreAdressesHorsCouverture(ban)).toBe('Rennes (35) : adresses hors couverture');
	});

	it('a postal code names its departement only: the served departement, never a guessed commune', () => {
		const ban = { ...base, territoire: { code_insee: null, nom: null, departement: { code: '35', nom: 'Ille-et-Vilaine' } } };
		expect(titreAdressesHorsCouverture(ban)).toBe('Ille-et-Vilaine (35) : adresses hors couverture');
	});

	it('homonyms all out of coverage are all named', () => {
		const ban = {
			...base,
			territoire: null,
			homonymes: [
				{ code_insee: '79211', nom: 'Le Pin', departement: { code: '79', nom: 'Deux-Sèvres' } },
				{ code_insee: '38307', nom: 'Le Pin', departement: { code: '38', nom: 'Isère' } }
			]
		};
		expect(titreAdressesHorsCouverture(ban)).toBe(
			'Le Pin (79), Le Pin (38) : adresses hors couverture'
		);
	});

	it('no territory served: the state alone', () => {
		expect(titreAdressesHorsCouverture(base)).toBe('Adresses hors couverture');
	});
});

describe('variantesOrthographe — une autre orthographe proposée sur une recherche vide (04.7-14)', () => {
	it('développe St et Ste en Saint et Sainte, en gardant la casse', () => {
		expect(variantesOrthographe('St Herblain')).toEqual(['Saint Herblain']);
		expect(variantesOrthographe('ste luce sur loire')).toEqual(['sainte luce sur loire']);
		expect(variantesOrthographe('St-Nazaire')).toEqual(['Saint-Nazaire']);
	});

	it('remet l’élision et l’apostrophe droite que le référentiel porte', () => {
		expect(variantesOrthographe('L ile d Olonne')).toEqual(["L'ile d'Olonne"]);
		expect(variantesOrthographe('L’Île-d’Olonne')).toEqual(["L'Île-d'Olonne"]);
	});

	it('ne propose rien quand rien ne change, ni pour un code postal', () => {
		expect(variantesOrthographe('Nantes')).toEqual([]);
		expect(variantesOrthographe('44000')).toEqual([]);
		expect(variantesOrthographe('Stade')).toEqual([]);
	});
});

describe('prefixesProches — communes au nom proche, seulement pour un nom sans chiffre', () => {
	it('raccourcit de deux lettres, puis de moitié, jamais sous trois lettres', () => {
		expect(prefixesProches('Nantesss')).toEqual(['Nantes', 'Nant']);
		expect(prefixesProches('Rezze')).toEqual(['Rez']);
		expect(prefixesProches('Nnt')).toEqual([]);
	});

	it('ne cherche pas de commune proche pour une adresse ou un code postal', () => {
		expect(prefixesProches('12 rue des Lilas')).toEqual([]);
		expect(prefixesProches('44000')).toEqual([]);
	});
});
