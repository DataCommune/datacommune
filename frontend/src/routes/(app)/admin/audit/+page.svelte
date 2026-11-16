<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { listAdminAudit, type AdminAuditEntry } from '$lib/api/admin-audit';
	import AdminAuditTable from '$lib/components/admin/AdminAuditTable.svelte';

	// Single source of truth for the audit action filter dropdown.
	// Must stay in sync with backend admin action set (Phases 15-19).
	// Closes the v2.2 milestone audit gap where late-phase actions were
	// undiscoverable from this UI filter.
	const ADMIN_AUDIT_ACTIONS = [
		'grant_plan',
		'toggle_is_admin',
		'reset_quota',
		'revoke_api_keys',
		'resend_verification',
		'revoke_api_key',
		'update_key_quota',
		'sync_subscription',
		'trigger_etl'
	] as const;

	// URL-synced state
	let adminUserId = $state($page.url.searchParams.get('admin_user_id') ?? '');
	let actionFilter = $state($page.url.searchParams.get('action') ?? '');
	let targetType = $state($page.url.searchParams.get('target_type') ?? '');
	let targetId = $state($page.url.searchParams.get('target_id') ?? '');
	let dateFrom = $state($page.url.searchParams.get('date_from') ?? '');
	let dateTo = $state($page.url.searchParams.get('date_to') ?? '');
	let currentPage = $state(Number($page.url.searchParams.get('page') ?? '1'));
	let lastUrlSearch = $state($page.url.search);

	// Fetch results
	let entries = $state<AdminAuditEntry[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let error = $state<string | null>(null);

	const PAGE_SIZE = 50;

	function pushUrl() {
		const sp = new URLSearchParams();
		if (adminUserId) sp.set('admin_user_id', adminUserId);
		if (actionFilter) sp.set('action', actionFilter);
		if (targetType) sp.set('target_type', targetType);
		if (targetId) sp.set('target_id', targetId);
		if (dateFrom) sp.set('date_from', dateFrom);
		if (dateTo) sp.set('date_to', dateTo);
		if (currentPage > 1) sp.set('page', String(currentPage));
		const qs = sp.toString();
		goto(`/admin/audit${qs ? '?' + qs : ''}`, { replaceState: true, keepFocus: true });
	}

	$effect(() => {
		const urlSearch = $page.url.search;
		if (urlSearch === lastUrlSearch) return;
		lastUrlSearch = urlSearch;
		const params = $page.url.searchParams;
		adminUserId = params.get('admin_user_id') ?? '';
		actionFilter = params.get('action') ?? '';
		targetType = params.get('target_type') ?? '';
		targetId = params.get('target_id') ?? '';
		dateFrom = params.get('date_from') ?? '';
		dateTo = params.get('date_to') ?? '';
		currentPage = Number(params.get('page') ?? '1');
	});

	async function fetchEntries() {
		loading = true;
		error = null;
		try {
			const res = await listAdminAudit({
				admin_user_id: adminUserId || undefined,
				action: actionFilter || undefined,
				target_type: targetType || undefined,
				target_id: targetId || undefined,
				date_from: dateFrom ? new Date(dateFrom).toISOString() : undefined,
				date_to: dateTo ? new Date(dateTo).toISOString() : undefined,
				page: currentPage,
				page_size: PAGE_SIZE
			});
			entries = res.rows;
			total = res.total;
		} catch (e: unknown) {
			error = e instanceof Error ? (e as { detail?: string }).detail ?? e.message : 'Erreur de chargement';
		} finally {
			loading = false;
		}
	}

	// Fetch on state changes
	$effect(() => {
		const _a = adminUserId;
		const _af = actionFilter;
		const _tt = targetType;
		const _ti = targetId;
		const _df = dateFrom;
		const _dt = dateTo;
		const _pg = currentPage;
		void _a; void _af; void _tt; void _ti; void _df; void _dt; void _pg;
		fetchEntries();
	});

	function handleFilterChange() {
		currentPage = 1;
		pushUrl();
	}

	function handlePageChange(p: number) {
		currentPage = p;
		pushUrl();
	}

	// Row toggle is handled inside AdminAuditTable — we just pass a handler here for parent awareness
	function handleRowToggle(_id: number) {
		// No-op: expansion state managed inside AdminAuditTable
	}
</script>

<svelte:head>
	<title>Admin — Audit</title>
</svelte:head>

<div class="space-y-4">
	<!-- Header -->
	<h1 class="font-display text-xl font-semibold text-ink">Journal d'audit</h1>

	<!-- Filters toolbar -->
	<div class="flex flex-wrap items-center gap-3">
		<input
			type="number"
			placeholder="ID admin…"
			bind:value={adminUserId}
			onchange={handleFilterChange}
			class="w-28 rounded border border-stone-border bg-white px-3 py-1.5 text-sm text-ink placeholder:text-ink-muted focus:border-bronze focus:outline-none"
		/>

		<select
			bind:value={actionFilter}
			onchange={handleFilterChange}
			class="rounded border border-stone-border bg-white px-3 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none"
		>
			<option value="">Toutes les actions</option>
			{#each ADMIN_AUDIT_ACTIONS as action (action)}
				<option value={action}>{action}</option>
			{/each}
		</select>

		<input
			type="text"
			placeholder="Type de cible…"
			bind:value={targetType}
			onchange={handleFilterChange}
			class="w-36 rounded border border-stone-border bg-white px-3 py-1.5 text-sm text-ink placeholder:text-ink-muted focus:border-bronze focus:outline-none"
		/>

		<input
			type="text"
			placeholder="ID cible…"
			bind:value={targetId}
			onchange={handleFilterChange}
			class="w-28 rounded border border-stone-border bg-white px-3 py-1.5 text-sm text-ink placeholder:text-ink-muted focus:border-bronze focus:outline-none"
		/>

		<div class="flex items-center gap-2">
			<label class="text-xs font-semibold text-ink-muted" for="audit-date-from">Du</label>
			<input
				id="audit-date-from"
				type="date"
				bind:value={dateFrom}
				onchange={handleFilterChange}
				class="rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none"
			/>
		</div>
		<div class="flex items-center gap-2">
			<label class="text-xs font-semibold text-ink-muted" for="audit-date-to">Au</label>
			<input
				id="audit-date-to"
				type="date"
				bind:value={dateTo}
				onchange={handleFilterChange}
				class="rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none"
			/>
		</div>

		{#if adminUserId || actionFilter || targetType || targetId || dateFrom || dateTo}
			<button
				class="text-sm text-ink-muted underline hover:text-ink"
				onclick={() => {
					adminUserId = '';
					actionFilter = '';
					targetType = '';
					targetId = '';
					dateFrom = '';
					dateTo = '';
					currentPage = 1;
					pushUrl();
				}}
			>
				Réinitialiser
			</button>
		{/if}
	</div>

	<!-- Error -->
	{#if error}
		<div class="rounded border border-terracotta bg-terracotta/10 px-3 py-2 text-sm text-terracotta">
			{error}
		</div>
	{/if}

	<!-- Loading -->
	{#if loading}
		<div class="flex justify-center py-10">
			<div class="h-6 w-6 animate-spin rounded-full border-2 border-stone-border border-t-bronze"></div>
		</div>
	{:else}
		<AdminAuditTable
			{entries}
			{total}
			page={currentPage}
			pageSize={PAGE_SIZE}
			onPageChange={handlePageChange}
			onRowToggle={handleRowToggle}
		/>
	{/if}
</div>
