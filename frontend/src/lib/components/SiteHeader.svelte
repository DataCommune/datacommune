<script lang="ts">
	// Single site header (D-50, UI-SPEC « En-tête et pied »): the same component for the
	// public pages and the connected space, in two forms.
	//
	// Public form (`publique`, 04.8-02, UI-SPEC v4 § 3.11), worn by the layouts that open
	// `.public-site`: brand to `/`, the compact search, then « Comparer » · « Données & méthode » ·
	// « Développeurs » and the account zone. Paper background, not sticky (A-01), one row when it fits
	// and `flex-wrap` otherwise. Up to 720px (D-41) two rows: the brand and a « Menu » disclosure, then
	// the search alone; the links and the account fold behind « Menu », a native `<details>` that opens
	// without JavaScript, its panel being the group that follows it. Three rows (181 px) broke H-01,
	// UI-012 and B6, which stay. The « Rechercher » link is gone: the field replaces it (A-03).
	// One rule for the search (4.9 lot 7, C8.5): a page has one search of the site. It is the large field of
	// the content when the search is the action of the page (`/`, `/communes`, the root error page, which
	// says so with `recherche`), else the field of the header.
	//
	// Connected form (the default, routes (app)): the form of 03-17, kept as it was. Brand, three
	// main links (« Rechercher » to `/`), the search from 1200 px, then « Développeurs » (from 1000px)
	// and the account zone; one line from 721 to 999px, two lines up to 720px (brand and account,
	// then the three links in a fixed three-column grid) with no burger menu.
	//
	// The counter and the width never change the height of the header (03-17 H-01, H-02): up to
	// 720px each link owns a third of the line and « Comparer » keeps its counter on its own line.
	//
	// The account zone reads the global auth store (initialised by the root layout). During
	// SSR, prerender and `auth.init()` it keeps a reserved width so nothing jumps; a session
	// that cannot be read ends as anonymous (« Se connecter »), never as signed in. The
	// « Administration » link is a display convenience only: authorisation stays in the API
	// and in the (app) layout guard.
	//
	// Ctrl+K (⌘K) focuses a search field from anywhere (04.7-14, Origami R17): the header field when
	// displayed, else the search of the page, else the one of `/` after opening it.
	//
	// « Comparer » carries the number of objects set aside in this tab (1 to 4, accent), read
	// from the comparison selection; no counter at 0. The count is shown once mounted: the
	// server cannot read the tab storage, and the server HTML must match the first client render.
	// 04-10 (UI-SPEC Q3): the objects of the nature modified last (communes, addresses or DPE
	// documents), the communes before any modification; one counter, the width of the header kept.
	import { onMount } from 'svelte';
	import { afterNavigate, goto } from '$app/navigation';
	import { page } from '$app/stores';
	import RechercheEnTete, { ID_RECHERCHE_EN_TETE } from '$lib/components/RechercheEnTete.svelte';
	import { ACCOUNTS_ENABLED, BILLING_ENABLED } from '$lib/config';
	import { auth } from '$lib/stores/auth.svelte';
	import { derniereNature, selectionDerniereNature } from '$lib/stores/comparison-selection.svelte';

	type MainLink = { href: string; label: string; prefixes: string[] };

	let { publique = false, recherche = true }: { publique?: boolean; recherche?: boolean } = $props();

	const LIENS_CONNECTES: MainLink[] = [
		{ href: '/', label: 'Rechercher', prefixes: ['/communes'] },
		{ href: '/comparer', label: 'Comparer', prefixes: ['/comparer'] },
		{ href: '/datasets', label: 'Données & méthode', prefixes: ['/datasets'] }
	];
	const LIENS_PUBLICS: MainLink[] = [
		{ href: '/comparer', label: 'Comparer', prefixes: ['/comparer'] },
		{ href: '/datasets', label: 'Données & méthode', prefixes: ['/datasets'] },
		{ href: '/developpeurs', label: 'Développeurs', prefixes: ['/developpeurs'] }
	];

	const MENU_ID = 'site-account-menu';

	let menuOpen = $state(false);
	let menuButton: HTMLButtonElement | undefined = $state(undefined);
	let mounted = $state(false);
	/** Disclosure of the public links and account up to 1120 px (D-41). Its `open` is the element's own,
	 *  never bound: a menu opened before hydration stays open once the page is hydrated. */
	let menuEtroit: HTMLDetailsElement | undefined = $state(undefined);
	let menuEtroitBouton: HTMLElement | undefined = $state(undefined);
	let panneauEtroit: HTMLElement | undefined = $state(undefined);

	onMount(() => {
		mounted = true;
	});

	// The header outlives a navigation: the next page opens with the menu folded.
	afterNavigate(({ type }) => {
		if (type !== 'enter' && menuEtroit) menuEtroit.open = false;
	});

	const comparisonCount = $derived(mounted ? selectionDerniereNature().count : 0);
	/** Spoken name of the counted objects, by nature (singular, plural). */
	const OBJETS_COMPTES = {
		commune: ['commune sélectionnée', 'communes sélectionnées'],
		adresse: ['adresse sélectionnée', 'adresses sélectionnées'],
		dpe: ['document DPE sélectionné', 'documents DPE sélectionnés']
	} as const;
	const objetsComptes = $derived(OBJETS_COMPTES[derniereNature.nature ?? 'commune']);

	function isCurrent(link: MainLink, pathname: string): boolean {
		if (pathname === link.href) return true;
		return link.prefixes.some((prefix) => pathname === prefix || pathname.startsWith(prefix + '/'));
	}

	/** Pages that carry their own search field: the header one would double it. */
	const PAGES_AVEC_RECHERCHE = ['/', '/communes'];
	const avecRecherche = $derived(recherche && !PAGES_AVEC_RECHERCHE.includes($page.url.pathname));

	/** The search field to focus: the header one when displayed, else the main search of the page. */
	function champDeRecherche(): HTMLInputElement | null {
		const enTete = document.getElementById(ID_RECHERCHE_EN_TETE) as HTMLInputElement | null;
		if (enTete && enTete.offsetParent !== null) return enTete;
		return document.querySelector<HTMLInputElement>('main [role="search"] input:not([type="hidden"])');
	}

	async function raccourciRecherche(event: KeyboardEvent) {
		if (event.key.toLowerCase() !== 'k' || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
		if (event.defaultPrevented || event.isComposing) return;
		// A field being filled keeps its own Ctrl+K: only a search field gives it up to the shortcut.
		const editable = (event.target as Element | null)?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"])');
		if (editable && !editable.closest('[role="search"]')) return;
		event.preventDefault();
		let champ = champDeRecherche();
		if (!champ) {
			await goto('/');
			champ = champDeRecherche();
		}
		champ?.focus();
		champ?.select();
	}

	function closeMenu() {
		menuOpen = false;
	}

	async function handleLogout() {
		closeMenu();
		await auth.logout();
		goto('/login');
	}

	function handleWindowClick(event: MouseEvent) {
		const target = event.target as HTMLElement | null;
		if (menuOpen && !target?.closest('[data-account-menu]')) closeMenu();
	}

	function handleMenuKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && menuOpen) {
			event.preventDefault();
			closeMenu();
			menuButton?.focus();
		}
	}

	/** Escape folds the narrow menu when the focus is on it, in its panel or in the header search, which
	 *  opens between them (audit C-2), and gives the focus back. A search field with text keeps its own
	 *  Escape: the browser clears a `type="search"` field, and only the next Escape folds the menu. */
	function fermerMenuEtroit(event: KeyboardEvent) {
		if (event.key !== 'Escape' || !menuEtroit?.open || event.defaultPrevented) return;
		const focus = document.activeElement;
		if (!focus) return;
		const recherche = focus.closest('.site-header [role="search"]');
		if (!(menuEtroit.contains(focus) || panneauEtroit?.contains(focus) || recherche)) return;
		if (recherche && focus instanceof HTMLInputElement && focus.value !== '') return;
		event.preventDefault();
		menuEtroit.open = false;
		menuEtroitBouton?.focus();
	}

	function surTouche(event: KeyboardEvent) {
		fermerMenuEtroit(event);
		void raccourciRecherche(event);
	}
