<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { afterNavigate } from '$app/navigation';
	import { page } from '$app/stores'; // Project convention: stores, never the state module.
	import { auth } from '$lib/stores/auth.svelte';
	import {
		ACCOUNTS_ENABLED,
		DEPLOYMENT_SHA,
		GOOGLE_SITE_VERIFICATION,
		IS_PREPRODUCTION,
		MESURE_AUDIENCE
	} from '$lib/config';
	import { canalProvenance, cheminMesure, cheminProvenance, compter } from '$lib/mesure-audience';

	onMount(() => {
		// No session to read when the account space is switched off: skip the /auth/me call.
		if (ACCOUNTS_ENABLED) auth.init();
	});

	// Audience measurement (04.7-14b): one count when a page loads and when its path changes,
	// never on a query or anchor change (a page of a list, a filter, a theme read). The path counted
	// comes from the matched route (`cheminMesure`), never from the URL.
	// Provenance by channel (2026-11-11): the entry page of a visit opened by a link carrying a known
	// `?src=<canal>` counts `/venu-de/<canal>` once more. The flag lives in memory only, for the life of
	// the document: nothing is written in the browser, so a reload of that link counts again.
	let pageDEntree = true;
	afterNavigate(({ from, to }) => {
		const entree = pageDEntree;
		pageDEntree = false;
		// `from.url` is null on the first navigation of a page rendered on the client only (ssr = false, the
		// (app) group), whatever its type says; an error here would stop SvelteKit before it starts its router.
		if (!MESURE_AUDIENCE || !to || from?.url?.pathname === to.url.pathname) return;
		// The page store, not `to`: on the first (« enter ») navigation `to.route.id` is null and `to`
		// carries no params; the store holds the matched route, its params and the status.
		const vue = get(page);
		const chemin = cheminMesure({ id: vue.route.id, params: vue.params, pathname: vue.url.pathname, statut: vue.status });
		if (!chemin) return;
		void compter(chemin);
		const canal = entree ? canalProvenance(vue.url) : null;
		if (canal) void compter(cheminProvenance(canal));
	});

	let { children } = $props();
</script>

<svelte:head>
	<title>DataCommune</title>
	<!-- No default meta description here: unlike <title>, <meta> tags in svelte:head
	     are NOT deduplicated across layout/page, so a layout-level description would
	     duplicate every page's own one in the SSR HTML (SEO snippet pollution).
	     Every (marketing) page defines its own description. -->

	{#if GOOGLE_SITE_VERIFICATION}
		<meta name="google-site-verification" content={GOOGLE_SITE_VERIFICATION} />
	{/if}
</svelte:head>

<a class="skip-link" href="#main-content">Aller au contenu principal</a>

{#if IS_PREPRODUCTION}
	<div class="w-full border-b border-line bg-warn-bg px-[var(--s4)] py-[var(--s1)] text-center text-meta font-semibold text-warn">
		Préproduction · {DEPLOYMENT_SHA.slice(0, 7)}
	</div>
{/if}

<!-- The hosted coverage is read from the API (/datasets), never written here (D-34). -->
{@render children()}
