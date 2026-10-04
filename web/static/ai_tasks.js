"use strict";
const $ = id => document.getElementById(id);
const labels = {data_analysis:"Анализ данных",web_search:"Веб-поиск",maps:"Поиск на карте",ignore:"Без ответа",imagegen:"Генерация изображения",mechanics:"Механики бота",response:"Общение",text_to_sql:"Запрос к БД",data_analysis_sql:"SQL для анализа",data_analysis_response:"Анализ данных",profile_update:"Профиль",chat_summary:"Саммари",type_check:"Типизация",search_plan:"План поиска",web_grounding:"Веб-поиск Google",maps_grounding:"Поиск Google Maps",maps_translation:"Перевод Maps",grounding_notice:"Уточнение места"};
const statuses = {pending:"В очереди",processing:"В работе",done:"Выполнено",failed:"Ошибка",cancelled:"Отменено",waiting:"Ожидание источника",retry:"Повторная попытка",delivery_unknown:"Доставка не подтверждена",fallback:"Резервный сценарий"};
const colors = ["#5474df","#71b4a7","#c6a477","#9f8ac1","#82a7ca","#a5b583","#d194a1","#8292a9"];
let page = 1, pages = 1, loading = false, requestVersion = 0, modalVersion = 0, modalData, historyData, historyId, historyBusy = false, returnFocus;
function el(tag, cls, text) { const n = document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=String(text); return n; }
function summaryDate(value) { return date(value && !/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value+"+05:00" : value); }
function date(value) { if(!value)return "—"; const parsed=new Date(value.match(/(?:Z|[+-]\d\d:\d\d)$/)?value:value+"Z"); return Number.isNaN(+parsed)?value:parsed.toLocaleString("ru-RU",{day:"2-digit",month:"short",year:"numeric",hour:"2-digit",minute:"2-digit"}); }
async function api(path) { const r = await fetch(path,{cache:"no-store"}); if(!r.ok) { const e=new Error(r.status===403?"Доступ только для администратора":r.status===401?"Требуется вход":"Не удалось загрузить данные"); e.status=r.status; throw e; } return r.json(); }
function options(id, items, first) { const select=$(id), value=select.value; const signature=JSON.stringify(items); if(select.dataset.signature===signature)return; select.dataset.signature=signature; select.replaceChildren(new Option(first,""),...items.map(i=>new Option(i.title,i.value))); select.value=value; }
let modelsExpanded=false, chartStats=[];
function chart(stats) {
  chartStats=stats;
  const models=new Map(), types=new Map(); let total=0,historical=0;
  for(const item of stats) { models.set(item.model,(models.get(item.model)||0)+item.count); types.set(item.task_type,(types.get(item.task_type)||0)+item.count); total+=item.count;historical+=item.historical; }
  $("call-count").textContent=total.toLocaleString("ru-RU"); $("model-chart").replaceChildren();$("type-chart").replaceChildren();
  const sorted=[...models].sort((a,b)=>b[1]-a[1]), max=Math.max(1,...models.values());
  $("models-more").hidden=sorted.length<=5;$("models-more").textContent=modelsExpanded?"свернуть":"ещё";$("models-more").setAttribute("aria-expanded",String(modelsExpanded));
  for(const [name,count] of (modelsExpanded?sorted:sorted.slice(0,5))) { const row=el("div","bar-row"),label=el("span","bar-name",name), track=el("div","bar-track"), fill=el("div","bar-fill"); label.title=name;fill.style.width=(count/max*100)+"%";track.append(fill);row.append(label,track,el("span","bar-count",count));$("model-chart").append(row); }
  const strip=el("div","distribution-strip"),legend=el("div","legend");
  [...types].sort((a,b)=>b[1]-a[1]).forEach(([name,count],i)=>{const c=colors[i%colors.length], segment=el("span");segment.style.background=c;segment.style.flex=count;segment.title=`${labels[name]||name}: ${count}`;strip.append(segment);const entry=el("div","legend-item"),swatch=el("i","swatch");swatch.style.background=c;entry.append(swatch,el("span",null,labels[name]||name),el("b",null,count));legend.append(entry);});
  if(total)$("type-chart").append(strip,legend);else {$("model-chart").append(el("p","caption","Обращений за этот период нет"));$("type-chart").append(el("p","caption","Здесь появится распределение задач"));}
  $("coverage").textContent=`Учитываются генеративные вызовы, включая ошибки и резервные модели. ${historical?`${historical} исторических обращений: без полного аудита повторов. `:""}Старые задачи без журнала отнесены к gemma4:e4b: одно обращение на задачу.`;
}
function outcome(row) {
  const receipt=row.receipt||{}, pieces=[];
  if(receipt.final_task_id)pieces.push(`Новая задача #${receipt.final_task_id}`);
  if(receipt.response_task_id)pieces.push(`Задача ответа #${receipt.response_task_id}`);
  if(receipt.response_message_id||row.response_message_id)pieces.push(`Ответ в чат #${receipt.response_message_id||row.response_message_id}`);
  if(!pieces.length&&row.status==="done")pieces.push(row.task_type==="profile_update"?"Профиль сохранён":row.task_type==="chat_summary"?"Саммари сохранён":"Результат принят");
  return pieces.join(" · ");
}
function render(data, force=false) {
  chart(data.stats); options("chat",data.chats.map(c=>({value:String(c.id),title:`${c.title} (${c.id})`})),"Все чаты");options("kind",data.kinds.map(k=>({value:k,title:labels[k]||k})),"Все типы");options("model",data.models.map(m=>({value:m,title:m})),"Все модели");
  const focusKey=$("rows").contains(document.activeElement)?document.activeElement.dataset.key:null;
  const rows=[];
  for(const row of data.rows) {const tr=el("tr"),dt=el("td",null,date(row.created_at)),kind=el("td",null,labels[row.task_type]||row.task_type);kind.append(el("span","secondary",`#${row.id} · ${row.queue}`));const chat=el("td",null,data.chats.find(c=>String(c.id)===String(row.chat_id))?.title||`Чат ${row.chat_id}`);chat.title=String(row.chat_id);tr.append(dt,chat,kind);
    for(const column of ["context","model","response","result"]) {const td=el("td");if(column==="model") {td.append(el("span",null,row.actual_model||"Ещё не исполнена / неизвестна"));if(row.assumed_model)td.append(el("span","secondary","Историческая модель"));if(!row.actual_model)td.className="muted";}else{const text=column==="context"?row.context_preview:column==="response"?(row.task_type==="type_check"?(labels[row.classification_type]||row.classification_type||row.response_preview):row.response_preview):(statuses[row.status]||row.status);const button=el("button","cell",(text||"—").replace(/\s+/g," "));button.type="button";if(column==="response"&&row.task_type==="type_check"&&row.classification_type)button.title=row.classification_type;button.dataset.key=`${row.queue}/${row.id}/${column}`;button.setAttribute("aria-label",`${column==="context"?"Контекст":column==="response"?"Ответ":"Результат"} задачи ${row.id}`);button.addEventListener("click",()=>open(row,column));if(column==="result"){button.classList.add("status",row.status);const note=outcome(row);td.append(button);if(note)td.append(el("span","secondary",note));}else td.append(button);}tr.append(td);}
    rows.push(tr);
  }
  $("rows").replaceChildren(...rows);
  if(focusKey)[...$("rows").querySelectorAll("button")].find(b=>b.dataset.key===focusKey)?.focus({preventScroll:true});
  page=data.page;pages=data.pages;$("task-count").textContent=data.total.toLocaleString("ru-RU");$("empty").hidden=!!data.total;$("page-label").textContent=`${page} / ${pages}`;$("prev-page").disabled=page<=1;$("next-page").disabled=page>=pages;
  $("live").classList.remove("error");$("live").textContent="● Обновлено "+new Date().toLocaleTimeString("ru-RU");
}
async function refresh(force=false) {
  if(loading&&!force)return; const version=++requestVersion;loading=true;$("refresh-tasks").disabled=true;
  const query=new URLSearchParams({page:String(page)});for(const id of ["chat","kind","model"])if($(id).value)query.set(id,$(id).value);
  if($("days").value==="custom") {if(!$("start").value||!$("end").value){loading=false;$("refresh-tasks").disabled=false;return;} query.set("start",$("start").value);query.set("end",$("end").value);}else query.set("days",$("days").value);
  try {const data=await api("/api/ai-dashboard/tasks?"+query);if(version!==requestVersion)return;$("login").hidden=true;$("dashboard").hidden=false;$("refresh-tasks").hidden=false;render(data,force);}
  catch(e) {if(version!==requestVersion)return;$("live").textContent=e.message+" · повтор через 2 минуты";$("live").classList.add("error");if(e.status===401||e.status===403){$("dashboard").hidden=true;$("refresh-tasks").hidden=true;$("login").hidden=false;$("login-error").textContent=e.status===403?e.message:"";}}
  finally {if(version===requestVersion){loading=false;$("refresh-tasks").disabled=false;}}
}
function parse(value) {if(typeof value!=="string")return value;const clean=value.trim().replace(/^```(?:json)?\s*/i,"").replace(/\s*```$/,"");try{return JSON.parse(clean);}catch{return value;}}
const fieldLabels={contents:"Сообщения",messages:"Сообщения",parts:"Содержимое",text:"Текст",role:"Роль",systemInstruction:"Системная инструкция",system:"Системная инструкция",generationConfig:"Параметры генерации",content:"Содержимое",prompt:"Запрос",short_summary:"Краткое описание",profile_json:"Профиль",summary_text:"Саммари"};
function structured(value, depth=0) {
  value=parse(value);const container=el("div",depth?"nested":"");
  if(value!==null&&typeof value==="object"&&depth<12){for(const [key,v] of Object.entries(value)){const field=el("section","field");field.append(el("div","field-name",Array.isArray(value)?`Элемент ${Number(key)+1}`:fieldLabels[key]||key),structured(v,depth+1));container.append(field);}}
  else {const text=typeof value==="object"?JSON.stringify(value,null,2):String(value??"Нет данных");if(typeof value==="string"&&depth===0&&value.includes("\n\n")){for(const block of value.split(/\n\n+/)){const f=el("section","field"), match=block.match(/^([^\n]{1,90}):\s*\n([\s\S]*)$/);if(match){f.append(el("div","field-name",match[1]),structured(match[2],depth+1));}else f.append(el("div","field-value",block));container.append(f);}}else container.append(el("div","field-value",text));}
  return container;
}
const profileLabels={communication_style:"Стиль общения",stable_interests:"Интересы",interests:"Интересы",preferences:"Предпочтения",current_topics:"Текущие темы",behavior_notes:"Особенности поведения",local_memes:"Мемы и шутки",facts:"Факты",do_not_assume:"Не стоит предполагать",confidence:"Уверенность в профиле",notes:"Заметки"};
function profileValue(value) {
  const box=el("div");
  if(Array.isArray(value)){const list=el("ul");for(const item of value){const li=el("li");li.append(profileValue(item));list.append(li);}box.append(list);}
  else if(value&&typeof value==="object"){for(const [key,item] of Object.entries(value)){const p=el("section","profile-detail");p.append(el("strong",null,profileLabels[key]||key.replaceAll("_"," ")),profileValue(item));box.append(p);}}
  else for(const paragraph of String(value??"").split(/\n\n+/))box.append(el("p",null,paragraph));
  return box;
}
function profileView(value,summary) {
  const box=el("div","profile-view"),profile=parse(value)||{};
  const lead=profile.short_summary||summary;if(lead)box.append(el("p","profile-lead",lead));
  for(const [key,raw] of Object.entries(profile)){
    if(["display_name","short_summary"].includes(key)||raw==null||raw===""||(Array.isArray(raw)&&!raw.length))continue;
    const row=el("section","profile-section"),body=el("div","profile-body");
    const value=key==="confidence"?({low:"Низкая",medium:"Средняя",high:"Высокая"}[raw]||raw):raw;
    body.append(profileValue(value));row.append(el("h3","profile-heading",profileLabels[key]||key.replaceAll("_"," ")),body);box.append(row);
  }return box;
}
function profileAvatar(user,title) {
  const avatar=$("modal-avatar");avatar.hidden=false;if(avatar.dataset.user===String(user))return;
  avatar.dataset.user=String(user);avatar.replaceChildren(el("span",null,title.replace(/\(.*/,"").trim().split(/\s+/).slice(0,2).map(s=>s[0]).join("").toUpperCase()));
  const img=document.createElement("img");img.alt="";img.addEventListener("error",()=>img.remove());img.src=`/api/ai-dashboard/avatar/tasks/${modalData.task.id}`;avatar.append(img);
}
function showAttempt(index,column) {
  const call=modalData.calls[index];$("content").replaceChildren();
  if(call){$("content").append(structured(column==="context"?call.context:call.response??{error:call.error||"Ответ не получен"}));$("modal-meta").textContent=`${call.model} · ${call.provider} · ${date(call.at)} · ${call.status==="error"?"Ошибка вызова":"Ответ получен"}`;}
  else {$("modal-meta").textContent="Историческая запись: точный переданный контекст и промежуточные ответы не сохранены.";$("content").append(structured(column==="context"?modalData.task.prompt:modalData.task.result_text||modalData.task.result_json||modalData.task.result_type||modalData.task.error_text));}
  if(column==="context"&&modalData.rag){const details=el("details","field"),summary=el("summary",null,"Поиск контекста · "+modalData.rag.state);details.append(summary,structured(modalData.rag.result||{query:modalData.rag.query,error:modalData.rag.error}));$("content").append(details);}
  [...$("attempts").children].forEach((n,i)=>n.classList.toggle("active",i===index));
}
async function open(row,column) {
  const version=++modalVersion;returnFocus=document.activeElement;modalData=null;historyData=null;historyId=row.id;$("modal").className="";$("modal-avatar").hidden=true;delete $("modal-avatar").dataset.user;document.querySelector(".modal-scroll").scrollTop=0;$("navigation").hidden=true;$("attempts").replaceChildren();$("content").replaceChildren(el("p",null,"Загрузка…"));$("modal-label").textContent=labels[row.task_type]||row.task_type;$("modal-title").textContent=column==="context"?"Переданный контекст":column==="response"?"Ответ модели":"Результат выполнения";$("modal-meta").textContent=`Задача #${row.id} · ${date(row.created_at)}`;$("modal").showModal();
  try {const data=await api(`/api/ai-dashboard/detail/${row.queue}/${row.id}`);if(version!==modalVersion)return;modalData=data;
    if(column==="result") {const result={status:statuses[data.task.status]||data.task.status,response_message_id:data.task.response_message_id,error:data.task.error_text,attempts:data.attempts.map(a=>({model:a.model,status:statuses[a.outcome]||a.outcome,result:a.receipt}))};$("content").replaceChildren(structured(result));return;}
    if(column==="response"&&["profile_update","chat_summary"].includes(row.task_type)) {const h=await api(`/api/ai-dashboard/history/${row.queue}/${row.id}`);if(version!==modalVersion)return;if(h.items.length){historyData=h;renderHistory();return;}}
    data.calls.forEach((c,i)=>{const b=el("button",null,`${i+1} · ${c.model}${c.status==="error"?" · ошибка":""}`);b.addEventListener("click",()=>showAttempt(i,column));$("attempts").append(b);});showAttempt(data.calls.length-1,column);
  }catch(e){if(version===modalVersion)$("content").replaceChildren(el("p","error",e.message));}
}
function renderHistory(direction=0) {
  const h=historyData, current=h.items[h.index], profile=modalData.task.task_type==="profile_update";$("modal").className=profile?"dossier":"summary";$("navigation").hidden=false;$("previous").disabled=h.index===0;$("following").disabled=h.index===h.items.length-1;$("attempts").replaceChildren();$("content").replaceChildren();
  if(profile) {const payload=parse(modalData.task.payload_json)||{},data=parse(current.profile_json)||{},title=data.display_name||payload.display_name||`Профиль пользователя ${current.user_id}`;$("modal-title").textContent=title;$("modal-label").textContent="ДОСЬЕ";$("modal-meta").textContent=`Пользователь ${current.user_id} · чат ${current.chat_id}`;$("history-date").textContent=current.profile_date;$("content").append(profileView(data,current.summary_text));profileAvatar(current.user_id,title);document.querySelector(".modal-scroll").scrollTop=0;}
  else {$("modal-title").textContent="Хроника чата";$("modal-label").textContent="САММАРИ";$("modal-meta").textContent=`Чат ${current.chat_id} · колесо мыши или ← → для перелистывания`;$("history-date").textContent=summaryDate(current.window_end);
    h.items.forEach((item,i)=>{const delta=i-h.index,card=el("article","summary-card");card.style.transform=`translateY(${delta*105}%) scale(${Math.max(.72,1-Math.abs(delta)*.12)})`;card.style.opacity=delta===0?"1":String(Math.max(0,.25-Math.abs(delta)*.08));card.style.pointerEvents=delta===0?"auto":"none";card.setAttribute("aria-hidden",delta===0?"false":"true");card.append(el("div","field-name",`${summaryDate(item.window_start)} — ${summaryDate(item.window_end)}`),el("div",null,item.summary_text));$("content").append(card);if(direction&&!matchMedia('(prefers-reduced-motion: reduce)').matches)card.animate([{transform:`translateY(${(delta+direction)*105}%) scale(${Math.max(.72,1-Math.abs(delta+direction)*.12)})`,opacity:delta+direction===0?1:.1},{transform:card.style.transform,opacity:card.style.opacity}],{duration:350,easing:'ease-out'});});}
}
async function navigate(direction) {
  if(!historyData||historyBusy)return;const next=historyData.items[historyData.index+direction];if(!next||!next.task_id)return;historyBusy=true;const version=modalVersion;
  try {const h=await api(`/api/ai-dashboard/history/tasks/${next.task_id}`);if(version===modalVersion&&h.items.length){historyData=h;historyId=next.task_id;renderHistory(direction);}}
  catch(e){$("modal-meta").textContent=e.message;}finally{historyBusy=false;}
}
function changed() {page=1;refresh(true);}
for(const id of ["chat","kind","model","start","end"])$(id).addEventListener("change",changed);
$("days").addEventListener("change",()=>{$("dates").hidden=$("days").value!=="custom";changed();});
document.querySelectorAll("[data-reset]").forEach(b=>b.addEventListener("click",()=>{$(b.dataset.reset).value="";changed();}));
$("reset-all").addEventListener("click",()=>{for(const id of ["chat","kind","model","start","end"])$(id).value="";$("days").value="7";$("dates").hidden=true;changed();});
$("prev-page").addEventListener("click",()=>{if(page>1){page--;refresh(true);}});$("next-page").addEventListener("click",()=>{if(page<pages){page++;refresh(true);}});
$("close").addEventListener("click",()=>$("modal").close());$("modal").addEventListener("close",()=>{modalVersion++;historyData=null;const target=returnFocus?.isConnected?returnFocus:[...$("rows").querySelectorAll("button")].find(b=>b.dataset.key===returnFocus?.dataset.key);target?.focus({preventScroll:true});});$("previous").addEventListener("click",()=>navigate(-1));$("following").addEventListener("click",()=>navigate(1));
let wheelAt=0;$("content").addEventListener("wheel",e=>{if(!historyData||!$("modal").classList.contains("summary"))return;const active=$("content").querySelector('[aria-hidden="false"]');const scrollable=active&&active.scrollHeight>active.clientHeight+1;const atEdge=!scrollable||(e.deltaY>0?active.scrollTop+active.clientHeight>=active.scrollHeight-2:active.scrollTop<=1);if(!atEdge)return;e.preventDefault();if(Math.abs(e.deltaY)>10&&Date.now()-wheelAt>400){wheelAt=Date.now();navigate(e.deltaY>0?1:-1);}},{passive:false});
$("modal").addEventListener("keydown",e=>{if(historyData&&["ArrowLeft","ArrowRight"].includes(e.key)){e.preventDefault();navigate(e.key==="ArrowRight"?1:-1);}});
$("login-form").addEventListener("submit",async e=>{e.preventDefault();try{const r=await fetch("/api/auth/code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:$("code").value.trim()})});if(!r.ok)throw new Error("Код недействителен или истёк. Получите новый командой /code.");$("code").value="";await refresh(true);}catch(err){$("login-error").textContent=err.message;}});
$("refresh-tasks").addEventListener("click",()=>refresh());
setInterval(()=>{if(!document.hidden)refresh();},120000);document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});refresh();

let outsidePointer=false;
function outsideModal(event){const r=$("modal").getBoundingClientRect();return event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom;}
$("modal").addEventListener("pointerdown",e=>{outsidePointer=outsideModal(e);});
$("modal").addEventListener("pointerup",e=>{if(outsidePointer&&outsideModal(e))$("modal").close();outsidePointer=false;});

$("models-more").addEventListener("click",()=>{modelsExpanded=!modelsExpanded;chart(chartStats);});
