<script lang="ts" module>
	/** One entry of the summary, built by the page: a real anchor of the fiche, in the order of the page. */
	import type { IdPartie } from '$lib/data/questions';

	export interface EntreeSommaire {
		ancre: string;
		titre: string;
		/** A category of the fiche: the entry wears its tint and its mark. */
		partie?: IdPartie;
		/** 1: a category, 2: a row of the category before it. */
		niveau: 1 | 2;
		/** Reading aids after the categories: set apart from them. */
		fin?: boolean;
	}
</script>

<script lang="ts">
	// Summary of the address fiche (decision of 06/11): an overview in the margin, sticky above the two-column
	// threshold, one compact control under it. The entries are plain links: the browser goes to the anchor, with
	// or without JavaScript. JavaScript only says where the reader is: the last entry whose place has passed the
	// top of the window, a place the reader cannot see being nowhere. Every row of the fiche is an entry, so the
	// list outgrows the window: it keeps the current entry in the middle of its own height while the page goes down.
	import { onMount } from 'svelte';
	import IconeCategorie from '$lib/components/ds/IconeCategorie.svelte';

	interface Props {
		entrees: readonly EntreeSommaire[];
	}
	let { entrees }: Props = $props();

	/** Just under where an anchor lands, 96 px down: the row reached by a link is the current one. */
	const HAUT_DE_LECTURE = 100;

	let courante = $state<string | null>(null);
	// Opened on the server render before the hydration: kept, never closed by `bind:open` (R-06).
	let menuOuvert = $state(typeof document !== 'undefined' && Boolean(document.querySelector<HTMLDetailsElement>('nav.sommaire-compact > details')?.open));
	let lecturePrevue = false;
	let marge = $state<HTMLElement>();
	let menu = $state<HTMLElement>();

	const arbre = $derived.by(() => {
		const parties: { entree: EntreeSommaire; lieux: EntreeSommaire[] }[] = [];
		for (const entree of entrees) {
			if (entree.fin) continue;
			if (entree.niveau === 2 && parties.length > 0) parties[parties.length - 1].lieux.push(entree);
			else parties.push({ entree, lieux: [] });
		}
		return parties;
	});
	const fin = $derived(entrees.filter((e) => e.fin));
	const titreCourant = $derived(entrees.find((e) => e.ancre === courante)?.titre ?? null);

	function lire() {
		lecturePrevue = false;
		let trouvee: string | null = null;
		let derniere: string | null = null;
		for (const entree of entrees) {
			const place = document.getElementById(entree.ancre);
			// A closed fold keeps a measured box: being inside one is not being on the page.
			if (!place || place.parentElement?.closest('details:not([open])')) continue;
			const boite = place.getBoundingClientRect();
			if (boite.height === 0) continue;
			derniere = entree.ancre;
			if (boite.top <= HAUT_DE_LECTURE) trouvee = entree.ancre;
		}
		// The end of the page never reaches the top of the window.
		const auBout = window.scrollY > 0 && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 1;
		courante = auBout && trouvee ? derniere : trouvee;
	}

	function suivre() {
		if (lecturePrevue) return;
		lecturePrevue = true;
		requestAnimationFrame(lire);
	}

	// Within its own list only: `scrollIntoView` would move the page too, under the reader's eyes.
	function centrer(liste: HTMLElement | undefined) {
		if (!liste) return;
		const lien = liste.querySelector<HTMLElement>('[aria-current]');
		if (!lien) return liste.scrollTo?.({ top: 0 });
		const ecart = lien.getBoundingClientRect().top - liste.getBoundingClientRect().top;
		liste.scrollTo?.({ top: liste.scrollTop + ecart - (liste.clientHeight - lien.offsetHeight) / 2 });
	}

	$effect(() => {
		void courante;
		centrer(marge);
		if (menuOuvert) centrer(menu);
	});

	onMount(() => {
		lire();
		// A fold that opens moves the places without any scroll; `toggle` does not bubble.
		window.addEventListener('toggle', suivre, true);
		return () => window.removeEventListener('toggle', suivre, true);
	});
</script>

<svelte:window onscroll={suivre} onresize={suivre} />

