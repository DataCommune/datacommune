// What stands in the way of opening the error report on the public instance (orchestrator,
// 2026-11-10): the wordings must be the owner's, and the privacy page must describe the report
// instead of denying it. Pure, so that `ouverture.test.ts` proves the rule bites.

export interface EtatOuverture {
	/** Literal of VITE_SIGNALEMENT in the public compose file. */
	site: string;
	/** Literal of SIGNALEMENT_ENABLED in the public compose file. */
	api: string;
	libellesValides: boolean;
	/** Source of the privacy page. */
	confidentialite: string;
}

// The sentence of § 2 a report with an e-mail address makes false.
const PHRASE_FAUSSE = /ne demande ni adresse email/i;

/** Reasons why the setting may not be on; empty when it is off or when everything agrees. */
export function obstaclesOuverture(etat: EtatOuverture): string[] {
	if (etat.site !== 'true' && etat.api !== 'true') return [];
	const texte = etat.confidentialite.replace(/\s+/g, ' ');
	const obstacles: string[] = [];
	if (!etat.libellesValides) {
		obstacles.push('LIBELLES_VALIDES is false: the wordings of $lib/signalement/libelles.ts are proposals, not the owner’s words');
	}
	if (PHRASE_FAUSSE.test(texte)) {
		obstacles.push('/confidentialite still says « il ne demande ni adresse email »: false once a report may carry one');
	}
	if (!/signalement/i.test(texte)) {
		obstacles.push('/confidentialite does not describe the signalement (data kept, retention, recipient)');
	}
	return obstacles;
}
