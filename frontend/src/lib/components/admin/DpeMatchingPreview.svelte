<script lang="ts">
	import CommuneSearch from '$lib/components/CommuneSearch.svelte';
	import { getDpeDocument, getDpeInventory, type DpeConsultedDocument, type DpeInventoryPage } from '$lib/api/dpe-inventory';
	import { resolveBienParAdresse } from '$lib/api/resolve';
	import DpeDocument from './DpeDocument.svelte';
	import { createDpeMatchingClient, DpeMatchingRequestError, type DpeMatchResult, type DpeMatchTarget, type DpeSurfaceNature } from '$lib/api/dpe-matching';
	import type { ResultatRechercheAdresse } from '$lib/api/adresses';
	import { libelleCommune, libelleVoie } from '$lib/data/recherche-lieux';

	type Compare = ReturnType<typeof createDpeMatchingClient>['compare'];
	interface Props { initialContextRef?:string|null; compare?:Compare; isAdmin?:boolean; isProduction?:boolean; onReturnInventory?:()=>void|Promise<void> }
	let { initialContextRef=null, compare=createDpeMatchingClient().compare, isAdmin=false, isProduction=true, onReturnInventory=()=>{} }:Props=$props();
	let contextRef=$state<string|null>(null);
	let inventory=$state<DpeInventoryPage|null>(null);
	let addressLabel=$state<string|null>(null);
	let result=$state<DpeMatchResult|null>(null);
	let document=$state<DpeConsultedDocument|null>(null);
	let documentState=$state<'idle'|'loading'|'ready'|'error'|'refresh_required'>('idle');
	let selectedRef=$state<string|null>(null);
	let consultationTrigger = $state<HTMLElement | null>(null);
	let requestId=0;
	$effect(() => { requestId+=1; contextRef=initialContextRef; result=null; document=null; documentState='idle'; });
	let typeBien=$state<''|'maison'|'appartement'>('');
	let surface = $state<number | undefined>(undefined);
	let nature=$state<DpeSurfaceNature>('habitable_dpe');
	let loading=$state(false);
	let error=$state<string|null>(null);
	let page=$state(1);
	const enabled=$derived(isAdmin&&!isProduction);
	const stateLabels:Record<string,string>={matching:'Valeur identique',known:'Écart constaté',conflicting:'Valeur différente',unknown:'Donnée source non renseignée',not_applicable:'Critère cible non renseigné',not_comparable:'Nature de surface non comparable'};
	const limitLabels:Record<string,string>={dwelling_identity_unproven:'L’identité du logement reste non prouvée.',address_association_unverified:'Association à l’adresse non vérifiée.',source_coverage_unknown:'Couverture de la source inconnue.',graph_incoherent:'Chaîne documentaire incohérente.',inventory_partial:'Inventaire partiel.',candidates_outside_page:'D’autres candidats existent hors de cette page.',candidate_count_unknown:'Nombre total de candidats inconnu.',surface_has_no_calibrated_threshold:'Aucun seuil de surface n’est calibré.'};
	const blockerLabels:Record<string,string>={address_unverified:'Adresse non vérifiée.',source_coverage_unknown:'Couverture source inconnue.',source_state_unknown:'État source inconnu.',source_absent:'Document absent de la source complète.',source_deactivated:'Document désactivé dans la source.',validity_unknown:'Validité inconnue.',validity_expired:'Validité expirée.',validity_inconsistent:'Dates de validité incohérentes.',validity_future:'Date d’établissement future.',replacement_unchecked:'Remplacement non vérifié.',replacement_replaced:'Document remplacé.',replacement_ambiguous:'Remplacement ambigu.',graph_incoherent:'Chaîne documentaire incohérente.',inventory_partial:'Inventaire partiel.'};

	function invalidate() {
		requestId += 1;
		result = null;
		document = null;
		documentState = 'idle';
		consultationTrigger = null;
		error = null;
		loading = false;
		page = 1;
	}

	// The address comes from this instance's search field (04-12); the resolver keeps receiving the
	// « {numéro}{indice} {voie}, {CP} {Commune} » label of the former field.
	async function selectAddress(address: ResultatRechercheAdresse) {
		invalidate();
		const current = requestId;
		const label = `${libelleVoie(address.numero, address.rep, address.nom_voie)}, ${libelleCommune(address.code_postal, address.nom_commune)}`;
		contextRef = null;
		inventory = null;
		addressLabel = label;
		loading = true;

		try {
			const response = await resolveBienParAdresse(label);
			if (current !== requestId) return;
			if (response.donnees.length !== 1) {
				error = 'Cette adresse ne correspond pas à un bien unique. Précisez la sélection.';
				return;
			}

			const embedded = response.donnees[0].dpe_inventory;
			if (!embedded?.context_ref) {
				error = 'L’inventaire DPE ne fournit pas de contexte comparable.';
				return;
			}

			const fresh = await getDpeInventory(embedded.context_ref, null, 20);
			if (current !== requestId) return;
			inventory = fresh;
			contextRef = fresh.context_ref;
			if (!contextRef) error = 'Le contexte DPE doit être actualisé.';
		} catch {
			if (current === requestId) error = 'L’inventaire DPE est momentanément indisponible.';
		} finally {
			if (current === requestId) loading = false;
		}
	}

	function target(): DpeMatchTarget {
		const next: DpeMatchTarget = {};
		if (typeBien) Object.assign(next, { type_bien: typeBien });
		if (typeof surface === 'number' && Number.isFinite(surface) && surface > 0) {
			Object.assign(next, { surface: { value: surface, nature } });
		}
		return next;
	}

	async function submit(cursor: string | null = null) {
		if (!contextRef) return;
		const current = ++requestId;
		loading = true;
		error = null;
		try {
			const next = await compare(contextRef, target(), cursor, 20);
			if (current === requestId && next) {
				result = next;
				inventory = next.inventory;
				if (cursor) page += 1;
			}
		} catch (reason) {
			if (current === requestId) {
				if (reason instanceof DpeMatchingRequestError && reason.kind === 'refresh_required') {
					contextRef = null;
					result = null;
					error = 'Le contexte DPE a changé. Sélectionnez de nouveau l’adresse.';
				} else {
					error = 'La comparaison est momentanément indisponible. L’inventaire ne constitue pas une absence.';
				}
			}
		} finally {
			if (current === requestId) loading = false;
		}
	}

	async function returnToInventory() {
		invalidate();
		await onReturnInventory();
	}

	function closeDocument() {
		requestId += 1;
		document = null;
		documentState = 'idle';
		selectedRef = null;
		consultationTrigger = null;
	}

	async function consult(reference: string, trigger?: HTMLElement) {
		if (!contextRef) return;
		if (trigger) consultationTrigger = trigger;
		const requestedContext = contextRef;
		const current = ++requestId;
		selectedRef = reference;
		document = null;
		documentState = 'loading';
		try {
			const nextDocument = await getDpeDocument(requestedContext, reference);
			if (current !== requestId || selectedRef !== reference || contextRef !== requestedContext) return;
			document = nextDocument;
			documentState = 'ready';
		} catch {
			if (current === requestId && selectedRef === reference && contextRef === requestedContext) {
				documentState = 'error';
			}
		}
	}
