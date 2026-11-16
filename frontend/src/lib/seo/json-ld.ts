// The one place where structured data becomes markup (04.7-10, decision 10). The pages inject the
// result with `{@html}` inside `<svelte:head>`, so the text must stay inert inside a `<script>`
// element whatever the data holds: a name read from the API may contain `</script>`.
//   - `<`, `>` and `&` would let HTML parsing leave the script or read an entity;
//   - the line and paragraph separators (U+2028, U+2029) are legal in JSON but end a line in JavaScript.
// Each is written as the unicode escape of JSON, which a JSON parser reads back as the same
// character, so the data is unchanged.

const LINE_SEPARATOR = String.fromCharCode(0x2028);
const PARAGRAPH_SEPARATOR = String.fromCharCode(0x2029);

const ESCAPES: ReadonlyMap<string, string> = new Map([
	['<', '\\u003c'],
	['>', '\\u003e'],
	['&', '\\u0026'],
	[LINE_SEPARATOR, '\\u2028'],
	[PARAGRAPH_SEPARATOR, '\\u2029']
]);

const TO_ESCAPE = new RegExp(`[${[...ESCAPES.keys()].join('')}]`, 'g');

/** JSON text of *data*, safe to place inside a `<script type="application/ld+json">` element. */
export function serializeJsonLd(data: unknown): string {
	const texte = JSON.stringify(data);
	if (texte === undefined) throw new TypeError('JSON-LD: the value has no JSON form');
	return texte.replace(TO_ESCAPE, (caractere) => ESCAPES.get(caractere) as string);
}

/** The complete `<script type="application/ld+json">` element for *data*, ready for `{@html}`. */
export function jsonLdScript(data: unknown): string {
	return `<script type="application/ld+json">${serializeJsonLd(data)}</script>`;
}
