/* Shares existing Daily Chance cards/session and never preloads the whole history. */
let dailyDiscoveryResults = [];
let dailyDiscoveryCursor = null;
let dailyDiscoveryDate = '';
let dailyDiscoveryGeneration = 0;
let dailyDiscoveryLoading = false;
let dailyDiscoveryTimer;
let dailyCalendarGeneration = 0;
let dailyCalendarMonth = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Yekaterinburg',year:'numeric',month:'2-digit'}).format(new Date());
// en-CA month-only formatting is not guaranteed to be ISO in every browser.
const dailyDateParts = new Intl.DateTimeFormat('en', {timeZone:'Asia/Yekaterinburg',year:'numeric',month:'2-digit'}).formatToParts(new Date());
dailyCalendarMonth = dailyDateParts.find(p=>p.type==='year').value+'-'+dailyDateParts.find(p=>p.type==='month').value;
const dailySearchInput = document.getElementById('dailySearchInput');
const dailyCalendarToggle = document.getElementById('dailyCalendarToggle');
const dailyCalendar = document.getElementById('dailyCalendar');
const dailySearchStatus = document.getElementById('dailySearchStatus');
function dailyDiscoveryActive() { return Boolean(dailySearchInput.value.trim() || dailyDiscoveryDate || dailyOnlyMineFilter); }
function resetDailyDiscovery() {
    dailyDiscoveryGeneration++;
    dailyCalendarGeneration++;
    clearTimeout(dailyDiscoveryTimer);
    dailyDiscoveryResults=[]; dailyDiscoveryCursor=null; dailyDiscoveryLoading=false; dailyDiscoveryDate='';
    dailySearchInput.value=''; dailySearchStatus.textContent=''; dailyCalendar.classList.add('hidden');
    dailyCalendarToggle.textContent='📅';
}
async function refreshDailyDiscovery(more=false) {
    if (more && dailyDiscoveryLoading) return;
    const generation=more ? dailyDiscoveryGeneration : ++dailyDiscoveryGeneration;
    if (!more) { dailyDiscoveryResults=[]; dailyDiscoveryCursor=null; }
    if (!dailyDiscoveryActive()) { dailyDiscoveryLoading=false; dailySearchStatus.textContent=''; renderDailyPanel(); return; }
    dailyDiscoveryLoading=true;
    dailySearchStatus.textContent='Поиск…';
    renderDailyPanel();
    const params=new URLSearchParams({q:dailySearchInput.value.trim(),date:dailyDiscoveryDate,only_mine:String(dailyOnlyMineFilter)});
    if (more && dailyDiscoveryCursor) params.set('cursor',dailyDiscoveryCursor);
    try {
        const payload=await dailyApi('/api/daily/search?'+params);
        if (generation!==dailyDiscoveryGeneration) return;
        dailyDiscoveryResults=[...dailyDiscoveryResults,...payload.events.map(normalizeDailyEvent)];
        dailyDiscoveryCursor=payload.next_cursor;
        dailySearchStatus.textContent=dailyDiscoveryDate ? 'Дейлики за '+dailyDiscoveryDate : '';
    } catch(error) {
        if (generation===dailyDiscoveryGeneration) dailySearchStatus.textContent=error.message;
    } finally {
        if (generation===dailyDiscoveryGeneration) { dailyDiscoveryLoading=false; renderDailyPanel(); }
    }
}
function appendDailyDiscoveryMore() {
    if (!dailyDiscoveryActive() || !dailyDiscoveryCursor) return;
    const more=document.createElement('button'); more.className='daily-load-old-btn';
    more.textContent=dailyDiscoveryLoading?'Загрузка…':'Показать ещё'; more.disabled=dailyDiscoveryLoading;
    more.onclick=()=>refreshDailyDiscovery(true); dailyList.append(more);
}
async function showDailyCalendar() {
    const generation=++dailyCalendarGeneration;
    dailyCalendar.classList.remove('hidden'); dailyCalendar.textContent='Загрузка…';
    const params=new URLSearchParams({month:dailyCalendarMonth,q:dailySearchInput.value.trim(),only_mine:String(dailyOnlyMineFilter)});
    try {
        const data=await dailyApi('/api/daily/calendar?'+params);
        if (generation!==dailyCalendarGeneration) return;
        dailyCalendar.replaceChildren();
        const header=document.createElement('div'); header.className='daily-calendar-head';
        for (const delta of [-1,0,1]) {
            const el=document.createElement(delta?'button':'strong');
            el.textContent=delta<0?'‹':delta>0?'›':new Date(dailyCalendarMonth+'-01T12:00:00').toLocaleDateString('ru',{month:'long',year:'numeric'});
            if(delta) { el.type='button'; el.setAttribute('aria-label',delta<0?'Предыдущий месяц':'Следующий месяц'); el.onclick=()=>{
                const [y,m]=dailyCalendarMonth.split('-').map(Number); const d=new Date(y,m-1+delta,1,12);
                dailyCalendarMonth=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'); void showDailyCalendar();
            }; } header.append(el);
        }
        dailyCalendar.append(header);
        const grid=document.createElement('div');grid.className='daily-calendar-grid';
        for(const name of ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']) {const el=document.createElement('small');el.textContent=name;grid.append(el);}
        const [y,m]=dailyCalendarMonth.split('-').map(Number);
        const offset=(new Date(y,m-1,1).getDay()+6)%7;
        for(let i=0;i<offset;i++)grid.append(document.createElement('span'));
        for(let day=1;day<=new Date(y,m,0).getDate();day++) {
            const date=dailyCalendarMonth+'-'+String(day).padStart(2,'0');
            const item=data.days.find(d=>d.date===date);
            const button=document.createElement('button');button.type='button'; button.disabled=!item;
            button.textContent=day+(item?(item.has_photos?' 📷':' •'):'');
            button.setAttribute('aria-label',date+(item?`, дейликов: ${item.event_count}`:''));
            button.onclick=()=>{dailyDiscoveryDate=date;dailyCalendar.classList.add('hidden');dailyCalendarToggle.textContent='×';dailyCalendarToggle.setAttribute('aria-label','Сбросить дату');void refreshDailyDiscovery();};
            grid.append(button);
        }
        dailyCalendar.append(grid);
    } catch(error) {if(generation===dailyCalendarGeneration)dailyCalendar.textContent=error.message;}
}
dailySearchInput.addEventListener('input',()=>{
    clearTimeout(dailyDiscoveryTimer); dailyDiscoveryGeneration++; dailyDiscoveryLoading=false;
    dailyDiscoveryTimer=setTimeout(()=>{void refreshDailyDiscovery();if(!dailyCalendar.classList.contains('hidden'))void showDailyCalendar();},300);
});
dailyCalendarToggle.onclick=()=>{
    if(dailyDiscoveryDate){dailyDiscoveryDate='';dailyCalendarToggle.textContent='📅';dailyCalendarToggle.setAttribute('aria-label','Календарь');void refreshDailyDiscovery();}
    else if(dailyCalendar.classList.contains('hidden'))void showDailyCalendar();
    else {dailyCalendarGeneration++;dailyCalendar.classList.add('hidden');}
};