{#snippet lien(entree: EntreeSommaire)}
	<a href={`#${entree.ancre}`} class={entree.partie ? `sommaire-partie teinte-${entree.partie}` : undefined} aria-current={entree.ancre === courante ? 'location' : undefined}
		>{#if entree.partie}<IconeCategorie partie={entree.partie} taille={18} />{/if}{entree.titre}</a
	>
{/snippet}

{#snippet liste()}
	<ol class="sommaire-parties">
		{#each arbre as { entree, lieux } (entree.ancre)}
			<li>
				{@render lien(entree)}
				{#if lieux.length}
					<ol class="sommaire-lieux">
						{#each lieux as lieu (lieu.ancre)}
							<li>{@render lien(lieu)}</li>
						{/each}
					</ol>
				{/if}
			</li>
		{/each}
	</ol>
	{#if fin.length}
		<ul class="sommaire-fin">
			{#each fin as entree (entree.ancre)}
				<li>{@render lien(entree)}</li>
			{/each}
		</ul>
	{/if}
{/snippet}

<nav class="sommaire sommaire-large" aria-label="Sur cette fiche" bind:this={marge}>
	<p class="sommaire-t">Sur cette fiche</p>
	{@render liste()}
</nav>

<nav class="sommaire sommaire-compact" aria-label="Sur cette fiche">
	<details bind:open={menuOuvert}>
		<summary><span class="sommaire-t" class:sr-only={titreCourant}>Sur cette fiche</span>{#if titreCourant}<span class="sommaire-courante">{titreCourant}</span>{/if}</summary>
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (a link reached by the keyboard fires this click too) -->
		<div class="sommaire-menu" bind:this={menu} onclick={(event) => (event.target as Element).closest('a') && (menuOuvert = false)}>
			{@render liste()}
		</div>
	</details>
</nav>

<style>
	.sommaire {
		font-size: 14px;
		line-height: 1.35;
	}

	.sommaire-t {
		margin: 0;
		color: var(--color-muted);
		font-weight: 600;
	}

	ol,
	ul {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* The whole row is the link. Where the reader is shows by the background and the weight, never by colour alone. */
	a {
		display: block;
		padding: var(--s1) var(--s2);
		border-radius: var(--radius-ligne);
		color: var(--color-ink);
		text-decoration: none;
	}

	/* The offset of the page's anchors, not here: this is on screen wherever the page stands. */
	.sommaire a,
	.sommaire summary {
		scroll-margin-top: 0;
	}

	a:hover {
		background: var(--color-soft);
		text-decoration: underline;
		text-underline-offset: 3px;
	}

	a[aria-current] {
		background: var(--color-soft);
		font-weight: 600;
	}

	.sommaire-parties {
		display: grid;
		gap: var(--s1);
	}

	.sommaire-parties > li > a {
		font-weight: 600;
	}

	/* A category: its tint and its mark. Hovered or current, a line of its ink closes the box. */
	.sommaire-partie {
		display: flex;
		align-items: center;
		gap: var(--s2);
		background: var(--c-fond);
		color: var(--c-encre);
	}

	.sommaire-partie:hover,
	.sommaire-partie[aria-current] {
		background: var(--c-fond);
		box-shadow: inset 0 0 0 1px var(--c-encre);
	}

	.sommaire-lieux {
		margin-left: var(--s2);
		padding-left: var(--s1);
		border-left: 1px solid var(--color-line);
	}

	.sommaire-lieux a {
		color: var(--color-muted);
	}

	.sommaire-lieux a[aria-current] {
		color: var(--color-ink);
	}

	.sommaire-fin {
		margin-top: var(--s2);
		padding-top: var(--s2);
		border-top: 1px solid var(--color-line);
	}

	/* Under the threshold: one control that stays in sight, its list laid over the page, which it never moves. */
	.sommaire-large {
		display: none;
	}

	.sommaire-compact {
		position: sticky;
		top: var(--s2);
		z-index: 5;
	}

	/* One line, whatever the row: a bar that grows would push the page it is following. */
	.sommaire-compact summary {
		display: flex;
		align-items: center;
		gap: var(--s2);
		box-sizing: border-box;
		min-height: 44px;
		padding: var(--s1) var(--s3);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		list-style: none;
		cursor: pointer;
	}

	/* The same chevron as « Autres actions ». */
	.sommaire-compact summary::after {
		content: '';
		flex: none;
		width: 7px;
		height: 7px;
		margin-left: auto;
		border-right: 1.5px solid currentColor;
		border-bottom: 1.5px solid currentColor;
		transform: rotate(45deg);
	}

	.sommaire-compact details[open] summary::after {
		transform: rotate(-135deg);
	}

	.sommaire-compact summary::-webkit-details-marker {
		display: none;
	}

	.sommaire-courante {
		min-width: 0;
		overflow: hidden;
		color: var(--color-ink);
		font-weight: 600;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.sommaire-menu {
		position: absolute;
		inset-inline: 0;
		top: 100%;
		box-sizing: border-box;
		max-height: 70dvh;
		margin-top: var(--s1);
		padding: var(--s2);
		overflow-y: auto;
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-panneau);
	}

	.sommaire-menu a {
		display: flex;
		align-items: center;
		min-height: 44px;
	}

	@media screen and (min-width: 1081px) {
		/* In the margin: the list scrolls by itself, and a wheel over it stops at its ends instead of moving the page. */
		.sommaire-large {
			position: sticky;
			top: var(--s4);
			display: block;
			box-sizing: border-box;
			max-height: calc(100dvh - 2 * var(--s4));
			overflow-y: auto;
			overscroll-behavior: contain;
			scrollbar-width: thin;
		}

		.sommaire-large .sommaire-t {
			padding: 0 var(--s2) var(--s2);
		}

		.sommaire-compact {
			display: none;
		}
	}

	@media (prefers-reduced-motion: no-preference) {
		.sommaire-large,
		.sommaire-menu {
			scroll-behavior: smooth;
		}
	}

	@media print {
		.sommaire {
			display: none;
		}
	}
</style>
