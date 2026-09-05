// Public Supabase project settings -- safe to be visible in client-side JS.
// The project URL and "anon" key are DESIGNED to be public (Supabase's Row
// Level Security is what actually protects data, not secrecy of this key).
// Never put the service_role key here or anywhere else client-side -- that
// one lives only as a GitHub Actions secret used by update_shared_events.py.
//
// Fill these in once the Supabase project exists (Dashboard > Project
// Settings > API):
window.SUPABASE_URL = "https://iicpmghhhllfhkrjtoiu.supabase.co";
window.SUPABASE_ANON_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlpY3BtZ2hoaGxsZmhrcmp0b2l1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg2Mjc5ODEsImV4cCI6MjEwNDIwMzk4MX0.sWDAej8utPrPwoJXhAJxUlkTuby-nsU_Fvj4usqQZYE";
