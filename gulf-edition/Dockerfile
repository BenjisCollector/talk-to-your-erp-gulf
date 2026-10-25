FROM python:3.12-slim
WORKDIR /app
COPY erp ./erp
COPY web ./web
ENV HOST=0.0.0.0 PORT=8080 ERP_DB=/app/data/erp.db PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python3", "-m", "erp.server"]
