<script lang="ts">
	import {
		getAdminUserDetail,
		toggleAdmin,
		type AdminUserDetailResponse
	} from '$lib/api/admin-users';
	import { ApiError } from '$lib/api/client';
	import { auth } from '$lib/stores/auth.svelte';
	import GrantPlanModal from './GrantPlanModal.svelte';
	import ResetQuotaModal from './ResetQuotaModal.svelte';
	import RevokeKeysModal from './RevokeKeysModal.svelte';
	import ResendVerifModal from './ResendVerifModal.svelte';
	import HeatmapGrid from './HeatmapGrid.svelte';
	import { getAdminUserHeatmap, type AdminUserHeatmap } from '$lib/api/admin-overview';

	interface Props {
		userId: number | null;
		onClose: () => void;
		onChanged: () => void;
	}

	let { userId, onClose, onChanged }: Props = $props();

	let detail = $state<AdminUserDetailResponse | null>(null);
	let loading = $state(false);
	let fetchError = $state<string | null>(null);

	// Grant plan modal state
	let showGrantModal = $state(false);

	// Action modal states
	let showResetQuotaModal = $state(false);
	let showRevokeKeysModal = $state(false);
	let showResendVerifModal = $state(false);

	// Toggle admin inline state
	let showToggleConfirm = $state(false);
	let toggleReason = $state('');
	let toggleLoading = $state(false);
	let toggleError = $state<string | null>(null);
	const toggleReasonTooShort = $derived(toggleReason.length > 0 && toggleReason.length < 10);
	const canToggle = $derived(toggleReason.length >= 10 && !toggleLoading);

	// Self-protection: disable actions if viewing own user
	const isSelf = $derived(auth.user?.id === userId);

	// Heatmap state (Phase 17)
	let heatmap = $state<AdminUserHeatmap | null>(null);
	let heatmapLoading = $state(false);
	let heatmapError = $state<string | null>(null);

	// Fetch detail whenever userId changes
	$effect(() => {
		if (userId === null) {
			detail = null;
			return;
		}
		const id = userId;
		loading = true;
		fetchError = null;
		showGrantModal = false;
		showResetQuotaModal = false;
		showRevokeKeysModal = false;
		showResendVerifModal = false;
		showToggleConfirm = false;
		toggleReason = '';
		toggleError = null;

		getAdminUserDetail(id)
			.then((d) => {
				detail = d;
			})
			.catch((e) => {
				fetchError = e instanceof ApiError ? e.detail : 'Erreur de chargement';
			})
			.finally(() => {
				loading = false;
			});
	});

	// Fetch heatmap whenever userId changes (Phase 17)
	$effect(() => {
		if (userId === null) {
			heatmap = null;
			return;
		}
		const id = userId;
		heatmapLoading = true;
		heatmapError = null;
		getAdminUserHeatmap(id)
			.then((r) => {
				heatmap = r;
			})
			.catch((e) => {
				heatmapError = e instanceof ApiError ? e.detail : 'Erreur de chargement';
				heatmap = null;
			})
			.finally(() => {
				heatmapLoading = false;
			});
	});

	async function refetch() {
		if (!userId) return;
		loading = true;
		fetchError = null;
		try {
			detail = await getAdminUserDetail(userId);
		} catch (e) {
			fetchError = e instanceof ApiError ? e.detail : 'Erreur de chargement';
		} finally {
			loading = false;
		}
	}

	function handleGrantSuccess() {
		showGrantModal = false;
		refetch();
		onChanged();
	}

	function handleActionSuccess() {
		showResetQuotaModal = false;
		showRevokeKeysModal = false;
		showResendVerifModal = false;
		refetch();
		onChanged();
	}

	async function handleToggleAdmin() {
		if (!detail || !canToggle) return;
		toggleLoading = true;
		toggleError = null;
		try {
			await toggleAdmin(detail.id, {
				is_admin: !detail.is_admin,
				reason: toggleReason
			});
			showToggleConfirm = false;
			toggleReason = '';
			refetch();
			onChanged();
		} catch (e) {
			if (e instanceof ApiError) {
				toggleError = e.detail;
			} else {
				toggleError = 'Erreur inattendue.';
			}
		} finally {
			toggleLoading = false;
		}
	}

	// Format helpers
	function fmtDate(d: string | null) {
		if (!d) return 'Jamais';
		return new Date(d).toLocaleDateString('fr-FR', {
			day: '2-digit',
			month: 'short',
			year: 'numeric'
		});
	}
	function fmtDateTime(d: string | null) {
		if (!d) return 'Jamais';
		const dt = new Date(d);
		return (
			dt.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' }) +
			' ' +
			dt.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
		);
	}
