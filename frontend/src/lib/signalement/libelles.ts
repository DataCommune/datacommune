// À VALIDER — every visible wording of the error report, in one place.
//
// These are PROPOSALS (framing of 2026-11-10, § 8, T1 to T16), not the owner's words. None of them is
// shown while the report is switched off, and `ouverture.test.ts` refuses to switch it on while
// LIBELLES_VALIDES is false. When Ianis has given his words: write them here, then set the constant.
// « adresse email » follows the spelling of /confidentialite.
export const LIBELLES_VALIDES = false;

export const ADRESSE_SIGNALEMENT = 'signalement@datacommune.fr';

export const LIBELLES = {
	/** T1: footer (unchanged). */
	pied: 'Signaler une erreur',
	/** T2: bottom of the information panel. */
	volet: 'Signaler cette valeur',
	/** T3: title of the page and of the tab. */
	titre: 'Signaler une erreur',
	onglet: 'Signaler une erreur — DataCommune',
	/** T4: opening sentence. */
	ouverture: 'Une valeur fausse, une source périmée, un affichage cassé : écrivez-le ici.',
	/** T5: message field. */
	commentaire: 'Ce qui ne va pas',
	aideCommentaire: '2 000 caractères au plus. N’y mettez ni nom ni numéro de téléphone.',
	/** T6: e-mail field. */
	email: 'Votre adresse email (facultatif)',
	aideEmail: 'Seulement si vous voulez une réponse.',
	/** T7: list of the context joined. */
	joint: 'Joint à votre message',
	termes: {
		page: 'Page',
		valeur: 'Valeur',
		source: 'Source',
		version: 'Version du site',
		largeur: 'Largeur de l’écran',
		navigateur: 'Navigateur'
	},
	/** T8: button. */
	envoyer: 'Envoyer',
	/** T9: under the button; the last word links to /confidentialite. */
	conservation: 'Conservé six mois, lu par l’éditeur seul. Détail : ',
	lienConfidentialite: 'Confidentialité',
	/** T10: the other way. */
	autreVoie: 'Vous préférez écrire ? ',
	/** T11: confirmation. */
	recu: 'Signalement reçu',
	merci: 'Merci.',
	reference: 'Référence : ',
	retour: 'Revenir à la page',
	/** T12 to T14: refusals of a field. */
	erreurs: {
		court: 'Décrivez l’erreur en quelques mots.',
		long: 'Le message dépasse 2 000 caractères.',
		invalide: 'Cette adresse email n’est pas valide.'
	},
	/** T15, T16: failures; each ends with the address to write to. */
	pannes: {
		debit: 'Trop de signalements envoyés. Réessayez plus tard ou écrivez à ',
		envoi: 'L’envoi n’a pas abouti. Réessayez ou écrivez à '
	}
} as const;
