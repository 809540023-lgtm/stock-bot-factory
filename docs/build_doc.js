const fs = require("fs");
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        AlignmentType, LevelFormat, HeadingLevel, BorderStyle, WidthType,
        ShadingType, PageBreak } = require("docx");

const FONT = "Microsoft JhengHei";
const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };

function H1(t){return new Paragraph({heading:HeadingLevel.HEADING_1,children:[new TextRun(t)]});}
function H2(t){return new Paragraph({heading:HeadingLevel.HEADING_2,children:[new TextRun(t)]});}
function P(t){return new Paragraph({spacing:{after:140},children:[new TextRun(t)]});}
function B(t){return new Paragraph({numbering:{reference:"b",level:0},spacing:{after:80},children:[new TextRun(t)]});}

function cell(t, w, head){
  return new TableCell({borders, width:{size:w,type:WidthType.DXA},
    shading: head?{fill:"1F2937",type:ShadingType.CLEAR}:{fill:"FFFFFF",type:ShadingType.CLEAR},
    margins:{top:80,bottom:80,left:120,right:120},
    children:[new Paragraph({children:[new TextRun({text:t,bold:!!head,color:head?"FFFFFF":"000000"})]})]});
}
function row(cells,w,head){return new TableRow({children:cells.map(c=>cell(c,w,head))});}

const phaseTable = new Table({
  width:{size:9360,type:WidthType.DXA}, columnWidths:[1400,3380,4580],
  rows:[
    row(["階段","目標","產出"],[1400,3380,4580].reduce((a,b)=>a,1400),true),
    new TableRow({children:[cell("第一期",1400),cell("能用：自己造 bot、回測、看訊號",3380),cell("積木式工廠介面 + 回測引擎 + 接證交所公開資料（已有原型與引擎）",4580)]}),
    new TableRow({children:[cell("第二期",1400),cell("能社交：朋友互相分享、參考",3380),cell("登入系統、機器人存檔、社群參數池、範例機器人定期生成",4580)]}),
    new TableRow({children:[cell("第三期",1400),cell("能成長：更多指標、更聰明",3380),cell("進階指標、AI 輔助建議參數、手機推播（LINE Notify）、短線模式",4580)]}),
  ]
});

