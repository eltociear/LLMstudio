import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from llmstudio.engine.providers import *

ENGINE_BASE_ENDPOINT = "/api/engine"
ENGINE_HEALTH_ENDPOINT = "/health"
ENGINE_TITLE = "LLMstudio Engine API"
ENGINE_DESCRIPTION = "The core API for LLM interactions"
ENGINE_VERSION = "0.1.0"
ENGINE_HOST = os.getenv("ENGINE_HOST", "localhost")
ENGINE_PORT = int(os.getenv("ENGINE_PORT", 8000))
ENGINE_URL = f"http://{ENGINE_HOST}:{ENGINE_PORT}"
UI_HOST = os.getenv("ENGINE_HOST", "localhost")
UI_PORT = int(os.getenv("UI_PORT", 8000))
UI_URL = f"http://{UI_HOST}:{UI_PORT}"
LOG_LEVEL = os.getenv("LOG_LEVEL", "critical")
TRACKING_BASE_ENDPOINT = "/api/tracking"


# Models for Configuration
class ModelConfig(BaseModel):
    mode: str
    max_tokens: int
    input_token_cost: float
    output_token_cost: float


class ProviderConfig(BaseModel):
    id: str
    name: str
    chat: bool
    embed: bool
    keys: Optional[List[str]] = None
    models: Optional[Dict[str, ModelConfig]] = None
    parameters: Optional[Dict[str, Any]] = None


class EngineConfig(BaseModel):
    providers: Dict[str, ProviderConfig]


# Configuration Loading
def _load_engine_config() -> EngineConfig:
    config_path = Path(os.path.join(os.path.dirname(__file__), "config.yaml"))
    try:
        config_data = yaml.safe_load(config_path.read_text())
        return EngineConfig(**config_data)
    except FileNotFoundError:
        raise RuntimeError(f"Configuration file not found at {config_path}")
    except yaml.YAMLError as e:
        raise RuntimeError(f"Error parsing YAML configuration: {e}")
    except ValidationError as e:
        raise RuntimeError(f"Error in configuration data: {e}")

## Tracking
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from llmstudio.tracking import crud, models, schemas
from llmstudio.tracking.database import SessionLocal, engine

models.Base.metadata.create_all(bind=engine)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Functions for API Operations
def create_engine_app(config: EngineConfig = _load_engine_config()) -> FastAPI:
    app = FastAPI(
        title=ENGINE_TITLE,
        description=ENGINE_DESCRIPTION,
        version=ENGINE_VERSION,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get(ENGINE_HEALTH_ENDPOINT)
    def health_check():
        """Health check endpoint to ensure the API is running."""
        return {"status": "healthy", "message": "Engine is up and running"}

    @app.get(f"{ENGINE_BASE_ENDPOINT}/providers")
    def get_providers():
        """Return all providers supported."""
        return list(config.providers.keys())

    @app.get(f"{ENGINE_BASE_ENDPOINT}/models")
    def get_models(provider: Optional[str] = None):
        """Return all models supported with the provider as a key."""
        all_models = {}
        for provider_name, provider_config in config.providers.items():
            if provider and provider_name != provider:
                continue
            if provider_config.models:
                all_models[provider_name] = {}
                all_models[provider_name]["name"] = provider_config.name
                all_models[provider_name]["models"] = list(
                    provider_config.models.keys()
                )
        return all_models[provider] if provider else all_models

    @app.get("/logs")
    def get_logs():
        """Return the logs in JSONL format."""
        logs_path = Path(os.path.join(os.path.dirname(__file__), "logs.jsonl"))
        if logs_path.exists():
            with open(logs_path, "r") as file:
                logs = [json.loads(line) for line in file]
            return logs

    @app.post("/api/export")
    async def export(request: Request):
        data = await request.json()
        csv_content = ""

        if len(data) > 0:
            csv_content += ";".join(data[0].keys()) + "\n"
            for execution in data:
                csv_content += (
                    ";".join([json.dumps(value) for value in execution.values()]) + "\n"
                )

        headers = {"Content-Disposition": "attachment; filename=parameters.csv"}
        return StreamingResponse(
            iter([csv_content]), media_type="text/csv", headers=headers
        )
    
    @app.post(f"{ENGINE_BASE_ENDPOINT}/projects/", response_model=schemas.Project)
    def create_project(project: schemas.ProjectCreate, db: Session = Depends(get_db)):
        db_project = crud.get_project_by_name(db, name=project.name)
        if db_project:
            raise HTTPException(status_code=400, detail="Project already registered")
        return crud.create_project(db=db, project=project)

    @app.get(f"{ENGINE_BASE_ENDPOINT}/projects/", response_model=list[schemas.Project])
    def read_projects(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
        projects = crud.get_projects(db, skip=skip, limit=limit)
        return projects

    @app.get(f"{ENGINE_BASE_ENDPOINT}/projects/{{project_id}}", response_model=schemas.Project)
    def read_project(project_id: int, db: Session = Depends(get_db)):
        db_project = crud.get_project(db, project_id=project_id)
        if db_project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return db_project

    @app.post(f"{ENGINE_BASE_ENDPOINT}/projects/{{project_id}}/sessions/", response_model=schemas.Session)
    def create_session(
        project_id: int, session: schemas.SessionCreate, db: Session = Depends(get_db)
    ):
        return crud.create_session(db=db, session=session, project_id=project_id)

    @app.get(f"{ENGINE_BASE_ENDPOINT}/sessions/", response_model=list[schemas.Session])
    def read_sessions(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
        sessions = crud.get_sessions(db, skip=skip, limit=limit)
        return sessions

    @app.post(f"{ENGINE_BASE_ENDPOINT}/sessions/{{session_id}}/logs/", response_model=schemas.Log)
    def add_log(
        session_id: int, log: schemas.LogCreate, db: Session = Depends(get_db)
    ):
        return crud.add_log(db=db, log=log, session_id=session_id)

    @app.get(f"{ENGINE_BASE_ENDPOINT}/logs/", response_model=list[schemas.Log])
    def read_logs(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
        logs = crud.get_logs(db, skip=skip, limit=limit)
        return logs
    
    # Function to create a chat handler for a provider
    def create_chat_handler(provider_config):
        async def chat_handler(request: Request):
            """Endpoint for chat functionality."""
            provider_class = provider_registry.get(f"{provider_config.name}Provider")
            provider_instance = provider_class(provider_config)
            return await provider_instance.chat(await request.json())

        return chat_handler

    # Dynamic route creation based on the 'chat' boolean
    for provider_name, provider_config in config.providers.items():
        if provider_config.chat:
            app.post(f"{ENGINE_BASE_ENDPOINT}/chat/{provider_name}")(
                create_chat_handler(provider_config)
            )

    return app


def is_api_running(url: str) -> bool:
    try:
        response = requests.get(url, timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False


def run_engine_app():
    print(f"Running Engine on {ENGINE_HOST}:{ENGINE_PORT}")
    try:
        engine = create_engine_app()
        uvicorn.run(
            engine,
            host=ENGINE_HOST,
            port=ENGINE_PORT,
        )
    except Exception as e:
        print(f"Error running the Engine app: {e}")
