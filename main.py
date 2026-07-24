from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import yt_dlp

app = FastAPI(title="Social Media Downloader API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"status": "API is running successfully!"}

@app.get("/extract")
def extract_media(url: str):
    ydl_opts = {
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            return {
                "title": info.get("title", "Unknown Title"),
                "thumbnail": info.get("thumbnail"),
                "platform": info.get("extractor_key"),
                "duration": info.get("duration"),
                "downloadUrl": info.get("url"),
                "ext": info.get("ext", "mp4")
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract video: {str(e)}")

@app.get("/proxy")
async def proxy_download(url: str, filename: str = "video.mp4"):
    client = httpx.AsyncClient(follow_redirects=True, timeout=60.0)
    
    # First, make a head/get request to fetch metadata from TikTok's CDN
    try:
        upstream_response = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/"
        }, stream=True)
    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Failed to connect to media source: {str(e)}")

    content_length = upstream_response.headers.get("content-length")

    async def stream_generator():
        try:
            async for chunk in upstream_response.aiter_bytes():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    # Pass the Content-Length so the browser displays progress and starts immediately
    response_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Allow-Origin": "*"
    }
    if content_length:
        response_headers["Content-Length"] = content_length

    return StreamingResponse(
        stream_generator(), 
        media_type="video/mp4", 
        headers=response_headers
    )