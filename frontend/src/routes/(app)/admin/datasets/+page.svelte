<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getDatasets,
		getDataGouvMeta,
		DATAGOUV_SLUGS,
		SOURCE_INFO,
		type DatasetInfo,
		type DataGouvMeta
	} from '$lib/api/admin';
	import { focusTrap } from '$lib/actions/focusTrap';

	// Auth guard is handled by /admin/+layout.svelte
	let datasets = $state<DatasetInfo[]>([]);
	let sourcesMeta = $state<Record<string, DataGouvMeta>>({});
	let loading = $state(true);
	let loadingSources = $state(false);
	let error = $state<string | null>(null);
	let selectedDataset = $state<string | null>(null);

	onMount(async () => {
		await loadDatasets();
	});

	async function loadDatasets() {
		loading = true;
		error = null;
		try {
			const res = await getDatasets();
			datasets = res.donnees;
		} catch (e: any) {
			error = e.detail ?? e.message ?? 'Erreur de chargement';
		} finally {
			loading = false;
		}
	}

	async function compareSources() {
		loadingSources = true;
		const entries = Object.entries(DATAGOUV_SLUGS);
		const results = await Promise.all(
			entries.map(async ([name, slug]) => {
				const meta = await getDataGouvMeta(slug);
				return [name, meta] as const;
			})
		);
		const newMeta: Record<string, DataGouvMeta> = {};
		for (const [name, meta] of results) {
			if (meta) newMeta[name] = meta;
		}
		sourcesMeta = newMeta;
		loadingSources = false;
	}

	// Helpers
	function fmtNum(n: number | null) {
		if (n == null) return '\u2014';
		return n.toLocaleString('fr-FR');
	}
	function fmtDate(d: string | null) {
		if (!d) return '\u2014';
		const dt = new Date(d);
		return dt.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' })
			+ ' ' + dt.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
	}
	function fmtDuration(s: number | null) {
		if (s == null) return '\u2014';
		if (s < 60) return `${Math.round(s)}s`;
		if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
		return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
	}
	function fmtSize(bytes: number) {
		if (!bytes) return '\u2014';
		if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
		if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
		return (bytes / 1073741824).toFixed(2) + ' GB';
	}
	function daysSince(d: string | null) {
		if (!d) return null;
		return Math.floor((Date.now() - new Date(d).getTime()) / 86400000);
	}

	// Computed
	let fresh = $derived(datasets.filter((d) => d.fraicheur === 'fresh').length);
	let stale = $derived(datasets.filter((d) => d.fraicheur === 'stale').length);
	let outdated = $derived(datasets.filter((d) => d.fraicheur === 'outdated').length);
	let totalRows = $derived(datasets.reduce((s, d) => s + (d.nombre_lignes ?? 0), 0));
	let sourcesVerified = $derived(Object.keys(sourcesMeta).length);

	let sortedDatasets = $derived(
		[...datasets].sort((a, b) => {
			const order: Record<string, number> = { outdated: 0, stale: 1, fresh: 2 };
			return (order[a.fraicheur ?? ''] ?? 3) - (order[b.fraicheur ?? ''] ?? 3);
		})
	);

	let selectedDs = $derived(datasets.find((d) => d.nom === selectedDataset));
	let selectedInfo = $derived(selectedDataset ? SOURCE_INFO[selectedDataset] : null);
	let selectedMeta = $derived(selectedDataset ? sourcesMeta[selectedDataset] : null);
</script>

<svelte:head>
	<title>Admin — DataCommune</title>
</svelte:head>

