FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12

# Tooling for extracting sparticuz/chromium's brotli-compressed pack;
# fontconfig + dejavu fonts so chromium can render text without
# "Fontconfig error: Cannot load default config file" warnings.
RUN dnf install -y brotli tar gzip fontconfig dejavu-sans-fonts && dnf clean all

# Python deps (playwright client only — no chromium install; we use sparticuz)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt playwright==1.49.0

# Sparticuz/chromium — a chromium build hardened for AWS Lambda's runtime
# constraints. Ships as a brotli-compressed pack containing the chromium
# binary, shared libs for Amazon Linux 2023, swiftshader (software
# renderer), and fonts. Layout shipped by v131.0.0 release:
#   chromium.br, al2023.tar.br, swiftshader.tar.br, fonts.tar.br
ARG CHROMIUM_VERSION=131.0.0
ARG CHROMIUM_PACK_SHA256=55a8b4f89b94d53ea1d939cdc4568825610246635e7f9bbcbbf6e167a9b208c8
RUN mkdir -p /opt/chromium /tmp/cpack && \
    cd /tmp/cpack && \
    curl -fL "https://github.com/Sparticuz/chromium/releases/download/v${CHROMIUM_VERSION}/chromium-v${CHROMIUM_VERSION}-pack.tar" -o pack.tar && \
    echo "${CHROMIUM_PACK_SHA256}  pack.tar" | sha256sum -c - && \
    tar -xf pack.tar && \
    brotli -d chromium.br -o /opt/chromium/chromium && \
    chmod +x /opt/chromium/chromium && \
    mkdir -p /opt/chromium/lib /opt/chromium/fonts && \
    brotli -d al2023.tar.br -o al2023.tar && tar -xf al2023.tar --strip-components=1 -C /opt/chromium/lib && \
    brotli -d swiftshader.tar.br -o swiftshader.tar && tar -xf swiftshader.tar -C /opt/chromium && \
    brotli -d fonts.tar.br -o fonts.tar && tar -xf fonts.tar --strip-components=1 -C /opt/chromium/fonts && \
    rm -rf /tmp/cpack

# Chromium needs its bundled libs on the loader path. /opt/chromium holds
# the swiftshader libs (libGLESv2.so, libEGL.so) which chromium loads via
# RPATH-less dlopen; /opt/chromium/lib holds the AL2023 system libs
# (libnss, libexpat, etc.) bundled by sparticuz. No trailing colon — a
# trailing/empty entry tells the dynamic linker to also search CWD,
# which is a security issue.
ENV LD_LIBRARY_PATH=/opt/chromium:/opt/chromium/lib
ENV CHROMIUM_EXECUTABLE=/opt/chromium/chromium

COPY src/ ${LAMBDA_TASK_ROOT}/

CMD ["crawler.workers.headless_worker.handler"]
