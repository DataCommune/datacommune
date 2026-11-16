<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { auth } from '$lib/stores/auth.svelte';
	import { onMount } from 'svelte';
	import SiteHeader from '$lib/components/SiteHeader.svelte';
	import SiteFooter from '$lib/components/SiteFooter.svelte';

	// The connected space wears the same header and footer as the public site (D-50); this layout adds
	// only the auth guard of the (app) route group.
	let { children } = $props();

	onMount(async () => {
		await auth.init();
		if (!auth.isAuthenticated) {
			// Preserve where the visitor was heading so /login can return them there.
			const dest = $page.url.pathname + $page.url.search;
			goto('/login?redirect=' + encodeURIComponent(dest));
		}
	});
</script>

<SiteHeader />

<main id="main-content" tabindex="-1" aria-busy={auth.loading ? 'true' : undefined}>
	{#if auth.loading}
		<div class="flex min-h-[50vh] items-center justify-center">
			<div class="h-8 w-8 animate-spin rounded-full border-2 border-line border-t-accent"></div>
		</div>
	{:else if auth.isAuthenticated}
		{@render children()}
	{/if}
</main>

<SiteFooter />
