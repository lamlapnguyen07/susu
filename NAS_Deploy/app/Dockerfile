FROM python:3.11-slim

WORKDIR /app

# 台北時區 + 即時 log
ENV TZ=Asia/Taipei
ENV PYTHONUNBUFFERED=1
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
