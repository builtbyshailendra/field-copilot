# API and worker image. The repo layout is kept (core/, packs/) and the package installed
# in editable mode, so pack and dataset paths resolve the same way as in development.
FROM python:3.12-slim AS build
WORKDIR /app
RUN python -m venv /venv
COPY core/pyproject.toml core/pyproject.toml
COPY core/src core/src
RUN /venv/bin/pip install --no-cache-dir -e "core[rag,agents,api]"

FROM python:3.12-slim
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build /venv /venv
COPY core/pyproject.toml core/pyproject.toml
COPY core/src core/src
COPY core/evals core/evals
COPY packs packs
ENV PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FIELD_LLM_MODE=fake \
    FIELD_EMBEDDER=hashing \
    HOME=/tmp
USER 10001
EXPOSE 8020
CMD ["uvicorn", "fieldcopilot.api.app:app", "--host", "0.0.0.0", "--port", "8020"]
