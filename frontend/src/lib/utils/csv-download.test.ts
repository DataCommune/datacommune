import { describe, it, expect, vi, beforeEach } from 'vitest';
import { downloadCsv } from './csv-download';

describe('downloadCsv()', () => {
	let clickedHref: string;
	let clickedDownload: string;
	let clicked: boolean;
	let capturedContent: string;

	beforeEach(() => {
		clickedHref = '';
		clickedDownload = '';
		clicked = false;
		capturedContent = '';

		vi.stubGlobal('URL', {
			createObjectURL: vi.fn(() => 'blob:test'),
			revokeObjectURL: vi.fn()
		});

		vi.stubGlobal(
			'Blob',
			class {
				constructor(parts: string[]) {
					capturedContent = parts.join('');
				}
			}
		);

		vi.spyOn(document, 'createElement').mockReturnValue({
			set href(v: string) {
				clickedHref = v;
			},
			set download(v: string) {
				clickedDownload = v;
			},
			click: vi.fn(() => {
				clicked = true;
			})
		} as unknown as HTMLAnchorElement);
	});

	it('prefixes the CSV with a UTF-8 BOM and triggers a download', () => {
		downloadCsv(['Indicateur', 'Valeur'], [['Loyer', '11,20 €/m²']], 'f.csv');

		expect(capturedContent.startsWith('﻿')).toBe(true);
		expect(clickedHref).toBe('blob:test');
		expect(clickedDownload).toBe('f.csv');
		expect(clicked).toBe(true);
	});

	it('quotes fields containing commas and keeps the others bare', () => {
		downloadCsv(['Indicateur', 'Valeur'], [['Loyer', '11,20 €/m²']], 'f.csv');

		const lines = capturedContent.replace('﻿', '').split('\n');
		expect(lines[0]).toBe('Indicateur,Valeur');
		expect(lines[1]).toBe('Loyer,"11,20 €/m²"');
	});

	it('doubles internal quotes and wraps the field in quotes', () => {
		downloadCsv(['Note'], [['Dit "bonjour"']], 'f.csv');

		const dataLine = capturedContent.replace('﻿', '').split('\n')[1];
		expect(dataLine).toBe('"Dit ""bonjour"""');
	});

	it('quotes fields containing newlines', () => {
		downloadCsv(['Note'], [['ligne1\nligne2']], 'f.csv');

		expect(capturedContent).toContain('"ligne1\nligne2"');
	});

	it('renders null and undefined cells as empty strings', () => {
		downloadCsv(['A', 'B', 'C'], [[null, undefined, 0]], 'f.csv');

		const dataLine = capturedContent.replace('﻿', '').split('\n')[1];
		expect(dataLine).toBe(',,0');
	});

	it('emits headers only when there are no rows', () => {
		downloadCsv(['A', 'B'], [], 'empty.csv');

		const lines = capturedContent.replace('﻿', '').split('\n');
		expect(lines).toHaveLength(1);
		expect(lines[0]).toBe('A,B');
		expect(clickedDownload).toBe('empty.csv');
	});

	// --- CSV / formula injection neutralization (D-12, T-10-07, OWASP CSV injection) ---

	it('neutralizes a leading "=" formula by prefixing an apostrophe', () => {
		downloadCsv(['Note'], [['=SUM(A1:A9)']], 'f.csv');

		const dataLine = capturedContent.replace('﻿', '').split('\n')[1];
		// Apostrophe prefix neutralizes the formula; no comma → no surrounding quotes.
		expect(dataLine).toBe("'=SUM(A1:A9)");
	});

	it('neutralizes the +, -, @ formula triggers as well', () => {
		downloadCsv(['A', 'B', 'C'], [['+1+1', '-2+3', '@cmd']], 'f.csv');

		const dataLine = capturedContent.replace('﻿', '').split('\n')[1];
		expect(dataLine).toBe("'+1+1,'-2+3,'@cmd");
	});

	it('neutralizes a formula AND still quotes when it contains a comma', () => {
		downloadCsv(['Note'], [['=HYPERLINK("a,b")']], 'f.csv');

		const dataLine = capturedContent.replace('﻿', '').split('\n')[1];
		// Prefixed first ('=...), then quoted because of the comma, inner quotes doubled.
		expect(dataLine).toBe(`"'=HYPERLINK(""a,b"")"`);
	});

	it('leaves normal values untouched (no parasitic prefix)', () => {
		downloadCsv(['Type', 'Surface', 'CP'], [['Maison', 75, '85047']], 'f.csv');

		const dataLine = capturedContent.replace('﻿', '').split('\n')[1];
		expect(dataLine).toBe('Maison,75,85047');
	});

	it('keeps the UTF-8 BOM in front even when a field is neutralized', () => {
		downloadCsv(['Note'], [['=BAD()']], 'f.csv');

		expect(capturedContent.startsWith('﻿')).toBe(true);
	});
});
