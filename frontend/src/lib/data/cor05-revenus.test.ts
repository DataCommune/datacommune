import { readFileSync } from 'node:fs';
import { mapCommuneToFiche } from './commune-fiche';
import type { CommuneSynthese } from '$lib/api/commune';
import { describe, it, expect } from 'vitest';
import { REFERENTIELS, comparerAuReferentiel } from './referentiels';
import { EXPLICATIONS } from './explications';

describe('COR05 — Filosofi déclaré 2021', () => {
  it('compares DEC with DEC, never the DISP counterexample', () => {
    expect(REFERENTIELS['revenus.revenu_median']).toMatchObject({ valeur: 22900, millesime: '2021' });
    expect(EXPLICATIONS['revenus.revenu_median'].definition).toContain('déclaré');
    expect(EXPLICATIONS['revenus.taux_pauvrete'].definition).toContain('disponible');
  });
  it('requires the same year and metropolitan territory', () => {
    const compare = (context?: { annee: number | null; codeInsee: string }) =>
      comparerAuReferentiel('revenus.revenu_median', 24380, context);
    expect(compare()).toBeNull();
    expect(compare({ annee: 2022, codeInsee: '44109' })).toBeNull();
    expect(compare({ annee: 2021, codeInsee: '97209' })).toBeNull();
    expect(compare({ annee: null, codeInsee: '44109' })).toBeNull();
    expect(compare({ annee: 2021, codeInsee: '44109' })?.ecartTexte).toContain('France métropolitaine');
  });
});

// Captured from the real HTTP route over the disposable SQL projection.
const capture = JSON.parse(readFileSync('../docs/audit-stabilisation-2026-09-08/lot-02/cor05-api-fixture.json', 'utf8'));
describe('COR05 real API payload -> mapper', () => {
  it('preserves the declared annual per-UC value and exact reference', () => {
    const commune = capture.payload.donnees[0] as CommuneSynthese;
    expect(commune.revenu_median).toBe(24380);
    expect(commune.taux_pauvrete).toBe(17);
    const item = mapCommuneToFiche(commune).kpi.find(k => k.fieldId === 'revenus.revenu_median')!;
    expect(item.label).toBe('Revenu déclaré médian');
    expect(item.unit).toBe('€/UC/an');
    expect(item.value).toBe('24 380');
    expect(item.ecartTexte).toContain('22 900');
    expect(item.ecartTexte).not.toContain('23 080');
  });
  it('keeps suppressed values unavailable and real zero numeric', () => {
    const base = capture.payload.donnees[0] as CommuneSynthese;
    const secret = capture.responses['01019'].donnees[0];
    const mapped = mapCommuneToFiche({ ...base, ...secret, annee_revenus: secret.annee });
    const item = mapped.kpi.find(k => k.fieldId === 'revenus.revenu_median')!;
    expect(item.value).toBe('n.d.');
    expect(item.verdictLabel).toBeUndefined();
    const zero = mapCommuneToFiche({ ...base, revenu_median: 0 }).kpi.find(k => k.fieldId === 'revenus.revenu_median')!;
    expect(zero.value).toBe('0');
  });
  it.each([2020, 2022, null])('suppresses comparison for vintage %s', annee => {
    const item = mapCommuneToFiche({ ...capture.payload.donnees[0], annee_revenus: annee }).kpi[3];
    expect(item.ecartTexte).toBeUndefined();
  });
});
