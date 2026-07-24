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
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import httpx
import yt_dlp

app = FastAPI(title="Multi-Platform Social Media Downloader API")

# Setup CORS middleware with exposed headers for forced downloads
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Length"],
)


def get_platform_headers(url: str) -> dict:
    """Generates appropriate browser headers and Referers to bypass CDN hotlinking blocks."""
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


@app.get("/")
def home():
    return {"status": "API is running successfully!"}


@app.get("/extract")
def extract_media(url: str):
    headers = get_platform_headers(url)

    ydl_opts = {
        # Select single-file MP4 streams to avoid requiring server-side ffmpeg merging
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "http_headers": headers,
    }

    # Pass cookies file to yt_dlp if present in environment/root directory
    if os.path.exists("cookies.txt"):
        ydl_opts["cookiefile"] = "cookies.txt"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Handle playlists or multi-item posts (e.g. IG Carousels)
            if "entries" in info and info["entries"]:
                info = info["entries"][0]

            download_url = info.get("url")
            if not download_url and info.get("requested_formats"):
                download_url = info["requested_formats"][0].get("url")

            if not download_url:
                raise HTTPException(
                    status_code=404, detail="Could not find a direct download stream URL."
                )

            return {
                "title": info.get("title", "Social Media Video"),
                "thumbnail": info.get("thumbnail"),
                "platform": info.get("extractor_key"),
                "duration": info.get("duration"),
                "downloadUrl": download_url,
                "ext": info.get("ext", "mp4"),
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract video: {str(e)}")


@app.get("/proxy")
async def proxy_download(url: str, filename: str = "video.mp4"):
    headers = get_platform_headers(url)

    async def video_stream_generator():
        # Open stream connection using client.stream context manager
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            async with client.stream("GET", url, headers=headers) as upstream_response:
                if upstream_response.status_code >= 400:
                    raise HTTPException(
                        status_code=upstream_response.status_code,
                        detail=f"CDN connection failed with status code {upstream_response.status_code}",
                    )

                async for chunk in upstream_response.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        video_stream_generator(),
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )