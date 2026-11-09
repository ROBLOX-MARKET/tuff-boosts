import { createClient } from "@supabase/supabase-js";

// Browser client: uses the public anon key. Row security in the database decides what each person can see.
export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  {
    auth: {
      flowType: "implicit",
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true
    }
  }
);
