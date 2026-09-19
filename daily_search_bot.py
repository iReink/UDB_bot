"""Owner-scoped, ephemeral Telegram discovery panels; independent of creation FSM."""
import asyncio
import calendar
import os
import re
import secrets
import time
from dataclasses import dataclass,field
from datetime import datetime,timedelta
from html import escape

from aiogram import BaseMiddleware,F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton as Button,InlineKeyboardMarkup as Keyboard
from aiogram.fsm.state import State, StatesGroup

import daily_search_store as store
import photo_albums as photos

TTL=1800
PROMPT_PREFIX='🔎 Поиск дейликов'


class SearchInput(StatesGroup):
    name=State()
    date=State()


INPUT_STATES={SearchInput.name.state,SearchInput.date.state}


@dataclass
class Search:
    key:str
    chat:int
    owner:int
    thread:int|None
    message:int=0
    q:str=''
    start:str=''
    end:str=''
    only_photos:bool=False
    page:int=0
    input_kind:str=''
    expires:float=field(default_factory=lambda:time.monotonic()+TTL)
    lock:asyncio.Lock=field(default_factory=asyncio.Lock)


sessions:dict[str,Search]={}


def prune():
    for key,s in list(sessions.items()):
        if s.expires<time.monotonic():sessions.pop(key,None)


def button(s,text,action):
    return Button(text=text,callback_data=f'dsearch:{s.key}:{action}')


def date_range(text):
    text=text.strip()
    try:
        if re.fullmatch(r'\d{2}\.\d{2}\.\d{4}',text):
            day=datetime.strptime(text,'%d.%m.%Y').date()
            return day.isoformat(),day.isoformat()
        if re.fullmatch(r'\d{2}\.\d{4}',text):
            day=datetime.strptime(text,'%m.%Y').date()
            return day.isoformat(),day.replace(day=calendar.monthrange(day.year,day.month)[1]).isoformat()
    except ValueError:
        raise ValueError('Такой даты не существует. Проверь день, месяц и год.') from None
    raise ValueError('Введите дату ДД.ММ.ГГГГ или месяц ММ.ГГГГ, например 12.09.2026 или 09.2026.')


async def edit(bot,s,text,rows):
    try:
        await bot.edit_message_text(text,chat_id=s.chat,message_id=s.message,
            reply_markup=Keyboard(inline_keyboard=rows),parse_mode='HTML',disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc):
            if 'message to edit not found' in str(exc):
                sessions.pop(s.key,None)
                await bot.send_message(s.chat,'Панель поиска удалена. Открой новый поиск через /daily.',message_thread_id=s.thread)
            else:raise


async def results(bot,s):
    rows,count,pages,s.page=await asyncio.to_thread(store.page,s.chat,s.owner,s.q,s.start,s.end,s.only_photos,s.page)
    date='любая' if not s.start else (s.start if s.start==s.end else f'{s.start} — {s.end}')
    text=(f'<b>🔎 Поиск дейликов</b>\n\nНазвание: {escape(s.q) if s.q else "любое"}\n'
          f'Дата: {date}\nФото: {"только с фото" if s.only_photos else "любые"}\n\n'
          f'Найдено: {count} · Страница {s.page+1} из {pages}\nСобытия расположены от новых к старым.')
    keys=[[button(s,'🔤 Название','name'),button(s,'📅 Дата','dates')],
          [button(s,('✅' if s.only_photos else '⬜')+' Только с фото','photos')]]
    for r in rows:
        day=datetime.strptime(r['date'],'%Y-%m-%d').strftime('%d.%m.%y')
        title=str(r['name'])
        title=title if len(title)<=38 else title[:37]+'…'
        keys.append([button(s,f'{day} · {title}'+(' 📷' if r['has_photos'] else ''),f'view.{r["id"]}')])
    nav=[]
    if s.page:nav.append(button(s,'← Назад',f'page.{s.page-1}'))
    if s.page+1<pages:nav.append(button(s,'Далее →',f'page.{s.page+1}'))
    if nav:keys.append(nav)
    if not count:text+='\n\nНичего не найдено. Попробуй изменить название, дату или отключить фильтр фотографий.'
    keys.extend([[button(s,'✖ Сбросить фильтры','reset')],[button(s,'⬅ В меню дейликов','home')]])
    await edit(bot,s,text,keys)


