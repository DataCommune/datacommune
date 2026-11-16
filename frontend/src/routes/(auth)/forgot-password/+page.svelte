<script lang="ts">
	import { forgotPassword } from '$lib/api/auth';

	let email = $state('');
	let sent = $state(false);
	let submitting = $state(false);

	async function handleSubmit(e: Event) {
		e.preventDefault();
		submitting = true;
		try {
			await forgotPassword(email);
		} finally {
			sent = true;
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Mot de passe oublié — DataCommune</title>
</svelte:head>

<div class="rounded-xl border border-stone-border bg-parchment p-6">
	{#if sent}
		<h1 class="mb-4 text-center text-xl font-semibold text-ink">Email envoyé</h1>
		<p class="text-center text-sm text-ink-muted">
			Si un compte existe pour <strong class="text-ink">{email}</strong>, vous recevrez un lien de réinitialisation.
		</p>
		<a
			href="/login"
			class="mt-6 block w-full rounded-lg bg-bronze px-6 py-3 text-center font-medium text-white transition-colors hover:bg-bronze-dark"
		>
			Retour à la connexion
		</a>
	{:else}
		<h1 class="mb-2 text-center font-display text-xl font-semibold text-ink">Mot de passe oublié</h1>
		<p class="mb-6 text-center text-sm text-ink-muted">
			Entrez votre email pour recevoir un lien de réinitialisation.
		</p>

		<form onsubmit={handleSubmit}>
			<label class="mb-6 block">
				<span class="mb-1 block text-sm font-medium text-ink">Email</span>
				<input
					type="email"
					required
					bind:value={email}
					placeholder="votre@email.com"
					class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze-dark/30"
				/>
			</label>

			<button
				type="submit"
				disabled={submitting}
				class="w-full rounded-lg bg-bronze px-6 py-3 font-medium text-white transition-colors hover:bg-bronze-dark disabled:opacity-50"
			>
				{submitting ? 'Envoi…' : 'Envoyer le lien'}
			</button>
		</form>

		<p class="mt-4 text-center text-sm text-ink-muted">
			<a href="/login" class="font-medium text-bronze transition-colors hover:text-bronze-dark">Retour à la connexion</a>
		</p>
	{/if}
</div>
