# 部署手冊（第一期 MVP）

平台分兩塊：**後端**（抓證交所資料 + 回測）上 Render，**前端**（網頁）上 GitHub Pages。
照下面做，最後你會得到一個手機可開的網址。

---

## 〇、先在本機跑起來（建議先確認沒問題再部署）

```bash
# 後端
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py          # 開在 http://localhost:5000

# 前端（另開一個終端機）
cd web
python3 -m http.server 8000      # 開在 http://localhost:8000
```

打開 http://localhost:8000 → 右上角顯示「● 後端已連線」→ 輸入 2330 → 按回測。
本機模式下前端會自動連 `http://localhost:5000`，不用改任何設定。

---

## 一、後端上 Render

1. 把整個專案推上 GitHub（包含 `backend/`、`engine/`、根目錄 `render.yaml`）。
2. 到 https://render.com → New → **Blueprint** → 選你的 repo。
   Render 會讀根目錄的 `render.yaml`，自動建立服務：
   - runtime: python，rootDir: `backend`
   - 啟動：`gunicorn app:app`
   - 健康檢查：`/api/health`
3. 等部署完成，會得到一個網址，例如：
   `https://stock-bot-backend.onrender.com`
4. 開 `https://你的後端網址/api/health`，看到 `{"status":"ok","bots":8}` 就成功。

> ⚠ 免費方案會「閒置休眠」。久沒人用時第一次請求要等 30~60 秒喚醒，屬正常。
> ⚠ 證交所限流：後端每次抓料間隔 2 秒，抓 12 個月首次約 24 秒；之後同股票走快取會秒回。

---

## 二、前端上 GitHub Pages

1. 編輯 `web/config.js`，把 `PROD_API` 換成你上一步的 Render 網址：
   ```js
   window.PROD_API = "https://stock-bot-backend.onrender.com";
   ```
2. 在 GitHub repo → Settings → Pages →
   Source 選 `Deploy from a branch`，Branch 選 `main`，資料夾選 `/web`（或把 web 內容放到 docs/ 再選 /docs）。
3. 等一兩分鐘，會得到前端網址，例如：
   `https://你的帳號.github.io/你的repo/`
4. 手機打開這個網址即可使用。

> CORS 已在後端用 `flask-cors` 全開（`CORS(app)`），GitHub Pages 跨網域呼叫沒問題。
> 若日後要鎖來源，再把 CORS 設成只允許你的 Pages 網域即可。

---

## 三、驗收清單

- [ ] `/api/health` 回 `{"status":"ok","bots":8}`
- [ ] 前端右上角顯示「● 後端已連線」
- [ ] 輸入 2330、按回測，看到交易次數／勝率／複利報酬／最大回撤／買進持有基準
- [ ] 「範例機器人」有 8 個，點一下能載入工廠
- [ ] 「💾 儲存」後，「我的機器人」出現該筆，重整網頁仍在，可載入

---

## 紅線（務必保留）
- 只出參考訊號，**不自動下單、不接券商、不碰金流**。
- 介面已放聲明：本平台為分析工具，非投資建議，回測不代表未來，投資有風險。
