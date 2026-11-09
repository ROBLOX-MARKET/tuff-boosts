"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";

const DEFAULT_SITE = {
  discord_url: "https://discord.gg/TuffBoosts",
  boosts_sold: 0,
  business_name: "",
  contact_email: "",
  business_address: "",
  coming_soon: { boosts: false, nitro: true }
};

const AppContext = createContext(null);

export function Providers({ children }) {
  const [session, setSession] = useState(null);
  const [profile, setProfile] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [site, setSite] = useState(DEFAULT_SITE);

  const loadProfile = useCallback(async (user) => {
    if (!user) {
      setProfile(null);
      return;
    }
    const { data } = await supabase
      .from("profiles")
      .select("id, username, is_admin, spent_cents, created_at")
      .eq("id", user.id)
      .maybeSingle();
    setProfile(data || null);
  }, []);

  const loadSite = useCallback(async () => {
    const { data } = await supabase.from("settings").select("*").eq("id", 1).maybeSingle();
    if (data) setSite({ ...DEFAULT_SITE, ...data });
  }, []);

  useEffect(() => {
    let active = true;
    supabase.auth.getSession().then(async ({ data }) => {
      if (!active) return;
      setSession(data.session);
      await loadProfile(data.session?.user);
      if (active) setAuthReady(true);
    });
    const { data: listener } = supabase.auth.onAuthStateChange((event, next) => {
      if (event === "PASSWORD_RECOVERY") setRecovering(true);
      setSession(next);
      // Load the profile outside the auth callback, as Supabase recommends.
      setTimeout(() => loadProfile(next?.user), 0);
    });
    loadSite();
    return () => {
      active = false;
      listener.subscription.unsubscribe();
    };
  }, [loadProfile, loadSite]);

  const value = {
    session,
    user: session?.user || null,
    profile,
    isAdmin: Boolean(profile?.is_admin),
    authReady,
    recovering,
    site,
    refreshProfile: () => loadProfile(session?.user),
    refreshSite: loadSite,
    signOut: () => supabase.auth.signOut()
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  return useContext(AppContext);
}

export function discordUrl(site) {
  const url = String(site?.discord_url || "");
  return url.startsWith("https://") ? url : DEFAULT_SITE.discord_url;
}

export function businessLine(site) {
  return [site?.business_name, site?.business_address, site?.contact_email].filter(Boolean).join(" · ");
}
