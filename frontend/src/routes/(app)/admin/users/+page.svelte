<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { listAdminUsers, type AdminUserRow } from '$lib/api/admin-users';
	import AdminUsersTable from '$lib/components/admin/AdminUsersTable.svelte';
	import BulkActionBar from '$lib/components/admin/BulkActionBar.svelte';
	import BulkActionModal from '$lib/components/admin/BulkActionModal.svelte';
	import UserDetailPanel from '$lib/components/admin/UserDetailPanel.svelte';

	// A shared/hand-crafted URL is untrusted input (T-50-08). Both numeric params below feed a
	// request path or a page index, so a non-integer must degrade to a safe default instead of
	// propagating a NaN (which produced a literal GET /v1/admin/users/NaN before this guard).
	// The string filters need no such guard: they only feed <select> bind:value and query
	// params, never raw HTML (T-50-09).
	function parsePositiveInt(raw: string | null): number | null {
		if (raw === null || !/^\d+$/.test(raw)) return null;
		const n = Number(raw);
		return Number.isSafeInteger(n) && n > 0 ? n : null;
	}

	// URL-synced state (read from URL params)
	let search = $state($page.url.searchParams.get('search') ?? '');
	let planFilter = $state($page.url.searchParams.get('plan') ?? '');
	let isAdminFilter = $state($page.url.searchParams.get('is_admin') ?? '');
	let emailVerifiedFilter = $state($page.url.searchParams.get('email_verified') ?? '');
	let currentPage = $state(parsePositiveInt($page.url.searchParams.get('page')) ?? 1);
	let sort = $state($page.url.searchParams.get('sort') ?? 'created_at_desc');
	let selectedUserId = $state<number | null>(
		parsePositiveInt($page.url.searchParams.get('selected_user_id'))
	);
	let lastUrlSearch = $state($page.url.search);

	// Bulk selection state
	let selectedIds = $state<Set<number>>(new Set());
	let bulkModalAction = $state<'reset_quota' | 'revoke_keys' | 'resend_verification' | null>(null);

	// Fetch results
	let users = $state<AdminUserRow[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let error = $state<string | null>(null);

	const PAGE_SIZE = 50;

	// Debounce search
	let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null;

	function pushUrl() {
		const sp = new URLSearchParams();
		if (search) sp.set('search', search);
		if (planFilter) sp.set('plan', planFilter);
		if (isAdminFilter) sp.set('is_admin', isAdminFilter);
		if (emailVerifiedFilter) sp.set('email_verified', emailVerifiedFilter);
		if (currentPage > 1) sp.set('page', String(currentPage));
		if (sort !== 'created_at_desc') sp.set('sort', sort);
		// Last key on purpose: the build order of the other keys is unchanged, so two shared
		// URLs stay comparable character for character (D-18).
		if (selectedUserId !== null) sp.set('selected_user_id', String(selectedUserId));
		const qs = sp.toString();
		goto(`/admin/users${qs ? '?' + qs : ''}`, { replaceState: true, keepFocus: true });
	}

	$effect(() => {
		const urlSearch = $page.url.search;
		if (urlSearch === lastUrlSearch) return;
		lastUrlSearch = urlSearch;
		const params = $page.url.searchParams;
		search = params.get('search') ?? '';
		planFilter = params.get('plan') ?? '';
		isAdminFilter = params.get('is_admin') ?? '';
		emailVerifiedFilter = params.get('email_verified') ?? '';
		currentPage = parsePositiveInt(params.get('page')) ?? 1;
		sort = params.get('sort') ?? 'created_at_desc';
		selectedUserId = parsePositiveInt(params.get('selected_user_id'));
	});

	async function fetchUsers() {
		loading = true;
		error = null;
		try {
			const res = await listAdminUsers({
				search: search || undefined,
				plan: planFilter || undefined,
				is_admin: isAdminFilter !== '' ? isAdminFilter : undefined,
				email_verified: emailVerifiedFilter !== '' ? emailVerifiedFilter : undefined,
				page: currentPage,
				page_size: PAGE_SIZE,
				sort
			});
			users = res.rows;
			total = res.total;
		} catch (e: unknown) {
			error = e instanceof Error ? (e as { detail?: string }).detail ?? e.message : 'Erreur de chargement';
		} finally {
			loading = false;
		}
	}

	// Fetch on filter changes
	$effect(() => {
		// Track all URL-driven state
		const _s = search;
		const _p = planFilter;
		const _a = isAdminFilter;
		const _ev = emailVerifiedFilter;
		const _pg = currentPage;
		const _sort = sort;
		void _s; void _p; void _a; void _ev; void _pg; void _sort;
		fetchUsers();
	});

	// Clear selection whenever page/filter/sort changes (selection does not span pages)
	$effect(() => {
		const _s = search;
		const _p = planFilter;
		const _a = isAdminFilter;
		const _ev = emailVerifiedFilter;
		const _pg = currentPage;
		const _sort = sort;
		void _s; void _p; void _a; void _ev; void _pg; void _sort;
		selectedIds = new Set();
	});

	function handleSearchInput(e: Event) {
		search = (e.target as HTMLInputElement).value;
		currentPage = 1;
		if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			pushUrl();
		}, 300);
	}

	function handleFilterChange() {
		currentPage = 1;
		pushUrl();
	}

	function handlePageChange(p: number) {
		currentPage = p;
		pushUrl();
	}

	function handleSortChange(s: string) {
		sort = s;
		currentPage = 1;
		pushUrl();
	}

	function handleRowClick(user: AdminUserRow) {
		selectedUserId = user.id;
		// The open row is part of the shareable state (D-18).
		pushUrl();
	}

	function handleDrawerClose() {
		selectedUserId = null;
		// Without this, closing the drawer would leave the stale id in the URL.
		pushUrl();
	}

	function handleDrawerChanged() {
		fetchUsers();
	}
