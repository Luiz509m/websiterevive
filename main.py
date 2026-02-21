from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from crawler import crawl_website
from generator import generate_website

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "ok"}

@app.post("/analyze")
async def analyze(data: dict):
    url = data.get("url")
    colors = data.get("colors")
    uploaded_images = data.get("uploaded_images", [])

    result = await crawl_website(url)

    generated_html = await generate_website(
        title=result["title"],
        texts=result["texts"],
        colors=colors,
        images=result["images"],
        meta_description=result["meta_description"],
        uploaded_images=uploaded_images
    )

    return {"html": generated_html, "original": result}
