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
from urllib.parse import urlparse, unquote
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
# Storage structure: { "original_post_url": (data_dict, timestamp) }
stream_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
CACHE_TTL_SECONDS = 3600  # 1 hour cache window
cache_lock = asyncio.Lock()


def get_platform_headers(url: str) -> dict:
    """
    Generates realistic browser headers, Range headers, and Referers
    matched to target domain CDNs (TikTok, Facebook, Instagram, YouTube).
    """
    domain = urlparse(url).netloc.lower()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Encoding": "identity",  # Prevent unexpected compression drops
        "Accept-Language": "en-US,en;q=0.9",
        "Range": "bytes=0-",            # Required for byte-range streaming from FB/TikTok CDNs
    }

    if "fbcdn.net" in domain or "facebook.com" in domain or "fb.watch" in domain:
        headers["Referer"] = "https://www.facebook.com/"
        headers["Origin"] = "https://www.facebook.com"
        headers["Sec-Fetch-Dest"] = "video"
        headers["Sec-Fetch-Mode"] = "cors"
        headers["Sec-Fetch-Site"] = "cross-site"
    elif "tiktok.com" in domain:
        headers["Referer"] = "https://www.tiktok.com/"
        headers["Origin"] = "https://www.tiktok.com"
    elif "instagram.com" in domain:
        headers["Referer"] = "https://www.instagram.com/"
        headers["Origin"] = "https://www.instagram.com"
    elif "youtube.com" in domain or "youtu.be" in domain or "googlevideo.com" in domain:
        headers["Referer"] = "https://www.youtube.com/"

    return headers


def run_yt_dlp_extract(url: str) -> dict:
    """Synchronous worker function to execute yt_dlp in a thread pool."""
    headers = get_platform_headers(url)

    ydl_opts = {
        # Prefers pre-merged single progressive MP4 streams
        "format": "b[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "http_headers": headers,
    }

    if os.path.exists("cookies.txt"):
        ydl_opts["cookiefile"] = "cookies.txt"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

        # Handle carousel/playlist posts
        if "entries" in info and info["entries"]:
            info = info["entries"][0]

        formats_list = []
        if "formats" in info:
            for f in info["formats"]:
                # Capture progressive audio+video formats for multi-quality selection
                if f.get("ext") == "mp4" and f.get("vcodec") != "none" and f.get("acodec") != "none":
                    formats_list.append({
                        "quality": f.get("format_note", "Standard Quality"),
                        "downloadUrl": f.get("url"),
                        "size": f"{round(f.get('filesize', 0) / (1024 * 1024), 1)} MB" if f.get('filesize') else "N/A"
                    })

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
            "qualities": formats_list if formats_list else None,
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
    Extracts video metadata and temporary CDN links on-demand.
    Caches results in memory for 1 hour for fast repeat views.
    """
    cleaned_url = unquote(url)
    now = time.time()

    # 1. Check cache hit
    async with cache_lock:
        if cleaned_url in stream_cache:
            cached_data, timestamp = stream_cache[cleaned_url]
            if now - timestamp < CACHE_TTL_SECONDS:
                return {**cached_data, "cached": True}
            else:
                del stream_cache[cleaned_url]  # Evict expired entry

    # 2. Extract fresh stream link off main async loop
    try:
        extracted_data = await asyncio.to_thread(run_yt_dlp_extract, cleaned_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")

    # 3. Store result in cache
    async with cache_lock:
        stream_cache[cleaned_url] = (extracted_data, now)

    return {**extracted_data, "cached": False}


@app.get("/proxy")
async def proxy_download(url: str, filename: str = "video.mp4"):
    """
    Proxies video streams directly from source CDNs.
    Unquotes escaped characters and pre-checks CDN response before streaming.
    """
    # Clean URL parameters (converts escaped u00253D -> = and unquotes encoded entities)
    cleaned_url = unquote(url).replace("u00253D", "=")
    headers = get_platform_headers(cleaned_url)

    client = httpx.AsyncClient(follow_redirects=True, timeout=60.0)

    try:
        # Pre-flight check: Open request stream BEFORE returning StreamingResponse
        req = client.build_request("GET", cleaned_url, headers=headers)
        upstream_response = await client.send(req, stream=True)

        if upstream_response.status_code >= 400:
            await upstream_response.aclose()
            await client.aclose()
            raise HTTPException(
                status_code=upstream_response.status_code,
                detail=f"CDN returned HTTP {upstream_response.status_code}"
            )
    except HTTPException:
        raise
    except Exception as e:
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail=f"Failed to connect to CDN: {str(e)}"
        )

    content_length = upstream_response.headers.get("content-length")
    content_type = upstream_response.headers.get("content-type", "video/mp4")

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
        media_type=content_type,
        headers=response_headers,
    )