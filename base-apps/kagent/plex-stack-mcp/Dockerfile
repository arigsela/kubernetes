# base-apps/kagent/plex-stack-mcp/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY plex_stack_mcp ./plex_stack_mcp
RUN pip install --no-cache-dir .
EXPOSE 3000
CMD ["python", "-m", "plex_stack_mcp.server"]