<div class="mx-auto max-w-6xl space-y-6 px-4 py-6 sm:px-6">
		<!-- Header -->
		<div class="flex items-center justify-between">
			<div>
				<h1 class="font-display text-2xl font-bold text-ink">Validation des Données</h1>
				<p class="text-sm text-ink-muted">Comparaison des datasets locaux avec les sources gouvernementales</p>
			</div>
			<div class="flex gap-3">
				<button
					class="rounded-lg bg-bronze px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-bronze-dark disabled:opacity-50"
					onclick={loadDatasets}
					disabled={loading}
				>
					{loading ? 'Chargement…' : 'Rafraichir'}
				</button>
				<button
					class="rounded-lg border border-bronze px-5 py-2 text-sm font-semibold text-bronze transition-colors hover:bg-bronze-light disabled:opacity-50"
					onclick={compareSources}
					disabled={loadingSources || datasets.length === 0}
				>
					{loadingSources ? 'Verification…' : 'Comparer avec les sources'}
				</button>
			</div>
		</div>

		{#if error}
			<div class="rounded-lg border border-terracotta bg-terracotta/10 px-4 py-3 text-sm text-terracotta">
				{error}
			</div>
		{/if}

		<!-- Summary cards -->
		{#if datasets.length > 0}
			<div class="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
				<div class="rounded-xl border border-stone-border bg-parchment p-4 text-center">
					<div class="text-2xl font-extrabold text-blue-600">{datasets.length}</div>
					<div class="text-xs font-semibold uppercase tracking-wide text-ink-muted">Datasets</div>
				</div>
				<div class="rounded-xl border border-stone-border bg-parchment p-4 text-center">
					<div class="text-2xl font-extrabold text-green-700">{fresh}</div>
					<div class="text-xs font-semibold uppercase tracking-wide text-ink-muted">Fresh</div>
				</div>
				<div class="rounded-xl border border-stone-border bg-parchment p-4 text-center">
					<div class="text-2xl font-extrabold text-amber-600">{stale}</div>
					<div class="text-xs font-semibold uppercase tracking-wide text-ink-muted">Stale</div>
				</div>
				<div class="rounded-xl border border-stone-border bg-parchment p-4 text-center">
					<div class="text-2xl font-extrabold text-red-600">{outdated}</div>
					<div class="text-xs font-semibold uppercase tracking-wide text-ink-muted">Outdated</div>
				</div>
				<div class="rounded-xl border border-stone-border bg-parchment p-4 text-center">
					<div class="text-2xl font-extrabold text-ink">{fmtNum(totalRows)}</div>
					<div class="text-xs font-semibold uppercase tracking-wide text-ink-muted">Lignes</div>
				</div>
				<div class="rounded-xl border border-stone-border bg-parchment p-4 text-center">
					<div class="text-2xl font-extrabold text-ink">{sourcesVerified}</div>
					<div class="text-xs font-semibold uppercase tracking-wide text-ink-muted">Sources vérifiées</div>
				</div>
			</div>
		{/if}

		<!-- Dataset grid -->
		{#if loading}
			<div class="flex justify-center py-16">
				<div class="h-8 w-8 animate-spin rounded-full border-2 border-stone-border border-t-bronze"></div>
			</div>
		{:else}
			<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
				{#each sortedDatasets as ds (ds.nom)}
					{@const info = SOURCE_INFO[ds.nom]}
					{@const meta = sourcesMeta[ds.nom]}
					{@const isProxy = info?.type === 'proxy'}
					{@const age = daysSince(ds.derniere_maj)}
					{@const srcAge = meta ? daysSince(meta.last_modified) : null}
					{@const delay = age != null && srcAge != null ? age - srcAge : null}

					<button
						class="cursor-pointer rounded-xl border bg-parchment p-4 text-left transition-shadow hover:shadow-md
							{ds.fraicheur === 'fresh' ? 'border-l-4 border-l-green-600 border-t-stone-border border-r-stone-border border-b-stone-border' :
							ds.fraicheur === 'stale' ? 'border-l-4 border-l-amber-500 border-t-stone-border border-r-stone-border border-b-stone-border' :
							ds.fraicheur === 'outdated' ? 'border-l-4 border-l-red-500 border-t-stone-border border-r-stone-border border-b-stone-border' :
							'border-stone-border'}"
						onclick={() => (selectedDataset = ds.nom)}
					>
						<div class="mb-3 flex items-start justify-between gap-2">
							<div>
								<div class="text-sm font-bold text-ink">{info?.nom ?? ds.nom}</div>
								<div class="text-xs text-ink-muted">{info?.producteur ?? ''} &middot; {ds.nom}</div>
							</div>
							{#if isProxy}
								<span class="shrink-0 rounded-full bg-blue-100 px-2.5 py-0.5 text-[10px] font-bold uppercase text-blue-700">Proxy</span>
							{:else if ds.fraicheur === 'fresh'}
								<span class="shrink-0 rounded-full bg-green-100 px-2.5 py-0.5 text-[10px] font-bold uppercase text-green-700">Fresh</span>
							{:else if ds.fraicheur === 'stale'}
								<span class="shrink-0 rounded-full bg-amber-100 px-2.5 py-0.5 text-[10px] font-bold uppercase text-amber-700">Stale</span>
							{:else if ds.fraicheur === 'outdated'}
								<span class="shrink-0 rounded-full bg-red-100 px-2.5 py-0.5 text-[10px] font-bold uppercase text-red-700">Outdated</span>
							{:else}
								<span class="shrink-0 rounded-full bg-marble-dark px-2.5 py-0.5 text-[10px] font-bold uppercase text-ink-muted">{ds.statut}</span>
							{/if}
						</div>

						<table class="w-full text-xs">
							<tbody>
								<tr>
									<td class="py-1 pr-2 font-medium text-ink-muted">Lignes</td>
									<td class="py-1 font-semibold">{fmtNum(ds.nombre_lignes)}</td>
								</tr>
								<tr>
									<td class="py-1 pr-2 font-medium text-ink-muted">MAJ locale</td>
									<td class="py-1">{fmtDate(ds.derniere_maj)} {age != null ? `(${age}j)` : ''}</td>
								</tr>
								{#if !isProxy && meta}
									<tr>
										<td class="py-1 pr-2 font-medium text-ink-muted">MAJ source</td>
										<td class="py-1 text-blue-600">
											{fmtDate(meta.last_modified)}
											{#if delay != null}
												{#if delay <= 1}
													<span class="text-green-600">&#10003;</span>
												{:else}
													<span class="font-semibold text-red-600">&#9888; {delay}j retard</span>
												{/if}
											{/if}
										</td>
									</tr>
								{/if}
								<tr>
									<td class="py-1 pr-2 font-medium text-ink-muted">ETL</td>
									<td class="py-1">
										{#if ds.derniere_run}
											<span class={ds.derniere_run.statut === 'success' ? 'text-green-600' : 'text-red-600'}>
												{ds.derniere_run.statut}
											</span>
											&middot; {fmtDuration(ds.derniere_run.duree_secondes)}
										{:else}
											<span class="text-ink-muted">&mdash;</span>
										{/if}
									</td>
								</tr>
							</tbody>
						</table>
					</button>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Detail modal -->
	{#if selectedDataset && selectedDs}
		<div
			class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/40 p-6 backdrop-blur-sm"
			onclick={(e) => { if (e.target === e.currentTarget) selectedDataset = null; }}
			onkeydown={(e) => { if (e.key === 'Escape') selectedDataset = null; }}
			role="dialog"
			aria-modal="true"
			tabindex="-1"
			use:focusTrap
		>
			<div class="w-full max-w-2xl rounded-xl border border-stone-border bg-parchment shadow-xl">
				<!-- Header -->
				<div class="flex items-center justify-between border-b border-stone-border px-6 py-4">
					<h2 class="text-lg font-bold text-ink">{selectedInfo?.nom ?? selectedDataset}</h2>
					<button
						class="text-2xl text-ink-muted transition-colors hover:text-ink"
						onclick={() => (selectedDataset = null)}
					>&times;</button>
				</div>

				<div class="space-y-5 px-6 py-5">
					<!-- Local data -->
					<section>
						<h3 class="mb-2 border-b-2 border-bronze-light pb-1 text-xs font-bold uppercase tracking-wider text-bronze">
							Données locales
						</h3>
						<table class="w-full text-sm">
							<tbody>
								<tr><td class="w-2/5 py-1.5 font-medium text-ink-muted">Statut</td><td>{selectedDs.statut}</td></tr>
								<tr><td class="py-1.5 font-medium text-ink-muted">Fraicheur</td><td>
									<span class="rounded-full px-2 py-0.5 text-xs font-bold
										{selectedDs.fraicheur === 'fresh' ? 'bg-green-100 text-green-700' :
										selectedDs.fraicheur === 'stale' ? 'bg-amber-100 text-amber-700' :
										selectedDs.fraicheur === 'outdated' ? 'bg-red-100 text-red-700' : 'bg-marble-dark text-ink-muted'}">
										{selectedDs.fraicheur ?? '?'}
									</span>
								</td></tr>
								<tr><td class="py-1.5 font-medium text-ink-muted">Lignes</td><td class="font-semibold">{fmtNum(selectedDs.nombre_lignes)}</td></tr>
								<tr><td class="py-1.5 font-medium text-ink-muted">Derniere MAJ</td><td>{fmtDate(selectedDs.derniere_maj)}</td></tr>
								<tr><td class="py-1.5 font-medium text-ink-muted">Duree import</td><td>{fmtDuration(selectedDs.duree_import_secondes)}</td></tr>
								<tr><td class="py-1.5 font-medium text-ink-muted">Cron</td><td><code class="text-xs">{selectedDs.cron_schedule ?? '\u2014'}</code></td></tr>
							</tbody>
						</table>
					</section>

					<!-- ETL Run -->
					{#if selectedDs.derniere_run}
						<section>
							<h3 class="mb-2 border-b-2 border-bronze-light pb-1 text-xs font-bold uppercase tracking-wider text-bronze">
								Dernier ETL
							</h3>
							<table class="w-full text-sm">
								<tbody>
									<tr><td class="w-2/5 py-1.5 font-medium text-ink-muted">Statut</td><td class={selectedDs.derniere_run.statut === 'success' ? 'font-semibold text-green-600' : 'font-semibold text-red-600'}>{selectedDs.derniere_run.statut}</td></tr>
									<tr><td class="py-1.5 font-medium text-ink-muted">Debut</td><td>{fmtDate(selectedDs.derniere_run.debut)}</td></tr>
									<tr><td class="py-1.5 font-medium text-ink-muted">Duree</td><td>{fmtDuration(selectedDs.derniere_run.duree_secondes)}</td></tr>
									<tr><td class="py-1.5 font-medium text-ink-muted">Lignes chargees</td><td>{fmtNum(selectedDs.derniere_run.lignes_chargees)}</td></tr>
									{#if selectedDs.derniere_run.erreur}
										<tr><td class="py-1.5 font-medium text-ink-muted">Erreur</td><td class="text-red-600">{selectedDs.derniere_run.erreur}</td></tr>
									{/if}
								</tbody>
							</table>
						</section>
					{/if}

					<!-- Source -->
					{#if selectedInfo}
						<section>
							<h3 class="mb-2 border-b-2 border-bronze-light pb-1 text-xs font-bold uppercase tracking-wider text-bronze">
								Source gouvernementale
							</h3>
							<table class="w-full text-sm">
								<tbody>
									<tr><td class="w-2/5 py-1.5 font-medium text-ink-muted">Producteur</td><td>{selectedInfo.producteur}</td></tr>
									<tr><td class="py-1.5 font-medium text-ink-muted">Type</td><td>{selectedInfo.type === 'proxy' ? 'API temps reel (proxy)' : 'ETL (import periodique)'}</td></tr>
									<tr><td class="py-1.5 font-medium text-ink-muted">URL</td><td><a href={selectedInfo.url} target="_blank" rel="noopener noreferrer" class="text-blue-600 hover:underline">{selectedInfo.url}</a></td></tr>
								</tbody>
							</table>
						</section>
					{/if}

					<!-- data.gouv.fr comparison -->
					{#if selectedMeta}
						{@const localAge = daysSince(selectedDs.derniere_maj)}
						{@const srcAge = daysSince(selectedMeta.last_modified)}
						{@const diff = localAge != null && srcAge != null ? localAge - srcAge : null}
						<section>
							<h3 class="mb-2 border-b-2 border-bronze-light pb-1 text-xs font-bold uppercase tracking-wider text-bronze">
								Comparaison data.gouv.fr
							</h3>
							<table class="w-full text-sm">
								<thead>
									<tr class="text-left text-xs font-bold uppercase text-ink-muted">
										<th class="w-2/5 py-2">Critere</th><th class="py-2">Local</th><th class="py-2">Source</th><th class="py-2">Verdict</th>
									</tr>
								</thead>
								<tbody>
									<tr class={diff != null ? (diff <= 1 ? 'bg-green-50' : 'bg-red-50') : ''}>
										<td class="py-1.5 font-medium text-ink-muted">MAJ</td>
										<td class="py-1.5">{fmtDate(selectedDs.derniere_maj)}</td>
										<td class="py-1.5">{fmtDate(selectedMeta.last_modified)}</td>
										<td class="py-1.5 font-semibold">
											{#if diff != null}
												{#if diff <= 1}
													<span class="text-green-600">&#10003; A jour</span>
												{:else}
													<span class="text-red-600">&#10007; {diff}j retard</span>
												{/if}
											{:else}
												&mdash;
											{/if}
										</td>
									</tr>
									<tr>
										<td class="py-1.5 font-medium text-ink-muted">Taille source</td>
										<td class="py-1.5"></td>
										<td class="py-1.5">{fmtSize(selectedMeta.resources.reduce((s, r) => s + (r.filesize || 0), 0))}</td>
										<td class="py-1.5">{selectedMeta.resources.length} fichier(s)</td>
									</tr>
								</tbody>
							</table>
						</section>
					{/if}
				</div>
			</div>
		</div>
	{/if}
