// ── 平台前端設定 ───────────────────────────────────────────
// 部署後，把下面的 PROD_API 換成你的 Render 後端網址即可，例如：
//   https://stock-bot-backend.onrender.com
// 本機開發時自動用 localhost:5000，不用改。
window.PROD_API = "https://stock-bot-backend-kcbc.onrender.com";

window.API_BASE = (location.hostname === "localhost" || location.hostname === "127.0.0.1")
  ? "http://localhost:5000"
  : window.PROD_API;