</script>

{#if userId !== null}
	<!-- Backdrop -->
	<div
		class="fixed inset-0 z-30 bg-ink/20"
		onclick={onClose}
		onkeydown={(e) => {
			if (e.key === 'Escape') onClose();
		}}
		role="button"
		tabindex="-1"
		aria-label="Fermer le panneau"
	></div>

	<!-- Drawer -->
	<div
		class="fixed right-0 top-0 z-40 flex h-full w-[min(560px,100vw)] flex-col overflow-y-auto border-l border-stone-border bg-parchment shadow-xl"
	>
		<!-- Header -->
		<div class="sticky top-0 z-10 flex items-center justify-between border-b border-stone-border bg-parchment px-5 py-4">
			<div>
				<h2 class="font-semibold text-ink">{detail?.email ?? '…'}</h2>
				<div class="mt-0.5 flex gap-1.5">
					{#if detail?.is_admin}
						<span class="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-800">
							Admin
						</span>
					{/if}
					{#if detail && !detail.email_verified}
						<span class="rounded-full bg-stone-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-stone-500">
							Non vérifié
						</span>
					{/if}
					{#if isSelf}
						<span class="rounded-full bg-blue-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-blue-700">
							Vous
						</span>
					{/if}
				</div>
			</div>
			<button
				class="text-2xl text-ink-muted transition-colors hover:text-ink"
				onclick={onClose}
				aria-label="Fermer"
			>&times;</button>
		</div>

		{#if loading}
			<div class="flex flex-1 items-center justify-center">
				<div class="h-6 w-6 animate-spin rounded-full border-2 border-stone-border border-t-bronze"></div>
			</div>
		{:else if fetchError}
			<div class="m-5 rounded border border-terracotta bg-terracotta/10 px-3 py-2 text-sm text-terracotta">
				{fetchError}
			</div>
		{:else if detail}
			<div class="flex-1 space-y-5 px-5 py-5">
				<!-- Informations -->
				<section>
					<h3 class="mb-2 border-b border-stone-border pb-1 text-xs font-bold uppercase tracking-wide text-ink-muted">
						Informations
					</h3>
					<table class="w-full text-sm">
						<tbody>
							<tr><td class="py-1 pr-4 font-medium text-ink-muted">ID</td><td>{detail.id}</td></tr>
							<tr><td class="py-1 pr-4 font-medium text-ink-muted">Nom</td><td>{detail.name ?? '—'}</td></tr>
							<tr><td class="py-1 pr-4 font-medium text-ink-muted">Email</td><td>{detail.email}</td></tr>
							<tr><td class="py-1 pr-4 font-medium text-ink-muted">Créé le</td><td>{fmtDate(detail.created_at)}</td></tr>
							<tr><td class="py-1 pr-4 font-medium text-ink-muted">Dernière connexion</td><td>{fmtDate(detail.last_login)}</td></tr>
						</tbody>
					</table>
				</section>

				<!-- Abonnement -->
				<section>
					<h3 class="mb-2 border-b border-stone-border pb-1 text-xs font-bold uppercase tracking-wide text-ink-muted">
						Abonnement
					</h3>
					{#if detail.subscription}
						{@const sub = detail.subscription}
						<table class="w-full text-sm">
							<tbody>
								<tr>
									<td class="py-1 pr-4 font-medium text-ink-muted">Plan</td>
									<td>
										<span class="rounded-full bg-bronze-light px-2 py-0.5 text-xs font-semibold text-bronze-dark capitalize">
											{sub.plan_name}
										</span>
										{#if sub.source === 'manual_grant'}
											<span class="ml-1.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-800">
												Manuel
											</span>
										{/if}
									</td>
								</tr>
								<tr>
									<td class="py-1 pr-4 font-medium text-ink-muted">Statut</td>
									<td class={sub.status === 'active' ? 'text-green-700 font-semibold' : 'text-ink-muted'}>
										{sub.status}
									</td>
								</tr>
								{#if sub.current_period_end}
									<tr>
										<td class="py-1 pr-4 font-medium text-ink-muted">Fin de période</td>
										<td>{fmtDate(sub.current_period_end)}</td>
									</tr>
								{/if}
								{#if sub.source && sub.source !== 'manual_grant' && sub.status === 'active'}
									<tr>
										<td colspan="2">
											<div class="mt-1 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800">
												Abonnement Stripe actif — un grant manuel l'écrasera localement.
											</div>
										</td>
									</tr>
								{/if}
							</tbody>
						</table>
					{:else}
						<p class="text-sm text-ink-muted">Aucun abonnement</p>
					{/if}
				</section>

				<!-- Clés API -->
				<section>
					<h3 class="mb-2 border-b border-stone-border pb-1 text-xs font-bold uppercase tracking-wide text-ink-muted">
						Clés API ({detail.api_keys.length})
					</h3>
					{#if detail.api_keys.length === 0}
						<p class="text-sm text-ink-muted">Aucune clé API</p>
					{:else}
						<div class="overflow-x-auto">
							<table class="w-full text-xs">
								<thead>
									<tr class="text-left font-semibold text-ink-muted">
										<th class="py-1 pr-2">Préfixe</th>
										<th class="py-1 pr-2">Label</th>
										<th class="py-1 pr-2">Quota</th>
										<th class="py-1 pr-2">Créée</th>
										<th class="py-1">Statut</th>
									</tr>
								</thead>
								<tbody>
									{#each detail.api_keys as key (key.id)}
										<tr class="border-t border-stone-border">
											<td class="py-1 pr-2 font-mono">{key.prefix}</td>
											<td class="py-1 pr-2">{key.label ?? '—'}</td>
											<td class="py-1 pr-2">{key.quota_monthly < 0 ? `Illimité · ${key.quota_used} utilisées` : `${key.quota_used}/${key.quota_monthly}`}</td>
											<td class="py-1 pr-2">{fmtDate(key.created_at)}</td>
											<td class="py-1">
												{#if key.revoked}
													<span class="text-terracotta">Révoquée</span>
												{:else if key.is_default}
													<span class="text-bronze font-semibold">Défaut</span>
												{:else}
													<span class="text-green-600">Active</span>
												{/if}
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				</section>

				<!-- Activité récente -->
				<section>
					<h3 class="mb-2 border-b border-stone-border pb-1 text-xs font-bold uppercase tracking-wide text-ink-muted">
						Activité récente
					</h3>
					{#if detail.recent_activity.length === 0}
						<p class="text-sm text-ink-muted">Aucune activité</p>
					{:else}
						<ul class="space-y-1">
							{#each detail.recent_activity as act (act.id)}
								<li class="border-b border-stone-border pb-1 text-xs">
									<span class="font-semibold text-ink">{act.action}</span>
									{#if act.reason}
										<span class="text-ink-muted"> — {act.reason}</span>
									{/if}
									<span class="ml-2 text-ink-muted">{fmtDateTime(act.created_at)}</span>
								</li>
							{/each}
						</ul>
					{/if}
				</section>

				<!-- Activité (heatmap — Phase 17) -->
				<section>
					<h3 class="mb-2 border-b border-stone-border pb-1 text-xs font-bold uppercase tracking-wide text-ink-muted">
						Activité 30 derniers jours
					</h3>
					{#if heatmapLoading}
						<div class="flex py-2">
							<div class="h-4 w-4 animate-spin rounded-full border-2 border-stone-border border-t-bronze"></div>
						</div>
					{:else if heatmapError}
						<p class="text-xs text-terracotta">{heatmapError}</p>
					{:else if heatmap}
						<HeatmapGrid days={heatmap.days} retentionDays={heatmap.retention_days} />
					{/if}
				</section>

				<!-- Actions -->
				<section class="border-t border-stone-border pt-4">
					{#if detail.subscription?.plan_name === 'internal'}
						<p class="mb-3 text-sm text-ink-muted">Compte interne protégé : plan, droits et clés gérés par le provisioner sécurisé.</p>
					{/if}
					<div class="flex flex-wrap gap-2">
						<div class="relative" title={detail.subscription?.plan_name === 'internal' ? 'Compte interne protégé' : isSelf ? 'Action sur soi-même interdite' : ''}>
							<button
								class="rounded bg-bronze px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-bronze-dark disabled:cursor-not-allowed disabled:opacity-50"
								disabled={isSelf || detail.subscription?.plan_name === 'internal'}
								onclick={() => (showGrantModal = true)}
							>
								Grant plan
							</button>
						</div>
						<div class="relative" title={detail.subscription?.plan_name === 'internal' ? 'Compte interne protégé' : isSelf ? 'Action sur soi-même interdite' : ''}>
							<button
								class="rounded border border-stone-border bg-parchment px-4 py-2 text-sm font-semibold text-ink transition-colors hover:bg-marble-dark disabled:cursor-not-allowed disabled:opacity-50"
								disabled={isSelf || detail.subscription?.plan_name === 'internal'}
								onclick={() => {
									showToggleConfirm = !showToggleConfirm;
									toggleError = null;
									toggleReason = '';
								}}
							>
								{detail.is_admin ? 'Révoquer admin' : 'Promouvoir admin'}
							</button>
						</div>

						<!-- Reset quota -->
						<button
							class="rounded bg-bronze px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-bronze-dark disabled:cursor-not-allowed disabled:opacity-50"
							disabled={isSelf || detail.subscription?.plan_name === 'internal'}
							title={detail.subscription?.plan_name === 'internal' ? 'Compte interne protégé' : isSelf ? 'Impossible sur soi-même' : ''}
							onclick={() => (showResetQuotaModal = true)}
						>
							Reset quota mensuel
						</button>

						<!-- Revoke all keys -->
						<button
							class="rounded bg-terracotta px-4 py-2 text-sm font-semibold text-white transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
							disabled={isSelf || detail.subscription?.plan_name === 'internal'}
							title={detail.subscription?.plan_name === 'internal' ? 'Compte interne protégé' : isSelf ? 'Impossible sur soi-même' : ''}
							onclick={() => (showRevokeKeysModal = true)}
						>
							Révoquer toutes les clés API
						</button>

						<!-- Resend verification — only for unverified users -->
						{#if !detail.email_verified}
							<button
								class="rounded border border-bronze px-4 py-2 text-sm font-semibold text-bronze transition-colors hover:bg-bronze hover:text-white"
								onclick={() => (showResendVerifModal = true)}
							>
								Renvoyer email de vérification
							</button>
						{/if}
					</div>

					<!-- Toggle admin inline confirm -->
					{#if showToggleConfirm}
						<div class="mt-3 rounded border border-stone-border bg-marble-dark p-3">
							<p class="mb-2 text-sm font-semibold text-ink">
								{detail.is_admin
									? `Révoquer les droits admin de ${detail.email} ?`
									: `Promouvoir ${detail.email} en admin ?`}
							</p>
							<label class="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink-muted" for="toggle-reason">
								Raison <span class="text-terracotta">*</span>
							</label>
							<textarea
								id="toggle-reason"
								bind:value={toggleReason}
								placeholder="Minimum 10 caractères…"
								rows="2"
								class="mb-2 w-full rounded border border-stone-border bg-white px-2 py-1.5 text-sm text-ink focus:border-bronze focus:outline-none
									{toggleReasonTooShort ? 'border-terracotta' : ''}"
							></textarea>
							{#if toggleReasonTooShort}
								<p class="mb-1 text-xs text-terracotta">La raison doit faire au moins 10 caractères.</p>
							{/if}
							{#if toggleError}
								<p class="mb-1 text-xs text-terracotta">{toggleError}</p>
							{/if}
							<div class="flex gap-2">
								<button
									class="rounded bg-terracotta px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-terracotta/80 disabled:opacity-50"
									disabled={!canToggle}
									onclick={handleToggleAdmin}
								>
									{toggleLoading ? 'En cours…' : 'Confirmer'}
								</button>
								<button
									class="rounded border border-stone-border px-3 py-1.5 text-sm text-ink-muted transition-colors hover:bg-marble"
									onclick={() => {
										showToggleConfirm = false;
										toggleReason = '';
										toggleError = null;
									}}
								>
									Annuler
								</button>
							</div>
						</div>
					{/if}
				</section>
			</div>
		{/if}
	</div>
{/if}

<!-- Grant plan modal (rendered outside the drawer scroll context) -->
{#if showGrantModal && detail}
	<GrantPlanModal
		userId={detail.id}
		userEmail={detail.email}
		currentPlan={detail.subscription?.plan_name ?? 'free'}
		subscriptionSource={detail.subscription?.source ?? null}
		onCancel={() => (showGrantModal = false)}
		onSuccess={handleGrantSuccess}
	/>
{/if}

{#if showResetQuotaModal && detail}
	<ResetQuotaModal
		userId={detail.id}
		userEmail={detail.email}
		quotaUsed={detail.quota_used}
		quotaMonthly={detail.quota_monthly}
		onCancel={() => (showResetQuotaModal = false)}
		onSuccess={handleActionSuccess}
	/>
{/if}

{#if showRevokeKeysModal && detail}
	<RevokeKeysModal
		userId={detail.id}
		userEmail={detail.email}
		activeKeysCount={detail.api_keys.filter((k) => !k.revoked).length}
		onCancel={() => (showRevokeKeysModal = false)}
		onSuccess={handleActionSuccess}
	/>
{/if}

{#if showResendVerifModal && detail}
	<ResendVerifModal
		userId={detail.id}
		userEmail={detail.email}
		onCancel={() => (showResendVerifModal = false)}
		onSuccess={handleActionSuccess}
	/>
{/if}