</script>

<svelte:window onclick={handleWindowClick} onkeydown={surTouche} />

{#snippet navigation(liens: MainLink[])}
	<nav class="site-nav" aria-label="Navigation principale">
		{#each liens as link (link.href)}
			<a
				href={link.href}
				class="site-nav-link"
				class:site-nav-link-compteur={link.href === '/comparer'}
				aria-current={isCurrent(link, $page.url.pathname) ? 'page' : undefined}
			>
				{link.label}
				{#if link.href === '/comparer' && comparisonCount > 0}
					<span class="site-nav-count" aria-hidden="true">{comparisonCount}</span>
					<span class="site-visually-hidden"
						>({comparisonCount}
						{comparisonCount === 1 ? objetsComptes[0] : objetsComptes[1]})</span
					>
				{/if}
			</a>
		{/each}
	</nav>
{/snippet}

{#snippet compte()}
	<!-- Build-time switch: with accounts off (VITE_ACCOUNTS_ENABLED=false) there is no account zone. -->
	{#if ACCOUNTS_ENABLED}
		<div class="site-account" aria-busy={auth.loading ? 'true' : undefined}>
			{#if auth.loading}
				<span class="site-account-placeholder" aria-hidden="true"></span>
			{:else if auth.isAuthenticated}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="site-menu" data-account-menu onkeydown={handleMenuKeydown}>
					<button
						bind:this={menuButton}
						type="button"
						class="site-account-button"
						aria-expanded={menuOpen}
						aria-controls={MENU_ID}
						onclick={() => (menuOpen = !menuOpen)}
					>
						Mon espace
					</button>
					<ul id={MENU_ID} class="site-menu-list" hidden={!menuOpen}>
						<li><a href="/settings" onclick={closeMenu}>Réglages</a></li>
						{#if BILLING_ENABLED}
							<li><a href="/billing" onclick={closeMenu}>Facturation</a></li>
						{/if}
						{#if auth.isAdmin}
							<li><a href="/admin" onclick={closeMenu}>Administration</a></li>
						{/if}
						<li><button type="button" onclick={handleLogout}>Se déconnecter</button></li>
					</ul>
				</div>
			{:else}
				<a href="/login" class="site-account-button">Se connecter</a>
			{/if}
		</div>
	{/if}
{/snippet}

<!-- print:hidden: site chrome, not document content (the fiche <header> keeps its h1). -->
<header class="site-header print:hidden">
	<div class="site-header-inner">
		<a href="/" class="site-brand">
			<span class="site-brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
			<span class="site-brand-label">DataCommune</span>
		</a>

		{#if publique}
			<!-- Shown up to 1120 px only (D-41), on the row of the brand. Its panel is the group of the links
			     and the account, a later sibling shown by CSS while it is open, so it opens without
			     JavaScript; it opens under the search, so the order of the keyboard is the order on screen
			     (brand, « Menu », search, then the panel). The counter of « Comparer » stays visible on it. -->
			<details class="site-menu-etroit" bind:this={menuEtroit}>
				<summary class="site-menu-etroit-bouton" bind:this={menuEtroitBouton}
					>Menu{#if comparisonCount > 0}<span class="site-menu-count" aria-hidden="true">{comparisonCount}</span>
						<span class="site-visually-hidden"
							>({comparisonCount}
							{comparisonCount === 1 ? objetsComptes[0] : objetsComptes[1]})</span
						>{/if}</summary
				>
			</details>
			{#if avecRecherche}<RechercheEnTete publique />{/if}
			<div class="site-end" bind:this={panneauEtroit}>
				{@render navigation(LIENS_PUBLICS)}
				{@render compte()}
			</div>
		{:else}
			{@render navigation(LIENS_CONNECTES)}
			{#if avecRecherche}<RechercheEnTete />{/if}
			<div class="site-end">
				<a href="/developpeurs" class="site-dev-link">Développeurs</a>
				{@render compte()}
			</div>
		{/if}
	</div>
</header>

<style>
	/* Connected form (the default). z-index 40: above the page, below the drawers and modals of the
	   connected space (z-40 and later in the DOM, z-50), which must cover the header to be closable.
	   Its values are those of 03-17, written with the tokens of :root (D-27). */
	.site-header {
		position: sticky;
		top: 0;
		z-index: 40;
		background: var(--color-paper);
		border-bottom: 1px solid var(--color-line);
		color: var(--color-ink);
	}

	.site-header-inner {
		max-width: 1280px;
		margin: 0 auto;
		padding: 0 var(--s6);
		min-height: 72px;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		column-gap: var(--s5);
	}

	.site-brand {
		display: inline-flex;
		align-items: center;
		gap: var(--s3);
		min-height: 44px;
		font-size: 20px;
		font-weight: 600;
		line-height: 1.2;
		color: var(--color-ink);
		text-decoration: none;
		white-space: nowrap;
	}

	.site-brand-mark {
		display: grid;
		grid-template-columns: repeat(2, 8px);
		gap: var(--s1);
	}

	.site-brand-mark i {
		width: 8px;
		height: 8px;
		background: var(--color-accent);
	}

	.site-brand-mark i:last-child {
		background: var(--color-line-strong);
	}

	.site-nav {
		display: flex;
		align-self: stretch;
		gap: var(--s1);
	}

	.site-nav-link {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 44px;
		min-width: 44px;
		padding: 0 var(--s3);
		border-bottom: 2px solid transparent;
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.2;
		text-align: center;
		text-decoration: none;
	}

	.site-nav-link:hover {
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	.site-nav-link[aria-current='page'] {
		border-bottom-color: var(--color-ink);
		font-weight: 600;
	}

	/* 20 px square for the one digit it shows (1 to 4): 50 % draws the same disc as a 10 px radius. */
	.site-nav-count,
	.site-menu-count {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 20px;
		height: 20px;
		margin-left: var(--s2);
		padding: 0 var(--s1);
		border-radius: 50%;
		background: var(--color-accent);
		color: var(--color-paper);
		font-size: 14px;
		font-weight: 600;
		line-height: 1;
		font-variant-numeric: tabular-nums;
	}

	.site-visually-hidden {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}

	.site-end {
		margin-left: auto;
		display: flex;
		align-items: center;
		gap: var(--s4);
	}

	.site-dev-link {
		display: none;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		font-size: 16px;
		text-decoration: none;
		white-space: nowrap;
	}

	.site-dev-link:hover {
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	/* Reserved width: the account zone keeps its room while the session is read. */
	.site-account {
		min-width: 128px;
		display: flex;
		justify-content: flex-end;
	}

	.site-account-placeholder {
		display: block;
		width: 128px;
		height: 44px;
	}

	.site-menu {
		position: relative;
	}

	.site-account-button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-height: 44px;
		padding: 0 var(--s4);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.2;
		text-decoration: none;
		white-space: nowrap;
		cursor: pointer;
	}

	.site-account-button:hover {
		background: var(--color-soft);
	}

	.site-menu-list {
		position: absolute;
		top: calc(100% + 4px);
		right: 0;
		z-index: 60;
		min-width: 208px;
		margin: 0;
		padding: var(--s1) 0;
		list-style: none;
		background: var(--color-paper);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-tuile);
	}

	.site-menu-list[hidden] {
		display: none;
	}

	.site-menu-list :is(a, button) {
		display: flex;
		align-items: center;
		width: 100%;
		min-height: 44px;
		padding: 0 var(--s4);
		border: 0;
		background: transparent;
		color: var(--color-ink);
		font-family: inherit;
		font-weight: inherit;
		font-style: inherit;
		line-height: inherit;
		font-size: 16px;
		text-align: left;
		text-decoration: none;
		cursor: pointer;
	}

	.site-menu-list :is(a, button):hover {
		background: var(--color-soft);
	}

	@media (min-width: 1000px) {
		.site-dev-link {
			display: inline-flex;
		}
	}

	@media (max-width: 999px) {
		.site-header-inner {
			padding: 0 var(--s4);
		}
	}

	/* One line from 721 to 999px (H-02), counter of « Comparer » included (03-17 G3): tighter
	   gaps, the reserved account width is kept so nothing jumps while the session is read.
	   Measured at 721px, anonymous: brand 176, links of 4px padding 328, account 128 and gaps 32
	   made 664 of 673px available, and the counter (8 + 20px) pushed the account zone to a
	   second line up to 739px. Gaps of 8px, a brand mark 8px from its word and a counter 4px
	   from its label give back 24px.
	   04.8-01 (14 px floor, links and account at 16px): brand 171.8, links 371.6, account 134.6
	   and gaps 16 made 694 of 673px with the counter. Side padding 16px (689 available at 721px),
	   gaps of 4px between brand, links and account, and no gap between the links: 678 of 689. */
	@media (min-width: 721px) and (max-width: 999px) {
		.site-header-inner {
			column-gap: var(--s1);
		}

		.site-nav {
			gap: 0;
		}

		.site-brand {
			gap: var(--s2);
		}

		.site-nav-link {
			padding: 0 var(--s1);
		}

		.site-nav-count {
			margin-left: var(--s1);
		}

		.site-end {
			gap: var(--s2);
		}
	}

	/* Two lines up to 720px: brand and account, then the three links (44px targets). */
	@media (max-width: 720px) {
		.site-header-inner {
			min-height: 0;
			padding: var(--s2) var(--s4) 0;
			row-gap: var(--s1);
			column-gap: var(--s2);
		}

		.site-brand {
			order: 1;
		}

		.site-end {
			order: 2;
		}

		/* Fixed grid (H-01): the counter of « Comparer » cannot push a link to a new line. */
		.site-nav {
			order: 3;
			display: grid;
			grid-template-columns: repeat(3, minmax(0, 1fr));
			width: 100%;
			gap: 0;
		}

		/* « Données & méthode » may take two lines inside its own cell (UI-SPEC backstop). */
		.site-nav-link {
			min-height: 44px;
			padding: 0 var(--s1);
			hyphens: manual;
			overflow-wrap: normal;
		}

		.site-nav-link-compteur {
			white-space: nowrap;
		}
	}

	/* 320px at 200 % zoom leaves 160 CSS px: the brand keeps its mark and its accessible
	   name, the word is visually hidden so nothing overflows. */
	@media (max-width: 239px) {
		.site-header-inner {
			padding: var(--s2) var(--s2) 0;
		}

		.site-brand-label {
			position: absolute;
			width: 1px;
			height: 1px;
			overflow: hidden;
			clip: rect(0 0 0 0);
			white-space: nowrap;
		}

		.site-account,
		.site-account-placeholder {
			min-width: 0;
			width: auto;
		}

		/* 04.8-01 (14 px floor): at 16px « méthode » ended at 162 of 160 px (horizontal scroll at a
		   real 200 % zoom of a 320 px window) and « Se connecter » (118 px) pushed the account zone
		   under the brand mark. At 14px the links end inside the window and the account zone keeps
		   the brand line. */
		.site-account-button {
			padding: 0 var(--s2);
			font-size: 14px;
		}

		.site-nav-link {
			font-size: 14px;
		}
	}

	/* Public form (UI-SPEC v4 § 3.11, style.css:50-62 of the mockup), only under .public-site: paper,
	   line, 1240 px inside, padding 12 and 24 px, gaps 12 and 24 px, not sticky (A-01). One row at every
	   width above 720 px: the links and the account fold under « Menu » up to 1120 px. */
	:global(.public-site) .site-header {
		position: static;
		background: var(--color-paper);
	}

	:global(.public-site) .site-header-inner {
		max-width: var(--largeur-page);
		min-height: 0;
		padding: var(--s3) var(--s5);
		gap: var(--s3) var(--s5);
	}

	:global(.public-site) .site-brand {
		gap: var(--s2);
		font-size: 18px;
		line-height: 1;
	}

	:global(.public-site) .site-end {
		gap: var(--s5);
	}

	:global(.public-site) .site-nav {
		align-self: auto;
		flex-wrap: wrap;
		gap: var(--s2) var(--s5);
	}

	:global(.public-site) .site-nav-link {
		padding: 0;
	}

	/* The narrow menu exists up to 1120 px only. */
	:global(.public-site) .site-menu-etroit {
		display: none;
	}

	/* Up to 1120 px (4.9, harmonisation) the links and the account are the panel of « Menu », in the flow
	   under the row while it is open (order 4), so the brand, « Menu » and the search hold one row: the
	   links on a second row, under an empty left half, was the defect. 1120 = 48 of padding, 158 of brand,
	   568 of links, account and counter, 48 of gaps, 298 of search (120 of button, 178 for its help text). */
	@media (max-width: 1120px) {
		:global(.public-site) .site-menu-etroit {
			display: block;
			margin-left: auto;
		}

		:global(.public-site) .site-end {
			display: none;
			order: 4;
			flex-basis: 100%;
			flex-direction: column;
			align-items: stretch;
			gap: var(--s2);
			margin-left: 0;
		}

		:global(.public-site) .site-menu-etroit[open] ~ .site-end {
			display: flex;
		}

		/* Order 0 undoes the order 3 of the connected form: the links, then the account. */
		:global(.public-site) .site-nav {
			order: 0;
			display: flex;
			flex-direction: column;
			width: 100%;
			gap: 0;
		}

		/* A wider text (letter spacing of WCAG 1.4.12, a fallback font) breaks inside its row instead of
		   overflowing the window. */
		:global(.public-site) .site-nav-link {
			justify-content: flex-start;
			padding: 0 var(--s2);
			font-size: 14px;
			text-align: left;
			overflow-wrap: anywhere;
		}

		:global(.public-site) .site-nav-link + .site-nav-link {
			border-top: 1px solid var(--color-filet-pale);
		}

		:global(.public-site) .site-account {
			justify-content: flex-start;
			min-width: 0;
		}

		/* The menu of the account is part of the panel, in its flow under its button: never wider than
		   the panel (208 px would leave the window at 160 px, a 200 % zoom of 320 px). */
		:global(.public-site) .site-menu-list {
			position: static;
			min-width: 0;
			margin-top: var(--s1);
		}

		:global(.public-site) .site-menu-list :is(a, button) {
			overflow-wrap: anywhere;
		}
	}

	/* Up to 720px (D-41): two rows of 44 px, the brand and « Menu », then the search alone
	   (RechercheEnTete, order 3). Three rows (brand and account, the links, the search) made 181 px and
	   broke H-01 (a fifth of the window), UI-012 (the first value of the comparator whole at 375 × 812,
	   805.3 of 812 px under the 101 px header before) and B6. Padding 4 px and a row gap of 8 px make
	   4 + 44 + 8 + 44 + 4 + 1 = 105 px; the 12 px of the mockup would make 125. The links and the
	   account are the panel of « Menu », in the flow under the search while it is open (order 4): the
	   order on screen is the order of the DOM and of the keyboard. */
	@media (max-width: 720px) {
		:global(.public-site) .site-header-inner {
			padding: var(--s1) var(--s4);
			gap: var(--s2) var(--s3);
		}

		:global(.public-site) .site-brand {
			order: 0;
		}

		:global(.public-site) .site-menu-etroit {
			order: 1;
		}
	}

	:global(.public-site) .site-menu-etroit-bouton {
		display: inline-flex;
		align-items: center;
		gap: var(--s2);
		min-height: 44px;
		padding: 0 var(--s3);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1;
		white-space: nowrap;
		list-style: none;
		cursor: pointer;
	}

	:global(.public-site) .site-menu-etroit-bouton::-webkit-details-marker {
		display: none;
	}

	/* Chevron drawn by CSS (as the detail of a criterion, UI-SPEC v4 § 3.3): down when folded, up when open. */
	:global(.public-site) .site-menu-etroit-bouton::after {
		content: '';
		width: 7px;
		height: 7px;
		border-right: 2px solid currentColor;
		border-bottom: 2px solid currentColor;
		transform: translateY(-2px) rotate(45deg);
	}

	:global(.public-site) .site-menu-etroit[open] > .site-menu-etroit-bouton::after {
		transform: translateY(2px) rotate(-135deg);
	}

	:global(.public-site) .site-menu-etroit-bouton:hover {
		background: var(--color-soft);
	}

	:global(.public-site) .site-menu-count {
		margin-left: 0;
	}

	/* 320px at 200 % zoom leaves 160 CSS px: the brand keeps its mark only (rule above), the inside its
	   4 px rows and 8 px sides. */
	@media (max-width: 239px) {
		:global(.public-site) .site-header-inner {
			padding: var(--s1) var(--s2);
		}
	}
</style>
