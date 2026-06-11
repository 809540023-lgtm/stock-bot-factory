// 機器人工廠引擎 — JS 版（與 Python 版 bot_engine.py 邏輯一致）
// 讓原型網頁能離線實際跑回測。正式版前後端可共用同一套規則 JSON。

function sma(p, n){const o=Array(p.length).fill(null);for(let i=n-1;i<p.length;i++){let s=0;for(let j=i-n+1;j<=i;j++)s+=p[j];o[i]=s/n;}return o;}
function rsi(p, n=14){const o=Array(p.length).fill(null),g=[],l=[];for(let i=1;i<p.length;i++){const d=p[i]-p[i-1];g.push(Math.max(d,0));l.push(Math.max(-d,0));if(i>=n){const ag=g.slice(-n).reduce((a,b)=>a+b,0)/n,al=l.slice(-n).reduce((a,b)=>a+b,0)/n;o[i]=al===0?100:100-100/(1+ag/al);}}return o;}
function kd(p, n=9){const rsv=Array(p.length).fill(null);for(let i=n-1;i<p.length;i++){const w=p.slice(i-n+1,i+1),lo=Math.min(...w),hi=Math.max(...w);rsv[i]=hi===lo?50:(p[i]-lo)/(hi-lo)*100;}const k=Array(p.length).fill(null),d=Array(p.length).fill(null);let kp=50,dp=50;for(let i=0;i<p.length;i++){if(rsv[i]===null)continue;kp=kp*2/3+rsv[i]/3;dp=dp*2/3+kp/3;k[i]=kp;d[i]=dp;}return{k,d};}
function pctChange(p){const o=[null];for(let i=1;i<p.length;i++)o.push(p[i-1]?(p[i]/p[i-1]-1)*100:null);return o;}

function valAt(ind,key,i){const s=ind[key];return s&&i<s.length?s[i]:null;}

function evalCond(c,ind,i){
  const left=valAt(ind,c.metric,i);if(left===null)return false;
  let right,rightPrev;
  if(c.value_metric){right=valAt(ind,c.value_metric,i);rightPrev=valAt(ind,c.value_metric,i-1);}
  else{right=c.value;rightPrev=c.value;}
  if(right===null||right===undefined)return false;
  if(c.op==="<")return left<right;
  if(c.op===">")return left>right;
  if(c.op==="cross_above"||c.op==="cross_below"){
    const lp=valAt(ind,c.metric,i-1);if(lp===null||rightPrev===null)return false;
    return c.op==="cross_above"?(lp<=rightPrev&&left>right):(lp>=rightPrev&&left<right);
  }
  return false;
}
function evalGroup(g,ind,i){const r=g.conditions.map(c=>evalCond(c,ind,i));if(!r.length)return false;return g.logic==="OR"?r.some(x=>x):r.every(x=>x);}

// 台股來回成本（%）：手續費0.1425%×2 + 證交稅0.3% ≈ 0.585%
const DEFAULT_FEE_PCT=0.585;

function runBot(bot,prices,feePct=0){
  if(!prices||!prices.length)return{trades:0,wins:0,winRate:0,totalReturn:0,compoundReturn:0,maxDrawdown:0,buyHoldReturn:0,avgReturn:0,feePct,finalSignal:"觀望",log:[],markers:[]};
  const kv=kd(prices);
  const ind={close:prices,kd_k:kv.k,kd_d:kv.d,rsi:rsi(prices),sma_5:sma(prices,5),sma_20:sma(prices,20),sma_60:sma(prices,60),pct:pctChange(prices)};
  let trades=0,wins=0,totalRet=0,holding=false,buyP=0;const log=[],markers=[];
  let equity=1,peak=1,maxDD=0;
  for(let i=1;i<prices.length;i++){
    if(!holding&&evalGroup(bot.buy,ind,i)){holding=true;buyP=prices[i];markers.push({i,type:"buy",price:prices[i]});log.push(`第${i}天 買進 @ ${prices[i].toFixed(2)}`);}
    else if(holding&&evalGroup(bot.sell,ind,i)){holding=false;const ret=(prices[i]/buyP-1)*100-feePct;trades++;if(ret>0)wins++;totalRet+=ret;equity*=(1+ret/100);peak=Math.max(peak,equity);maxDD=Math.max(maxDD,(peak-equity)/peak*100);markers.push({i,type:"sell",price:prices[i]});log.push(`第${i}天 賣出 @ ${prices[i].toFixed(2)}（這筆 ${ret>=0?"+":""}${ret.toFixed(1)}%，含成本）`);}
  }
  let sig="觀望";const last=prices.length-1;
  if(evalGroup(bot.buy,ind,last))sig="買進";
  else if(holding&&evalGroup(bot.sell,ind,last))sig="賣出";
  else if(holding)sig="續抱";
  const r1=x=>Math.round(x*10)/10;
  return{trades,wins,winRate:trades?Math.round(wins/trades*1000)/10:0,
    totalReturn:r1(totalRet),compoundReturn:r1((equity-1)*100),maxDrawdown:r1(maxDD),
    buyHoldReturn:r1((prices[last]/prices[0]-1)*100),avgReturn:trades?r1(totalRet/trades):0,
    feePct,finalSignal:sig,log,markers};
}

// 與 Python 版同樣的模擬股價（沙盒/離線用）
function fakePrices(){const o=[];for(let t=0;t<120;t++){const w=15*Math.sin(t/8)+(t-60)*0.3;o.push(Math.round((100+w)*100)/100);}return o;}
