"""FastAPI application."""

from fastapi import FastAPI
from routes import router

app = FastAPI(title="TODO API")
app.include_router(router)
