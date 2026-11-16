// The one serializer of JSON-LD (04.7-10, decision 10): a value read from the API (a commune name, a
// department name) reaches a `<script type="application/ld+json">` through `{@html}`, so nothing
// that can close the tag or end a JavaScript line may leave unescaped.
import { describe, expect, it } from 'vitest';
import { jsonLdScript, serializeJsonLd } from './json-ld';

const LINE_SEPARATOR = String.fromCharCode(0x2028);
const PARAGRAPH_SEPARATOR = String.fromCharCode(0x2029);

describe('serializeJsonLd', () => {
	it('escapes < > & U+2028 and U+2029 and parses back to the same value', () => {
		const objet = {
			'@context': 'https://schema.org',
			name: `</script><script>alert(1)</script> & <b>${LINE_SEPARATOR}x${PARAGRAPH_SEPARATOR}y`
		};
		const texte = serializeJsonLd(objet);
		for (const interdit of ['<', '>', '&', LINE_SEPARATOR, PARAGRAPH_SEPARATOR]) {
			expect(texte.includes(interdit), JSON.stringify(interdit)).toBe(false);
		}
		expect(texte).toContain('\\u003c/script\\u003e');
		expect(texte).toContain('\\u0026');
		expect(texte).toContain('\\u2028');
		expect(texte).toContain('\\u2029');
		expect(JSON.parse(texte)).toEqual(objet);
	});

	it('escapes the keys as well as the values', () => {
		const texte = serializeJsonLd({ '</script>': 1 });
		expect(texte).not.toContain('<');
		expect(JSON.parse(texte)).toEqual({ '</script>': 1 });
	});

	it('leaves a plain object as compact JSON, accents and quotes intact', () => {
		const objet = { name: 'Côtes-d’Armor', '@type': 'AdministrativeArea' };
		expect(serializeJsonLd(objet)).toBe(JSON.stringify(objet));
	});

	it('refuses a value that has no JSON form instead of printing « undefined »', () => {
		expect(() => serializeJsonLd(undefined)).toThrow(TypeError);
		expect(() => serializeJsonLd(() => 1)).toThrow(TypeError);
	});
});

describe('jsonLdScript', () => {
	it('wraps the escaped text in one ld+json script, closed only by its own end tag', () => {
		const html = jsonLdScript({ name: '</script><img src=x onerror=alert(1)>' });
		expect(html.startsWith('<script type="application/ld+json">')).toBe(true);
		expect(html.endsWith('</script>')).toBe(true);
		expect(html.match(/<\/script>/g)).toHaveLength(1);
		expect(html.match(/<script/g)).toHaveLength(1);
	});

	it('is read back by a DOM parser as one script whose text is the original JSON', () => {
		const objet = { name: `a</script><p id="intrus">b</p>&amp;${LINE_SEPARATOR}` };
		const gabarit = document.createElement('div');
		gabarit.innerHTML = jsonLdScript(objet);
		expect(gabarit.querySelectorAll('script')).toHaveLength(1);
		expect(gabarit.querySelector('#intrus')).toBeNull();
		expect(JSON.parse(gabarit.querySelector('script')!.textContent ?? '')).toEqual(objet);
	});
});
