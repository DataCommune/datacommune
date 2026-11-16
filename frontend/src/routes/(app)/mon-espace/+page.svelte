<script lang="ts">
	// Hub « Mon espace » (SHELL-01, D9-03/05) — landing screen of the authenticated space.
	// Quick access at the top: the site-wide commune search (`CommuneSearch`, D-50), the same
	// component as the home page; a chosen commune opens its Fiche Commune, the form opens the
	// results page; since 04-17 it searches addresses too. The « Fiche Bien » shortcut below leads to
	// that search on `/` (04-08: `/bien` is a 308 to `/`, the link no longer takes the detour). Below:
	// a « Reprendre » list of the last fiches
	// viewed (T3.4, hidden when empty) then compact secondary shortcuts. The signed-in email is
	// shown under the title.
	// Auth guard is inherited from (app)/+layout.svelte — NOT duplicated here.
	import { auth } from '$lib/stores/auth.svelte';
	import CommuneSearch from '$lib/components/CommuneSearch.svelte';
	import { recentFiches, type RecentFiche } from '$lib/stores/recent-fiches.svelte';
	import IconImmeuble from '$lib/components/ds/icons/IconImmeuble.svelte';
	import IconMaison from '$lib/components/ds/icons/IconMaison.svelte';

	// « Reprendre » links back to the exact fiche recorded by each page (T3.4/T3.3). A legacy `bien`
	// entry keeps its `/bien/{token}` link: the route redirects it to the address or the search.
	function ficheHref(f: RecentFiche) {
		return f.kind === 'commune' ? `/commune/${f.id}` : `/bien/${f.id}`;
	}
</script>

<svelte:head>
	<title>Mon espace — DataCommune</title>
</svelte:head>

<div class="mx-auto max-w-4xl px-4 py-6 sm:px-6">
	<h1 class="mb-2 font-display text-2xl font-bold text-ink">Mon espace</h1>
	{#if auth.user?.email}
		<p class="mb-1 text-sm text-ink-muted">
			Connecté avec <span class="font-medium text-ink">{auth.user.email}</span>
		</p>
	{/if}
	<p class="mb-8 text-sm text-ink-muted">
		Ouvrez la fiche d'une commune ou d'un bien en quelques secondes, ou choisissez un parcours.
	</p>

	<!-- Quick access (D9-03, D-50) — the site-wide commune search; a commune opens its Fiche Commune. -->
	<section class="mb-10">
		<p class="mb-2 text-xs font-semibold uppercase tracking-wide text-bronze-dark">
			Accès rapide — commune
		</p>
		<CommuneSearch />
	</section>

	<!-- « Reprendre » (D-UX-02, T3.4) — last fiches viewed, from the client-only store.
	     Hidden entirely when the history is empty (fresh account / cleared storage). -->
	{#if recentFiches.list().length > 0}
		<section class="mb-10">
			<p class="mb-2 text-xs font-semibold uppercase tracking-wide text-bronze-dark">Reprendre</p>
			<ul class="divide-y divide-stone-border overflow-hidden rounded-xl border border-stone-border bg-parchment">
				{#each recentFiches.list() as f (f.kind + ':' + f.id)}
					<li>
						<a
							href={ficheHref(f)}
							class="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-marble-dark"
						>
							{#if f.kind === 'commune'}
								<IconImmeuble class="h-5 w-5 flex-shrink-0 text-ink-muted" />
							{:else}
								<IconMaison class="h-5 w-5 flex-shrink-0 text-ink-muted" />
							{/if}
							<span class="min-w-0 flex-1">
								<span class="block truncate text-sm font-medium text-ink">{f.label}</span>
								<span class="block text-xs text-ink-muted">
									{f.kind === 'commune' ? 'Fiche commune' : 'Fiche bien'}
								</span>
							</span>
							<span class="text-ink-muted" aria-hidden="true">→</span>
						</a>
					</li>
				{/each}
			</ul>
		</section>
	{/if}

	<!-- Secondary shortcuts (T3.4) — the former 3 big cards, degraded to compact links now
	     that the search + « Reprendre » carry the hub. Prospection removed in 03-02 (D-39, D-50). -->
	<section class="flex flex-wrap gap-3">
		<a
			href="/communes"
			class="inline-flex items-center gap-2 rounded-lg border border-stone-border bg-parchment px-4 py-3 text-sm font-medium text-ink-muted transition-colors hover:border-bronze hover:text-bronze"
		>
			<IconImmeuble class="h-4 w-4" /> Communes
		</a>
		<a
			href="/"
			class="inline-flex items-center gap-2 rounded-lg border border-stone-border bg-parchment px-4 py-3 text-sm font-medium text-ink-muted transition-colors hover:border-bronze hover:text-bronze"
		>
			<IconMaison class="h-4 w-4" /> Fiche Bien
		</a>
	</section>
</div>