async def dates(bot,s):
    await edit(bot,s,'📅 Выбери период или введи дату.',[
        [button(s,'Сегодня','today'),button(s,'Вчера','yesterday')],
        [button(s,'Этот месяц','month'),button(s,'Прошлый месяц','previous')],
        [button(s,'✍️ Ввести дату или месяц','dateinput')],
        [button(s,'✖ Убрать дату','cleardate')],[button(s,'⬅ К результатам','list')]])


async def prompt(bot,s,kind,state,error=''):
    s.input_kind=kind
    await state.set_state(SearchInput.name if kind=='name' else SearchInput.date)
    await state.update_data(daily_search_key=s.key)
    hint=('Отправь название или часть названия дейлика. Например: «уктус» или «кофе».'
          if kind=='name' else 'Отправь дату ДД.ММ.ГГГГ или месяц ММ.ГГГГ. Например: 12.09.2026 или 09.2026.')
    text=PROMPT_PREFIX+f'\nДля <a href="tg://user?id={s.owner}">тебя</a>\n\n'+(escape(error)+'\n\n' if error else '')+hint
    text+='\n\nОтправь <code>-</code> или <code>—</code>, чтобы убрать этот фильтр. Отвечать на сообщение бота не нужно.'
    await edit(bot,s,text,[
        [button(s,'✖ Убрать название' if kind=='name' else '✖ Убрать дату','clearname' if kind=='name' else 'cleardate')],
        [button(s,'⬅ Отмена ввода','list')]])


async def card(bot,s,daily_id):
    from daily import get_daily_participants
    r=await asyncio.to_thread(store.event,s.chat,daily_id)
    if not r:
        await edit(bot,s,'Дейлик больше не существует.',[[button(s,'⬅ К результатам','list')]])
        return
    people=await asyncio.to_thread(get_daily_participants,daily_id,s.chat)
    summary=await asyncio.to_thread(photos.daily_summary,daily_id,s.chat)
    past=photos.event_time(r)<=datetime.now(photos.TZ)
    # Truncate raw fields before HTML escaping so long descriptions cannot break Telegram limits.
    text=(f'🎉 <b>{escape(str(r["name"])[:300])}</b>\n📅 {datetime.strptime(r["date"],"%Y-%m-%d").strftime("%d.%m.%Y")}, {r["time"]}\n'
          f'{"Прошедший дейлик" if past else "Предстоящий дейлик"}\n\n{escape(str(r.get("description") or "")[:1400])}\n\n'
          f'Участники: {escape(", ".join(str(p["name"]) for p in people)[:700]) or "никого"}')
    if r.get('link') and str(r['link']).startswith(('https://','http://')):
        text+=f'\n<a href="{escape(r["link"],quote=True)}">Информация</a>'
    drivers=[p for p in people if p.get('is_driver')]
    if drivers:
        text+='\n🚗 Водители: '+escape(', '.join(str(p['name']) for p in drivers)[:400])
    if not past and str(r.get('cars')) in ('да','1') and len(people)>len(drivers)*5:
        text+=f'\n⛔️ Не хватает машин: участников {len(people)}, мест {len(drivers)*5}.'
    keys=[]
    if not past:
        keys.append([button(s,'👋 Присоединиться / Отказаться',f'join.{daily_id}')])
        if str(r.get('cars')) in ('да','1'):
            keys.append([button(s,'🚗 Я водитель / Я не водитель',f'driver.{daily_id}')])
        keys.append([button(s,'Тегнуть участников',f'tag.{daily_id}')])
    if summary['photo_count']:
        text+=f'\n📷 Фотографий: {summary["photo_count"]}'
        base=os.getenv('PHOTO_PUBLIC_BASE_URL','').rstrip('/')
        if base:keys.append([Button(text=f'📷 Открыть фотоальбом · {summary["photo_count"]}',url=base+summary['album_url'])])
    keys.extend([[button(s,'⬅ К результатам','list')],[button(s,'⬅ В меню дейликов','home')]])
    await edit(bot,s,text,keys)


