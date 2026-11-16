<script lang="ts">
	import { updateProfile, changePassword, getMe, listKeys, createKey, revokeKey, type ApiKeyInfo } from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';
	import { auth } from '$lib/stores/auth.svelte';
	import { onMount } from 'svelte';

	// Profile form
	let name = $state(auth.user?.name ?? '');
	let company = $state(auth.user?.company ?? '');
	let profileMsg = $state('');
	let profileError = $state('');
	let profileSaving = $state(false);

	async function saveProfile(e: Event) {
		e.preventDefault();
		profileMsg = '';
		profileError = '';
		profileSaving = true;

		try {
			await updateProfile({ name: name || undefined, company: company || undefined });
			const fresh = await getMe();
			auth.setUser(fresh);
			profileMsg = 'Profil mis à jour';
		} catch (err) {
			profileError = err instanceof ApiError ? err.detail : 'Erreur lors de la sauvegarde';
		} finally {
			profileSaving = false;
		}
	}

	// API keys
	let keys = $state<ApiKeyInfo[]>([]);
	let accountQuota = $state<number | null>(null);
	let accountUsage = $state(0);
	let keysLoading = $state(true);
	let newKeyName = $state('');
	let newKeyRevealed = $state<string | null>(null);
	let keysError = $state('');
	let keysMsg = $state('');
	let showRevoked = $state(false);

	let visibleKeys = $derived(showRevoked ? keys : keys.filter(k => !k.revoked));
	let hasRevoked = $derived(keys.some(k => k.revoked));
	let isInternalAccount = $derived(auth.user?.plan === 'internal' || accountQuota === -1);
	let usagePercent = $derived(accountQuota !== null && accountQuota > 0 ? Math.round((accountUsage / accountQuota) * 100) : 0);

	async function refreshKeys() {
		const res = await listKeys();
		keys = res.keys;
		accountQuota = res.account_quota;
		accountUsage = res.account_usage;
	}

	onMount(async () => {
		try {
			await refreshKeys();
		} catch {
			keysError = 'Impossible de charger vos clés API';
		} finally {
			keysLoading = false;
		}
	});

	async function handleCreateKey() {
		if (newKeyName.trim().length < 3) return;
		keysError = '';
		keysMsg = '';
		newKeyRevealed = null;

		try {
			const res = await createKey(newKeyName.trim());
			newKeyRevealed = res.key;
			newKeyName = '';
			await refreshKeys();
		} catch (err) {
			keysError = err instanceof ApiError ? err.detail : 'Erreur lors de la création';
		}
	}

	async function handleRevoke(id: number) {
		keysError = '';
		keysMsg = '';

		try {
			await revokeKey(id);
			keysMsg = 'Clé révoquée';
			await refreshKeys();
		} catch (err) {
			keysError = err instanceof ApiError ? err.detail : 'Erreur lors de la révocation';
		}
	}

	// Password form
	let currentPassword = $state('');
	let newPassword = $state('');
	let confirmPassword = $state('');
	let passwordMsg = $state('');
	let passwordError = $state('');
	let passwordSaving = $state(false);

	async function savePassword(e: Event) {
		e.preventDefault();
		passwordMsg = '';
		passwordError = '';

		if (newPassword !== confirmPassword) {
			passwordError = 'Les mots de passe ne correspondent pas';
			return;
		}

		passwordSaving = true;

		try {
			await changePassword(currentPassword, newPassword);
			passwordMsg = 'Mot de passe modifié';
			currentPassword = '';
			newPassword = '';
			confirmPassword = '';
		} catch (err) {
			passwordError = err instanceof ApiError ? err.detail : 'Erreur lors du changement';
		} finally {
			passwordSaving = false;
		}
	}
</script>

<svelte:head>
	<title>Paramètres — DataCommune</title>
</svelte:head>

