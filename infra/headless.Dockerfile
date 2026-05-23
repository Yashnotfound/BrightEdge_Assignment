FROM public.ecr.aws/lambda/python:3.12

# System deps for chromium
RUN dnf install -y \
    nss nspr atk at-spi2-atk cups-libs dbus-libs libdrm libxkbcommon \
    libXcomposite libXdamage libXfixes libXrandr mesa-libgbm pango \
    cairo alsa-lib && dnf clean all

# Install Python deps
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt playwright==1.49.0

# Install chromium browser
RUN playwright install chromium

# Application source
COPY src/ ${LAMBDA_TASK_ROOT}/

# Lambda will invoke this handler
CMD ["crawler.workers.headless_worker.handler"]
