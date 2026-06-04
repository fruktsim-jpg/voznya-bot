FROM python:3.12-slim

# Чтобы Python не буферизовал логи и не создавал .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Сначала зависимости — лучше кэшируется при пересборке
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Затем исходный код
COPY . .

# Точка входа: применяем миграции, затем запускаем бота
CMD ["sh", "-c", "alembic upgrade head && python -m app.main"]
