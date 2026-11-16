<script lang="ts">
	import { register } from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';

	let email = $state('');
	let password = $state('');
	let confirm = $state('');
	let error = $state('');
	let success = $state(false);
	let requiresVerification = $state(true);
	let submitting = $state(false);

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';

		if (password !== confirm) {
			error = 'Les mots de passe ne correspondent pas';
			return;
		}

		submitting = true;

		try {
			const result = await register(email, password);
			requiresVerification = result.requires_verification;
			success = true;
		} catch (err) {
			if (err instanceof ApiError) {
				error = err.detail;
			} else {
				error = "Erreur lors de l'inscription";
			}
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Inscription — DataCommune</title>
</svelte:head>

{#if success}
	<div class="rounded-xl border border-stone-border bg-parchment p-6 text-center">
		{#if requiresVerification}
			<h1 class="mb-4 text-xl font-semibold text-ink">Vérifiez vos emails</h1>
			<p class="text-sm text-ink-muted">
				Un lien de vérification a été envoyé à <strong class="text-ink">{email}</strong>.
			</p>
		{:else}
			<h1 class="mb-4 text-xl font-semibold text-ink">Compte créé</h1>
			<p class="text-sm text-ink-muted">
				Vous pouvez maintenant vous connecter avec <strong class="text-ink">{email}</strong>.
			</p>
		{/if}
		<a
			href="/login"
			class="mt-6 inline-block rounded-lg bg-bronze px-6 py-3 font-medium text-white transition-colors hover:bg-bronze-dark"
		>
			Se connecter
		</a>
	</div>
{:else}
	<form onsubmit={handleSubmit} class="rounded-xl border border-stone-border bg-parchment p-6">
		<h1 class="mb-6 text-center font-display text-xl font-semibold text-ink">Créer un compte</h1>

		{#if error}
			<p class="mb-4 rounded-lg border border-terracotta/30 bg-terracotta/10 px-4 py-2 text-sm text-terracotta">
				{error}
			</p>
		{/if}

		<label class="mb-4 block">
			<span class="mb-1 block text-sm font-medium text-ink">Email</span>
			<input
				type="email"
				required
				bind:value={email}
				placeholder="votre@email.com"
				class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze-dark/30"
			/>
		</label>

		<label class="mb-4 block">
			<span class="mb-1 block text-sm font-medium text-ink">Mot de passe</span>
			<input
				type="password"
				required
				minlength={8}
				bind:value={password}
				placeholder="8 caractères minimum"
				class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze-dark/30"
			/>
		</label>

		<label class="mb-6 block">
			<span class="mb-1 block text-sm font-medium text-ink">Confirmer le mot de passe</span>
			<input
				type="password"
				required
				minlength={8}
				bind:value={confirm}
				placeholder="••••••••"
				class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze-dark/30"
			/>
		</label>

		<button
			type="submit"
			disabled={submitting}
			class="w-full rounded-lg bg-bronze px-6 py-3 font-medium text-white transition-colors hover:bg-bronze-dark disabled:opacity-50"
		>
			{submitting ? 'Inscription…' : "S'inscrire"}
		</button>

		<p class="mt-4 text-center text-sm text-ink-muted">
			Déjà un compte ?
			<a href="/login" class="font-medium text-bronze transition-colors hover:text-bronze-dark">Se connecter</a>
		</p>
	</form>
{/if}
