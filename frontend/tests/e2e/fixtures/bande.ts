import type { Page } from '@playwright/test';

/**
 * Text actually painted in each cell of the comparator's header band (03-18, D-15 CR-01), in
 * column order. `innerText` returns the whole name even when the ellipsis cuts it: here a
 * character counts only when its box lies inside the cell and inside its own clipping span, minus
 * the room the ellipsis takes when that span overflows; a cut span gets « … ». Two columns the
 * reader cannot tell apart in the band give the same string.
 */
export function textesVisiblesDeLaBande(page: Page): Promise<string[]> {
	return page.evaluate(() => {
		const contexte = document.createElement('canvas').getContext('2d')!;
		return [...document.querySelectorAll<HTMLElement>('.compare-bande-cellule')].map((cellule) => {
			const boite = cellule.getBoundingClientRect();
			let visible = '';
			const marcheur = document.createTreeWalker(cellule, NodeFilter.SHOW_TEXT);
			for (let noeud = marcheur.nextNode() as Text | null; noeud; noeud = marcheur.nextNode() as Text | null) {
				const span = noeud.parentElement!;
				const style = getComputedStyle(span);
				const coupe = style.textOverflow === 'ellipsis' && span.scrollWidth > span.clientWidth;
				contexte.font = style.font;
				const cadre = span.getBoundingClientRect();
				const gauche = Math.max(boite.left, cadre.left) - 0.5;
				const droite = Math.min(boite.right, cadre.right - (coupe ? contexte.measureText('…').width : 0)) + 0.5;
				const texte = noeud.textContent ?? '';
				for (let i = 0; i < texte.length; i++) {
					const range = document.createRange();
					range.setStart(noeud, i);
					range.setEnd(noeud, i + 1);
					const r = range.getBoundingClientRect();
					if (r.left >= gauche && r.right <= droite) visible += texte[i];
				}
				if (coupe) visible += '…';
			}
			return visible.replace(/\s+/g, ' ').trim();
		});
	});
}
