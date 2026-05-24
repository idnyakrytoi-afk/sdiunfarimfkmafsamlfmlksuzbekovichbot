# Используем легкий и современный образ Python
FROM python:3.11-slim

# Устанавливаем ffmpeg (необходим для работы с голосовыми каналами и музыкой)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем исходный код бота в контейнер
COPY . /app

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Открываем порт 5000 для Flask дашборда (веб-панели)
EXPOSE 5000

# Команда для запуска бота
CMD ["python", "main.py"]