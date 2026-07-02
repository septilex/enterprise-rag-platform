from fastapi import FastAPI

app = FastAPI(title="Enterprise RAG Platform")


@app.get("/health")
def health():
    return {"status": "ok"}