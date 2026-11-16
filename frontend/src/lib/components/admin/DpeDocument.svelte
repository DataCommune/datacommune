<script lang="ts">
	import type { DpeConsultedDocument } from '$lib/api/dpe-inventory';
	import {
		deriveDpeDocumentView,
		deriveDpeLifecycleRows,
		type DpeDocumentUiStatus
	} from '$lib/data/dpe-inventory-view';

	type Callback = () => void | Promise<void>;

	interface Props {
		document: DpeConsultedDocument | null;
		state: DpeDocumentUiStatus;
		presentationLabel?: string | null;
		onRetry: Callback;
		onClose: Callback;
		returnFocusTo?: HTMLElement | null;
	}

	let {
		document,
		state: documentState,
		presentationLabel = null,
		onRetry,
		onClose,
		returnFocusTo = null
	}: Props = $props();

	let titleElement = $state<HTMLHeadingElement>();
	const view = $derived(document == null ? null : deriveDpeDocumentView(document));
	const lifecycleRows = $derived(document == null ? [] : deriveDpeLifecycleRows(document));
	const reconciled = $derived(document?.lifecycle?.detail_availability === 'source_reconciled');
	const revisionLabel = $derived.by(() => {
		const cycle = document?.lifecycle;
		if (cycle?.revision_status === 'verified_attestation' && cycle.revision_date != null) {
			const date = new Intl.DateTimeFormat('fr-FR', {
				day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC'
			}).format(new Date(cycle.revision_date));
			return `Étiquette issue d’une attestation — ${date}`;
		}
		return 'Étiquette fournie par la source. La prise en compte d’une attestation de révision n’est pas établie avec les données disponibles.';
	});

	$effect(() => {
		if (documentState === 'idle') return;
		void document;
		queueMicrotask(() => {
			titleElement?.focus();
			titleElement?.scrollIntoView?.({ block: 'nearest'	});
		});
	});

	async function close(): Promise<void> {
		const preferredDestination = returnFocusTo;
		await onClose();
		queueMicrotask(() => {
			const destination = preferredDestination?.isConnected
				? preferredDestination
				: globalThis.document?.getElementById('dpe-inventory-title');
			destination?.focus();
		});
	}
</script>

{#if documentState !== 'idle'}
	<section
		id="dpe-consulted-document"
		class="dpe-document print:hidden"
		aria-labelledby="dpe-document-title"
		aria-busy={documentState === 'loading'}
	>
		<header class="document-header">
			<div>
				<p class="eyebrow">Consultation documentaire</p>
				<h2 id="dpe-document-title" tabindex="-1" bind:this={titleElement}>
					Diagnostic consulté
				</h2>
				{#if presentationLabel}
					<p class="presentation-label chiffre">{presentationLabel}</p>
				{/if}
			</div>
			<button type="button" class="close" onclick={close}>Fermer et revenir à la liste</button>
		</header>

		<p class="reserve">Ce document n'est pas attribué au logement recherché</p>

		{#if documentState === 'loading'}
			<p class="loading" role="status">Chargement du document…</p>
		{:else if documentState === 'error' || (documentState === 'ready' && view == null)}
			<div class="request-state" role="status">
				<p>Le détail de ce document est momentanément indisponible.</p>
				<button type="button" class="retry" onclick={onRetry}>Réessayer le détail</button>
			</div>
		{:else if documentState === 'refresh_required'}
			<div class="request-state" role="status">
				<p>La référence du document doit être actualisée avant une nouvelle consultation.</p>
				<button type="button" class="retry" onclick={onRetry}>Réessayer le détail</button>
			</div>
		{:else if view}
			<dl class="details">
				{#each [...(reconciled ? [] : view.rows), ...lifecycleRows] as row (row.label)}
					<div class="detail-row">
						<dt>{row.label}</dt>
						<dd class="chiffre">
							{#if row.energyClass}
								<span class={`dpe-badge sm dpe-${row.energyClass}`}>{row.energyClass}</span>
								<span class="sr-only">{row.value}</span>
							{:else}
								{row.value}
							{/if}
						</dd>
					</div>
				{/each}
			</dl>

			<div class="document-notes">
				{#if document?.lifecycle == null}
					<p>Les informations reçues ne permettent pas de décrire le cycle de vie.</p>
					<p>{view.validityNote}</p>
					<p>{view.graphNote}</p>
				{/if}
				{#if !reconciled}<p>{revisionLabel}</p>{/if}
				<p>{view.addressCheckLabel}</p>
				<p>{view.sourceLabel}</p>
				<p>{view.importedAtLabel}</p>
			</div>
		{/if}
	</section>
{/if}

<style>
	.dpe-document {
		--space-2: 8px;
		--space-3: 12px;
		--space-4: 16px;
		--space-6: 24px;
		min-width: 0;
		margin-top: var(--space-6);
		padding-top: var(--space-4);
		border-top: 3px solid var(--color-bronze);
		color: var(--color-ink);
	}
	.document-header {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		justify-content: space-between;
		gap: var(--space-3);
	}
	.eyebrow {
		margin: 0 0 4px;
		font-size: 12px;
		font-weight: 600;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--color-ink-muted);
	}
	.presentation-label {
		margin: 4px 0 0;
		font-size: 14px;
		font-weight: 600;
		color: var(--color-bronze-dark);
	}
	h2 {
		margin: 0;
		font-size: 20px;
		font-weight: 700;
		line-height: 1.3;
	}
	.close,
	.retry {
		min-height: 44px;
		min-width: 44px;
		max-width: 100%;
		overflow-wrap: anywhere;
		padding: 0 var(--space-4);
		border: 1px solid var(--color-stone-border);
		border-radius: 8px;
		background: var(--color-marble);
		font-size: 14px;
		font-weight: 600;
		color: var(--color-bronze-dark);
		cursor: pointer;
	}
	.reserve {
		margin: var(--space-4) 0;
		padding: var(--space-3) var(--space-4);
		border-left: 3px solid var(--color-bronze);
		background: var(--color-parchment);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.5;
	}
	.loading,
	.request-state {
		margin: var(--space-4) 0 0;
		padding: var(--space-4);
		border: 1px dashed var(--color-stone-border);
		border-radius: 8px;
		background: var(--color-parchment);
		font-size: 14px;
	}
	.request-state {
		display: grid;
		gap: var(--space-3);
	}
	.request-state p {
		margin: 0;
	}
	.retry {
		justify-self: start;
	}
	.details {
		margin: var(--space-4) 0 0;
	}
	.detail-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		gap: var(--space-4);
		padding: var(--space-3) 0;
		border-bottom: 1px solid var(--color-hairline);
	}
	dt,
	dd {
		min-width: 0;
		margin: 0;
		overflow-wrap: anywhere;
	}
	dt {
		font-size: 14px;
		color: var(--color-ink-muted);
	}
	dd {
		font-size: 14px;
		font-weight: 600;
		text-align: right;
	}
	.document-notes {
		display: grid;
		gap: 4px;
		margin-top: var(--space-4);
		padding-top: var(--space-3);
		border-top: 1px solid var(--color-hairline);
		font-size: 12px;
		line-height: 1.5;
		color: var(--color-ink-muted);
	}
	.document-notes p {
		margin: 0;
		overflow-wrap: anywhere;
	}
	@media (max-width: 374px) {
		.detail-row {
			grid-template-columns: minmax(0, 1fr);
			gap: 4px;
		}
		dd {
			text-align: left;
		}
		.close {
			width: 100%;
		}
	}
</style>
