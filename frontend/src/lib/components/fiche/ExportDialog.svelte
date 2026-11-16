<script lang="ts">
	// Export builder of the commune fiche (UI-SPEC « Télécharger (CSV) »): a modal window to choose
	// exactly which rows to export, grouped by theme in the fiche order, with their visible values
	// to keep the context. Every row keeps its served state, its grain and its caveats. Reuses
	// downloadCsv (UTF-8 BOM + neutralisation of formula injection, csv-escape.ts).
	//
	// R-01 (review of 2026-11-10): a native modal `<dialog>`, like the source panel (`SourcePanel`). The
	// browser makes the page behind inert and closes on Escape; the focus goes to the title on opening,
	// stays in the dialog (Tab wraps) and goes back to what had it on closing. The former `div` said
	// `aria-modal` and did none of it.
	import { untrack } from 'svelte';
	import type { GroupeExport } from '$lib/data/commune-grid';
	import { downloadCsv } from '$lib/utils/csv-download';
	import IconFermer from '$lib/components/ds/icons/IconFermer.svelte';
	import IconTelecharger from '$lib/components/ds/icons/IconTelecharger.svelte';

	interface Props {
		open: boolean;
		/** Themes in their order; the `ThemeModel` of the other fiches fits this shape. */
		themes: readonly GroupeExport[];
		filenameBase: string;
		onclose: () => void;
	}

	let { open, themes, filenameBase, onclose }: Props = $props();

	const titreId = $props.id();
	let dialog = $state<HTMLDialogElement>();
	let titreElement = $state<HTMLHeadingElement>();
	// What had the focus when opening: the closing gives it back there.
	let focusAvant: Element | null = null;

	type Item = {
		id: string;
		label: string;
		value: string;
		state: string;
		grain: string;
		context: string;
	};
	type Group = { key: string; name: string; items: Item[] };

	// Value and qualified absence rows are both evidence and remain selectable.
	// id = theme key + row index → stable, insensitive to duplicated labels.
	const groups = $derived<Group[]>(
		themes
			.map((t) => ({
				key: t.key,
				name: t.name,
				items: t.rows.map((r, i) => ({
					id: t.key + '::' + i,
					label: r.label,
					value: r.value,
					state: r.kind === 'context'
						? 'context'
						: (r.state ?? r.presentation?.state ?? (r.available === false ? 'not_reported' : 'value')),
					grain: r.presentation?.grain ?? 'Maille non renseignée',
					context: (r.presentation?.reserves ?? []).join(' · ')
				}))
			}))
			.filter((g) => g.items.length > 0)
	);

	let selected = $state<Record<string, boolean>>({});

	// Every row checked at each opening: the complete export stays the default, the visitor
	// unchecks what they do not want.
	$effect(() => {
		if (open) {
			const next: Record<string, boolean> = {};
			for (const g of groups) for (const it of g.items) next[it.id] = true;
			selected = next;
		}
	});

	const selectedCount = $derived(
		groups.reduce((n, g) => n + g.items.filter((it) => selected[it.id]).length, 0)
	);

	function setAll(value: boolean) {
		const next: Record<string, boolean> = {};
		for (const g of groups) for (const it of g.items) next[it.id] = value;
		selected = next;
	}

	function groupState(g: Group): 'all' | 'none' | 'some' {
		const on = g.items.filter((it) => selected[it.id]).length;
		if (on === 0) return 'none';
		if (on === g.items.length) return 'all';
		return 'some';
	}

	function toggleGroup(g: Group) {
		const target = groupState(g) !== 'all'; // not all checked → check all; otherwise uncheck all
		for (const it of g.items) selected[it.id] = target;
	}

	function download() {
		const rows: string[][] = [];
		for (const g of groups)
			for (const it of g.items) if (selected[it.id]) rows.push([
				g.name, it.label, it.value, it.state, it.grain, it.context
			]);
		if (rows.length === 0) return;
		downloadCsv(
			['Thème', 'Indicateur', 'Valeur ou motif', 'État', 'Maille', 'Période, population, source et réserves'],
			rows,
			filenameBase + '-export.csv'
		);
		fermer();
	}

	// The dialog exists only while `open`: it is shown as a modal as soon as it is in the page.
	$effect(() => {
		const element = dialog;
		if (!element) return;
		untrack(() => {
			focusAvant = document.activeElement;
			if (typeof element.showModal === 'function') element.showModal();
			else element.setAttribute('open', '');
			titreElement?.focus();
		});
	});

	/** Every way out (« Fermer », « Annuler », the backdrop, a download) goes through the native close. */
	function fermer() {
		if (dialog?.open && typeof dialog.close === 'function') dialog.close();
		else surFermeture();
	}

	function surFermeture() {
		// A native modal has already given the focus back; only a focus still left in the dialog or
		// dropped to the body is brought back (a click does not always focus its button: Safari).
		const actif = document.activeElement;
		const perdu = !actif || actif === document.body || dialog?.contains(actif);
		if (perdu && focusAvant instanceof HTMLElement && focusAvant !== document.body) focusAvant.focus();
		focusAvant = null;
		onclose();
	}

	function surClic(event: MouseEvent) {
		// The content fills the box of the dialog: only the backdrop targets the dialog itself.
		if (event.target === dialog) fermer();
	}

	function surTouche(event: KeyboardEvent) {
		// A native modal dialog closes itself on Escape; this covers the environments without one.
		if (event.key === 'Escape' && typeof dialog?.showModal !== 'function') {
			event.preventDefault();
			fermer();
			return;
		}
		// The focus stays in the dialog: Tab from its last control comes back to the first one (a modal
		// dialog makes the page inert, but lets the focus leave for the browser chrome).
		if (event.key !== 'Tab' || !dialog) return;
		const focusables = [...dialog.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled])')];
		if (focusables.length === 0) return;
		const premier = focusables[0];
		const dernier = focusables[focusables.length - 1];
		const actif = document.activeElement;
		if (event.shiftKey && (actif === premier || actif === titreElement)) {
			event.preventDefault();
			dernier.focus();
		} else if (!event.shiftKey && actif === dernier) {
			event.preventDefault();
			premier.focus();
		}
	}
