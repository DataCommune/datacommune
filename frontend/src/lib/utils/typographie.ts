// French spacing of the served sentences (04.8-03, D-37; UI-SPEC v4 § 7.5). The texts the API writes in
// its themes carry ordinary spaces (`api/backend/app/rapport/themes/altitude.py:50`); this pure function
// gives them, at render time, the rule of `typographie-francaise.test.ts`: U+00A0 before « : », U+202F
// before « ; », « ! », « ? » and inside « », U+00A0 between a number and its unit. It only replaces a
// space that is already there, so a URL, a time, an identifier or a code stays as it is, and it is
// idempotent. It is applied to served sentences only (title, verdict, attachment sentence and alert),
// never to a value, a badge, a table cell or an attribute.

const NBSP = ' ';
const NNBSP = ' ';

// Units that follow a number in the served sentences; « m² » before « m ».
const UNITES = ['m²', 'km', 'm', '€', '%', 'mois', 'ans', 'habitants', 'ventes', 'diagnostics', 'logements', 'documents'];
const NOMBRE_UNITE = new RegExp(`(\\d)[ \\u202f](${UNITES.join('|')})(?![\\p{L}\\p{N}])`, 'gu');

export function typographieFr(texte: string): string {
	return texte
		.replace(/[  ]:/g, `${NBSP}:`)
		.replace(/[  ]([;!?])/g, `${NNBSP}$1`)
		.replace(/«[  ]/g, `«${NNBSP}`)
		.replace(/[  ]»/g, `${NNBSP}»`)
		.replace(NOMBRE_UNITE, `$1${NBSP}$2`);
}
