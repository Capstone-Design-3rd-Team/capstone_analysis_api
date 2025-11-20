## 🌐 EC2에서 최종 재배포
# ===============================
# Stage 1: Build (패키지 설치)
# ===============================
FROM python:3.10-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium-driver \
    tesseract-ocr \
    tesseract-ocr-kor \
    fonts-nanum \
    libglib2.0-0 \
    libnss3 \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU 전용 PyTorch 설치
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    torch==2.5.1+cpu torchvision==0.20.1+cpu

# 나머지 Python 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ===============================
# Stage 2: Runtime
# ===============================
FROM python:3.10-slim

# 런타임에도 tesseract 포함 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium-driver \
    tesseract-ocr \
    tesseract-ocr-kor \
    fonts-nanum \
    libglib2.0-0 \
    libnss3 \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 빌드 단계에서 설치한 Python 패키지 복사
COPY --from=builder /usr/local /usr/local

# 코드와 모델 복사
COPY . /app
COPY downloads/checkpoints /app/checkpoints

# 환경 변수
ENV CHROME_BIN=/usr/bin/chromium
ENV PATH=$PATH:/usr/bin
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/4.00/tessdata/
ENV MODEL_PATH=/app/checkpoints/screenrecognition-web350k-vins.torchscript

# 실행
ENTRYPOINT ["python", "/app/element_analysis/main.py"]
