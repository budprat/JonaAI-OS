// ABOUTME: Supabase client singleton for the JonaAI frontend.
// Reads VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY from Vite env at build time.

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

if (!supabaseUrl || !supabaseAnonKey) {
  // Loud failure in dev — silent in production builds where env should be set
  // eslint-disable-next-line no-console
  console.error(
    "[supabase] Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY — auth will not work. " +
      "Set them in openeo_ai/frontend/.env.local"
  );
}

/**
 * Singleton Supabase client.
 * - Persists session in localStorage automatically (auto-refresh tokens)
 * - Uses PKCE flow for OAuth
 * - access_token from this client is the JWT sent to the FastAPI backend
 */
export const supabase: SupabaseClient = createClient(
  supabaseUrl ?? "https://placeholder.supabase.co",
  supabaseAnonKey ?? "placeholder-anon-key",
  {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      flowType: "pkce",
    },
  }
);
