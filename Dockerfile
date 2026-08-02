FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 8000
EXPOSE 8443

CMD ["python", "-m", "dynamokv.run"]
