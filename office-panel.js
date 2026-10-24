'use strict';

// Render the entire rail on a logical pixel grid, then scale with nearest-neighbour.
let panelScroll = 0, panelMetrics = null;
const uiCanvas = document.createElement('canvas');
const uiContext = uiCanvas.getContext('2d');
const DETAIL_FONT = 'Tiny5';

function panelText(text, width, maxLines = 3, context = uiContext) {
  const words = String(text || '').split(/\s+/), lines = [];
  let line = '';
  for (const word of words) {
    if (!word) continue;
    if (context.measureText(line ? line + ' ' + word : word).width <= width) line += (line ? ' ' : '') + word;
    else {
      if (line) lines.push(line);
      line = word;
      while (context.measureText(line).width > width) {
        let n = line.length;
        while (n > 1 && context.measureText(line.slice(0, n)).width > width) n--;
        lines.push(line.slice(0, n)); line = line.slice(n);
      }
    }
  }
  if (line) lines.push(line);
  if (lines.length > maxLines) {
    lines.length = maxLines;
    let last = lines.at(-1);
    while (last && context.measureText(last + '…').width > width) last = last.slice(0, -1);
    lines[maxLines - 1] = last + '…';
  }
  return lines;
}

function panel(t) {
  btns = [];
  if (!SHOW_HUD) return;
  const w = Math.round(hudWidth() / dpr), h = Math.round(cv.height / dpr);
  if (uiCanvas.width !== w || uiCanvas.height !== h) { uiCanvas.width = w; uiCanvas.height = h; }
  const c = uiContext;
  c.imageSmoothingEnabled = false; c.textAlign = 'left'; c.textBaseline = 'top';
  const colors = {bg:'#161426', surface:'#211d37', edge:'#51416f', muted:'#a497bd', text:'#eee6ff', green:'#91edb6', violet:'#bea3ff', yellow:'#ffd16b'};
  const pad = 20, iw = w - pad * 2, safeBottom = 80;
  const text = (value, x, y, size=20, color=colors.text) => {
    c.font = `${size}px Tiny5`; c.fillStyle = color;
    c.fillText(String(value), Math.round(x), Math.round(y));
  };
  const box = (x,y,bw,bh,fill,edge=colors.edge) => {
    x=Math.round(x); y=Math.round(y); bw=Math.round(bw); bh=Math.round(bh);
    c.fillStyle=edge; c.fillRect(x+2,y,bw-4,bh); c.fillRect(x,y+2,bw,bh-4);
    c.fillStyle=fill; c.fillRect(x+4,y+2,bw-8,bh-4); c.fillRect(x+2,y+4,bw-4,bh-8);
    c.fillStyle='#ffffff0a'; c.fillRect(x+4,y+2,bw-8,2);
  };
  const fit = (value,width,size=20,lines=1) => { c.font=`${size}px Tiny5`; return panelText(value,width,lines,c); };
  const lines = (values,x,y,color=colors.text,size=20) => {
    for(const line of values) { text(line,x,y,size,color); y+=size+6; }
    return y;
  };
  const button = (key, x, y, bw, bh, label, value, active=false) => {
    const i = btns.length;
    const hovered = hover === i;
    box(x,y,bw,bh,hovered?'#3b2e55':active?'#244336':'#2a223e',active?colors.green:hovered?colors.violet:colors.edge);
    text(label,x+12,y+7,20,active?colors.green:colors.muted);
    if(value) text(fit(value,bw-38)[0] || value,x+12,y+29,20,colors.text);
    if(value) text('>',x+bw-22,y+29,20,active?colors.green:colors.violet);
    btns.push({key,x:x*dpr,y:y*dpr,w:bw*dpr,h:bh*dpr});
  };
  c.fillStyle=colors.bg; c.fillRect(0,0,w,h);
  c.fillStyle='#29213f'; c.fillRect(0,0,w,92);
  c.fillStyle=colors.violet; c.fillRect(w-2,0,2,h);
  text('ЖИВОЙ AI-ОФИС',pad,14,30);
  const k=kyiv();
  text(`${String(k.h).padStart(2,'0')}:${String(k.m).padStart(2,'0')}`,w-96,18,20,colors.yellow);
  const live=online && bridgeHealthy;
  const list=[...agents.values()].filter(a=>!a.leaving && ['codex','codex-agent','chatgpt'].includes(a.kind));
  c.fillStyle=live?colors.green:colors.yellow; c.fillRect(pad,58,8,8);
  text(live?`ОНЛАЙН / В ОФИСЕ: ${list.length}`:online?'ОЖИДАЮ СОБЫТИЯ':'МОСТ НЕ ПОДКЛЮЧЁН',pad+18,52,20,live?colors.green:colors.yellow);
  c.fillStyle=colors.edge; c.fillRect(pad,90,iw,2);

  // Controls are pinned at the top, safely above any bottom Windows taskbar.
  text('НАСТРОЙКИ ОФИСА',pad,108,20,colors.violet);
  const gap=8, bw=Math.floor((iw-gap)/2), bh=58, settingsY=140;
  button('season',pad,settingsY,bw,bh,'СЕЗОН',seasonPick==='auto'?`Авто / ${SEASON_RU[season]}`:SEASON_RU[season]);
  button('weather',pad+bw+gap,settingsY,bw,bh,'ПОГОДА',weatherPick==='auto'?`Авто / ${WEATHER_RU[weather]}`:WEATHER_RU[weather]);
  button('sound',pad,settingsY+bh+gap,bw,bh,'ЗВУК',soundOn?'ВКЛЮЧЁН':'ВЫКЛЮЧЕН',soundOn);
  button('demo',pad+bw+gap,settingsY+bh+gap,bw,bh,'ДЕМО',demoOn?'ВКЛЮЧЕНО':'ВЫКЛЮЧЕНО',demoOn);
  const onBreak=list.some(a=>a.brk?.manual||a.restQueued);
  button('rest',pad,272,iw,40,onBreak?'ВЕРНУТЬ В ОФИС':'ОТДЫХ / ПЕРЕКУР',null,onBreak);
  const sectionY=328, cardsTop=366, cardsBottom=Math.max(cardsTop+60,h-safeBottom-38);
  const viewportH=cardsBottom-cardsTop;
  text(`АГЕНТЫ / ${String(list.length).padStart(2,'0')}`,pad,sectionY,20,colors.violet);
  button('scrollUp',w-pad-80,sectionY-4,36,30,'↑',null);
  button('scrollDown',w-pad-40,sectionY-4,36,30,'↓',null);
  const cards=list.map(a=>{
    const title=fit(a.activityTitle || (a.status==='working'?doing(a)||'Работает над задачей':a.status==='waiting'?'Нужен твой ответ':'Свободен'),iw-32,20,2);
    const detail=fit(a.activityDetail || (a.activityTitle?'':a.detail) || '',iw-32,20,3);
    const task=fit(a.task || '',iw-32,20,2);
    const progress=fit(a.progress || '',iw-32,20,2);
    const height=110+title.length*26+detail.length*26+(task.length?30+task.length*26:0)+(progress.length?12+progress.length*26:0)+30;
    return {a,title,detail,task,progress,height};
  });
  const contentH=cards.reduce((sum,card)=>sum+card.height+12,0);
  panelScroll=Math.max(0,Math.min(panelScroll,Math.max(0,(contentH-viewportH)*dpr)));
  panelMetrics={x:0,y:0,w:w*dpr,h:h*dpr,cardsTop:cardsTop*dpr,cardsBottom:cardsBottom*dpr,contentH:contentH*dpr,viewportH:viewportH*dpr,safeBottom:safeBottom*dpr,settingsBottom:312*dpr};
  c.save(); c.beginPath(); c.rect(8,cardsTop,w-16,viewportH); c.clip();
  let cy=Math.round(cardsTop-panelScroll/dpr);
  if(!cards.length) lines(fit(online?'Жду появления агентов…':'Запусти start-office.ps1',iw,20,2),pad,cy+20,colors.muted);
  for(const card of cards) {
    const {a}=card, kd=KIND[a.kind];
    const color=a.status==='working'?colors.green:a.status==='waiting'?colors.yellow:colors.violet;
    box(pad-4,cy,iw+8,card.height,colors.surface,'#443650');
    c.fillStyle=kd.tag||kd.head; c.fillRect(pad-4,cy+4,4,card.height-8);
    // Block-built avatar and status badge, using the same square pixels as the scene.
    c.fillStyle=kd.head; c.fillRect(pad+10,cy+16,24,20);
    c.fillStyle='#141124'; c.fillRect(pad+12,cy+24,20,8);
    c.fillStyle=kd.eye; c.fillRect(pad+16,cy+26,4,4); c.fillRect(pad+26,cy+26,4,4);
    c.fillStyle=kd.body; c.fillRect(pad+14,cy+36,16,6);
    text(fit(a.name,iw-56,30)[0] || a.name,pad+46,cy+10,30);
    const status=a.status==='working'?'РАБОТАЕТ':a.status==='waiting'?'ЖДЁТ ТЕБЯ':'ОТДЫХАЕТ';
    text(status,pad+10,cy+52,20,color);
    if(a.status==='working') for(let i=0;i<3;i++){c.fillStyle=(Math.floor(t*3)%3)===i?color:'#3c4b43';c.fillRect(w-pad-38+i*10,cy+58,6,6);}
    text('СЕЙЧАС',pad+10,cy+84,20,colors.muted);
    let ty=lines(card.title,pad+10,cy+110,color);
    ty=lines(card.detail,pad+10,ty,colors.muted);
    if(card.task.length){ text('ЗАДАЧА',pad+10,ty+4,20,colors.violet); ty=lines(card.task,pad+10,ty+30,colors.text); }
    if(card.progress.length){ ty=lines(card.progress,pad+10,ty+12,'#d8c9ec'); }
    const act=actRU(a,activity(a,t)) || 'в офисе';
    text(fit(`В офисе: ${act}`,iw-24)[0],pad+10,cy+card.height-26,20,'#9e8eb5');
    cy+=card.height+12;
  }
  if(contentH<viewportH-72 && cards.length){
    lines(fit('Помощники появятся, когда Codex запустит их для задачи.',iw,20,3),pad,cy+18,colors.muted);
  }
  c.restore();
  if(contentH>viewportH){
    const thumb=Math.max(28,Math.round(viewportH*viewportH/contentH));
    const sy=Math.round(cardsTop+(viewportH-thumb)*(panelScroll/dpr)/(contentH-viewportH));
    c.fillStyle=colors.violet; c.fillRect(w-8,sy,4,thumb);
  }
  c.fillStyle=colors.edge; c.fillRect(pad,cardsBottom+8,iw,2);
  text(contentH>viewportH?'КОЛЁСИКО ИЛИ СТРЕЛКИ: ПРОКРУТКА':'СОБЫТИЯ ОБНОВЛЯЮТСЯ КАЖДУЮ СЕКУНДУ',pad,cardsBottom+16,20,colors.muted);
  // The bottom area continues the panel, but contains no controls under the taskbar.
  ctx.save(); ctx.imageSmoothingEnabled=false; ctx.drawImage(uiCanvas,0,0,Math.round(w*dpr),Math.round(h*dpr)); ctx.restore();
}

addEventListener('wheel', e=>{
  if(!panelMetrics || !SHOW_HUD) return;
  const x=e.clientX*dpr,y=e.clientY*dpr,m=panelMetrics;
  if(x>=m.x && x<=m.x+m.w && y>=m.cardsTop && y<=m.cardsBottom){
    const delta=e.deltaY*(e.deltaMode===1?24:e.deltaMode===2?innerHeight:1);
    panelScroll=Math.max(0,Math.min(panelScroll+delta*dpr,Math.max(0,m.contentH-m.viewportH)));
    e.preventDefault();
  }
},{passive:false});

requestAnimationFrame(loop);
