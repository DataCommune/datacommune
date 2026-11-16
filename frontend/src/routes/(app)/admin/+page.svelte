<script lang="ts">
	import { onMount } from 'svelte';
	import { getAdminOverview, type AdminOverview } from '$lib/api/admin-overview';
	import OverviewCards from '$lib/components/admin/OverviewCards.svelte';

	let data = $state<AdminOverview | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(true);

	onMount(async () => {
		try {
			data = await getAdminOverview();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Erreur de chargement';
		} finally {
			loading = false;
		}
	});
</script>

<svelte:head>
	<title>Admin — DataCommune</title>
</svelte:head>

<h1 class="mb-6 font-display text-2xl font-semibold text-ink">Administration</h1>

{#if loading}
	<div class="mb-6 flex justify-center py-4">
		<div class="h-5 w-5 animate-spin rounded-full border-2 border-stone-border border-t-bronze"></div>
	</div>
{:else if error}
	<p class="mb-6 rounded border border-terracotta bg-terracotta/10 px-3 py-2 text-sm text-terracotta">{error}</p>
{:else if data}
	<div class="mb-6">
		<OverviewCards {data} />
	</div>
{/if}

<nav class="flex flex-col gap-2">
	<a
		href="/admin/users"
		class="rounded border border-stone-border bg-parchment px-4 py-3 transition-shadow hover:border-stone-400 hover:shadow-sm"
	>
		<div class="font-medium text-ink">Utilisateurs</div>
		<div class="text-sm text-ink-muted">
			Table paginée, recherche, filtres, panneau de détail, grant plan
		</div>
	</a>
	<a
		href="/admin/subscriptions"
		class="rounded border border-stone-border bg-parchment px-4 py-3 transition-shadow hover:border-stone-400 hover:shadow-sm"
	>
		<div class="font-medium text-ink">Abonnements</div>
		<div class="text-sm text-ink-muted">
			Stripe et grants manuels, filtre par source, sync forcé
		</div>
	</a>
	<a
		href="/admin/api-keys"
		class="rounded border border-stone-border bg-parchment px-4 py-3 transition-shadow hover:border-stone-400 hover:shadow-sm"
	>
		<div class="font-medium text-ink">Clés API</div>
		<div class="text-sm text-ink-muted">
			Vue cross-user, révocation individuelle, modification de quota
		</div>
	</a>
	<a
		href="/admin/requests"
		class="rounded border border-stone-border bg-parchment px-4 py-3 transition-shadow hover:border-stone-400 hover:shadow-sm"
	>
		<div class="font-medium text-ink">Journal des requêtes</div>
		<div class="text-sm text-ink-muted">
			Filtres par user/endpoint/status/date, export CSV (7 jours de rétention)
		</div>
	</a>
	<a
		href="/admin/audit"
		class="rounded border border-stone-border bg-parchment px-4 py-3 transition-shadow hover:border-stone-400 hover:shadow-sm"
	>
		<div class="font-medium text-ink">Journal d'audit</div>
		<div class="text-sm text-ink-muted">
			Historique des mutations admin, filtrable par admin/action/cible/date
		</div>
	</a>
	<a
		href="/admin/datasets"
		class="rounded border border-stone-border bg-parchment px-4 py-3 transition-shadow hover:border-stone-400 hover:shadow-sm"
	>
		<div class="font-medium text-ink">Datasets</div>
		<div class="text-sm text-ink-muted">
			Validation des données, comparaison avec les sources gouvernementales
		</div>
	</a>
	<a
		href="/admin/etl"
		class="rounded border border-stone-border bg-parchment px-4 py-3 transition-shadow hover:border-stone-400 hover:shadow-sm"
	>
		<div class="font-medium text-ink">Ops ETL</div>
		<div class="text-sm text-ink-muted">
			Historique des runs, dernier succès, trigger manuel de refresh
		</div>
	</a>
	<a
		href="/admin/system"
		class="rounded border border-stone-border bg-parchment px-4 py-3 transition-shadow hover:border-stone-400 hover:shadow-sm"
	>
		<div class="font-medium text-ink">Système</div>
		<div class="text-sm text-ink-muted">
			DB, rate limit, disk, services — temps réel (refresh 10s)
		</div>
	</a>
</nav>
