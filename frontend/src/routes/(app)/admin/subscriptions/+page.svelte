<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listAdminSubscriptions,
		syncAdminSubscription,
		type AdminSubscriptionRow,
		type AdminSubscriptionsList,
		type SubscriptionFilters
	} from '$lib/api/admin-subscriptions';
	import AdminSubscriptionsTable from '$lib/components/admin/AdminSubscriptionsTable.svelte';
	import SyncSubscriptionModal from '$lib/components/admin/SyncSubscriptionModal.svelte';

	let filters = $state<SubscriptionFilters>({ page: 1, page_size: 50 });
	let data = $state<AdminSubscriptionsList | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let syncTarget = $state<AdminSubscriptionRow | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			data = await listAdminSubscriptions(filters);
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
		filters = { ...filters, page: 1 };
		load();
	}

	async function handleSync(reason: string) {
		if (!syncTarget) return;
		// Re-throw so the modal catches and displays the server error
		await syncAdminSubscription(syncTarget.id, reason);
		syncTarget = null;
		await load();
	}

	let totalPages = $derived(
		data ? Math.ceil(data.total / (data.page_size || 50)) : 0
	);
</script>

<svelte:head>
	<title>Abonnements — Admin — DataCommune</title>
</svelte:head>

<h1 class="mb-4 font-display text-2xl font-semibold text-ink">Abonnements</h1>

<p class="mb-4 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
	Les grants manuels (<code>source='manual_grant'</code>) ne peuvent pas être synchronisés depuis
	Stripe. Les webhooks Stripe n'écrasent jamais un grant manuel existant (BILL-04).
</p>

<form class="mb-4 flex flex-wrap items-end gap-3" onsubmit={applyFilters}>
	<label class="flex flex-col text-sm">
		<span class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">Source</span>
		<select
			class="rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none"
			bind:value={filters.source}
		>
			<option value={undefined}>Tous</option>
			<option value="stripe">Stripe</option>
			<option value="manual_grant">Grant manuel</option>
		</select>
	</label>
	<label class="flex flex-col text-sm">
		<span class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">Plan</span>
		<input
			class="rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink placeholder:text-ink-muted focus:border-bronze focus:outline-none"
			type="text"
			placeholder="pro / business / enterprise / free"
			maxlength="50"
			bind:value={filters.plan_name}
		/>
	</label>
	<label class="flex flex-col text-sm">
		<span class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">Statut</span>
		<input
			class="rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink placeholder:text-ink-muted focus:border-bronze focus:outline-none"
			type="text"
			placeholder="active / canceled / …"
			maxlength="50"
			bind:value={filters.status}
		/>
	</label>
	<label class="flex flex-col text-sm">
		<span class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">User email (contient)</span>
		<input
			class="w-56 rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink placeholder:text-ink-muted focus:border-bronze focus:outline-none"
			type="text"
			maxlength="255"
			bind:value={filters.user_email}
		/>
	</label>
	<button
		type="submit"
		class="rounded bg-bronze px-4 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-bronze-dark"
	>
		Filtrer
	</button>
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
	<p class="mb-2 text-xs text-ink-muted">{data.total.toLocaleString('fr-FR')} abonnement{data.total !== 1 ? 's' : ''}</p>
	<AdminSubscriptionsTable rows={data.rows} onSync={(r) => (syncTarget = r)} />
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

{#if syncTarget}
	<SyncSubscriptionModal
		subRow={syncTarget}
		onConfirm={handleSync}
		onCancel={() => (syncTarget = null)}
	/>
{/if}