def participation(s,daily_id,action):
    with photos.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        r=c.execute('SELECT * FROM daily_events WHERE id=? AND chat_id=?',(daily_id,s.chat)).fetchone()
        if not r:raise ValueError('Дейлик удалён')
        if photos.event_time(r)<=datetime.now(photos.TZ):raise ValueError('Прошедший дейлик доступен только для просмотра')
        member=c.execute('SELECT is_driver FROM daily_participants WHERE daily_id=? AND user_id=?',(daily_id,s.owner)).fetchone()
        if action=='join':
            if member:c.execute('DELETE FROM daily_participants WHERE daily_id=? AND user_id=?',(daily_id,s.owner))
            else:c.execute('INSERT INTO daily_participants(daily_id,user_id,is_driver) VALUES (?,?,0)',(daily_id,s.owner))
        elif action=='driver':
            if not member:raise ValueError('Сначала нужно участвовать')
            if str(r['cars']) not in ('да','1'):raise ValueError('Для этого дейлика водители не требуются')
            c.execute('UPDATE daily_participants SET is_driver=? WHERE daily_id=? AND user_id=?',(not member[0],daily_id,s.owner))
    return dict(r)


async def clear_input(state):
    if state and await state.get_state() in INPUT_STATES:
        key=(await state.get_data()).get('daily_search_key')
        if key in sessions:sessions[key].input_kind=''
        await state.clear()


class SearchMessages(BaseMiddleware):
    async def __call__(self,handler,message,data):
        state=data.get('state')
        if not state or await state.get_state() not in INPUT_STATES:
            return await handler(message,data)
        prune()
        match=sessions.get((await state.get_data()).get('daily_search_key'))
        if not match or not match.input_kind:
            await clear_input(state);data['raw_state']=None
            return await handler(message,data)
        if (match.chat,match.owner,match.thread)!=(message.chat.id,
            message.from_user.id if message.from_user and not message.sender_chat else None,message.message_thread_id):
            return await handler(message,data)
        value=(message.text or '').strip()
        if value.startswith('/'):
            await clear_input(state);data['raw_state']=None
            if value.split()[0].split('@')[0] == '/cancel':
                await results(message.bot,match);return
            return await handler(message,data)
        async with match.lock:
            if not match.input_kind or await state.get_state() not in INPUT_STATES:
                return await handler(message,data)
            match.expires=time.monotonic()+TTL
            try:
                if value in ('-','—'):
                    if match.input_kind=='name':match.q=''
                    else:match.start=match.end=''
                elif match.input_kind=='name':
                    if not value or len(value)>200:raise ValueError('Название должно содержать от 1 до 200 символов.')
                    match.q=value
                else:match.start,match.end=date_range(value)
            except ValueError as exc:
                await prompt(message.bot,match,match.input_kind,state,str(exc));return
            await clear_input(state);data['raw_state']=None;match.page=0
            await results(message.bot,match)


class LeaveSearchInput(BaseMiddleware):
    async def __call__(self,handler,query,data):
        if not (query.data or '').startswith('dsearch:'):
            state=data.get('state')
            if state and await state.get_state() in INPUT_STATES:
                await clear_input(state);data['raw_state']=None
        return await handler(query,data)