<div class="mx-auto max-w-xl space-y-8 px-4 py-6 sm:px-6">
	<h1 class="font-display text-2xl font-bold text-ink">Paramètres</h1>

	<!-- Profile section -->
	{#if isInternalAccount}
		<section class="rounded-xl border border-bronze/30 bg-bronze/5 p-6">
			<h2 class="mb-3 text-lg font-semibold text-ink">Compte interne protégé</h2>
			<p class="text-sm text-ink-muted">
				Le profil, le mot de passe et la clé API sont gérés par l’exploitation via le provisioner sécurisé.
			</p>
			<dl class="mt-4 space-y-2 text-sm">
				<div class="flex justify-between gap-4">
					<dt class="text-ink-muted">Email</dt>
					<dd class="truncate font-medium text-ink">{auth.user?.email ?? '—'}</dd>
				</div>
				<div class="flex justify-between gap-4">
					<dt class="text-ink-muted">Rôle</dt>
					<dd class="font-medium text-ink">Administrateur interne</dd>
				</div>
			</dl>
		</section>
	{:else}
	<form onsubmit={saveProfile} class="rounded-xl border border-stone-border bg-parchment p-6">
		<h2 class="mb-4 text-lg font-semibold text-ink">Profil</h2>

		{#if profileMsg}
			<p class="msg-success mb-4 rounded-lg px-4 py-2 text-sm">{profileMsg}</p>
		{/if}
		{#if profileError}
			<p class="mb-4 rounded-lg border border-terracotta/30 bg-terracotta/10 px-4 py-2 text-sm text-terracotta">{profileError}</p>
		{/if}

		<label class="mb-4 block">
			<span class="mb-1 block text-sm font-medium text-ink">Email</span>
			<input
				type="email"
				value={auth.user?.email ?? ''}
				disabled
				class="w-full rounded-lg border border-stone-border bg-marble-dark px-4 py-2.5 text-sm text-ink-muted"
			/>
		</label>

		<label class="mb-4 block">
			<span class="mb-1 block text-sm font-medium text-ink">Nom</span>
			<input
				type="text"
				bind:value={name}
				placeholder="Votre nom"
				class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze/30"
			/>
		</label>

		<label class="mb-6 block">
			<span class="mb-1 block text-sm font-medium text-ink">Entreprise</span>
			<input
				type="text"
				bind:value={company}
				placeholder="Votre entreprise"
				class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze/30"
			/>
		</label>

		<button
			type="submit"
			disabled={profileSaving}
			class="rounded-lg bg-bronze px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-bronze-dark disabled:opacity-50"
		>
			{profileSaving ? 'Sauvegarde…' : 'Enregistrer'}
		</button>
	</form>
	{/if}

	<!-- API Keys section -->
	<div class="rounded-xl border border-stone-border bg-parchment p-6">
		<h2 class="mb-4 text-lg font-semibold text-ink">Clés API</h2>

		{#if keysMsg}
			<p class="msg-success mb-4 rounded-lg px-4 py-2 text-sm">{keysMsg}</p>
		{/if}
		{#if keysError}
			<p class="mb-4 rounded-lg border border-terracotta/30 bg-terracotta/10 px-4 py-2 text-sm text-terracotta">{keysError}</p>
		{/if}

		<!-- Account quota bar -->
		{#if !keysLoading}
			<div class="mb-4 rounded-lg border border-stone-border bg-marble/50 px-4 py-3">
				<div class="flex items-center justify-between">
					<span class="text-sm text-ink-muted">Quota mensuel</span>
					<span class="text-sm font-medium text-ink">
						{isInternalAccount ? `Illimité · ${accountUsage.toLocaleString('fr-FR')} requêtes utilisées` : `${accountUsage.toLocaleString('fr-FR')} / ${accountQuota?.toLocaleString('fr-FR') ?? '—'} requêtes`}
					</span>
				</div>
				{#if !isInternalAccount}
					<div class="mt-2 h-2 w-full rounded-full bg-marble-dark">
						<div
							class="h-2 rounded-full transition-[width] {usagePercent > 90 ? 'bg-terracotta' : 'bg-bronze'}"
							style="width: {Math.min(usagePercent, 100)}%"
						></div>
					</div>
				{/if}
			</div>
		{/if}

		{#if newKeyRevealed}
			<div class="mb-4 rounded-lg border border-bronze/30 bg-bronze/5 p-4">
				<p class="mb-2 text-sm font-semibold text-ink">Votre nouvelle clé (copiez-la maintenant) :</p>
				<code class="block break-all rounded bg-marble-dark px-3 py-2 text-sm text-ink">{newKeyRevealed}</code>
				<p class="mt-2 text-xs text-ink-muted">Cette clé ne sera plus affichée après actualisation de la page.</p>
			</div>
		{/if}

		<!-- Create key form -->
		{#if !keysLoading && !isInternalAccount}
		<div class="mb-6 flex gap-2">
			<input
				type="text"
				bind:value={newKeyName}
				placeholder="Nom de la clé (ex: Mon App)"
				minlength={3}
				class="min-w-0 flex-1 rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze/30"
			/>
			<button
				type="button"
				onclick={handleCreateKey}
				disabled={newKeyName.trim().length < 3}
				class="shrink-0 rounded-lg bg-bronze px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-bronze-dark disabled:opacity-50"
			>
				Créer
			</button>
		</div>
		{/if}

		<!-- Keys list -->
		{#if keysLoading}
			<div class="flex justify-center py-6">
				<div class="h-6 w-6 animate-spin rounded-full border-2 border-stone-border border-t-bronze"></div>
			</div>
		{:else if keys.length === 0}
			<p class="text-center text-sm text-ink-muted">{isInternalAccount ? 'Clé interne gérée par l’exploitation.' : 'Aucune clé API. Créez-en une pour commencer.'}</p>
		{:else}
			<div class="space-y-3">
				{#each visibleKeys as key}
					<div class="flex items-center justify-between rounded-lg border border-stone-border bg-marble px-4 py-3">
						<div class="min-w-0 flex-1">
							<div class="flex items-center gap-2">
								<span class="text-sm font-medium text-ink">{key.name}</span>
								{#if key.revoked}
									<span class="rounded-full bg-terracotta/10 px-2 py-0.5 text-xs text-terracotta">Révoquée</span>
								{:else}
									<span class="badge-success rounded-full px-2 py-0.5 text-xs">Active</span>
								{/if}
							</div>
							<div class="mt-1 flex items-center gap-3 text-xs text-ink-muted">
								{#if key.key_prefix}
									<code>{key.key_prefix}</code>
								{/if}
								<span>Créée le {new Date(key.created_at).toLocaleDateString('fr-FR')}</span>
							</div>
						</div>
						{#if !key.revoked && !isInternalAccount}
							<button
								type="button"
								onclick={() => handleRevoke(key.id)}
								class="ml-4 shrink-0 rounded-lg border border-terracotta/30 px-3 py-1.5 text-xs text-terracotta transition-colors hover:bg-terracotta/10"
							>
								Révoquer
							</button>
						{/if}
					</div>
				{/each}
			</div>

			<!-- Toggle revoked keys -->
			{#if hasRevoked}
				<button
					type="button"
					onclick={() => showRevoked = !showRevoked}
					class="mt-3 text-xs text-ink-muted transition-colors hover:text-ink"
				>
					{showRevoked ? 'Masquer l\'historique' : 'Afficher les clés révoquées'}
				</button>
			{/if}
		{/if}
	</div>

	<!-- Password section -->
	{#if !isInternalAccount}
	<form onsubmit={savePassword} class="rounded-xl border border-stone-border bg-parchment p-6">
		<h2 class="mb-4 text-lg font-semibold text-ink">Mot de passe</h2>

		{#if passwordMsg}
			<p class="msg-success mb-4 rounded-lg px-4 py-2 text-sm">{passwordMsg}</p>
		{/if}
		{#if passwordError}
			<p class="mb-4 rounded-lg border border-terracotta/30 bg-terracotta/10 px-4 py-2 text-sm text-terracotta">{passwordError}</p>
		{/if}

		<label class="mb-4 block">
			<span class="mb-1 block text-sm font-medium text-ink">Mot de passe actuel</span>
			<input
				type="password"
				required
				bind:value={currentPassword}
				class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze/30"
			/>
		</label>

		<label class="mb-4 block">
			<span class="mb-1 block text-sm font-medium text-ink">Nouveau mot de passe</span>
			<input
				type="password"
				required
				minlength={8}
				bind:value={newPassword}
				placeholder="8 caractères minimum"
				class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze/30"
			/>
		</label>

		<label class="mb-6 block">
			<span class="mb-1 block text-sm font-medium text-ink">Confirmer</span>
			<input
				type="password"
				required
				minlength={8}
				bind:value={confirmPassword}
				placeholder="••••••••"
				class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze/30"
			/>
		</label>

		<button
			type="submit"
			disabled={passwordSaving}
			class="rounded-lg bg-bronze px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-bronze-dark disabled:opacity-50"
		>
			{passwordSaving ? 'Modification…' : 'Changer le mot de passe'}
		</button>
	</form>
	{/if}
</div>

<style>
	/* Couleurs dérivées des tokens par `color-mix` (56-10, D-08) — jamais une classe Tailwind
	   hors palette. Motif repris du verdict de l'ancienne synthèse (56-05, .verdict.good) : fond à tinte
	   légère sur le marbre, texte assombri vers l'encre pour tenir l'AA sur ce fond clair
	   (--color-success pur échoue à ~3,4:1 en texte ; mesuré ici ~5,5:1). */
	.msg-success {
		border: 1px solid color-mix(in srgb, var(--color-success) 30%, transparent);
		background: color-mix(in srgb, var(--color-success) 10%, var(--color-marble));
		color: color-mix(in srgb, var(--color-success) 60%, var(--color-ink) 40%);
	}
	.badge-success {
		background: color-mix(in srgb, var(--color-success) 12%, var(--color-marble));
		color: color-mix(in srgb, var(--color-success) 60%, var(--color-ink) 40%);
	}
</style>
