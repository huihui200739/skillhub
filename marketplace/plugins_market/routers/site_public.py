import asyncio

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from plugins_market.services.privacy_statement_file import read_privacy_statement_markdown

router = APIRouter(prefix="/site", tags=["site"])


@router.get("/privacy-statement")
async def get_privacy_statement():
    """直接返回 Markdown 正文（非 JSON）；浏览器新标签打开即可查看。"""
    text = await asyncio.to_thread(read_privacy_statement_markdown)
    return PlainTextResponse(
        content=text or "",
        media_type="text/markdown; charset=utf-8",
    )
