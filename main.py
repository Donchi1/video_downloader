from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import yt_dlp
import httpx

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
    client = httpx.AsyncClient(follow_redirects=True)
    
    async def stream_generator():
        try:
            async with client.stream("GET", url) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk
        finally:
            await client.aclose()

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }

    return StreamingResponse(
        stream_generator(), 
        media_type="video/mp4", 
        headers=headers
    )