</script>

<svelte:head>
	<title>Admin — Utilisateurs</title>
</svelte:head>

<div class="space-y-4">
	<!-- Header -->
	<div class="flex items-center justify-between">
		<h1 class="font-display text-xl font-semibold text-ink">Utilisateurs</h1>
	</div>

	<!-- Search + Filters -->
	<div class="flex flex-wrap items-center gap-3">
		<input
			type="search"
			placeholder="Recherche par email ou ID…"
			value={search}
			oninput={handleSearchInput}
			class="w-64 rounded border border-stone-border bg-white px-3 py-1.5 text-sm text-ink placeholder:text-ink-muted focus:border-bronze focus:outline-none"
		/>

		<select
			aria-label="Filtre plan"
			bind:value={planFilter}
			onchange={handleFilterChange}
			class="rounded border border-stone-border bg-white px-3 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none"
		>
			<option value="">Tous les plans</option>
			<option value="free">Free</option>
			<option value="pro">Pro</option>
			<option value="business">Business</option>
			<option value="enterprise">Enterprise</option>
		</select>

		<select
			aria-label="Filtre administrateur"
			bind:value={isAdminFilter}
			onchange={handleFilterChange}
			class="rounded border border-stone-border bg-white px-3 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none"
		>
			<option value="">Admin : tous</option>
			<option value="true">Admin uniquement</option>
			<option value="false">Non-admin uniquement</option>
		</select>

		<select
			aria-label="Filtre email vérifié"
			bind:value={emailVerifiedFilter}
			onchange={handleFilterChange}
			class="rounded border border-stone-border bg-white px-3 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none"
		>
			<option value="">Email : tous</option>
			<option value="true">Vérifiés</option>
			<option value="false">Non vérifiés</option>
		</select>
	</div>

	<!-- Error -->
	{#if error}
		<div class="rounded border border-terracotta bg-terracotta/10 px-3 py-2 text-sm text-terracotta">
			{error}
		</div>
	{/if}

	<!-- Bulk action toolbar (shown when selection is non-empty) -->
	{#if selectedIds.size > 0}
		<BulkActionBar
			selectedCount={selectedIds.size}
			onResetQuota={() => (bulkModalAction = 'reset_quota')}
			onRevokeKeys={() => (bulkModalAction = 'revoke_keys')}
			onResendVerif={() => (bulkModalAction = 'resend_verification')}
			onClearSelection={() => (selectedIds = new Set())}
		/>
	{/if}

	<!-- Loading -->
	{#if loading}
		<div class="flex justify-center py-10">
			<div class="h-6 w-6 animate-spin rounded-full border-2 border-stone-border border-t-bronze"></div>
		</div>
	{:else}
		<AdminUsersTable
			{users}
			{total}
			page={currentPage}
			pageSize={PAGE_SIZE}
			{sort}
			onPageChange={handlePageChange}
			onSortChange={handleSortChange}
			onRowClick={handleRowClick}
			selectedIds={selectedIds}
			onSelectionChange={(next) => (selectedIds = next)}
		/>
	{/if}
</div>

<!-- Bulk action modal -->
{#if bulkModalAction !== null}
	{@const selected = (users ?? [])
		.filter((u) => selectedIds.has(u.id))
		.map((u) => ({ id: u.id, email: u.email }))}
	<BulkActionModal
		action={bulkModalAction}
		selectedUsers={selected}
		onCancel={() => (bulkModalAction = null)}
		onComplete={() => {
			bulkModalAction = null;
			selectedIds = new Set();
			fetchUsers();
		}}
	/>
{/if}

<!-- User detail drawer (side panel) -->
<UserDetailPanel
	userId={selectedUserId}
	onClose={handleDrawerClose}
	onChanged={handleDrawerChanged}
/>
