// Build-time switch of the error report (framing of 2026-11-10), read like VITE_MESURE_AUDIENCE.
// Off unless the build sets VITE_SIGNALEMENT=true: the page /signaler answers 404, the footer keeps
// its `mailto` and the information panel shows no link. The public build sets it through a literal
// of `api/docker-compose.public.yml`, guarded by `ouverture.test.ts`.
export const SIGNALEMENT = (import.meta.env.VITE_SIGNALEMENT ?? '').trim().toLowerCase() === 'true';
