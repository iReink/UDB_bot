(() => {
    const $=id=>document.getElementById(id),token=location.pathname.split('/').pop();
    let photos=[],cursor=0,index=0,loading=false;
    const viewer=$('albumViewer'),image=$('viewerImage');
    const back=$('albumBack');
    back.hidden=new URLSearchParams(location.search).get('from')!=='daily';
    back.addEventListener('click',event=>{
        const referrer=document.referrer ? new URL(document.referrer) : null;
        if(referrer && referrer.origin===location.origin && referrer.pathname==='/' && history.length>1){
            event.preventDefault();history.back();
        }
    });
    const stage=image.closest('.viewer-stage'),loadStatus=$('photoLoadStatus');
    const decodedPhotos=new Map(); // bounded decoded-image cache; HTTP cache handles the rest
    let imageGeneration=0,lastSwipeAt=0;
    function fullImage(url){
        if(decodedPhotos.has(url)){
            const cached=decodedPhotos.get(url);decodedPhotos.delete(url);decodedPhotos.set(url,cached);return cached;
        }
        const loader=new Image();loader.decoding='async';
        const entry={loader,ready:false};
        entry.promise=new Promise((resolve,reject)=>{
            loader.onload=async()=>{try{await loader.decode();}catch(_){} entry.ready=true;resolve(loader);};
            loader.onerror=()=>{decodedPhotos.delete(url);reject(Error('Не удалось загрузить фото'));};
        });
        decodedPhotos.set(url,entry);
        while(decodedPhotos.size>6)decodedPhotos.delete(decodedPhotos.keys().next().value);
        loader.src=url;
        return entry;
    }
    function showPhoto(photo){
        const generation=++imageGeneration,entry=fullImage(photo.image_url);
        stage.classList.toggle('is-loading',!entry.ready);stage.setAttribute('aria-busy',String(!entry.ready));
        loadStatus.hidden=entry.ready;loadStatus.textContent='Загрузка фото…';
        image.src=entry.ready?photo.image_url:photo.thumb_url;
        entry.promise.then(()=>{
            if(generation!==imageGeneration||!viewer.open)return;
            image.src=photo.image_url;stage.classList.remove('is-loading');stage.setAttribute('aria-busy','false');loadStatus.hidden=true;
        }).catch(()=>{
            if(generation!==imageGeneration||!viewer.open)return;
            stage.classList.remove('is-loading');stage.setAttribute('aria-busy','false');
            loadStatus.textContent='Не удалось загрузить фото — показано превью';loadStatus.hidden=false;
        });
    }
    async function load(){
        if(loading)return; loading=true;$('albumMore').disabled=true;
        try{
            const r=await fetch(`/api/albums/${encodeURIComponent(token)}?after=${cursor}`,{credentials:'same-origin'});
            if(!r.ok)throw Error(r.status===404?'Альбом не найден':'Не удалось загрузить фотографии');
            const data=await r.json(); $('albumTitle').textContent=data.name;document.title=data.name+' — фото';
            $('albumLogin').hidden=data.authenticated;
            photos.push(...data.photos);cursor=data.next_cursor;
            $('albumStatus').textContent=photos.length?'':'В альбоме пока нет фотографий.';
            render();$('albumMore').hidden=cursor===null;
        }catch(e){$('albumStatus').textContent=e.message;}
        finally{loading=false;$('albumMore').disabled=false;}
    }
    function tile(p,i,strip=false){
        const b=document.createElement('button');b.type='button';b.setAttribute('aria-label','Открыть фото '+(i+1));
        const img=document.createElement('img');img.src=p.thumb_url;img.loading='lazy';img.alt='';img.width=256;img.height=256;b.append(img);
        if(strip&&i===index)b.classList.add('selected');b.onclick=()=>open(i);return b;
    }
    function render(){
        $('albumGrid').replaceChildren(...photos.map((p,i)=>tile(p,i)));
        if(viewer.open)open(Math.min(index,photos.length-1));
    }
    function open(i){
        if(i<0||!photos.length){viewer.close();return;}
        index=Math.min(i,photos.length-1);const p=photos[index];
        showPhoto(p);$('albumCounter').textContent=`${index+1} / ${photos.length}${cursor!==null?' +':''}`;
        $('photoMenuButton').hidden=!(p.can_delete||p.can_move);$('photoMenu').hidden=true;
        $('photoDelete').hidden=!p.can_delete;$('photoMove').hidden=!p.can_move;
        $('photoPrev').disabled=index===0;$('photoNext').disabled=index===photos.length-1&&cursor===null;
        $('albumStrip').replaceChildren(...photos.map((p,j)=>tile(p,j,true)));
        if(!viewer.open)viewer.showModal();
        $('albumStrip').children[index]?.scrollIntoView({block:'nearest',inline:'center'});
    }
    async function next(){if(index===photos.length-1&&cursor!==null)await load();open(Math.min(index+1,photos.length-1));}
    $('albumMore').onclick=load;$('viewerClose').onclick=()=>viewer.close();
    $('photoPrev').onclick=()=>{if(Date.now()-lastSwipeAt>500)open(Math.max(0,index-1));};
    $('photoNext').onclick=()=>{if(Date.now()-lastSwipeAt>500)void next();};
    viewer.addEventListener('close',()=>{imageGeneration++;});
    $('photoMenuButton').onclick=()=>{$('photoMenu').hidden=!$('photoMenu').hidden;};
    $('photoMove').onclick=()=>{
        $('confirmText').textContent='Перемещение фото в другой дейлик будет реализовано позже';
        $('confirmYes').hidden=true;$('confirmCancel').textContent='Закрыть';$('albumConfirm').showModal();
    };
    $('photoDelete').onclick=()=>{
        $('confirmText').textContent='Удалить фотографию из альбома? В Telegram она останется.';
        $('confirmYes').hidden=false;$('confirmCancel').textContent='Отмена';$('albumConfirm').showModal();
    };
    $('confirmCancel').onclick=()=>$('albumConfirm').close();
    $('confirmYes').onclick=async()=>{
        const p=photos[index];$('confirmYes').disabled=true;
        try{
            const r=await fetch('/api/album-photos/'+p.id,{method:'DELETE',credentials:'same-origin'});
            if(!r.ok){const d=await r.json();throw Error(d.detail||'Не удалось удалить фото');}
            decodedPhotos.delete(p.image_url);
            photos=photos.filter(item=>item.id!==p.id);$('albumConfirm').close();render();
        }catch(e){$('confirmText').textContent=e.message;}finally{$('confirmYes').disabled=false;}
    };
    document.addEventListener('keydown',e=>{if(viewer.open&&!$('albumConfirm').open){if(e.key==='ArrowLeft')open(Math.max(0,index-1));if(e.key==='ArrowRight')void next();}});
    let startX=0;
    stage.addEventListener('touchstart',e=>{startX=e.changedTouches[0].clientX;},{passive:true});
    stage.addEventListener('touchend',e=>{const dx=e.changedTouches[0].clientX-startX;if(Math.abs(dx)>60){lastSwipeAt=Date.now();if(dx<0)void next();else open(Math.max(0,index-1));}},{passive:true});
    $('albumLogin').onclick=()=>{sessionStorage.setItem('albumReturn',location.pathname);location.href='/';};
    void load();
})();
