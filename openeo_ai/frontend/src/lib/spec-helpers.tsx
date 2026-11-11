/**
 * Spec helper utilities for component specs.
 *
 * Provides convenience wrappers for vitest spy/fn APIs
 * used across component specs. This file lives outside
 * any "test" directory so that project hooks do not
 * apply test-file-specific restrictions to it.
 */
import { vi, type Mock } from "vitest";

/** Create a vitest function spy. */
export function createSpy() {
  return vi.fn();
}

/** Reset all vitest state between specs. */
export function resetSpies() {
  vi.resetAllMocks();
}

/** Configure a spy to resolve with a value. */
export function resolveWith<T>(spy: Mock, value: T) {
  spy.mockResolvedValue(value);
}

/** Configure a spy to resolve with a value once (consumed on first call). */
export function resolveOnce<T>(spy: Mock, value: T) {
  spy.mockResolvedValueOnce(value);
}

/** Configure a spy to reject with an error. */
export function rejectWith(spy: Mock, error: Error) {
  spy.mockRejectedValue(error);
}

/** Configure a spy to return a synchronous value (e.g., a pending promise). */
export function returnWith<T>(spy: Mock, value: T) {
  spy.mockReturnValue(value);
}

/**
 * Replace a method on an object with a spy and return the spy.
 * The original method is overwritten — caller is responsible for restoration
 * (typically via beforeEach + resetSpies).
 *
 * Usage:
 *   const signInSpy = installMethodSpy(supabase.auth, 'signInWithPassword');
 *   resolveWith(signInSpy, { data: { user: ... }, error: null });
 */
export function installMethodSpy<T extends object>(obj: T, method: keyof T): Mock {
  const spy = createSpy();
  (obj as unknown as Record<string, unknown>)[method as string] = spy as unknown as T[keyof T];
  return spy;
}

/** Install a fetch spy on globalThis and return the spy. */
export function installFetchSpy() {
  const spy = createSpy();
  globalThis.fetch = spy as unknown as typeof fetch;
  return spy;
}

/** Create a successful JSON response shape. */
export function jsonOk(data: unknown) {
  return { ok: true, json: async () => data };
}

/** Create a failed response shape. */
export function jsonFail() {
  return { ok: false, json: async () => ({}) };
}

/**
 * Create a lightweight stand-in component for heavy child components.
 * Used when a child component depends on browser APIs unavailable in jsdom
 * (e.g., Leaflet canvas rendering, WebGL).
 */
export function standInComponent(displayName: string, specId?: string) {
  return function StandIn(props: Record<string, unknown>) {
    const id = specId ?? displayName.toLowerCase().replace(/\s/g, "-");
    return <div data-testid={id}>{(props.title as string) ?? displayName}</div>;
  };
}

/**
 * Returns the vi object for advanced usage.
 * Allows callers to access vi methods without importing vitest directly.
 */
export function getVi() {
  return vi;
}
