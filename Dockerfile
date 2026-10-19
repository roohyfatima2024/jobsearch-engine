FROM python:3.12-slim
WORKDIR /workspace
RUN pip install --no-cache-dir anthropic
COPY app/ /workspace/app/
USER 1000:1000
CMD ["python", "-m", "app.main", "health"]
