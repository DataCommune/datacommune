<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listEtlPipelines,
		getEtlPipelineHistory,
		triggerEtlPipeline,
		type AdminEtlPipelineRow,
		type AdminEtlPipelineList,
		type EtlRunHistory
	} from '$lib/api/admin-etl';
	import EtlPipelineTable from '$lib/components/admin/EtlPipelineTable.svelte';
	import EtlRunHistoryTable from '$lib/components/admin/EtlRunHistoryTable.svelte';
	import TriggerPipelineModal from '$lib/components/admin/TriggerPipelineModal.svelte';

	let list = $state<AdminEtlPipelineList | null>(null);
	let drill = $state<EtlRunHistory | null>(null);
	let selected = $state<string | null>(null);
	let triggerTarget = $state<AdminEtlPipelineRow | null>(null);
	let toast = $state<string | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function loadList() {
		loading = true;
		error = null;
		try {
			list = await listEtlPipelines();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Erreur';
		} finally {
			loading = false;
		}
	}

	async function loadDrill(pipelineName: string, page = 1) {
		loading = true;
		error = null;
		try {
			drill = await getEtlPipelineHistory(pipelineName, page, 20);
			selected = pipelineName;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Erreur';
		} finally {
			loading = false;
		}
	}

	function backToList() {
		selected = null;
		drill = null;
	}

	async function handleTrigger(reason: string) {
		if (!triggerTarget) return;
		const name = triggerTarget.pipeline;
		try {
			await triggerEtlPipeline(name, reason);
			triggerTarget = null;
			toast = `Pipeline ${name} déclenché — ignoré si déjà en cours`;
			setTimeout(() => (toast = null), 6000);
			await loadList();
		} catch (e) {
			throw e; // modal displays it
		}
	}

	onMount(loadList);

	let totalPages = $derived(drill ? Math.ceil(drill.total / (drill.page_size || 20)) : 0);
</script>

<svelte:head><title>Ops ETL — Admin — DataCommune</title></svelte:head>

<h1 class="mb-4 font-display text-2xl font-semibold text-ink">Ops ETL</h1>

{#if toast}
	<p class="mb-3 rounded border border-emerald-300 bg-emerald-50 p-2 text-sm text-emerald-800">
		{toast}
	</p>
{/if}
{#if error}
	<p class="mb-3 rounded border border-red-300 bg-red-50 p-2 text-sm text-red-800">{error}</p>
{/if}

{#if selected && drill}
	<button class="mb-3 text-sm text-bronze hover:underline" onclick={backToList}>← Retour</button>
	<h2 class="mb-2 text-xl font-semibold text-ink">Historique — {drill.pipeline}</h2>
	<p class="mb-2 text-xs text-ink-muted">{drill.total} runs</p>
	<EtlRunHistoryTable rows={drill.rows} />
	{#if totalPages > 1}
		<div class="mt-3 flex items-center gap-2 text-sm">
			<button
				disabled={drill.page <= 1}
				onclick={() => loadDrill(drill!.pipeline, drill!.page - 1)}
				class="rounded border border-stone-border px-2 py-1 disabled:opacity-40"
			>Précédent</button>
			<span>Page {drill.page} / {totalPages}</span>
			<button
				disabled={drill.page >= totalPages}
				onclick={() => loadDrill(drill!.pipeline, drill!.page + 1)}
				class="rounded border border-stone-border px-2 py-1 disabled:opacity-40"
			>Suivant</button>
		</div>
	{/if}
{:else if loading && !list}
	<p class="text-ink-muted">Chargement…</p>
{:else if list}
	<p class="mb-2 text-xs text-ink-muted">{list.total} pipelines</p>
	<EtlPipelineTable
		rows={list.rows}
		onSelect={(name) => loadDrill(name)}
		onTrigger={(r) => (triggerTarget = r)}
	/>
{/if}

{#if triggerTarget}
	<TriggerPipelineModal
		pipelineName={triggerTarget.pipeline}
		onConfirm={handleTrigger}
		onCancel={() => (triggerTarget = null)}
	/>
{/if}
