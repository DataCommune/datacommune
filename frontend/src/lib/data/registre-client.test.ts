import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import registre from './fiche-fields.v1.json';
import { registreAllege, registreAllegePlugin } from './registre-client';

const complet = registre as unknown as { fields: Record<string, unknown>[] } & Record<string, unknown>;

describe('registre allégé du build', () => {
	const allege = registreAllege(complet) as typeof complet;

	it('garde chaque champ, dans le même ordre, avec tout ce que lisent les formateurs', () => {
		expect(allege.fields.map((f) => f.field_id)).toEqual(complet.fields.map((f) => f.field_id));
		allege.fields.forEach((champ, i) => {
			const { description: _d, ...attendu } = complet.fields[i];
			expect(champ).toEqual(attendu);
		});
		const { description: _d, fields: _f, ...entete } = complet;
		const { fields: _g, ...enteteAllege } = allege;
		expect(enteteAllege).toEqual(entete);
	});

	it('ne garde aucune description', () => {
		expect(JSON.stringify(allege)).not.toContain('"description"');
		expect(JSON.stringify(allege).length).toBeLessThan(JSON.stringify(complet).length * 0.7);
	});

	it('est branché sur le build : le plugin rend le registre allégé, au build seulement', () => {
		const plugin = registreAllegePlugin as unknown as {
			apply: string;
			enforce: string;
			transform: (code: string, id: string) => { code: string } | null;
		};
		expect(plugin.apply).toBe('build');
		expect(plugin.enforce).toBe('pre');
		const brut = readFileSync('src/lib/data/fiche-fields.v1.json', 'utf8');
		const sortie = plugin.transform(brut, '/x/src/lib/data/fiche-fields.v1.json');
		expect(JSON.parse(sortie!.code)).toEqual(registreAllege(JSON.parse(brut)));
		expect(plugin.transform('{}', '/x/src/lib/data/autre.json')).toBeNull();
		const config = readFileSync('vite.config.ts', 'utf8');
		expect(config).toMatch(/plugins:\s*\[[^\]]*registreAllegePlugin/);
	});
});