def register(dp,home):
    dp.message.outer_middleware(SearchMessages())
    dp.callback_query.outer_middleware(LeaveSearchInput())

    @dp.callback_query(F.data.startswith('dsearch:'))
    async def callback(query,state):
        prune()
        if not query.message:return await query.answer()
        if query.data=='dsearch:open':
            await clear_input(state)
            if await state.get_state():
                await query.answer('Сначала заверши текущее создание или редактирование дейлика.',show_alert=True);return
            for key,old in list(sessions.items()):
                if (old.chat,old.owner,old.thread)==(query.message.chat.id,query.from_user.id,query.message.message_thread_id):sessions.pop(key,None)
            if len(sessions)>=1000:sessions.pop(min(sessions,key=lambda k:sessions[k].expires),None)
            s=Search(secrets.token_hex(5),query.message.chat.id,query.from_user.id,query.message.message_thread_id)
            await query.answer()
            msg=await query.message.answer('🔎 Открываю поиск…')
            s.message=msg.message_id;sessions[s.key]=s
            await results(query.bot,s);return
        parts=query.data.split(':',2)
        s=sessions.get(parts[1]) if len(parts)==3 else None
        if not s:
            await clear_input(state)
            await query.answer('Поиск устарел. Открой новый через /daily.',show_alert=True);return
        if s.owner!=query.from_user.id or s.chat!=query.message.chat.id or s.message!=query.message.message_id:
            await query.answer('Открой свой поиск через /daily',show_alert=True);return
        current=await state.get_state()
        if current and current not in INPUT_STATES:
            await query.answer('Сначала заверши текущее создание или редактирование дейлика.',show_alert=True);return
        async with s.lock:
            await clear_input(state)
            s.expires=time.monotonic()+TTL;s.input_kind=''
            action=parts[2]
            if action=='home':
                sessions.pop(s.key,None);await query.answer();await home(query.message);return
            if action in ('name','dateinput'):
                await query.answer();await prompt(query.bot,s,'name' if action=='name' else 'date',state);return
            if action=='dates':await query.answer();await dates(query.bot,s);return
            if action.startswith(('view.','join.','driver.','tag.')):
                verb,raw_id=action.split('.',1)
                if not raw_id.isdigit():await query.answer();return
                daily_id=int(raw_id)
                try:
                    if verb in ('join','driver','tag'):
                        r=await asyncio.to_thread(participation,s,daily_id,verb)
                        if verb=='tag':
                            from daily import get_daily_participants
                            people=await asyncio.to_thread(get_daily_participants,daily_id,s.chat)
                            if not people:raise ValueError('В этом дейлике пока нет участников')
                            mentions=[f'<a href="tg://user?id={p["user_id"]}">{escape(str(p["name"])[:80])}</a>' for p in people]
                            # Bound each Telegram message without breaking HTML entities.
                            chunk=f'{escape(query.from_user.full_name)} тегает участников дейлика <b>{escape(str(r["name"])[:200])}</b>:\n'
                            for mention in mentions:
                                if len(chunk)+len(mention)>3500:await query.message.answer(chunk,parse_mode='HTML');chunk=''
                                chunk+=mention+' '
                            if chunk:await query.message.answer(chunk,parse_mode='HTML')
                    await query.answer('Готово' if verb!='view' else None)
                except ValueError as exc:
                    await query.answer(str(exc),show_alert=True)
                await card(query.bot,s,daily_id);return
            if action=='photos':s.only_photos=not s.only_photos;s.page=0
            elif action=='reset':s.q=s.start=s.end='';s.only_photos=False;s.page=0
            elif action=='clearname':s.q='';s.page=0
            elif action=='cleardate':s.start=s.end='';s.page=0
            elif action.startswith('page.'):
                try:s.page=max(0,int(action.split('.')[1]))
                except ValueError:s.page=0
            elif action in ('today','yesterday','month','previous'):
                day=datetime.now(photos.TZ).date()
                if action=='yesterday':day-=timedelta(days=1)
                if action=='previous':day=day.replace(day=1)-timedelta(days=1)
                s.start,s.end=date_range(day.strftime('%m.%Y' if action in ('month','previous') else '%d.%m.%Y'));s.page=0
            await query.answer();await results(query.bot,s)
