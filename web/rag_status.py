"""Read-only RAG status, protected by the existing administrator session."""
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import rag_repository


def register(app, templates, db_path, require_session, admin_ids):
    @app.get('/rag_status',response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse('rag_status.html',{'request':request},headers={'Cache-Control':'no-store'})

    @app.get('/api/rag/status')
    def status(request: Request):
        payload=require_session(request)
        if int(payload['telegram_user_id']) not in admin_ids:
            raise HTTPException(403,'Доступ только для администратора')
        return JSONResponse(rag_repository.snapshot(db_path),headers={'Cache-Control':'no-store'})