</script>

{#if !enabled}
	<p role="status">Cet aperçu privé est indisponible dans cet environnement.</p>
{:else}
	<section class="preview" aria-labelledby="matching-title">
<header>
<p class="eyebrow">Aperçu privé · non adopté</p>
<h1 id="matching-title">Comparaison factuelle DPE</h1>
<p>Compare chaque document connu sans attribuer de diagnostic au logement.</p>
</header>
<div class="address">
<p class="address-label">Adresse de test (44 ou 85)</p>
<CommuneSearch types={['adresse']} onselectadresse={selectAddress} />{#if addressLabel}<p class="selected">Adresse sélectionnée : {addressLabel}</p>{/if}</div>
<form aria-label="Critères de comparaison" onsubmit={(event)=>{event.preventDefault();void submit();}}>
<label>Type de bien<select aria-label="Type de bien" bind:value={typeBien} onchange={invalidate} disabled={!contextRef}>
<option value="">Non renseigné</option>
<option value="maison">Maison</option>
<option value="appartement">Appartement</option>
</select>
</label>
<label>Surface<input aria-label="Surface" type="number" min="0.01" step="0.01" bind:value={surface} oninput={invalidate} disabled={!contextRef} />
</label>
<label>Nature de la surface<select aria-label="Nature de la surface" bind:value={nature} onchange={invalidate} disabled={!contextRef}>
<option value="habitable_dpe">Habitable DPE</option>
<option value="carrez">Carrez</option>
<option value="annonce">Annonce</option>
<option value="dvf">DVF</option>
<option value="autre">Autre</option>
</select>
</label>
<div class="actions">
<button class="primary" type="submit" disabled={!contextRef||loading}>Comparer les documents</button>
<button type="button" onclick={returnToInventory}>Retour à l’inventaire</button>
</div>
</form>
		{#if loading}<p role="status">Chargement…</p>{/if}{#if error}<p class="error" role="alert">{error}</p>{/if}
		{#if inventory&&!result}<table>
<caption>Inventaire DPE conservé</caption>
<thead>
<tr>
<th scope="col">Document</th>
<th scope="col">DPE</th>
<th scope="col">Action</th>
</tr>
</thead>
<tbody>{#each inventory.items as item,index (item.document_ref)}<tr>
<th scope="row">Document {String(index+1).padStart(2,'0')}</th>
<td>{item.etiquette_dpe??'non renseigné'}</td>
<td>
<button type="button" disabled={loading} onclick={(event)=>consult(item.document_ref,event.currentTarget)}>Consulter</button>
</td>
</tr>{/each}</tbody>
</table>{/if}
		{#if result}
			<div class="facts">
<p>Règle : <span class="chiffre">{result.rule_version}</span>
</p>
<p>Page {page} · {result.comparisons.length} candidat(s) conservé(s)</p>{#each result.limits as limit}<p>{limitLabels[limit]}</p>{/each}</div>
<!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access is required for the horizontal scroll region) -->
<div
	class="comparison-scroll"
	role="region"
	aria-label="Tableau de comparaison, défilement horizontal"
	tabindex="0"
>
<table class="comparison-table">
<caption>Comparaison de tous les documents de la page</caption>
<thead>
<tr>
<th scope="col">Document</th>
<th scope="col">Type</th>
<th scope="col">Surface</th>
<th scope="col">Réserves</th>
<th scope="col">Action</th>
</tr>
</thead>
<tbody>{#each result.comparisons as comparison,index (comparison.document_ref)}<tr>
<th scope="row">Document {String(index+1).padStart(2,'0')}</th>{#each comparison.criteria as criterion}<td>
<strong>{stateLabels[criterion.state]}</strong>{#if criterion.criterion==='surface'&&criterion.delta!==null}<span class="chiffre">Écart : {criterion.delta} m²</span>{/if}</td>{/each}<td>{comparison.blockers.length?comparison.blockers.map((blocker)=>blockerLabels[blocker]).join(' '):'Aucune réserve supplémentaire'}</td>
<td>
<button type="button" disabled={loading} onclick={(event)=>consult(comparison.document_ref,event.currentTarget)}>Consulter</button>
</td>
</tr>{/each}</tbody>
</table>
</div>
			{#if result.inventory.has_more&&result.inventory.next_cursor}<button type="button" disabled={loading} onclick={()=>submit(result?.inventory.next_cursor??null)}>Page suivante</button>{/if}
		{/if}
		<DpeDocument {document} state={documentState} onRetry={()=>selectedRef?consult(selectedRef):undefined} onClose={closeDocument} returnFocusTo={consultationTrigger} />
</section>
{/if}

<style>
.preview {
	display:grid;
	grid-template-columns:minmax(0,1fr);
	gap:24px;
	min-width:0;
	max-width:100%;
	color:var(--color-ink)
}
 header p,h1 {
	margin:0
}
.eyebrow {
	font-size:12px;
	font-weight:600;
	letter-spacing:.08em;
	text-transform:uppercase;
	color:var(--color-ink-muted)
}
h1 {
	font-family:var(--font-display);
	font-size:clamp(28px,7vw,44px);
	line-height:1.1
}
.address,form,.facts {
	display:grid;
	gap:12px;
	padding:16px;
	border:1px solid var(--color-stone-border);
	border-radius:8px;
	background:var(--color-parchment)
}
.address {
	grid-template-columns:minmax(0,1fr);
	min-width:0
}
label {
	display:grid;
	gap:4px;
	font-size:14px;
	font-weight:600
}
input,select,button {
	min-height:44px;
	border:1px solid var(--color-stone-border);
	border-radius:8px;
	background:var(--color-marble);
	color:var(--color-ink);
	padding:8px 12px
}
button {
	font-weight:600;
	cursor:pointer
}
.primary {
	border:0;
	background:var(--color-bronze);
	color:var(--color-white)
}
button:disabled {
	opacity:.5;
	cursor:default
}
.actions {
	display:flex;
	flex-wrap:wrap;
	gap:8px
}
.selected,.facts p {
	margin:0;
	font-size:12px;
	color:var(--color-ink-muted)
}
.error {
	padding:12px;
	border-left:3px solid var(--color-terracotta);
	background:var(--color-parchment)
}
table {
	width:100%;
	border-collapse:collapse;
	table-layout:fixed;
	font-size:12px
}
.comparison-scroll {
	min-width: 0;
	max-width: 100%;
	overflow-x: auto;
	overscroll-behavior-inline: contain;
}
.comparison-scroll:focus-visible {
	outline: 2px solid var(--color-bronze-dark);
	outline-offset: 3px;
}
.comparison-table {
	min-width: 680px;
}
.comparison-table button {
	white-space: nowrap;
}
caption {
	text-align:left;
	font-weight:600;
	font-size:14px;
	margin-bottom:8px
}
th,td {
	padding:10px 4px;
	border-bottom:1px solid var(--color-hairline);
	text-align:left;
	vertical-align:top;
	overflow-wrap:anywhere
}
td strong,td span {
	display:block
}
td span {
	margin-top:4px
}
@media(max-width:480px) {
.actions button {
	width:100%
}
}

</style>
