FROM node:22.16.0-bookworm-slim AS node
FROM python:3.12.13-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git curl unzip ca-certificates pandoc poppler-utils ripgrep sqlite3 nodejs npm && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pandas==2.3.3 openpyxl==3.1.5 python-docx==1.2.0 python-pptx==1.0.2 pdfplumber==0.11.9 beautifulsoup4==4.14.3 pyyaml==6.0.3 scipy==1.16.3 statsmodels==0.14.5 reportlab==4.4.4 pypdf==6.1.1 matplotlib==3.10.7 pillow==12.0.0
RUN curl -fsSL https://bun.sh/install | bash -s 'bun-v1.3.11'
ENV PATH="/root/.bun/bin:${PATH}"
RUN git init /opt/opencode && cd /opt/opencode && git remote add origin https://github.com/anomalyco/opencode.git && git fetch --depth 1 origin 23fb5e0516c99ac04a1aa46c193efda2e1b9bb24 && git checkout --detach FETCH_HEAD
COPY third_party/patches/opencode-compat.diff /tmp/opencode-compat.diff
RUN cd /opt/opencode && git apply /tmp/opencode-compat.diff && PYTHON=/usr/bin/python3 bun install --frozen-lockfile
RUN cp /root/.bun/bin/bun /usr/local/bin/bun && chmod -R a+rX /opt/opencode && useradd -m -u 10001 runner
ENV OPENCODE_DISABLE_MODELS_FETCH=1 OPENCODE_DISABLE_AUTOUPDATE=1 OPENCODE_DISABLE_SHARE=1
COPY showcase/worker.py /opt/showcase-worker.py
USER runner
WORKDIR /work
ENTRYPOINT ["python", "/opt/showcase-worker.py"]
