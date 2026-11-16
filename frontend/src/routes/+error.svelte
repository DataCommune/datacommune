<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/stores';
	import CommuneSearch from '$lib/components/CommuneSearch.svelte';
	import SiteHeader from '$lib/components/SiteHeader.svelte';
	import SiteFooter from '$lib/components/SiteFooter.svelte';
	import PageErreur from '$lib/components/ds/PageErreur.svelte';
	import { EXEMPLES_ACCUEIL } from '$lib/data/recherche-lieux';

	// Root error boundary. It renders outside the (marketing) and (app) layouts, so it mounts the
	// single site header and footer itself (D-50). A message served by a load function (error(404,
	// '...')) is shown as is; SvelteKit's own English defaults are replaced by a French sentence.
	// 04.8-07 (UI-SPEC § 6.10, § 7.4): the v4 form. No breadcrumb and no header search; the h1, a lead,
	// the large field and « Pour repartir » as whole-row links: the home, then the generic entries of the
	// home (no place and no count written here, D-34). Any other error keeps its text, same template.
	// Since the shared `PageErreur` (T5, 2026-11-10) the body is the one of the two other boundaries; a 429
	// or a 5xx now offers the reload the other two already had (I8). The lead is spelled out by the
	// component (acronyms outside quotations, never the title), as it was here by `developperSigles`.
	const FRAMEWORK_DEFAULTS = new Set(['Not Found', 'Internal Error']);

	const status = $derived($page.status);
	const introuvable = $derived(status === 404);
	const title = $derived(introuvable ? 'Page introuvable' : 'Une erreur est survenue');
	const servi = $derived.by(() => {
		const message = $page.error?.message;
		return message && !FRAMEWORK_DEFAULTS.has(message) ? message : null;
	});
	const chapo = $derived(
		servi ??
			(introuvable
				? 'Cette adresse ou cette page n’existe pas sur DataCommune. Le lien est peut-être incomplet, ou l’adresse ne figure pas encore dans la Base Adresse Nationale.'
				: "Le service n'a pas répondu. Réessayez dans un instant.")
	);
	const sorties = [{ href: '/', titre: 'L’accueil' }, ...EXEMPLES_ACCUEIL.map((e) => ({ href: e.href, titre: e.label }))];
	// Reload only where waiting can help: a rate limit or a failure of the service (a 404 or a 400 stay as they are).
	const relance = $derived(
		status === 429 || status >= 500 ? { libelle: 'Recharger la page', action: () => invalidateAll() } : undefined
	);
</script>

<svelte:head>
	<title>{title} — DataCommune</title>
	{#if $page.url.pathname === '/bien' || $page.url.pathname.startsWith('/bien/')}
		<!-- KI-P12: nothing under /bien may be indexed (the header is set by hooks.server.ts). -->
		<meta name="robots" content="noindex, nofollow" />
	{/if}
</svelte:head>

<div class="public-site" data-ui="registre-sauge">
	<SiteHeader publique recherche={false} />
	<main id="main-content" tabindex="-1">
		<!-- The simple template without breadcrumb (§ 6.10). Another error keeps its status, so a reader tells a
		     503 from a 500 (the 404 says it by its title). -->
		<PageErreur
			titre={title}
			statut={introuvable ? undefined : status}
			message={chapo}
			panne={status >= 500}
			{relance}
			liens={sorties}
		>
			{#if introuvable && !servi}<p class="precision">Une adresse récente peut y manquer quelque temps.</p>{/if}
			<div class="champ">
				<CommuneSearch variante="grand" etiquette="Chercher une adresse ou une commune" sansDepartement />
			</div>
		</PageErreur>
	</main>
	<SiteFooter />
</div>

<style>
	.precision {
		margin: var(--s3) 0 0;
	}

	.champ {
		margin-top: var(--s5);
	}
</style>
