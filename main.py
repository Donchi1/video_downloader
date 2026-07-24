# from fastapi import FastAPI, HTTPException
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import RedirectResponse
# import yt_dlp

# app = FastAPI(title="Social Media Downloader API")

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# @app.get("/")
# def home():
#     return {"status": "API is running successfully!"}

# @app.get("/extract")
# def extract_media(url: str):
#     ydl_opts = {
#         'format': 'best',
#         'quiet': True,
#         'no_warnings': True,
#     }
    
#     try:
#         with yt_dlp.YoutubeDL(ydl_opts) as ydl:
#             info = ydl.extract_info(url, download=False)
            
#             return {
#                 "title": info.get("title", "Unknown Title"),
#                 "thumbnail": info.get("thumbnail"),
#                 "platform": info.get("extractor_key"),
#                 "duration": info.get("duration"),
#                 "downloadUrl": info.get("url"),
#                 "ext": info.get("ext", "mp4")
#             }
#     except Exception as e:
#         raise HTTPException(status_code=400, detail=f"Failed to extract video: {str(e)}")

# @app.get("/proxy")
# async def proxy_download(url: str, filename: str = "video.mp4"):
#     client = httpx.AsyncClient(follow_redirects=True, timeout=60.0)
    
#     # First, make a head/get request to fetch metadata from TikTok's CDN
#     try:
#         upstream_response = await client.get(url, headers={
#             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
#             "Referer": "https://www.tiktok.com/"
#         }, stream=True)
#     except Exception as e:
#         await client.aclose()
#         raise HTTPException(status_code=502, detail=f"Failed to connect to media source: {str(e)}")

#     content_length = upstream_response.headers.get("content-length")

#     async def stream_generator():
#         try:
#             async for chunk in upstream_response.aiter_bytes():
#                 yield chunk
#         finally:
#             await upstream_response.aclose()
#             await client.aclose()

#     # Pass the Content-Length so the browser displays progress and starts immediately
#     response_headers = {
#         "Content-Disposition": f'attachment; filename="{filename}"',
#         "Access-Control-Allow-Origin": "*"
#     }
#     if content_length:
#         response_headers["Content-Length"] = content_length

#     return StreamingResponse(
#         stream_generator(), 
#         media_type="video/mp4", 
#         headers=response_headers
#     )


import os
import time
import asyncio
from typing import Dict, Tuple, Any
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import httpx
import yt_dlp

app = FastAPI(
    title="Multi-Platform Social Media Downloader API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

# Enable CORS with explicit exposed headers for cross-origin downloads
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Length"],
)

# ------------------------------------------------------------------------------
# IN-MEMORY TTL CACHE CONFIGURATION
# ------------------------------------------------------------------------------
stream_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
CACHE_TTL_SECONDS = 3600  # 1 hour cache window
cache_lock = asyncio.Lock()


def get_platform_headers(url: str) -> dict:
    """Generates anti-hotlinking headers matched to target domain CDNs."""
    domain = urlparse(url).netloc.lower()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    if "tiktok.com" in domain:
        headers["Referer"] = "https://www.tiktok.com/"
    elif "instagram.com" in domain:
        headers["Referer"] = "https://www.instagram.com/"
    elif "facebook.com" in domain or "fb.watch" in domain:
        headers["Referer"] = "https://www.facebook.com/"
    elif "youtube.com" in domain or "youtu.be" in domain:
        headers["Referer"] = "https://www.youtube.com/"

    return headers


def run_yt_dlp_extract(url: str) -> dict:
    """Synchronous worker function to execute yt_dlp in a thread pool."""
    headers = get_platform_headers(url)

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "http_headers": headers,
    }

    if os.path.exists("cookies.txt"):
        ydl_opts["cookiefile"] = "cookies.txt"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

        if "entries" in info and info["entries"]:
            info = info["entries"][0]

        download_url = info.get("url")
        if not download_url and info.get("requested_formats"):
            download_url = info["requested_formats"][0].get("url")

        if not download_url:
            raise ValueError("No direct video download stream found.")

        return {
            "title": info.get("title", "Social Media Video"),
            "thumbnail": info.get("thumbnail"),
            "platform": info.get("extractor_key"),
            "duration": info.get("duration"),
            "downloadUrl": download_url,
            "ext": info.get("ext", "mp4"),
        }


# ------------------------------------------------------------------------------
# API ENDPOINTS
# ------------------------------------------------------------------------------

@app.get("/")
def health_check():
    return {"status": "healthy", "service": "Media Extractor API"}


@app.get("/extract")
async def extract_media(url: str):
    """
    Extracts video metadata and temporary CDN link on-demand.
    Caches extraction results for 1 hour.
    """
    now = time.time()

    # 1. Check cache hit
    async with cache_lock:
        if url in stream_cache:
            cached_data, timestamp = stream_cache[url]
            if now - timestamp < CACHE_TTL_SECONDS:
                return {**cached_data, "cached": True}
            else:
                del stream_cache[url]

    # 2. Extract fresh stream link off main async loop
    try:
        extracted_data = await asyncio.to_thread(run_yt_dlp_extract, url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")

    # 3. Store result in cache
    async with cache_lock:
        stream_cache[url] = (extracted_data, now)

    return {**extracted_data, "cached": False}


@app.get("/proxy")
async def proxy_download(url: str, filename: str = "video.mp4"):
    """
    Proxies video streams directly from source CDNs.
    """
    headers = get_platform_headers(url)

    client = httpx.AsyncClient(follow_redirects=True, timeout=60.0)

    try:
        # Step 1: Open stream connection BEFORE sending StreamingResponse
        req = client.build_request("GET", url, headers=headers)
        upstream_response = await client.send(req, stream=True)

        if upstream_response.status_code >= 400:
            await upstream_response.aclose()
            await client.aclose()
            raise HTTPException(
                status_code=upstream_response.status_code,
                detail=f"CDN server returned status {upstream_response.status_code}"
            )
    except HTTPException:
        raise
    except Exception as e:
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail=f"Failed to connect to CDN: {str(e)}"
        )

    # Step 2: Extract Content-Length header if provided by CDN
    content_length = upstream_response.headers.get("content-length")

    async def video_stream_generator():
        try:
            async for chunk in upstream_response.aiter_bytes(chunk_size=65536):
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    response_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }
    if content_length:
        response_headers["Content-Length"] = content_length

    return StreamingResponse(
        video_stream_generator(),
        media_type="video/mp4",
        headers=response_headers,
    )