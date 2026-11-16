<script lang="ts">
	let { children } = $props();
</script>

<div class="flex min-h-screen items-center justify-center bg-marble px-4">
	<main id="main-content" tabindex="-1" class="w-full max-w-sm">
		<a href="/" class="mb-8 block text-center font-display text-2xl font-bold tracking-tight text-ink">
			Data<span class="text-bronze">Commune</span>
		</a>
		{@render children()}
	</main>
</div>
