from aiohttp import web
import re
import math
import logging
import secrets
import mimetypes
from aiohttp.http_exceptions import BadStatusLine
from dreamxbotz.Bot import multi_clients, work_loads, temp
from dreamxbotz.server.exceptions import FIleNotFound, InvalidHash
from dreamxbotz.util.custom_dl import ByteStreamer
from dreamxbotz.util.render_template import render_page
from database.users_chats_db import db
from utils import get_shortlink, get_settings
from info import *


routes = web.RouteTableDef()

@routes.get("/favicon.ico")
async def favicon_route_handler(request):
    return web.FileResponse('dreamxbotz/template/favicon.ico')

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.json_response("dreamxbotz")

# ============================
# 5-Second Verify Redirect Route
# ============================
@routes.get("/verify", allow_head=True)
async def verify_redirect_handler(request: web.Request):
    try:
        type_ = request.query.get("type", "notcopy")
        user_id = int(request.query.get("user_id"))
        verify_id = request.query.get("verify_id")
        file_id = request.query.get("file_id")
        grp_id = int(request.query.get("grp_id", 0))

        settings = await get_settings(grp_id)
        is_second_shortener = await db.use_second_shortener(user_id, settings.get('verify_time', TWO_VERIFY_GAP))
        is_third_shortener = await db.use_third_shortener(user_id, settings.get('third_verify_time', THREE_VERIFY_GAP))

        bot_target_link = f"https://telegram.me/{temp.U_NAME}?start={type_}_{user_id}_{verify_id}_{file_id}"
        short_url = await get_shortlink(bot_target_link, grp_id, is_second_shortener, is_third_shortener)

        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <meta http-equiv="refresh" content="5;url={short_url}">
            <title>Please Wait...</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    background-color: #0f172a;
                    color: #ffffff;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }}
                .card {{
                    background: #1e293b;
                    padding: 30px;
                    border-radius: 12px;
                    text-align: center;
                    box-shadow: 0 4px 20px rgba(0,0,0,0.5);
                    max-width: 90%;
                    width: 380px;
                }}
                .timer {{
                    font-size: 38px;
                    font-weight: bold;
                    color: #38bdf8;
                    margin: 15px 0;
                }}
                .btn {{
                    display: inline-block;
                    background: #2563eb;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 6px;
                    font-size: 14px;
                    margin-top: 10px;
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Verifying Link 🔐</h2>
                <p>Redirecting to Shortener in</p>
                <div class="timer" id="time">5</div>
                <p style="font-size: 13px; color: #94a3b8;">Agar automatically redirect na ho toh:</p>
                <a class="btn" href="{short_url}">Click Here to Proceed</a>
            </div>
            <script>
                let sec = 5;
                const timerElement = document.getElementById('time');
                const interval = setInterval(() => {{
                    sec--;
                    timerElement.textContent = sec;
                    if (sec <= 0) {{
                        clearInterval(interval);
                        window.location.href = "{short_url}";
                    }}
                }}, 1000);
            </script>
        </body>
        </html>
        """
        return web.Response(text=html, content_type="text/html")
    except Exception as e:
        logging.error(f"Error in /verify route: {e}")
        return web.Response(text=f"Verification Error: {str(e)}", status=500)

@routes.get(r"/watch/{path:\S+}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
        if match:
            secure_hash = match.group(1)
            id = int(match.group(2))
        else:
            id = int(re.search(r"(\d+)(?:\/\S+)?", path).group(1))
            secure_hash = request.rel_url.query.get("hash")
        return web.Response(text=await render_page(id, secure_hash), content_type='text/html')
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass
    except Exception as e:
        logging.critical(e.with_traceback(None))
        raise web.HTTPInternalServerError(text=str(e))

@routes.get(r"/{path:\S+}", allow_head=True)
async def stream_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
        if match:
            secure_hash = match.group(1)
            id = int(match.group(2))
        else:
            id_match = re.search(r"(\d+)(?:\/\S+)?", path)
            if not id_match:
                raise web.HTTPNotFound(text="Not found")
            id = int(id_match.group(1))
            secure_hash = request.rel_url.query.get("hash")
        
        return await media_streamer(request, id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except web.HTTPNotFound:
        raise
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass
    except Exception as e:
        logging.critical(e.with_traceback(None))
        raise web.HTTPInternalServerError(text=str(e))

class_cache = {}

async def media_streamer(request: web.Request, id: int, secure_hash: str):
    range_header = request.headers.get("Range", 0)
    
    index = min(work_loads, key=work_loads.get)
    faster_client = multi_clients[index]
    
    if MULTI_CLIENT:
        logging.info(f"Client {index} is now serving {request.remote}")

    if faster_client in class_cache:
        tg_connect = class_cache[faster_client]
        logging.debug(f"Using cached ByteStreamer object for client {index}")
    else:
        logging.debug(f"Creating new ByteStreamer object for client {index}")
        tg_connect = ByteStreamer(faster_client)
        class_cache[faster_client] = tg_connect
    file_id = await tg_connect.get_file_properties(id)
    
    if file_id.unique_id[:6] != secure_hash:
        logging.debug(f"Invalid hash for message with ID {id}")
        raise InvalidHash
    
    file_size = file_id.file_size

    if range_header:
        from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = (request.http_range.stop or file_size) - 1

    if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(
            status=416,
            body="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 1024 * 1024
    until_bytes = min(until_bytes, file_size - 1)

    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)
    body = tg_connect.yield_file(
        file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
    )

    mime_type = file_id.mime_type
    file_name = file_id.file_name
    disposition = "attachment"

    if mime_type:
        if not file_name:
            try:
                file_name = f"{secrets.token_hex(2)}.{mime_type.split('/')[1]}"
            except (IndexError, AttributeError):
                file_name = f"{secrets.token_hex(2)}.unknown"
    else:
        if file_name:
            mime_type = mimetypes.guess_type(file_id.file_name)
        else:
            mime_type = "application/octet-stream"
            file_name = f"{secrets.token_hex(2)}.unknown"

    return web.Response(
        status=206 if range_header else 200,
        body=body,
        headers={
            "Content-Type": f"{mime_type}",
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'inline; filename="{file_name}"',
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        },
    )
