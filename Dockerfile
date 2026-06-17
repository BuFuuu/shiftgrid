FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PROJECTS_DIR=/data/projects
# 8000 = operator UI (+ API); 8001 = agent-facing API only. 
EXPOSE 8000 8001
USER nobody
CMD ["python", "app.py"]
