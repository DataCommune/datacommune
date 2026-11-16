import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';
export default defineConfig({ plugins: [sveltekit()], resolve: { conditions: ['browser'] }, test: { environment: 'jsdom', setupFiles: ['src/tests/setup.ts'], include: ['src/lib/data/bien-proxy-coverage.test.ts', 'src/lib/data/bien-fiche.test.ts', 'src/lib/api/resolve.test.ts'] } });
