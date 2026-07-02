// ── Supabase 會員系統設定 ───────────────────────────────────
// 把下面兩個值換成你 Supabase 專案的（後台 Settings → API）：
//   SUPABASE_URL       = Project URL，例如 https://abcdefgh.supabase.co
//   SUPABASE_ANON_KEY  = anon / public key（eyJ... 開頭那串，公開設計、由 RLS 保護）
// ⚠ 不要放 service_role key（那把是機密）。
//
// 兩個值都填好後，會員登入/註冊會自動啟用，並鎖住「儲存機器人」「排行榜」等進階功能。
// 只要還是下面的佔位字串，網站會維持「免登入、全功能開放」的狀態，不會壞。
window.SUPABASE_URL = "https://YOUR-PROJECT.supabase.co";
window.SUPABASE_ANON_KEY = "YOUR-ANON-KEY";

// 是否已正確設定（兩者皆非佔位符才算啟用）
window.AUTH_ENABLED = !!(window.SUPABASE_URL && window.SUPABASE_ANON_KEY
  && !window.SUPABASE_URL.includes("YOUR-PROJECT")
  && !window.SUPABASE_ANON_KEY.includes("YOUR-ANON-KEY"));
