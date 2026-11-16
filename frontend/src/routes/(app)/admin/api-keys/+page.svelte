<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listAdminApiKeys,
		revokeAdminApiKey,
		updateAdminApiKeyQuota,
		type AdminApiKeyRow,
		type AdminApiKeysList,
		type ApiKeyFilters
	} from '$lib/api/admin-api-keys';
	import AdminApiKeysTable from '$lib/components/admin/AdminApiKeysTable.svelte';
	import RevokeKeyModal from '$lib/components/admin/RevokeKeyModal.svelte';
	import QuotaEditModal from '$lib/components/admin/QuotaEditModal.svelte';

	let filterEmail = $state('');
	let filterRevoked = $state(false);

	let filters = $state<ApiKeyFilters>({ page: 1, page_size: 50 });
	let data = $state<AdminApiKeysList | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	// Modal targets
	let revokeTarget = $state<AdminApiKeyRow | null>(null);
	let quotaTarget = $state<AdminApiKeyRow | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			data = await listAdminApiKeys(filters);
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
			owner_email: filterEmail || undefined,
			revoked: filterRevoked ? true : undefined
		};
		load();
	}

	function resetFilters() {
		filterEmail = '';
		filterRevoked = false;
		filters = { page: 1, page_size: 50 };
		load();
	}

	async function handleRevoke(reason: string) {
		if (!revokeTarget) return;
		await revokeAdminApiKey(revokeTarget.id, reason);
		revokeTarget = null;
		await load();
	}

	async function handleQuota(quota: number, reason: string) {
		if (!quotaTarget) return;
		await updateAdminApiKeyQuota(quotaTarget.id, quota, reason);
		quotaTarget = null;
		await load();
	}

	let totalPages = $derived(data ? Math.ceil(data.total / (data.page_size || 50)) : 0);
</script>

<svelte:head>
	<title>Admin — Clés API</title>
</svelte:head>

<h1 class="mb-4 font-display text-2xl font-semibold text-ink">Clés API</h1>

<form class="mb-4 flex flex-wrap items-end gap-3" onsubmit={applyFilters}>
	<label class="flex flex-col text-sm">
		<span class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">Owner email (contient)</span>
		<input
			type="text"
			class="w-56 rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink placeholder:text-ink-muted focus:border-bronze focus:outline-none"
			placeholder="utilisateur@exemple.fr"
			bind:value={filterEmail}
		/>
	</label>
	<label class="flex items-center gap-2 text-sm">
		<input
			type="checkbox"
			checked={filterRevoked}
			onchange={(e) => (filterRevoked = (e.currentTarget as HTMLInputElement).checked)}
			class="h-4 w-4 accent-bronze"
		/>
		<span class="text-ink">Révoquées uniquement</span>
	</label>
	<div class="flex items-end gap-2">
		<button
			type="submit"
			class="rounded bg-bronze px-4 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-bronze-dark"
		>
			Filtrer
		</button>
		{#if filterEmail || filterRevoked}
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
	<p class="mb-2 text-xs text-ink-muted">{data.total.toLocaleString('fr-FR')} clé{data.total !== 1 ? 's' : ''}</p>
	<AdminApiKeysTable
		rows={data.rows}
		onRevoke={(r) => (revokeTarget = r)}
		onEditQuota={(r) => (quotaTarget = r)}
	/>
	{#if totalPages > 1}
		<div class="mt-3 flex items-center gap-2 text-sm">
			<button
				disabled={data.page <= 1}
				onclick={() => {
					filters = { ...filters, page: data!.page - 1 };
					load();
				}}
				class="rounded border border-stone-border px-3 py-1 disabled:opacity-40 hover:bg-marble"
			>
				Précédent
			</button>
			<span class="text-ink-muted">Page {data.page} / {totalPages}</span>
			<button
				disabled={data.page >= totalPages}
				onclick={() => {
					filters = { ...filters, page: data!.page + 1 };
					load();
				}}
				class="rounded border border-stone-border px-3 py-1 disabled:opacity-40 hover:bg-marble"
			>
				Suivant
			</button>
		</div>
	{/if}
{/if}

{#if revokeTarget}
	<RevokeKeyModal
		keyRow={revokeTarget}
		onConfirm={handleRevoke}
		onCancel={() => (revokeTarget = null)}
	/>
{/if}

{#if quotaTarget}
	<QuotaEditModal
		keyRow={quotaTarget}
		onConfirm={handleQuota}
		onCancel={() => (quotaTarget = null)}
	/>
{/if}
