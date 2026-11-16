<script lang="ts">
	import type { AdminAuditEntry } from '$lib/api/admin-audit';

	interface Props {
		entries: AdminAuditEntry[];
		total: number;
		page: number;
		pageSize: number;
		onPageChange: (page: number) => void;
		onRowToggle: (id: number) => void;
	}

	let { entries, total, page, pageSize, onPageChange, onRowToggle }: Props = $props();

	let expandedIds = $state<Set<number>>(new Set());

	const totalPages = $derived(Math.ceil(total / pageSize) || 1);

	function toggle(id: number) {
		const next = new Set(expandedIds);
		if (next.has(id)) {
			next.delete(id);
		} else {
			next.add(id);
		}
		expandedIds = next;
		onRowToggle(id);
	}

	// Format helpers
	function fmtDateTime(d: string) {
		const dt = new Date(d);
		return (
			dt.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' }) +
			' ' +
			dt.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
		);
	}

	// Compute diff: keys where before[k] !== after[k]
	function diffKeys(
		before: Record<string, unknown> | null,
		after: Record<string, unknown> | null
	): Set<string> {
		if (!before || !after) return new Set();
		const changed = new Set<string>();
		const allKeys = new Set([...Object.keys(before), ...Object.keys(after)]);
		for (const k of allKeys) {
			if (JSON.stringify(before[k]) !== JSON.stringify(after[k])) {
				changed.add(k);
			}
		}
		return changed;
	}

	function formatJson(obj: Record<string, unknown> | null, changedKeys: Set<string>): string {
		if (!obj) return 'null';
		return JSON.stringify(obj, null, 2);
	}

	// For inline highlighting, we need line-by-line rendering
	function jsonLines(
		obj: Record<string, unknown> | null,
		changedKeys: Set<string>
	): { line: string; highlight: boolean }[] {
		if (!obj) return [{ line: 'null', highlight: false }];
		const raw = JSON.stringify(obj, null, 2);
		return raw.split('\n').map((line) => {
			const highlight = changedKeys.size > 0 && [...changedKeys].some((k) => line.includes(`"${k}"`));
			return { line, highlight };
		});
	}
</script>

<div class="overflow-x-auto rounded-lg border border-stone-border">
	<table class="w-full text-sm">
		<thead class="bg-marble-dark">
			<tr>
				<th class="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-ink-muted">
					Date
				</th>
				<th class="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-ink-muted">
					Admin
				</th>
				<th class="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-ink-muted">
					Action
				</th>
				<th class="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-ink-muted">
					Cible
				</th>
			</tr>
		</thead>
		<tbody>
			{#if entries.length === 0}
				<tr>
					<td colspan="4" class="py-12 text-center text-sm text-ink-muted">
						Aucune entrée trouvée
					</td>
				</tr>
			{/if}
			{#each entries as entry (entry.id)}
				{@const expanded = expandedIds.has(entry.id)}
				{@const changed = diffKeys(entry.before, entry.after)}

				<!-- Main row -->
				<tr
					class="cursor-pointer border-t border-stone-border transition-colors hover:bg-parchment {expanded ? 'bg-parchment' : ''}"
					onclick={() => toggle(entry.id)}
				>
					<td class="px-3 py-2.5 text-xs text-ink-muted">
						{fmtDateTime(entry.created_at)}
					</td>
					<td class="px-3 py-2.5 text-xs">
						{entry.admin_email ?? `#${entry.admin_user_id}`}
					</td>
					<td class="px-3 py-2.5">
						<span class="font-semibold text-ink">{entry.action}</span>
					</td>
					<td class="px-3 py-2.5 text-xs">
						{#if entry.target_type === 'user'}
							<a
								href="/admin/users?selected_user_id={entry.target_id}"
								class="text-bronze underline hover:text-bronze-dark"
								onclick={(e) => e.stopPropagation()}
							>
								{entry.target_type}:{entry.target_id}
							</a>
						{:else}
							<span class="text-ink-muted">{entry.target_type}:{entry.target_id}</span>
						{/if}
					</td>
				</tr>

				<!-- Expanded detail row -->
				{#if expanded}
					<tr class="border-t border-stone-border bg-marble">
						<td colspan="4" class="px-4 py-4">
							<!-- Reason -->
							<div class="mb-3">
								<span class="text-xs font-semibold uppercase tracking-wide text-ink-muted">Raison</span>
								<p class="mt-0.5 text-sm text-ink">{entry.reason}</p>
							</div>

							<!-- Before / After -->
							{#if entry.before !== null || entry.after !== null}
								<div class="grid grid-cols-2 gap-3">
									<div>
										<div class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">
											Avant
										</div>
										<pre class="overflow-auto rounded bg-stone-50 p-2 text-xs leading-relaxed">
{#each jsonLines(entry.before, changed) as { line, highlight }}
<span class={highlight ? 'block bg-amber-100' : 'block'}>{line}</span>
{/each}</pre>
									</div>
									<div>
										<div class="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">
											Après
										</div>
										<pre class="overflow-auto rounded bg-stone-50 p-2 text-xs leading-relaxed">
{#each jsonLines(entry.after, changed) as { line, highlight }}
<span class={highlight ? 'block bg-amber-100' : 'block'}>{line}</span>
{/each}</pre>
									</div>
								</div>
							{/if}

							<!-- IP + UA footer -->
							{#if entry.ip || entry.user_agent}
								<div class="mt-3 flex gap-4 text-xs text-ink-muted">
									{#if entry.ip}
										<span>IP : {entry.ip}</span>
									{/if}
									{#if entry.user_agent}
										<span class="truncate">UA : {entry.user_agent}</span>
									{/if}
								</div>
							{/if}
						</td>
					</tr>
				{/if}
			{/each}
		</tbody>
	</table>
</div>

<!-- Pagination -->
<div class="mt-3 flex items-center justify-between text-sm text-ink-muted">
	<span>
		Page {page} / {totalPages} — {total} entrée{total !== 1 ? 's' : ''}
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