const doc = new Document({
  styles:{
    default:{document:{run:{font:FONT,size:22}}},
    paragraphStyles:[
      {id:"Heading1",name:"Heading 1",basedOn:"Normal",next:"Normal",quickFormat:true,
       run:{size:30,bold:true,font:FONT,color:"0F766E"},paragraph:{spacing:{before:260,after:160},outlineLevel:0}},
      {id:"Heading2",name:"Heading 2",basedOn:"Normal",next:"Normal",quickFormat:true,
       run:{size:25,bold:true,font:FONT,color:"115E59"},paragraph:{spacing:{before:200,after:120},outlineLevel:1}},
    ]
  },
  numbering:{config:[
    {reference:"b",levels:[{level:0,format:LevelFormat.BULLET,text:"•",alignment:AlignmentType.LEFT,
      style:{paragraph:{indent:{left:720,hanging:360}}}}]}
  ]},
  sections:[{
    properties:{page:{size:{width:12240,height:15840},margin:{top:1440,right:1440,bottom:1440,left:1440}}},
    children:[
      new Paragraph({alignment:AlignmentType.CENTER,spacing:{after:60},
        children:[new TextRun({text:"股票機器人工廠",bold:true,size:44,font:FONT,color:"0F766E"})]}),
      new Paragraph({alignment:AlignmentType.CENTER,spacing:{after:40},
        children:[new TextRun({text:"社群型 AI 選股機器人平台 — 規劃書",size:26,font:FONT,color:"555555"})]}),
      new Paragraph({alignment:AlignmentType.CENTER,spacing:{after:240},
        children:[new TextRun({text:"v1 草案",size:20,font:FONT,color:"999999"})]}),

      H1("一、這個平台是什麼"),
      P("一個讓你的朋友——不管是股市老手、想短線進出的人、還是完全沒接觸過股票的新手——都能進來的社群平台。核心不是「給大家一套寫好的策略」，而是給每個人一座「工廠」，讓他們用積木組出符合自己邏輯的 AI 股票機器人。"),
      P("因為每個人對股票的看法不同，硬套別人的機器人沒有意義。平台的價值在於：人人造自己的 bot、用歷史資料驗證、彼此交流參數，平台再定期生成範例供大家參考改寫。"),

      H1("二、核心設計原則"),
      B("人人造自己的 bot：用拖拉積木組買賣邏輯，不懂程式也能用；懂程式的可切進階模式。"),
      B("先回測再相信：任何機器人都先用歷史資料跑過，看勝率與報酬，不是憑感覺。"),
      B("只出訊號、不自動下單：平台是分析工具，最後由每個人自己在券商手動確認。這是對你（平台方）最重要的法律與責任保護。"),
      B("定期更新、沒有標準答案：範例機器人定期生成，大家喜歡這種持續有新東西參考的感覺。"),
      B("長短線通吃：想短線抓波段的人不用另做系統，用同一座工廠把參數調積極即可。"),

      H1("三、五大功能模塊"),
      H2("1. 機器人工廠（核心）"),
      P("積木式介面。每個積木是一個條件，例如「KD 的 K 值低於 20」「股價跌破月線」「某人分享的參數觸發」。使用者拖拉組合成買進與賣出規則。每個機器人底層就是一份 JSON，引擎讀它就能執行。"),
      H2("2. 範例機器人產生器"),
      P("平台每期自動生成幾個範本機器人（如 KD 抄底、均線多頭、短線積極），附說明與歷史表現，使用者一鍵載入工廠、改成自己的版本。"),
      H2("3. 社群參數池"),
      P("朋友們把觀察到的漲跌參數貼出來互相交流。好的參數可以被別人加進自己的機器人。"),
      H2("4. 回測 + 訊號引擎"),
      P("系統的心臟。算技術指標、把規則跑成買賣訊號、用歷史資料回測。引擎已經寫好並驗證：同一段股價上，不同邏輯的機器人表現差很多，正好證明「該各造各的」。"),
      H2("5. 手動下單（平台外）"),
      P("平台只給訊號與報告，使用者自己到券商 App 確認下單。平台不碰任何人的資金。"),

      new Paragraph({children:[new PageBreak()]}),
      H1("四、分期開發建議"),
      P("不用一次做完。建議分三期，先讓平台能用、再加社交、最後變聰明："),
      phaseTable,

      H1("五、技術選型（延續你熟悉的工具）"),
      B("前端：HTML/JS 積木介面，部署到 GitHub Pages（你已熟悉）。"),
      B("後端與資料：Node.js + 台灣證交所公開 OpenAPI（免金鑰），部署到 Render（你已熟悉）。"),
      B("機器人存檔：每個 bot 是一份 JSON，初期可存瀏覽器或簡單資料庫，第二期再上正式資料庫。"),
      B("推播：第三期接 LINE Notify，把訊號推到手機（你 AdPilot 已有經驗）。"),

      H1("六、現在已經有的東西"),
      B("可操作的網頁原型：能實際拖積木、跑回測、看訊號（本次附上）。"),
      B("回測引擎：Python 版與 JS 版各一套，結果一致，前後端可共用。"),
      B("三個範例機器人：KD 抄底、均線多頭、短線積極，皆可跑。"),

      H1("七、重要聲明"),
      P("本平台提供分析與交流工具，不是投資顧問服務，不提供個人化投資建議。所有機器人的歷史回測結果都不代表未來表現，股市投資可能造成虧損，使用者須自行判斷並承擔風險。平台不代為下單、不經手使用者資金。"),
    ]
  }]
});

Packer.toBuffer(doc).then(buf=>{fs.writeFileSync("平台規劃書.docx",buf);console.log("規劃書產生完成");});
