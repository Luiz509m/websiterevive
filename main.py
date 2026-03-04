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
    colors = data.get("colors", ["#1a1a1a", "#c9a84c"])
    uploaded_images = data.get("uploaded_images", [])
    business_type = data.get("business_type", "restaurant")
    food_type = data.get("food_type", "fine_dining")

    result = await crawl_website(url)
    generated_html = await generate_website(
        title=result["title"],
        texts=result["texts"],
        colors=colors,
        images=result["images"],
        meta_description=result["meta_description"],
        uploaded_images=uploaded_images,
        business_type=business_type,
        food_type=food_type
    )
    return {"html": generated_html, "original": result}
