<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { getAdminSystemStatus, type SystemStatus } from '$lib/api/admin-system';
	import SystemStatusCard from '$lib/components/admin/SystemStatusCard.svelte';

	let status = $state<SystemStatus | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(false);
	let timer: ReturnType<typeof setInterval> | null = null;

	async function load() {
		loading = true;
		try {
			status = await getAdminSystemStatus();
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Erreur';
		} finally {
			loading = false;
		}
	}

	function fmtBytes(b: number | null): string {
		if (b == null) return '—';
		const units = ['B', 'KB', 'MB', 'GB', 'TB'];
		let v = b;
		let i = 0;
		while (v >= 1024 && i < units.length - 1) {
			v /= 1024;
			i++;
		}
		return `${v.toFixed(1)} ${units[i]}`;
	}

	function svcBadge(s: string): { label: string; className: string } {
		if (s === 'up') return { label: 'up', className: 'bg-emerald-100 text-emerald-800' };
		if (s === 'down') return { label: 'down', className: 'bg-red-100 text-red-800' };
		if (s === 'stale') return { label: 'stale', className: 'bg-amber-100 text-amber-800' };
		return { label: 'unknown', className: 'bg-stone-200 text-ink-muted' };
	}

	onMount(() => {
		load();
		timer = setInterval(load, 10_000);
	});

	onDestroy(() => {
		if (timer) clearInterval(timer);
	});

	let topBuckets = $derived(status ? status.rate_limit.active_buckets.slice(0, 10) : []);
</script>

<svelte:head><title>Système — Admin — DataCommune</title></svelte:head>

<div class="mb-4 flex items-center justify-between">
	<h1 class="font-display text-2xl font-semibold text-ink">Système</h1>
	<button
		onclick={load}
		disabled={loading}
		class="rounded border border-bronze px-3 py-1 text-sm text-bronze hover:bg-bronze hover:text-parchment disabled:opacity-50"
	>
		{loading ? 'Chargement…' : 'Rafraîchir'}
	</button>
</div>

{#if error}
	<p class="mb-3 rounded border border-red-300 bg-red-50 p-2 text-sm text-red-800">{error}</p>
{/if}

{#if status}
	<div class="grid gap-4 md:grid-cols-2">
		<SystemStatusCard title="Base de données" badge={svcBadge(status.db.status)}>
			<dl class="grid grid-cols-2 gap-1 text-sm">
				<dt class="text-ink-muted">Connexions actives</dt>
				<dd>{status.db.connections_active ?? '—'}</dd>
				<dt class="text-ink-muted">Connexions idle</dt>
				<dd>{status.db.connections_idle ?? '—'}</dd>
				<dt class="text-ink-muted">Total</dt>
				<dd>{status.db.connections_total ?? '—'}</dd>
				<dt class="text-ink-muted">Taille DB</dt>
				<dd>{status.db.db_size_pretty ?? '—'}</dd>
			</dl>
			{#if status.db.error}
				<p class="mt-2 text-xs text-red-700">{status.db.error}</p>
			{/if}
		</SystemStatusCard>

		<SystemStatusCard
			title="Rate limit"
			badge={{ label: `${status.rate_limit.total_buckets} buckets`, className: 'bg-stone-200 text-ink-muted' }}
		>
			<p class="mb-2 text-xs text-ink-muted">
				{status.rate_limit.active_buckets.length} buckets actifs sur {status.rate_limit
					.window_seconds}s
			</p>
			{#if topBuckets.length === 0}
				<p class="text-sm text-ink-muted">Aucun bucket actif</p>
			{:else}
				<table class="w-full text-sm">
					<tbody>
						{#each topBuckets as b (b.bucket)}
							<tr>
								<td class="truncate font-mono text-xs" title={b.bucket}>{b.bucket}</td>
								<td class="text-right">{b.count}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</SystemStatusCard>

		<SystemStatusCard title="Disque">
			{#each status.disk as d (d.mount)}
				<div class="mb-3 last:mb-0">
					<div class="flex items-center justify-between text-sm">
						<span class="font-mono">{d.mount}</span>
						{#if d.status === 'ok'}
							<span class="text-xs text-ink-muted"
								>{fmtBytes(d.used_bytes)} / {fmtBytes(d.total_bytes)} ({d.percent_used ?? 0}%)</span
							>
						{:else}
							<span class="text-xs text-ink-muted">{d.detail ?? 'indisponible'}</span>
						{/if}
					</div>
					{#if d.status === 'ok' && d.percent_used !== null}
						<div class="mt-1 h-2 w-full rounded bg-stone-200">
							<div class="h-2 rounded bg-bronze" style:width="{d.percent_used}%"></div>
						</div>
					{/if}
				</div>
			{/each}
		</SystemStatusCard>

		<SystemStatusCard title="Services">
			<table class="w-full text-sm">
				<tbody>
					{#each status.services as s (s.name)}
						{@const b = svcBadge(s.status)}
						<tr class="border-b border-stone-border/50 last:border-0">
							<td class="px-1 py-1 font-medium">{s.name}</td>
							<td class="px-1 py-1">
								<span class="rounded px-2 py-0.5 text-xs {b.className}">{b.label}</span>
							</td>
							<td class="px-1 py-1 text-xs text-ink-muted">{s.detail ?? ''}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</SystemStatusCard>
	</div>

	<p class="mt-4 text-xs text-ink-muted">
		Snapshot généré : {new Date(status.generated_at).toLocaleString('fr-FR')}
	</p>
{:else if loading}
	<p class="text-ink-muted">Chargement…</p>
{/if}