</script>

{#if open}
	<!-- Keyboard closing: Escape (native modal dialog, or surTouche), « Fermer » and « Annuler ». -->
	<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_noninteractive_element_interactions -->
	<dialog
		bind:this={dialog}
		class="export-dialog"
		aria-labelledby={titreId}
		onclose={surFermeture}
		onkeydown={surTouche}
		onclick={surClic}
	>
		<div class="export-boite">
			<div class="export-head">
				<h2 id={titreId} class="export-title" tabindex="-1" bind:this={titreElement}>Exporter les données</h2>
				<button type="button" class="export-icon-button" onclick={fermer} aria-label="Fermer">
					<IconFermer class="export-icon" />
				</button>
			</div>

			<div class="export-toolbar">
				<button type="button" class="export-link" onclick={() => setAll(true)}>Tout cocher</button>
				<button type="button" class="export-link" onclick={() => setAll(false)}>Tout décocher</button>
				<span class="export-count">{selectedCount} sélectionné{selectedCount > 1 ? 's' : ''}</span>
			</div>

			<div class="export-list">
				{#each groups as g (g.key)}
					{@const gs = groupState(g)}
					<fieldset class="export-group">
						<legend class="export-group-legend">
							<label class="export-row export-row-group">
								<input
									type="checkbox"
									checked={gs === 'all'}
									indeterminate={gs === 'some'}
									onchange={() => toggleGroup(g)}
								/>
								<span>{g.name}</span>
							</label>
						</legend>
						{#each g.items as it (it.id)}
							<label class="export-row">
								<input type="checkbox" bind:checked={selected[it.id]} />
								<span class="export-label">{it.label}</span>
								<span class="export-value">{it.value}</span>
							</label>
						{/each}
					</fieldset>
				{/each}
			</div>

			<div class="export-foot">
				<button type="button" class="export-secondary" onclick={fermer}>Annuler</button>
				<button type="button" class="export-primary" onclick={download} disabled={selectedCount === 0}>
					<IconTelecharger class="export-icon" />
					Télécharger ({selectedCount})
				</button>
			</div>
		</div>
	</dialog>
{/if}

<style>
	/* Centred in the window by the browser (a modal dialog: `inset: 0` and automatic margins), 16 px
	   from its edges at least. The box inside takes the whole dialog, so a click on the dialog itself
	   is a click on the backdrop. */
	.export-dialog {
		box-sizing: border-box;
		width: min(552px, calc(100% - 2 * var(--s4)));
		max-width: none;
		max-height: 90vh;
		padding: 0;
		overflow: hidden;
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-panneau);
		color: var(--color-ink);
	}

	.export-dialog::backdrop {
		background: color-mix(in srgb, var(--color-ink) 40%, transparent);
	}

	.export-boite {
		display: flex;
		flex-direction: column;
		max-height: calc(90vh - 2px);
	}

	/* The title takes the focus on opening, as in the source panel: a landing point, not a control. */
	.export-title:focus {
		outline: none;
	}

	.export-head,
	.export-foot {
		display: flex;
		align-items: center;
		gap: var(--s2);
		padding: var(--s4) var(--s5);
	}

	.export-head {
		justify-content: space-between;
		border-bottom: 1px solid var(--color-line);
	}

	.export-foot {
		justify-content: flex-end;
		border-top: 1px solid var(--color-line);
	}

	.export-title {
		margin: 0;
		font-size: 20px;
		font-weight: 600;
		line-height: 1.2;
	}

	.export-toolbar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s4);
		padding: var(--s2) var(--s5);
		border-bottom: 1px solid var(--color-line);
		background: var(--color-soft);
		font-size: 14px;
		line-height: 1.4;
	}

	.export-count {
		margin-left: auto;
		color: var(--color-muted);
	}

	.export-list {
		flex: 1;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
		padding: var(--s3) var(--s5);
	}

	.export-group {
		margin: 0 0 var(--s4);
		padding: 0;
		border: 0;
	}

	.export-group:last-child {
		margin-bottom: 0;
	}

	.export-group-legend {
		width: 100%;
		padding: 0;
	}

	.export-row {
		display: flex;
		align-items: center;
		gap: var(--s2);
		min-height: 44px;
		padding: 0 var(--s1);
		border-radius: var(--radius-control);
		font-size: 16px;
		line-height: 1.5;
		cursor: pointer;
	}

	.export-row:hover {
		background: var(--color-soft);
	}

	.export-row-group {
		border-bottom: 1px solid var(--color-line);
		border-radius: 0;
		font-weight: 600;
	}

	.export-row input {
		flex: none;
		width: 16px;
		height: 16px;
		margin: 0;
	}

	.export-label {
		flex: 1;
		min-width: 0;
		color: var(--color-muted);
		overflow-wrap: anywhere;
	}

	.export-value {
		flex: none;
		max-width: 45%;
		font-weight: 600;
		text-align: right;
		font-variant-numeric: tabular-nums;
		overflow-wrap: anywhere;
	}

	.export-link,
	.export-icon-button,
	.export-secondary,
	.export-primary {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: var(--s2);
		min-height: 44px;
		border-radius: var(--radius-control);
		font-family: inherit;
		font-size: inherit;
		font-weight: inherit;
		font-style: inherit;
		line-height: inherit;
		cursor: pointer;
	}

	.export-link {
		padding: 0;
		border: 0;
		background: transparent;
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	.export-icon-button {
		min-width: 44px;
		padding: 0;
		border: 0;
		background: transparent;
		color: var(--color-ink);
	}

	.export-icon-button:hover {
		background: var(--color-soft);
	}

	.export-secondary,
	.export-primary {
		padding: 0 var(--s4);
		font-size: 16px;
	}

	.export-secondary {
		border: 1px solid var(--color-line-strong);
		background: var(--color-paper);
		color: var(--color-ink);
	}

	.export-secondary:hover {
		background: var(--color-soft);
	}

	.export-primary {
		border: 1px solid var(--color-accent);
		background: var(--color-accent);
		color: var(--color-paper);
		font-weight: 600;
	}

	.export-primary:hover {
		background: var(--color-accent-hover);
	}

	.export-primary:disabled {
		cursor: not-allowed;
		opacity: 0.5;
	}

	.export-dialog :global(.export-icon) {
		width: 16px;
		height: 16px;
	}

	@media (max-width: 720px) {
		/* A sheet at the bottom of the window, full width. */
		.export-dialog {
			width: 100%;
			margin: auto 0 0;
			border-radius: var(--radius-panneau) var(--radius-panneau) 0 0;
		}

		.export-head,
		.export-foot,
		.export-list {
			padding-left: var(--s4);
			padding-right: var(--s4);
		}

		.export-toolbar {
			padding: var(--s2) var(--s4);
		}
	}

	@media print {
		.export-dialog[open] {
			display: none;
		}
	}
</style>
