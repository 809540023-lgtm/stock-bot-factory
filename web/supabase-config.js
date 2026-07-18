// ── Supabase 會員系統設定 ───────────────────────────────────
// 專案：linbo-stock-bot（jbg 免費組織 · 東京區）
// URL / anon key 皆為公開設計（會出現在網頁原始碼、由 RLS 保護），非機密。
// ⚠ 切勿放 service_role key（那把才是機密）。
window.SUPABASE_URL = "https://fpyfgpuzchchzznlpovf.supabase.co";
window.SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZweWZncHV6Y2hjaHp6bmxwb3ZmIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQzODc1NzksImV4cCI6MjA5OTk2MzU3OX0.WFMnhxHVAl2qC6z4FVgRLE2wOAvdkWrHUmi8joswedY";

// 是否已正確設定（兩者皆非佔位符才算啟用）
window.AUTH_ENABLED = !!(window.SUPABASE_URL && window.SUPABASE_ANON_KEY
  && !window.SUPABASE_URL.includes("YOUR-PROJECT")
  && !window.SUPABASE_ANON_KEY.includes("YOUR-ANON-KEY"));
