// The error report cannot be opened on the public instance while what the site says is false or
// unvalidated (orchestrator, 2026-11-10). Opening it is a commit that sets the two literals of
// `api/docker-compose.public.yml`; this test reads that file, the wording module and the privacy
// page, and refuses the commit until the three agree.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { LIBELLES_VALIDES } from './libelles';
import { obstaclesOuverture } from './ouverture';

const RACINE = resolve(__dirname, '../../../..');
const lire = (chemin: string) => readFileSync(resolve(RACINE, chemin), 'utf8').replace(/\r\n/g, '\n');
const compose = lire('api/docker-compose.public.yml');
const confidentialite = lire('frontend/src/routes/(marketing)/confidentialite/+page.svelte');

function reglage(nom: string): string {
	const trouve = compose.match(new RegExp(`^\\s+${nom}: "([a-z]+)"\\s*$`, 'm'));
	expect(trouve, `${nom} must be a quoted literal of docker-compose.public.yml, never read from the .env of the server`).not.toBeNull();
	return trouve![1];
}

const FAUSSE = "il ne demande ni adresse email, ni mot de passe";
const A_JOUR = "Le site n'a pas de comptes. Signalement d'une erreur : le formulaire enregistre votre message.";

describe('opening the error report on the public instance', () => {
	it('is decided by two literals of the public compose file, both off today', () => {
		expect(reglage('VITE_SIGNALEMENT')).toBe('false');
		expect(reglage('SIGNALEMENT_ENABLED')).toBe('false');
	});

	it('the Dockerfile of the site builds it closed unless told otherwise', () => {
		expect(lire('frontend/Dockerfile')).toMatch(/^ARG VITE_SIGNALEMENT=false$/m);
	});

	it('holds as committed: whatever the compose file sets, nothing stands in the way', () => {
		expect(
			obstaclesOuverture({
				site: reglage('VITE_SIGNALEMENT'),
				api: reglage('SIGNALEMENT_ENABLED'),
				libellesValides: LIBELLES_VALIDES,
				confidentialite
			})
		).toEqual([]);
	});

	it('the privacy page still says today what a report with an e-mail address would make false', () => {
		// When this fails, the page was rewritten: check the rule below still reads the right sentence.
		expect(confidentialite.replace(/\s+/g, ' ')).toContain(FAUSSE);
		expect(LIBELLES_VALIDES).toBe(false);
	});

	// The rule itself, so that the guard is known to bite.
	it('closed, nothing is asked', () => {
		expect(obstaclesOuverture({ site: 'false', api: 'false', libellesValides: false, confidentialite: FAUSSE })).toEqual([]);
	});

	it.each([
		['the site alone', 'true', 'false'],
		['the API alone', 'false', 'true'],
		['both', 'true', 'true']
	])('opening %s is refused while the privacy page is false and the wordings unvalidated', (_nom, site, api) => {
		const obstacles = obstaclesOuverture({ site, api, libellesValides: false, confidentialite: `<p>Le site\n n'a pas de comptes : ${FAUSSE}.</p>` });

		expect(obstacles).toHaveLength(3);
		expect(obstacles.join(' ')).toMatch(/libelles/i);
		expect(obstacles.join(' ')).toMatch(/adresse email/);
		expect(obstacles.join(' ')).toMatch(/signalement/i);
	});

	it('each condition counts on its own', () => {
		expect(obstaclesOuverture({ site: 'true', api: 'true', libellesValides: true, confidentialite: FAUSSE })).toHaveLength(2);
		expect(obstaclesOuverture({ site: 'true', api: 'true', libellesValides: false, confidentialite: A_JOUR })).toHaveLength(1);
		expect(obstaclesOuverture({ site: 'true', api: 'true', libellesValides: true, confidentialite: "Le site n'a pas de comptes." })).toHaveLength(1);
	});

	it('opening is allowed once the wordings are validated and the privacy page describes the report', () => {
		expect(obstaclesOuverture({ site: 'true', api: 'true', libellesValides: true, confidentialite: A_JOUR })).toEqual([]);
	});
});
