<script lang="ts">
	import type { AdminUserRow } from '$lib/api/admin-users';

	interface Props {
		users: AdminUserRow[];
		total: number;
		page: number;
		pageSize: number;
		sort: string;
		onPageChange: (page: number) => void;
		onSortChange: (sort: string) => void;
		onRowClick: (user: AdminUserRow) => void;
		selectedIds: Set<number>;
		onSelectionChange: (next: Set<number>) => void;
	}

	let { users, total, page, pageSize, sort, onPageChange, onSortChange, onRowClick, selectedIds, onSelectionChange }: Props =
		$props();

	const allSelectedOnPage = $derived(
		users.length > 0 && users.every((u) => selectedIds.has(u.id))
	);
	const someSelectedOnPage = $derived(
		users.some((u) => selectedIds.has(u.id)) && !allSelectedOnPage
	);

	const totalPages = $derived(Math.ceil(total / pageSize) || 1);

	// Sort helpers
	function getSortField(s: string) {
		return s.replace('_desc', '').replace('_asc', '');
	}
	function getSortDir(s: string) {
		return s.endsWith('_asc') ? 'asc' : 'desc';
	}

	function handleSortClick(field: string) {
		const cur = getSortField(sort);
		const dir = getSortDir(sort);
		if (cur === field) {
			// Toggle direction
			onSortChange(dir === 'desc' ? `${field}_asc` : `${field}_desc`);
		} else {
			onSortChange(`${field}_desc`);
		}
	}

	function sortIndicator(field: string) {
		if (getSortField(sort) !== field) return '';
		return getSortDir(sort) === 'desc' ? ' ↓' : ' ↑';
	}

	// Format helpers
	function fmtDate(d: string | null) {
		if (!d) return 'Jamais';
		return d.slice(0, 10);
	}

	function quotaRatio(used: number, monthly: number) {
		if (monthly <= 0) return 0;
		return Math.min(used / monthly, 1);
	}

	const COLUMNS = [
		{ field: 'email', label: 'Email' },
		{ field: 'plan', label: 'Plan' },
		{ field: 'quota', label: 'Quota' },
		{ field: 'api_keys_count', label: 'Clés API' },
		{ field: 'created_at', label: 'Créé le' },
		{ field: 'last_login', label: 'Dernière connexion' }
	];

	// Ref for header checkbox to set indeterminate state
	let headerCheckboxEl = $state<HTMLInputElement | null>(null);
	$effect(() => {
		if (headerCheckboxEl) {
			headerCheckboxEl.indeterminate = someSelectedOnPage;
		}
	});
</script>

<div class="overflow-x-auto rounded-lg border border-stone-border">
	<table class="w-full text-sm">
		<thead class="bg-marble-dark">
			<tr>
				<th class="px-2 py-2.5 w-10">
					<input
						bind:this={headerCheckboxEl}
						type="checkbox"
						class="accent-bronze"
						aria-label="Tout sélectionner sur cette page"
						checked={allSelectedOnPage}
						onchange={(e) => {
							const next = new Set(selectedIds);
							const target = e.target as HTMLInputElement;
							if (target.checked) {
								for (const u of users) next.add(u.id);
							} else {
								for (const u of users) next.delete(u.id);
							}
							onSelectionChange(next);
						}}
					/>
				</th>
				{#each COLUMNS as col}
					<th
						class="cursor-pointer select-none px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-ink-muted transition-colors hover:text-ink"
						onclick={() => handleSortClick(col.field)}
					>
						{col.label}{sortIndicator(col.field)}
					</th>
				{/each}
			</tr>
		</thead>
		<tbody>
			{#if users.length === 0}
				<tr>
					<td colspan="7" class="py-12 text-center text-sm text-ink-muted">
						Aucun utilisateur trouvé
					</td>
				</tr>
			{/if}
			{#each users as user (user.id)}
				<tr
					class="cursor-pointer border-t border-stone-border transition-colors hover:bg-parchment"
					onclick={() => onRowClick(user)}
				>
					<!-- Checkbox -->
					<td class="px-2 py-2.5">
						<input
							type="checkbox"
							class="accent-bronze"
							checked={selectedIds.has(user.id)}
							onclick={(e) => e.stopPropagation()}
							onchange={(e) => {
								const next = new Set(selectedIds);
								const target = e.target as HTMLInputElement;
								if (target.checked) next.add(user.id);
								else next.delete(user.id);
								onSelectionChange(next);
							}}
						/>
					</td>
					<!-- Email + badges -->
					<td class="px-3 py-2.5">
						<span class="font-medium text-ink">{user.email}</span>
						{#if user.is_admin}
							<span
								class="ml-1.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-800"
							>
								Admin
							</span>
						{/if}
						{#if !user.email_verified}
							<span
								class="ml-1.5 rounded-full bg-stone-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-stone-500"
							>
								Non vérifié
							</span>
						{/if}
					</td>
					<!-- Plan -->
					<td class="px-3 py-2.5">
						{#if user.plan}
							<span class="rounded-full bg-bronze-light px-2 py-0.5 text-xs font-semibold text-bronze-dark capitalize">
								{user.plan}
							</span>
						{:else}
							<span class="text-ink-muted">—</span>
						{/if}
					</td>
					<!-- Quota with progress bar -->
					<td class="px-3 py-2.5">
						<div class="min-w-[80px]">
							<div class="text-xs text-ink-muted">
								{user.quota_monthly < 0 ? `Illimité · ${user.quota_used} utilisées` : `${user.quota_used} / ${user.quota_monthly}`}
							</div>
							{#if user.quota_monthly >= 0}
							<div class="mt-1 h-1.5 w-full rounded-full bg-stone-200">
								<div
									class="h-1.5 rounded-full {quotaRatio(user.quota_used, user.quota_monthly) > 0.9
										? 'bg-terracotta'
										: quotaRatio(user.quota_used, user.quota_monthly) > 0.7
											? 'bg-amber-500'
											: 'bg-bronze'}"
									style="width: {(quotaRatio(user.quota_used, user.quota_monthly) * 100).toFixed(0)}%"
								></div>
							</div>
							{/if}
						</div>
					</td>
					<!-- API keys count -->
					<td class="px-3 py-2.5 text-center">
						<span class="font-medium">{user.api_keys_count}</span>
					</td>
					<!-- created_at -->
					<td class="px-3 py-2.5 text-ink-muted">
						{fmtDate(user.created_at)}
					</td>
					<!-- last_login -->
					<td class="px-3 py-2.5 text-ink-muted">
						{fmtDate(user.last_login)}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<!-- Pagination -->
<div class="mt-3 flex items-center justify-between text-sm text-ink-muted">
	<span>
		Page {page} / {totalPages} — {total} utilisateur{total !== 1 ? 's' : ''}
	</span>
	<div class="flex gap-2">
		<button
			class="rounded border border-stone-border bg-parchment px-3 py-1 transition-colors hover:bg-marble-dark disabled:cursor-not-allowed disabled:opacity-40"
			disabled={page <= 1}
			onclick={() => onPageChange(page - 1)}
		>
			Précédent
		</button>
		<button
			class="rounded border border-stone-border bg-parchment px-3 py-1 transition-colors hover:bg-marble-dark disabled:cursor-not-allowed disabled:opacity-40"
			disabled={page >= totalPages}
			onclick={() => onPageChange(page + 1)}
		>
			Suivant
		</button>
	</div>
</div>
