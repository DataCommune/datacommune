<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listAdminRequests,
		buildExportCsvUrl,
		type RequestFilters,
		type AdminRequestsList
	} from '$lib/api/admin-requests';
	import AdminRequestsTable from '$lib/components/admin/AdminRequestsTable.svelte';

	// Filter state — use strings for bound inputs, convert on submit
	let filterUserId = $state('');
	let filterEndpoint = $state('');
	let filterStatusCode = $state('');
	let filterDateFrom = $state('');
	let filterDateTo = $state('');

	let filters = $state<RequestFilters>({ page: 1, page_size: 50 });
	let data = $state<AdminRequestsList | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			data = await listAdminRequests(filters);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Erreur de chargement';
			data = null;
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function applyFilters(e: SubmitEvent) {
		e.preventDefault();
		filters = {
			page: 1,
			page_size: 50,
			user_id: filterUserId ? Number(filterUserId) : undefined,
			endpoint: filterEndpoint || undefined,
			status_code: filterStatusCode ? Number(filterStatusCode) : undefined,
			date_from: filterDateFrom ? new Date(filterDateFrom).toISOString() : undefined,
			date_to: filterDateTo ? new Date(filterDateTo).toISOString() : undefined
		};
		load();
	}

	function gotoPage(p: number) {
		filters = { ...filters, page: p };
		load();
	}

	function resetFilters() {
		filterUserId = '';
		filterEndpoint = '';
		filterStatusCode = '';
		filterDateFrom = '';
		filterDateTo = '';
		filters = { page: 1, page_size: 50 };
		load();
	}

	let exportUrl = $derived(buildExportCsvUrl(filters));
	let totalPages = $derived(data ? Math.ceil(data.total / (data.page_size || 50)) : 0);
</script>

<svelte:head>
	<title>Admin — Journal des requêtes</title>
</svelte:head>

<h1 class="mb-4 font-display text-2xl font-semibold text-ink">Journal des requêtes</h1>

<div class="mb-4 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
	Les requêtes <code class="font-mono">/admin/*</code> ne sont pas journalisées ici — voir le
	<a class="underline" href="/admin/audit">Journal d'audit</a>.
	Rétention : 7 jours. Maximum 50 000 lignes à l'export.
</div>

<form class="mb-4 flex flex-wrap items-end gap-3" onsubmit={applyFilters}>
	<label class="flex flex-col text-sm">
		<span class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">User ID</span>
		<input
			class="w-24 rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none"
			type="number"
			min="1"
			placeholder="123"
			bind:value={filterUserId}
		/>
	</label>
	<label class="flex flex-col text-sm">
		<span class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">Endpoint (préfixe)</span>
		<input
			class="w-44 rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink placeholder:text-ink-muted focus:border-bronze focus:outline-none"
			type="text"
			maxlength="200"
			placeholder="/v1/dpe"
			bind:value={filterEndpoint}
		/>
	</label>
	<label class="flex flex-col text-sm">
		<span class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">Status code</span>
		<input
			class="w-24 rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none"
			type="number"
			min="100"
			max="599"
			placeholder="200"
			bind:value={filterStatusCode}
		/>
	</label>
	<label class="flex flex-col text-sm">
		<span class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">Depuis</span>
		<input
			class="rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none"
			type="datetime-local"
			bind:value={filterDateFrom}
		/>
	</label>
	<label class="flex flex-col text-sm">
		<span class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">Jusqu'à</span>
		<input
			class="rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none"
			type="datetime-local"
			bind:value={filterDateTo}
		/>
	</label>
	<div class="flex items-end gap-2">
		<button
			type="submit"
			class="rounded bg-bronze px-4 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-bronze-dark"
		>
			Filtrer
		</button>
		<a
			href={exportUrl}
			download="admin_requests.csv"
			class="rounded border border-bronze px-4 py-1.5 text-sm font-semibold text-bronze transition-colors hover:bg-bronze hover:text-white"
		>
			Export CSV
		</a>
		{#if filterUserId || filterEndpoint || filterStatusCode || filterDateFrom || filterDateTo}
			<button
				type="button"
				class="text-sm text-ink-muted underline hover:text-ink"
				onclick={resetFilters}
			>
				Réinitialiser
			</button>
		{/if}
	</div>
</form>

{#if error}
	<div class="rounded border border-terracotta bg-terracotta/10 px-3 py-2 text-sm text-terracotta">
		{error}
	</div>
{:else if loading}
	<div class="flex justify-center py-10">
		<div class="h-5 w-5 animate-spin rounded-full border-2 border-stone-border border-t-bronze"></div>
	</div>
{:else if data}
	<p class="mb-2 text-xs text-ink-muted">{data.total.toLocaleString('fr-FR')} résultat{data.total !== 1 ? 's' : ''}</p>
	<AdminRequestsTable rows={data.rows} />
	{#if totalPages > 1}
		<div class="mt-3 flex items-center gap-2 text-sm">
			<button
				disabled={data.page <= 1}
				onclick={() => gotoPage(data!.page - 1)}
				class="rounded border border-stone-border px-3 py-1 disabled:opacity-40 hover:bg-marble"
			>
				Précédent
			</button>
			<span class="text-ink-muted">Page {data.page} / {totalPages}</span>
			<button
				disabled={data.page >= totalPages}
				onclick={() => gotoPage(data!.page + 1)}
				class="rounded border border-stone-border px-3 py-1 disabled:opacity-40 hover:bg-marble"
			>
				Suivant
			</button>
		</div>
	{/if}
{/if}
