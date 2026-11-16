<script lang="ts">
  import SiteHeader from '$lib/components/SiteHeader.svelte';
  import SiteFooter from '$lib/components/SiteFooter.svelte';

  let { children } = $props();
</script>

<svelte:head>
  <link rel="preload" href="/fonts/inter-latin.woff2" as="font" type="font/woff2" crossorigin="anonymous" />
  <link rel="preload" href="/fonts/newsreader-latin.woff2" as="font" type="font/woff2" crossorigin="anonymous" />
</svelte:head>

<div class="public-site" data-ui="registre-sauge">
  <SiteHeader publique />
  <main id="main-content" tabindex="-1">
    {@render children()}
  </main>
  <SiteFooter />
</div>
